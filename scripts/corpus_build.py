"""Rebuild source and segmentation proofs without changing retained curation.

Every subprocess is isolated by workbook, bounded by time and memory, and writes
durable stage evidence. Passing reconstruction is not Harbor promotion.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import json
import os
import re
from pathlib import Path
import shutil
import signal
import sys
import time

from scripts.corpus import ROOT, read_inventory, digest, code_hash, object_hash, snapshot_code
from scripts.batch.hardened_runner import atomic_write_json, _run_in_process_group, ConversionTimeout


@contextmanager
def cohort_lock(run_root):
    """Serialize writers to the same ledgers across different code snapshots."""
    root = Path(run_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / ".workbook-build.lock",
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def archive_build_log(directory):
    """Retain exact attempt logs before the compatibility path is reused."""
    directory = Path(directory)
    log = directory / "build.log"
    if not log.is_file():
        return None
    fingerprint = digest(log)
    archived = directory / "build-logs" / f"{fingerprint}.log"
    archived.parent.mkdir(exist_ok=True)
    if not archived.exists():
        shutil.copyfile(log, archived)
    if digest(archived) != fingerprint:
        raise ValueError("archived workbook build log changed")
    return {"path": str(archived), "sha256": fingerprint}


def retained_curation(args, source, ledger):
    """Keep an initial selection even if its first transformation was interrupted."""
    wb = source["workbook_id"]
    configured = Path(args.curation_root) / wb / "curation.toml"
    if configured.is_file():
        return configured
    run = Path(args.run_root)
    candidates = []
    for attempt in ledger.get("build_history", []) + [ledger.get("build", {})]:
        normalization = attempt.get("normalization", {})
        if normalization.get("curation"):
            receipt_path = Path(normalization["manifest"])
            if digest(receipt_path) != normalization["manifest_sha256"]:
                raise ValueError("retained selection receipt changed")
            receipt = json.loads(receipt_path.read_text())
            path = Path(normalization["curation"])
            if (receipt.get("original_sha256") != source["sha256"]
                    or digest(path) != receipt.get("curation_sha256")):
                raise ValueError("retained selection source or curation changed")
            candidates.append(path)
    # A killed inspection can have selected targets before publishing a receipt.
    # Preserve that selection, bound to the exact copied original workbook.
    for path in sorted((run / "normalization" / wb).glob(
            "tables-attempt-*/inspection-segments/*/curation.toml")):
        original = path.parents[2] / "inspection-source" / f"{wb}.xlsx"
        if path.parent.name != wb or digest(original) != source["sha256"]:
            raise ValueError("interrupted selection is not bound to the original source")
        candidates.append(path)
    segmented = run / "segments" / wb / "curation.toml"
    if segmented.is_file():
        candidates.append(segmented)
    if len({digest(path) for path in candidates}) > 1:
        raise ValueError("conflicting retained target selections require explicit curation")
    return candidates[0] if candidates else configured


def reusable_table_candidate(source, curation, ledger):
    """Reuse a completed transformation only after all current proofs validate."""
    from xl_presentation_tables import POLICY, verify_materialization
    for attempt in reversed(ledger.get('build_history', [])):
        record = attempt.get('normalization', {})
        if record.get('policy_version') != POLICY:
            continue
        try:
            if (record['original_sha256'] != source['sha256']
                    or digest(record['candidate']) != record['candidate_sha256']
                    or digest(record['manifest']) != record['manifest_sha256']
                    or digest(record['curation']) != digest(curation)):
                continue
            receipt = json.loads(Path(record['manifest']).read_text())
            verify_materialization(source['path'], record['candidate'], curation, receipt)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        return dict(record)
    return None



SOURCE_CHECKPOINT_SCHEMA = "corpus-source-checkpoint/v1"
QUARANTINE_POLICY = "quarantined-table-source-inventory/v1"


def configured_quarantine(args, source, curation):
    """Select only an explicit approved two-edge record; never infer a profile."""
    if not args.source_inventory:
        return None
    from xl_table_quarantine_lineage import load
    selected = [r for r in load(args.source_inventory).get("workbooks", [])
                if r.get("workbook_id") == source["workbook_id"]
                and (r.get("transformation") or {}).get("schema_version") == "source-transformation-lineage/v2"]
    if not selected:
        return None
    if recalc_evidence_present(args, source["workbook_id"]):
        raise ValueError("quarantine chain cannot be mixed with ordinary recalculation evidence")
    from xl_table_quarantine_inventory import adopt_approved_inventory
    result = adopt_approved_inventory(args.source_inventory, batch_id=args.inventory_batch_id,
        workbook_id=source["workbook_id"], original=source["path"], curation=curation)
    if result["original_sha256"] != source["sha256"]:
        raise ValueError("quarantine original differs from frozen corpus")
    return result


def source_checkpoint_bindings(args, source, build, prepared_source):
    """Dependencies of source/AST construction, separate from segmentation code."""
    return {
        "workbook_id": source["workbook_id"],
        "original_sha256": source["sha256"],
        "prepared_source_sha256": digest(prepared_source),
        "curation_sha256": build["bindings"].get("curation_sha256"),
        "inventory_sha256": digest(args.source_inventory) if args.source_inventory else None,
        "inventory_batch_id": args.inventory_batch_id if args.source_inventory else None,
        "normalization_sha256": object_hash(build.get("normalization")),
        "recalculation_sha256": object_hash(build.get("recalculation")),
        "trusted_public_key_sha256": (
            digest(args.trusted_public_key) if build.get("recalculation") else None),
    }


def reuse_completed_source(args, source, ledger, prepared_source, restriction_options,
                           recalc_options):
    """Revalidate a completed source through the production publication gates.

    A broad implementation fingerprint never licenses reuse. The original,
    transformation, inventory and trusted inputs must match, and current source
    health plus the production AST builder and all publication proofs must pass.
    Re-publication validates existing immutable bytes; it never activates them or
    invokes the AST builder. Segmentation is deliberately outside this checkpoint.
    """
    from xl_source_publication import (
        validate_source_generation, validate_ast_provenance,
        create_ast_provenance, publish_source_generation,
    )
    from xl_source_health import inspect_workbook
    from xl_source_recalc import create_identity_documents

    current = ledger["build"]
    normalization = current.get("normalization")
    if normalization:
        if (normalization.get("original_sha256") != source["sha256"]
                or digest(normalization["candidate"]) != normalization["candidate_sha256"]
                or digest(normalization["manifest"]) != normalization["manifest_sha256"]
                or digest(prepared_source) != normalization["candidate_sha256"]):
            raise ValueError("normalization evidence changed before source reuse")
        receipt = json.loads(Path(normalization["manifest"]).read_text())
        if normalization.get("policy_version") == QUARANTINE_POLICY:
            from xl_table_quarantine_inventory import validate_adoption
            validate_adoption(source["path"], prepared_source, normalization)
        elif normalization.get("policy_version") in {"presentation-data-tables/v1", "presentation-data-tables/v2"}:
            from xl_presentation_tables import verify_materialization
            verify_materialization(source["path"], prepared_source, normalization["curation"], receipt)
        else:
            from xl_volatile_formula_remediation import verify_manifest
            if digest(normalization["plan"]) != normalization["plan_sha256"]:
                raise ValueError("normalization plan changed before source reuse")
            verify_manifest(source["path"], prepared_source,
                            json.loads(Path(normalization["plan"]).read_text()), receipt)
    expected = source_checkpoint_bindings(args, source, current, prepared_source)
    if digest(source["path"]) != source["sha256"]:
        raise ValueError("original source changed before source checkpoint reuse")
    root = Path(args.run_root).resolve() / "sources" / source["workbook_id"]
    production_args = dict(production=True, workbook_id=source["workbook_id"],
                           max_range_expand=100, include_constants=True,
                           include_cached_values=True)
    for previous in reversed(ledger.get("build_history", [])):
        if (previous.get("stage") not in {"segmentation", "preflight", "reconstruction"}
                or not previous.get("source_generation_id")
                or not previous.get("source_manifest_sha256")):
            continue
        try:
            generation_id = previous["source_generation_id"]
            # Never follow an arbitrary generation path from a mutable ledger.
            if not re.fullmatch(r"[0-9a-f]{64}", generation_id):
                raise ValueError("invalid source generation identity")
            generation = root / "generations" / generation_id
            manifest_path = generation / "generation-manifest.json"
            if digest(manifest_path) != previous["source_manifest_sha256"]:
                raise ValueError("source manifest differs from completed checkpoint")
            manifest = validate_source_generation(generation, expected_generation_id=generation_id)
            if manifest["layout"]["workbook_id"] != source["workbook_id"]:
                raise ValueError("source workbook identity changed")
            frozen_source = generation / manifest["layout"]["source_workbook"]
            if digest(frozen_source) != expected["prepared_source_sha256"]:
                raise ValueError("prepared source differs from completed checkpoint")
            health = inspect_workbook(frozen_source)
            if health != json.loads((generation / "health.json").read_text()):
                raise ValueError("current source health differs from checkpoint")
            request = json.loads((generation / "request.json").read_text())
            result = json.loads((generation / "result.json").read_text())
            if recalc_options:
                if request != recalc_options["request"] or result != recalc_options["result"]:
                    raise ValueError("trusted recalculation documents changed")
            elif request.get("schema_version") == "source-identity-request/v2":
                if (request, result) != create_identity_documents(frozen_source):
                    raise ValueError("identity evidence changed")
            elif request.get("schema_version") == "source-recalc-request/v2":
                raise ValueError("trusted recalculation requires current original and trust anchor")
            # Output selection and unrelated inventory entries do not affect
            # an untransformed native identity source's AST. Establish that
            # narrow case from validated bytes and current health first; every
            # AST/provenance/publication gate below still runs on reuse.
            native_identity = (
                health.get("route") == "pass"
                and request.get("schema_version") == "source-identity-request/v2"
                and Path(source["path"]).suffix.casefold() == ".xlsx"
                and expected["original_sha256"] == expected["prepared_source_sha256"]
                and not recalc_options
                and not restriction_options
                and all(attempt.get(kind) is None
                        for attempt in (current, previous)
                        for kind in ("normalization", "recalculation"))
            )
            irrelevant = ({"curation_sha256", "inventory_sha256", "inventory_batch_id"}
                          if native_identity else set())

            def source_dependencies(bindings):
                if not isinstance(bindings, dict):
                    return bindings
                return {key: value for key, value in bindings.items()
                        if key not in irrelevant}

            checkpoint = previous.get("source_checkpoint")
            if checkpoint:
                if (previous.get("source_checkpoint_sha256") != object_hash(checkpoint)
                        or checkpoint.get("ast_provenance_sha256") != digest(generation / "ast-provenance.json")
                        or checkpoint.get("schema_version") != SOURCE_CHECKPOINT_SCHEMA
                        or checkpoint.get("state") != "complete"
                        or checkpoint.get("generation_id") != generation_id
                        or checkpoint.get("manifest_sha256") != digest(manifest_path)
                        or source_dependencies(checkpoint.get("bindings"))
                        != source_dependencies(expected)):
                    raise ValueError("source checkpoint dependencies changed")
            else:
                # Migration of old completed ledgers is conservative and still
                # runs every current validator below. No incomplete AST is used.
                bindings = previous.get("bindings", {})
                for key, value in (("source_sha256", expected["original_sha256"]),
                                   ("curation_sha256", expected["curation_sha256"]),
                                   ("inventory_sha256", expected["inventory_sha256"])):
                    if key in irrelevant:
                        continue
                    if key not in bindings or bindings[key] != value:
                        raise ValueError("historical source dependency changed")
                if (object_hash(previous.get("normalization")) != expected["normalization_sha256"]
                        or object_hash(previous.get("recalculation")) != expected["recalculation_sha256"]):
                    raise ValueError("historical source evidence changed")
            ast = generation / manifest["layout"]["ast_directory"]
            provenance = validate_ast_provenance(frozen_source, ast,
                generation / "ast-provenance.json", builder_args=production_args)
            live = create_ast_provenance(frozen_source, ast, builder_args=production_args)
            # Snapshots have different absolute paths. Require the complete same
            # production builder file set, names, contents and sizes, while each
            # recorded path was independently validated above.
            project = lambda entries: [{k: row[k] for k in ("name", "sha256", "size_bytes")}
                                       for row in entries]
            if project(provenance["builder"]["code"]) != project(live["builder"]["code"]):
                raise ValueError("current production AST builder changed")
            options = dict(restriction_options)
            options.update({k: v for k, v in recalc_options.items() if k not in {"request", "result"}})
            destination, verified = publish_source_generation(frozen_source, ast, root,
                request=request, result=result, health=health, ast_provenance=provenance,
                builder_args=production_args, activate=False, **options)
            if (destination.resolve() != generation.resolve()
                    or verified != manifest or digest(manifest_path) != previous["source_manifest_sha256"]):
                raise ValueError("source revalidation did not preserve the exact immutable generation")
            return generation, manifest, {
                "schema_version": SOURCE_CHECKPOINT_SCHEMA, "state": "complete",
                "generation_id": generation_id, "manifest_sha256": digest(manifest_path),
                "bindings": expected, "reused_from_attempt": previous.get("attempts"),
                "ast_provenance_sha256": digest(generation / "ast-provenance.json"),
                "current_builder": live["builder"],
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            current.setdefault("source_reuse_rejections", []).append({
                "generation_id": previous.get("source_generation_id"),
                "attempt": previous.get("attempts"), "reason": str(exc)})
    return None


def dead_name_only_plan(source_path, route, *, recalculated=False):
    """Select existing, proven dead-name cleanup for an original restricted source."""
    if recalculated or route != "restricted_pass" or Path(source_path).suffix.casefold() != ".xlsx":
        return None
    from xl_volatile_formula_remediation import build_plan, RemediationError
    try:
        plan = build_plan(source_path)
    except (OSError, RemediationError):
        # This optional recovery must not replace the original source gate.
        return None
    actions = plan.get("actions")
    if (plan.get("status") != "eligible" or plan.get("unresolved") != []
            or plan.get("before_health", {}).get("route") != "restricted_pass"
            or not isinstance(actions, list) or not actions
            or any(not isinstance(action, dict)
                   or action.get("kind") != "remove_dead_defined_name"
                   for action in actions)):
        return None
    return plan


def recalc_evidence_present(args, workbook_id):
    """A shared result root selects only workbooks with explicit job documents."""
    if not args.recalc_root:
        return False
    job = Path(args.recalc_root) / workbook_id
    return any((job / name).exists() for name in ("request.json", "result.json"))


def worker(args, source):
    from xl_source_recalc import prepare_source_generation
    from xl_segment import segment
    from xl_preflight import preflight
    from argparse import Namespace
    wb = source["workbook_id"]
    run = Path(args.run_root).resolve()
    ledger_path = run / "workbooks" / wb / "ledger.json"
    ledger = json.loads(ledger_path.read_text())
    build = ledger["build"]

    def checkpoint(stage, **details):
        build.update(stage=stage, **details)
        atomic_write_json(ledger_path, ledger)

    try:
        if code_hash() != build["bindings"]["code_sha256"]:
            raise ValueError("worker implementation differs from checkpoint binding")
        if digest(source["path"]) != source["sha256"]:
            raise ValueError("original source hash changed")
        curation = Path(build["curation_path"])
        if (digest(curation) if curation.is_file() else None) != build["bindings"]["curation_sha256"]:
            raise ValueError("retained curation changed after checkpoint binding")
        if ((digest(args.source_inventory) if args.source_inventory else None)
                != build["bindings"].get("inventory_sha256")
                or (args.inventory_batch_id if args.source_inventory else None)
                != build["bindings"].get("inventory_batch_id")):
            raise ValueError("source inventory changed after checkpoint binding")
        source_path = source["path"]
        route = ledger.get("route")
        quarantine = configured_quarantine(args, source, curation)
        if (object_hash(quarantine) if quarantine else None) != build["bindings"].get("quarantine_adoption_sha256"):
            raise ValueError("quarantine approval or evidence changed after checkpoint binding")
        recalc_options = {}
        if args.recalc_root and (
                ledger.get("failure") == "trusted_recalculation_required"
                or build["bindings"].get("recalc_request_sha256") is not None
                or recalc_evidence_present(args, wb)):
            if (ledger.get("failure") != "trusted_recalculation_required"
                    and not (ledger.get("state") == "READY" and route == "pass")):
                raise ValueError("explicit recalculation adoption requires READY/pass source or existing recalculation-required route")
            # Intake readiness does not supersede explicitly supplied, bound
            # trusted evidence. The unchanged source publication gate validates
            # signatures, current health and original/output equivalence.
            job = Path(args.recalc_root) / wb
            request = json.loads((job / "request.json").read_text())
            result = json.loads((job / "result.json").read_text())
            if request.get("source_sha256") != source["sha256"]:
                raise ValueError("trusted request is not bound to original corpus source")
            if (digest(job / "request.json") != build["bindings"].get("recalc_request_sha256")
                    or digest(job / "result.json") != build["bindings"].get("recalc_result_sha256")
                    or digest(args.trusted_public_key) != build["bindings"].get("trusted_public_key_sha256")):
                raise ValueError("trusted evidence changed after checkpoint binding")
            source_path = job / f"{wb}.xlsx"
            recalc_options = dict(request=request, result=result,
                trusted_runner_public_key=args.trusted_public_key, original_source_path=source["path"])
            checkpoint("trusted_recalculation", recalculation={
                "request": str(job / "request.json"), "request_sha256": digest(job / "request.json"),
                "result": str(job / "result.json"), "result_sha256": digest(job / "result.json")})
        dead_name_plan = dead_name_only_plan(
            source_path, route,
            recalculated=(bool(recalc_options)
                          or build.get("recalculation") is not None
                          or ledger.get("failure") == "trusted_recalculation_required"))
        if quarantine:
            from xl_source_health import inspect_workbook
            checkpoint("quarantine_chain", normalization=quarantine)
            source_path = Path(quarantine["candidate"])
            route = inspect_workbook(source_path)["route"]
        elif set(ledger.get("reason_codes", [])) == {"data_tables_present"}:
            from xl_presentation_tables import materialize, verify_materialization
            from xl_source_health import inspect_workbook
            checkpoint("presentation_tables")
            normalization = reusable_table_candidate(source, curation, ledger)
            if normalization is None:
                transformed = run / "normalization" / wb / f"tables-attempt-{build['attempts']}"
                candidate, receipt = materialize(source_path, wb, curation, transformed,
                                                  allow_initial_curation=True)
                verify_materialization(source_path, candidate, receipt["curation"], receipt)
                normalization = {
                    "original_sha256": source["sha256"], "candidate": str(candidate),
                    "candidate_sha256": digest(candidate), "manifest": str(transformed / "manifest.json"),
                    "manifest_sha256": digest(transformed / "manifest.json"),
                    "policy_version": receipt["policy_version"], "curation": receipt["curation"]}
            candidate = Path(normalization['candidate'])
            checkpoint("presentation_tables", normalization=normalization)
            source_path = candidate
            route = inspect_workbook(candidate)["route"]
        elif ledger.get("failure") == "source_policy_unsupported" or dead_name_plan is not None:
            from xl_volatile_formula_remediation import build_plan, apply_plan, verify_manifest
            from xl_source_health import inspect_workbook
            checkpoint("normalization")
            plan = dead_name_plan if dead_name_plan is not None else build_plan(source_path)
            normalized = run / "normalization" / wb / plan["plan_sha256"]
            normalized.mkdir(parents=True, exist_ok=True)
            atomic_write_json(normalized / "plan.json", plan)
            if plan["status"] != "eligible":
                checkpoint("normalization", state="STOPPED", failure="volatile_remediation_unresolved",
                           plan_sha256=digest(normalized / "plan.json"))
                return 1
            candidate = normalized / f"{wb}.xlsx"
            receipt_path = normalized / "manifest.json"
            if candidate.exists() and not receipt_path.exists():
                # An interrupted publication is never accepted as evidence.
                # Preserve it for diagnosis and retry in a fresh attempt path.
                normalized = normalized / f"attempt-{build['attempts']}"
                normalized.mkdir()
                candidate = normalized / f"{wb}.xlsx"
                receipt_path = normalized / "manifest.json"
                atomic_write_json(normalized / "plan.json", plan)
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
            else:
                receipt = apply_plan(source_path, plan, candidate)
                atomic_write_json(receipt_path, receipt)
            verify_manifest(source_path, candidate, plan, receipt)
            checkpoint("normalization", normalization={
                "original_sha256": source["sha256"], "candidate": str(candidate),
                "candidate_sha256": digest(candidate), "plan": str(normalized / "plan.json"),
                "plan_sha256": digest(normalized / "plan.json"), "manifest": str(receipt_path),
                "manifest_sha256": digest(receipt_path), "action_counts": plan["action_counts"]})
            source_path = candidate
            route = inspect_workbook(candidate)["route"]
        checkpoint("source")
        restriction_options = ({"inventory": args.source_inventory,
                                "inventory_batch_id": args.inventory_batch_id}
                               if route in {"restricted_pass", "restricted_recalc_pass"} else {})
        reused = reuse_completed_source(args, source, ledger, source_path,
                                        restriction_options, recalc_options)
        if reused is None:
            generation, manifest = prepare_source_generation(source_path, wb, run / "sources" / wb,
                **restriction_options, **recalc_options)
            source_checkpoint = {
                "schema_version": SOURCE_CHECKPOINT_SCHEMA, "state": "complete",
                "generation_id": manifest["generation_id"],
                "manifest_sha256": digest(generation / "generation-manifest.json"),
                "bindings": source_checkpoint_bindings(args, source, build, source_path),
                "ast_provenance_sha256": digest(generation / "ast-provenance.json"),
                "current_builder": manifest["ast_provenance"]["builder"],
            }
        else:
            generation, manifest, source_checkpoint = reused
        checkpoint("segmentation", source_generation_id=manifest["generation_id"],
                   source_manifest_sha256=digest(generation / "generation-manifest.json"),
                   source_checkpoint=source_checkpoint,
                   source_checkpoint_sha256=object_hash(source_checkpoint),
                   source_reused=reused is not None)
        if build.get("normalization", {}).get("curation"):
            curation = Path(build["normalization"]["curation"])
        out = run / "segments" / wb
        out.mkdir(parents=True, exist_ok=True)
        if curation.is_file() and curation.resolve() != (out / "curation.toml").resolve():
            shutil.copyfile(curation, out / "curation.toml")
        elif (out / "curation.toml").exists():
            # A previous automatic shortlist is itself a retained selection.
            # Keep it rather than silently selecting new targets on recovery.
            pass
        options = Namespace(ast_dir="ast_out", source=str(generation / "source"),
            source_generation_id=manifest["generation_id"], source_generation_path=str(generation),
            source_generation_root=str(run / "sources"), out=str(run / "segments"),
            allow_legacy_ast=False, threshold=6.0, top=40, sample=400, lineage_max=4000,
            recurate=False, llm=False, model="unused", env_file=".env", no_auto_adjudication=True,
            no_verify=False)
        result = segment(wb, options)
        if build.get("normalization", {}).get("policy_version") == QUARANTINE_POLICY:
            from xl_table_quarantine_inventory import validate_adoption
            validate_adoption(source["path"], source_path, build["normalization"])
            if result["verification"].get("ordered_output_cells") != build["normalization"]["ordered_outputs"]:
                raise ValueError("quarantine chain changed the selected target cells")
        if build.get("normalization", {}).get("policy_version") in {"presentation-data-tables/v1", "presentation-data-tables/v2"}:
            receipt = json.loads(Path(build["normalization"]["manifest"]).read_text())
            if result["verification"].get("ordered_output_cells") != receipt["ordered_outputs"]:
                raise ValueError("materialization changed the selected target cells")
        seg = result["generation"]
        checkpoint("preflight", segmentation_generation_id=seg["generation_id"],
                   segmentation_manifest_sha256=digest(seg["manifest"]),
                   outputs=result["verification"]["outputs"],
                   blocking_reasons=result["verification"].get("blocking_reasons", []),
                   verification_evidence={
                       "path": str(Path(seg["directory"]) / "segments.json"),
                       "sha256": digest(Path(seg["directory"]) / "segments.json")})
        verdict = preflight(wb, generation / "source", run / "segments", run / "preflight",
            source_generation_root=run / "sources", source_generation_id=manifest["generation_id"],
            segmentation_generation_id=seg["generation_id"])
        checkpoint("reconstruction", state="VERIFIED" if verdict["healthy"] else "STOPPED",
                   failure=None if verdict["healthy"] else verdict["classification"],
                   outputs=result["verification"]["outputs"])
        return 0 if verdict["healthy"] else 1
    except (Exception, SystemExit) as exc:
        diagnostic_root = run / "segments" / wb / "failed-attempts"
        for diagnostic in sorted(diagnostic_root.glob("*.json"), reverse=True):
            record = json.loads(diagnostic.read_text())
            if record.get("source_generation_id") == build.get("source_generation_id"):
                build["failure_evidence"] = {"path": str(diagnostic), "sha256": digest(diagnostic)}
                break
        checkpoint(build["stage"], state="STOPPED", failure=f"{type(exc).__name__}:{exc}")
        raise


def execute_one(args, source, fingerprint):
    run = Path(args.run_root).resolve()
    wb = source["workbook_id"]
    directory = run / "workbooks" / wb
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "ledger.json"
        ledger = json.loads(path.read_text()) if path.is_file() else {}
        remediable = (source["format"] == ".xlsx" and ledger.get("failure") == "source_policy_unsupported"
                      and set(ledger.get("reason_codes", [])) <= {
                          "unsupported_volatile_formulas_present", "external_links_present"})
        remediable |= (source["format"] == ".xlsx" and set(ledger.get("reason_codes", [])) == {"data_tables_present"})
        remediable |= (ledger.get("failure") == "trusted_recalculation_required" and args.recalc_root
                       and (Path(args.recalc_root) / wb / "result.json").is_file())
        if ledger.get("state") != "READY" and not remediable:
            return {"workbook_id": wb, "state": "STOPPED", "failure": ledger.get("failure", "intake_required")}
        curation = retained_curation(args, source, ledger)
        quarantine = configured_quarantine(args, source, curation)
        bindings = dict(code_sha256=fingerprint, source_sha256=source["sha256"],
            curation_sha256=digest(curation) if curation.is_file() else None,
            inventory_sha256=digest(args.source_inventory) if args.source_inventory else None,
            inventory_batch_id=args.inventory_batch_id if args.source_inventory else None)
        if quarantine:
            bindings["quarantine_adoption_sha256"] = object_hash(quarantine)
        if recalc_evidence_present(args, wb):
            if (ledger.get("failure") != "trusted_recalculation_required"
                    and not (ledger.get("state") == "READY" and ledger.get("route") == "pass")):
                raise ValueError("explicit recalculation adoption requires READY/pass source or existing recalculation-required route")
            job = Path(args.recalc_root) / wb
            if not all((job / name).is_file() for name in ("request.json", "result.json")):
                raise ValueError("explicit trusted recalculation evidence is incomplete")
            bindings["recalc_result_sha256"] = digest(Path(args.recalc_root) / wb / "result.json")
            bindings["recalc_request_sha256"] = digest(Path(args.recalc_root) / wb / "request.json")
            bindings["trusted_public_key_sha256"] = digest(args.trusted_public_key)
        previous = ledger.get("build", {})
        previous_bindings = previous.get("bindings")
        same_dependencies = isinstance(previous_bindings, dict) and {
            key: value for key, value in previous_bindings.items() if key != "code_sha256"
        } == {key: value for key, value in bindings.items() if key != "code_sha256"}
        if same_dependencies and previous.get("state") == "VERIFIED":
            from xl_release_publication import resolve_build_context
            try:
                if digest(source["path"]) != source["sha256"]:
                    raise ValueError("original source changed")
                normalization = previous.get("normalization")
                if normalization:
                    if any(digest(normalization[k]) != normalization[k + "_sha256"]
                           for k in ("candidate", "manifest")):
                        raise ValueError("normalization evidence changed")
                    receipt = json.loads(Path(normalization["manifest"]).read_text())
                    if normalization.get("policy_version") == QUARANTINE_POLICY:
                        from xl_table_quarantine_inventory import validate_adoption
                        validate_adoption(source["path"], normalization["candidate"], normalization)
                    elif normalization.get("policy_version") in {"presentation-data-tables/v1", "presentation-data-tables/v2"}:
                        from xl_presentation_tables import verify_materialization
                        verify_materialization(source["path"], normalization["candidate"], normalization["curation"], receipt)
                    else:
                        from xl_volatile_formula_remediation import verify_manifest
                        if digest(normalization["plan"]) != normalization["plan_sha256"]:
                            raise ValueError("normalization plan changed")
                        verify_manifest(source["path"], normalization["candidate"],
                                        json.loads(Path(normalization["plan"]).read_text()), receipt)
                context = resolve_build_context(wb, source_root=run / "sources" / wb,
                    segmentation_root=run / "segments" / wb,
                    source_generation_id=previous["source_generation_id"],
                    segmentation_generation_id=previous["segmentation_generation_id"])
                # Artifact hashes and a successful segmentation are not a
                # substitute for current source authorization or signed receipts.
                # Reuse the exact production source gate without rebuilding ASTs
                # or segmentation, even on the already-VERIFIED fast path.
                prepared_source = source["path"]
                if bindings.get("recalc_result_sha256") and previous.get("recalculation") is None:
                    raise ValueError("configured trusted result was not consumed by previous build")
                recalc_options = {}
                if previous.get("recalculation") is not None:
                    if not args.recalc_root:
                        raise ValueError("current trusted recalculation evidence is required")
                    job = Path(args.recalc_root) / wb
                    request = json.loads((job / "request.json").read_text())
                    result = json.loads((job / "result.json").read_text())
                    if (request.get("source_sha256") != source["sha256"]
                            or digest(job / "request.json") != bindings.get("recalc_request_sha256")
                            or digest(job / "result.json") != bindings.get("recalc_result_sha256")
                            or digest(args.trusted_public_key) != bindings.get("trusted_public_key_sha256")):
                        raise ValueError("current trusted recalculation bindings changed")
                    recalc_options = dict(request=request, result=result,
                        trusted_runner_public_key=args.trusted_public_key,
                        original_source_path=source["path"])
                    # The immutable copy remains available after an intermediate
                    # runner output is pruned; the signed output hash binds it.
                    prepared_source = context["source_path"]
                if normalization:
                    prepared_source = normalization["candidate"]
                health = json.loads((Path(context["source_dir"]) / "health.json").read_text())
                restrictions = ({"inventory": args.source_inventory,
                                 "inventory_batch_id": args.inventory_batch_id}
                                if health.get("route") in {"restricted_pass", "restricted_recalc_pass"}
                                else {})
                source_reuse = {"build": dict(previous, source_reuse_rejections=[]),
                                "build_history": [previous]}
                if reuse_completed_source(args, source, source_reuse, prepared_source,
                                          restrictions, recalc_options) is None:
                    reasons = source_reuse["build"].get("source_reuse_rejections", [])
                    raise ValueError("verified source failed current source gates: "
                                     + "; ".join(item["reason"] for item in reasons))
                if previous_bindings != bindings:
                    # The full snapshot also contains tests, reporting and
                    # scheduling code. Current generation resolution above
                    # already rechecks the exact parser/verifier fingerprints.
                    # Reapply current preflight before retaining a completed
                    # proof across an unrelated snapshot change.
                    from xl_preflight import preflight
                    recheck_root = directory / "checkpoint-revalidation" / fingerprint
                    verdict = preflight(wb, Path(context["source_path"]).parent,
                        run / "segments", recheck_root,
                        source_generation_root=run / "sources",
                        source_generation_id=previous["source_generation_id"],
                        segmentation_generation_id=previous["segmentation_generation_id"])
                    if verdict.get("healthy") is not True:
                        raise ValueError("current preflight rejects completed checkpoint")
                    refreshed = dict(previous, bindings=bindings,
                        checkpoint_revalidation={"previous_code_sha256": previous_bindings.get("code_sha256"),
                            "preflight": str(recheck_root / f"{wb}.json"),
                            "preflight_sha256": digest(recheck_root / f"{wb}.json")})
                    ledger.setdefault("build_history", []).append(previous)
                    ledger["build"] = refreshed
                    atomic_write_json(path, ledger)
                    previous = refreshed
                return dict(previous, workbook_id=wb, reused=True)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                ledger.setdefault("verified_reuse_rejections", []).append({
                    "attempt": previous.get("attempts"), "reason": str(exc)})
        old_log = archive_build_log(directory)
        if previous and old_log:
            if previous.get("log_evidence") not in (None, old_log):
                raise ValueError("previous build log disagrees with its checkpoint")
            previous = dict(previous, log_evidence=old_log)
        ledger["build_history"] = ledger.get("build_history", []) + ([previous] if previous else [])
        ledger["build"] = dict(bindings=bindings, curation_path=str(curation.resolve()),
                               state="RUNNING", stage="source",
                               attempts=previous.get("attempts", 0) + 1)
        atomic_write_json(path, ledger)
        command = [sys.executable, "-m", "scripts.corpus_build", "--inventory", args.inventory,
                   "--run-root", str(run), "--curation-root", args.curation_root, "--worker", wb]
        if args.source_inventory:
            command += ["--source-inventory", args.source_inventory, "--inventory-batch-id", args.inventory_batch_id]
        if args.recalc_root:
            command += ["--recalc-root", args.recalc_root, "--trusted-public-key", args.trusted_public_key]
        metrics = {}
        started = time.monotonic()
        try:
            with (directory / "build.log").open("wb") as log:
                code = _run_in_process_group(command, stdout=log, timeout_seconds=args.timeout,
                    grace_seconds=5, memory_limit_bytes=args.memory_mb * 1024**2,
                    metrics=metrics, cwd=Path(args.code_root),
                    guardian_lock_fds=(lock.fileno(),))
        except ConversionTimeout:
            code = 124
        metrics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        ledger = json.loads(path.read_text())
        build = ledger["build"]
        build["resources"] = metrics
        build["exit_code"] = code
        build["log_evidence"] = archive_build_log(directory)
        if build["state"] == "RUNNING":
            build.update(state="STOPPED", failure="execution_interrupted_or_resource_limit")
        atomic_write_json(path, ledger)
        return dict(build, workbook_id=wb)


def record_orchestration_failure(run, source, result, fingerprint):
    """Keep an early controller failure in the same original-workbook ledger."""
    directory = Path(run) / 'workbooks' / source['workbook_id']
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / 'ledger.json'
        try:
            ledger = json.loads(path.read_text())
            if not isinstance(ledger, dict):
                raise ValueError('checkpoint must be an object')
        except (OSError, ValueError):
            ledger = {'source': source, 'state': 'STOPPED', 'failure': 'checkpoint_unreadable'}
            if path.is_file():
                original = path.read_bytes()
                backup = directory / ('ledger-corrupt-' + digest(path) + '.json')
                if not backup.exists():
                    backup.write_bytes(original)
                ledger['corrupt_checkpoint'] = {'path': str(backup), 'sha256': digest(backup)}
        previous = ledger.get('build', {})
        if previous:
            ledger.setdefault('build_history', []).append(previous)
        ledger['build'] = {**result, 'attempts': previous.get('attempts', 0) + 1,
                           'bindings': {'code_sha256': fingerprint, 'source_sha256': source['sha256']}}
        atomic_write_json(directory / 'orchestration-failure.json', result)
        atomic_write_json(path, ledger)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--curation-root", default="seg_out")
    parser.add_argument("--source-inventory")
    parser.add_argument("--inventory-batch-id")
    parser.add_argument("--recalc-root")
    parser.add_argument("--trusted-public-key", default="/etc/harbor/excel-runner-public.pem")
    parser.add_argument("--workbooks", nargs="+")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--memory-mb", type=int, default=3072)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    for field in ("inventory", "run_root", "curation_root", "source_inventory", "recalc_root", "trusted_public_key"):
        value = getattr(args, field)
        if value:
            setattr(args, field, str(Path(value).absolute()))
    if bool(args.source_inventory) != bool(args.inventory_batch_id):
        parser.error("source inventory and batch ID must be paired")
    manifest = read_inventory(args.inventory)
    sources = {s["workbook_id"]: s for s in manifest["sources"]}
    if args.worker:
        return worker(args, sources[args.worker])
    selected = args.workbooks or list(sources)
    if len(set(selected)) != len(selected) or set(selected) - sources.keys():
        parser.error("workbooks must be unique inventory identities")
    with cohort_lock(args.run_root):
        return run_cohort(args, manifest, sources, selected)


def run_cohort(args, manifest, sources, selected):
    snapshot, fingerprint = snapshot_code(Path(args.run_root) / "code")
    args.code_root = str(snapshot)
    results = []
    remaining = iter(selected)
    exhausted = False
    stopping = False

    def stop_launches(_signal, _frame):
        nonlocal stopping
        stopping = True

    previous_handlers = {sig: signal.signal(sig, stop_launches)
                         for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {}
            while futures or (not exhausted and not stopping):
                cap_file = Path(args.run_root) / "worker-cap.json"
                cap = args.workers
                if cap_file.exists():
                    requested = json.loads(cap_file.read_text()).get("reconstruction_workers")
                    if type(requested) is not int or not 0 <= requested <= 4:
                        raise ValueError("reconstruction worker cap must be an integer from zero to four")
                    cap = min(cap, requested)
                while not stopping and not exhausted and len(futures) < cap:
                    wb = next(remaining, None)
                    if wb is None:
                        exhausted = True
                        break
                    futures[pool.submit(execute_one, args, sources[wb], fingerprint)] = wb
                if not futures:
                    if not stopping and not exhausted:
                        time.sleep(1)
                    continue
                completed, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                for future in completed:
                    wb = futures.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        # One unreadable checkpoint or worker exception must not
                        # cancel healthy siblings or erase the remaining queue.
                        result = {"workbook_id": wb, "state": "STOPPED", "stage": "orchestration",
                                  "failure": f"{type(exc).__name__}:{exc}"}
                        record_orchestration_failure(args.run_root, sources[wb], result, fingerprint)
                    results.append(result)
                    print(json.dumps({k:result[k] for k in ("workbook_id", "state", "stage", "failure") if k in result}), flush=True)
    finally:
        for sig, previous in previous_handlers.items():
            signal.signal(sig, previous)
    atomic_write_json(Path(args.run_root) / "reconstruction-summary.json", {
        "inventory_sha256": manifest["inventory_sha256"], "code_sha256": fingerprint,
        "attempted": len(results), "requested": len(selected), "interrupted": stopping,
        "raw_denominator": len(sources),
        "reconstruction_verified": sum(r["state"] == "VERIFIED" for r in results),
        "results": results, "harbor_promotion_measured": False})


if __name__ == "__main__":
    raise SystemExit(main())
