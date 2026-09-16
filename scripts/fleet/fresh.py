"""Fresh fleet composition using unchanged owning reconstruction/authoring gates.

An existing VERIFIED reconstruction can be selected without executing it again.
Fresh reconstruction has the original one-attempt, 900-second/8-GiB inner and
1350-second/8-GiB outer guards. Authoring uses the explicitly pinned preparer
and dispatcher; all original model reviews remain, publication stays central.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding, checked, retain, safe

SCHEMA = "harbor-fleet-fresh-plan/v1"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load_module(name, row):
    require(binding(row["path"]) == row, "selected driver changed")
    spec = importlib.util.spec_from_file_location(name, row["path"])
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _validate(plan, survey):
    required = {"schema_version", "workbook", "queue", "inventory", "history_roots",
        "code_root", "home", "preparer", "dispatcher", "taxonomy", "env_file",
        "batch", "owner", "publication_root", "output_root", "reconstruction"}
    legacy = plan.get("legacy_research_history")
    require(set(plan) == required | ({"legacy_research_history"} if legacy else set()) and plan["schema_version"] == SCHEMA, "closed fresh plan required")
    queue = checked(plan["queue"]); inventory = checked(plan["inventory"])
    item = next(i for i in queue["items"] if i["workbook_id"] == plan["workbook"])
    source = next(i for i in inventory["sources"] if i["workbook_id"] == plan["workbook"])
    require(item["original_source"] == source, "original source identity required")
    if legacy:
        from scripts.batch import harbor_legacy_research_history as L
        inherited=L.validate(legacy,workbook=plan['workbook'],original={k:source[k] for k in ('path','sha256')},history_roots=plan['history_roots'],known_ids={i['workbook_id']for i in inventory['sources']})
        require(inherited['schema_version']not in L.SOURCE_ONLY_SCHEMAS,'source-only package cannot start authoring')
        if inherited['schema_version']in L.AUTHOR_SCHEMAS:require(plan['code_root']==checked(inherited['authoring']['snapshot'])['root'],'package author snapshot differs')
        require(item['history_class']=='legacy_model_artifacts' and item['preserved_history']==inherited['histories'][plan['workbook']] and item['preserved_curations']==inherited['curations'], 'legacy research history or curation differs')
        require(plan['reconstruction']['mode']=='existing' and plan['reconstruction']['generated_curation'] is None and plan['home']==inherited['home']+'/authoring', 'legacy authoring must retain completed source and scope')
    else:
        require(item["history_class"] == "no_history" and not item["preserved_history"] and not item["preserved_curations"], "fresh-only original identity required")
    require(binding(source["path"]) == {k: source[k] for k in ("path", "sha256")}, "original changed")
    for name in ("preparer", "dispatcher", "taxonomy"):
        require(binding(plan[name]["path"]) == plan[name], "selected input changed")
    root = safe(plan["code_root"])
    require(Path(__file__).resolve().is_relative_to(root), "execute fresh driver from selected code")
    home = safe(plan["home"])
    require(not home.is_relative_to(root), "attempt must be outside immutable code")
    excluded, histories = survey(tuple(map(Path, plan["history_roots"])),
                                                      {i["workbook_id"] for i in inventory["sources"]})
    require(plan["workbook"] not in excluded and (histories.get(plan['workbook'])==inherited['histories'][plan['workbook']] if legacy else not histories.get(plan["workbook"])), "prior task requires supported continuation")
    return source


def validate(plan):
    from scripts.batch import harbor_admission as admission
    return _validate(plan, admission.survey_namespaces)


def inspect(plan_binding, history_binding):
    """Run actual plan/driver setup checks without admission or a fake claim."""
    from scripts.fleet.history import inspect_projection
    plan = checked(plan_binding)
    if plan.get('legacy_research_history'):
        from scripts.batch import harbor_legacy_research_history as L
        require(history_binding==plan['legacy_research_history'],'legacy inspection history differs')
        inherited=L.validate(history_binding)
        source=_validate(plan,lambda roots,ids:L.author_survey(inherited))
    else:
        source = _validate(plan, lambda roots, ids:
            inspect_projection(roots, ids, plan["workbook"], history_binding))
    require(not Path(plan["home"]).exists(), "fresh attempt consumed; reconcile")
    load_module("fleet_probe_preparer", plan["preparer"])
    load_module("fleet_probe_dispatcher", plan["dispatcher"])
    return {"status": "FRESH_PLAN_INSPECTION_PASS", "plan": plan_binding,
        "history": history_binding, "source": source, "actual_plan_validation": True,
        "actual_driver_imports": True, "history_evidence_validated": True,
        "custody_acquired": False, "admissions": 0, "models_dispatched": 0,
        "reconstructions": 0, "workers_started": 0}


def reconstruction_child(plan_binding):
    plan = checked(plan_binding); source = validate(plan); recipe = plan["reconstruction"]
    require(recipe["mode"] == "fresh", "only fresh reconstruction enters worker")
    root = Path(plan["code_root"]); home = Path(plan["home"]); corpus_root = home / "reconstruction" / "corpus"
    require(not corpus_root.exists(), "consumed reconstruction cannot replay")
    from scripts import corpus, corpus_build
    from xl_inventory_approval import resolve_inventory_approval
    restricted = checked(recipe["restriction_inventory"])
    resolve_inventory_approval(restricted, batch_id=recipe["inventory_batch_id"])
    corpus_root.mkdir(parents=True)
    fingerprint = corpus.code_hash()
    screen = corpus.screen_one(source, corpus_root, fingerprint, 300)
    retain(home / "reconstruction" / "screen.json", screen)
    args = SimpleNamespace(inventory=plan["inventory"]["path"], run_root=str(corpus_root),
        curation_root=str(home / "reconstruction" / "initial-curations"), code_root=str(root),
        source_inventory=recipe["restriction_inventory"]["path"], inventory_batch_id=recipe["inventory_batch_id"],
        recalc_root=None, trusted_public_key="/etc/harbor/excel-runner-public.pem", timeout=900, memory_mb=8192)
    result = corpus_build.execute_one(args, source, fingerprint)
    retain(home / "reconstruction" / "result.json", {"result": result,
        "ledger": binding(corpus_root / "workbooks" / plan["workbook"] / "ledger.json"),
        "models_dispatched": 0, "attempts_reset": False})


def reconstruct(plan_binding, plan):
    recipe = plan["reconstruction"]; home = Path(plan["home"])
    if recipe["mode"] == "source-first":
        return source_first_reconstruction(plan_binding, plan)
    if recipe["mode"] == "existing":
        require(set(recipe) == {"mode", "handoff", "generated_curation"}, "exact existing reconstruction required")
        handoff = checked(recipe["handoff"])
        require(handoff["workbook"] == plan["workbook"] and handoff["state"] == "VERIFIED", "verified original reconstruction required")
        for name in ("ledger", "source_manifest", "segmentation_manifest"):
            checked(handoff[name])
        if recipe["generated_curation"] is None:
            ledger = checked(handoff["ledger"])
            build = ledger["build"]
            curation = binding(Path(handoff["segmentation_manifest"]["path"]).parent / "curation.toml")
            if plan.get('legacy_research_history'):
                from scripts.batch import harbor_legacy_research_history as L
                L.source_handoff(plan['legacy_research_history'],handoff)
            else:
                require(ledger["workbook_id"] == plan["workbook"] and build["state"] == "VERIFIED"
                    and build["bindings"]["curation_sha256"] == curation["sha256"]
                    and binding(build["curation_path"])["sha256"] == curation["sha256"],
                    "existing reconstruction must retain its exact input curation")
        else:
            checked(recipe["generated_curation"])
        retain(home / "reconstruction-custody.json", {"handoff": recipe["handoff"],
            "historical_bytes_rewritten": False, "reconstruction_executed": False})
        return {k: handoff[k] for k in ("ledger", "source_manifest", "segmentation_manifest")}, recipe["generated_curation"]
    require(recipe["mode"] == "fresh" and set(recipe) == {"mode", "prior_preparation", "restriction_inventory", "inventory_batch_id"}, "exact fresh reconstruction required")
    previous = checked(recipe["prior_preparation"])
    require(previous["selected_item"]["workbook_id"] == plan["workbook"], "preparation workbook changed")
    limits = {"new_local_attempt_limit": 1, "inner_timeout_seconds": 900, "inner_memory_bytes": 8*1024**3,
              "screen_timeout_seconds": 300, "outer_timeout_seconds": 1350, "models": False, "automatic_retry": False}
    require(all(previous.get(k) == v for k, v in limits.items()), "inherited reconstruction guards changed")
    require(previous["queue"] == plan["queue"] and previous["inventory"] == plan["inventory"]
        and previous["history_roots"] == plan["history_roots"] and previous["restriction_inventory"] == recipe["restriction_inventory"]
        and previous["inventory_batch_id"] == recipe["inventory_batch_id"], "preparation scope changed")
    directory = home / "reconstruction"; directory.mkdir()
    intent = retain(directory / "intent.json", {"plan": plan_binding, "previous_preparation": recipe["prior_preparation"],
        "code": binding(__file__), **limits, "historical_bytes_rewritten": False})
    from scripts.batch.hardened_runner import _run_in_process_group
    metrics = {}; failure = None; rc = None
    try:
        with (directory / "build.log").open("xb") as log:
            rc = _run_in_process_group([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "reconstruct-child",
                "--plan", plan_binding["path"], "--plan-sha256", plan_binding["sha256"]], stdout=log,
                timeout_seconds=1350, grace_seconds=5, memory_limit_bytes=8*1024**3,
                metrics=metrics, cwd=Path(plan["code_root"]), env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except Exception as exc:
        failure = type(exc).__name__
    retain(directory / "resource.json", {"intent": intent, "returncode": rc, "failure_type": failure,
        "metrics": metrics, "log": binding(directory / "build.log"), "automatic_retry": False})
    require(rc == 0 and failure is None, "reconstruction requires reconciliation")
    result = checked(binding(directory / "result.json")); ledger = checked(result["ledger"])
    require(ledger["build"]["state"] == "VERIFIED", "reconstruction did not qualify")
    corpus_root = directory / "corpus"; wb = plan["workbook"]
    bindings = {"ledger": result["ledger"]}
    for kind, folder in (("source", "sources"), ("segmentation", "segments")):
        bindings[kind + "_manifest"] = binding(corpus_root / folder / wb / "generations" /
            ledger["build"][kind + "_generation_id"] / "generation-manifest.json")
    segment = checked(bindings["segmentation_manifest"])
    proof = retain(directory / "generated-curation.json", {"schema_version": "harbor-generated-curation-adoption/v1",
        "workbook": wb, "queue": plan["queue"], **bindings,
        "curation": binding(Path(bindings["segmentation_manifest"]["path"]).parent / "curation.toml"),
        "ordered_targets": segment["curated_output_cells"], "history_roots": plan["history_roots"]})
    return bindings, proof


def source_first_reconstruction(plan_binding, plan):
    """One bounded source attempt, followed by the original authoring gates."""
    recipe = plan["reconstruction"]; home = Path(plan["home"])
    require(set(recipe) == {"mode", "plan"}, "closed source-first selection required")
    source = checked(recipe["plan"])
    require(source["workbook"] == plan["workbook"] and source["inventory"] == plan["inventory"]
        and source["history_roots"] == plan["history_roots"], "source-first identity differs")
    snapshot = checked(source["snapshot"])
    require(snapshot["root"] == plan["code_root"]
        and source["helper"] == binding(Path(plan["code_root"]) / "scripts/fleet/source_first.py"),
        "source-first implementation differs")
    require(source["next_attempt"] == source["cumulative_reconstruction_limit"] == 1
        and source["models"] == 0 and source["automatic_retry"] is False,
        "initial source attempt only")
    from scripts.batch import harbor_continuation_allowance as A
    import time
    policy = A.validate(source["allowance"], expected_scope=A.scope(plan["workbook"], source["source"]["sha256"]), at=time.time_ns())
    require(0 < source["seconds"] <= policy["limits"]["stage_seconds"]
        and 0 < source["memory_bytes"] <= policy["limits"]["memory_bytes"], "source guard exceeds allowance")
    directory = home / "reconstruction"; directory.mkdir()
    intent = retain(directory / "intent.json", {"plan": plan_binding, "source_plan": recipe["plan"],
        "allowance": source["allowance"], "timeout_seconds": source["seconds"]+450,
        "memory_bytes": source["memory_bytes"], "models": False, "automatic_retry": False})
    from scripts.batch.hardened_runner import _run_in_process_group
    from scripts.fleet.python_entry import command
    source_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    # The source child surveys its actual VM namespaces; the central authoring
    # projection is retained for the parent and has a different root set.
    for name in ("HARBOR_FLEET_HISTORY_PATH", "HARBOR_FLEET_HISTORY_SHA256"):
        source_env.pop(name, None)
    metrics = {}; rc = None; failure = None
    try:
        with (directory / "build.log").open("xb") as log:
            rc = _run_in_process_group(command(sys.executable, source["helper"]["path"], "run",
                "--plan", recipe["plan"]["path"], "--sha256", recipe["plan"]["sha256"]), stdout=log,
                timeout_seconds=source["seconds"]+450, grace_seconds=5,
                memory_limit_bytes=source["memory_bytes"], metrics=metrics,
                cwd=Path(plan["code_root"]), env=source_env)
    except Exception as exc:
        failure = type(exc).__name__
    retain(directory / "resource.json", {"intent": intent, "returncode": rc, "failure_type": failure,
        "metrics": metrics, "log": binding(directory / "build.log"), "automatic_retry": False})
    require(rc == 0 and failure is None, "source-first reconstruction requires reconciliation")
    result_binding = binding(Path(source["home"]) / "result.json"); result = checked(result_binding)
    require(result["status"] == "RECONSTRUCTION_VERIFIED" and result["plan"] == recipe["plan"]
        and result["models_dispatched"] == 0 and result["automatic_retry"] is False,
        "source-first did not qualify")
    ledger = checked(result["ledger"])
    require(ledger["workbook_id"] == plan["workbook"] and ledger["build"]["state"] == "VERIFIED"
        and ledger["build"]["attempts"] == 1 and not ledger.get("build_history"), "source attempt lineage differs")
    corpus_root = Path(source["home"]) / "corpus"; wb = plan["workbook"]
    bindings = {"ledger": result["ledger"]}
    for kind, folder in (("source", "sources"), ("segmentation", "segments")):
        bindings[kind + "_manifest"] = binding(corpus_root / folder / wb / "generations" /
            ledger["build"][kind + "_generation_id"] / "generation-manifest.json")
    segment = checked(bindings["segmentation_manifest"])
    curation = binding(Path(bindings["segmentation_manifest"]["path"]).parent / "curation.toml")
    if ledger["build"]["bindings"]["curation_sha256"] is None:
        proof = retain(directory / "generated-curation.json", {"schema_version": "harbor-generated-curation-adoption/v1",
            "workbook": wb, "queue": plan["queue"], **bindings, "curation": curation,
            "ordered_targets": segment["curated_output_cells"], "history_roots": plan["history_roots"]})
    else:
        require(ledger["build"]["bindings"]["curation_sha256"] == curation["sha256"]
            and binding(ledger["build"]["curation_path"])["sha256"] == curation["sha256"], "input curation changed")
        proof = None
    retain(directory / "handoff.json", {"source_result": result_binding, **bindings,
        "source_attempts": 1, "automatic_retry": False, "authoring_qualified": False})
    return bindings, proof


def run(plan_binding):
    plan = checked(plan_binding)
    if plan.get('legacy_research_history'):
        legacy=plan['legacy_research_history']
        require(not any(os.environ.get(k)for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_SOURCE_HISTORY_PATH')),'foreign history projection cannot author legacy research')
        os.environ['HARBOR_FLEET_LEGACY_RESEARCH_PATH']=legacy['path'];os.environ['HARBOR_FLEET_LEGACY_RESEARCH_SHA256']=legacy['sha256']
    validate(plan); home = Path(plan["home"])
    require(not home.exists(), "fresh composition already attempted; reconcile retained boundary")
    home.mkdir(parents=True, mode=0o700)
    retain(home / "intent.json", {"plan": plan_binding, "driver": binding(__file__), "automatic_retry": False})
    try:
        reconstructed, curation = reconstruct(plan_binding, plan)
        author = load_module("fleet_selected_preparer", plan["preparer"])
        sys.path.insert(0, str(Path(plan["dispatcher"]["path"]).parent))
        dispatcher = load_module("fleet_selected_dispatcher", plan["dispatcher"])
        args = SimpleNamespace(code_root=plan["code_root"], run_root=str(home / "authoring"), workbook=plan["workbook"],
            ledger=reconstructed["ledger"]["path"], inventory=plan["inventory"]["path"],
            history_root=plan["history_roots"], pre_audit_history=plan.get('legacy_research_history',{}).get('path'), generated_curation=curation["path"] if curation else None,
            source_manifest=reconstructed["source_manifest"]["path"], segmentation_manifest=reconstructed["segmentation_manifest"]["path"],
            taxonomy=plan["taxonomy"]["path"], env_file=plan["env_file"], batch=plan["batch"], owner=plan["owner"],
            slot_root=str(dispatcher.BASE / "root" / "workbook-slots" / plan["workbook"]),
            model_slot_root=str(dispatcher.MODEL), queue_lock=str(dispatcher.QUEUE_LOCK),
            publication_root=plan["publication_root"], output_root=plan["output_root"], project_id="cms6m4urm006n07z8ecxi1oi2")
        config_binding = author.prepare(args); config = checked(config_binding)
        base, _, _ = author.imports(Path(plan["code_root"])); controls = base.decode(config["controls"])
        outcome = base.run("admit", {"intake": config["intake"]}, controls, home / "authoring" / "admission-001")
        job = {"schema_version": dispatcher.SCHEMA, "driver": plan["dispatcher"], "queue": plan["queue"],
            "config": config_binding, "outcome": outcome, "operations": str(home / "authoring" / "dispatcher-v2" / "first"),
            "maximum_steps": 100, "models": True, "commands": True, "containers": True, "publish": False}
        job_binding = retain(home / "authoring-job.json", job)
        dispatcher.successful(outcome, dispatcher.setup(job)); dispatcher.run(job_binding)
        held = checked(binding(Path(job["operations"]) / "hold.json"))
        checkpoint, result = dispatcher.successful(held["outcome"], dispatcher.setup(job))
        require(result["status"] == "PUBLICATION_READY", "authoring stopped at an owning hold")
        return retain(home / "result.json", {"status": "PUBLICATION_READY", "workbook": plan["workbook"],
            "plan": plan_binding, "job": job_binding, "config": config_binding, "outcome": held["outcome"],
            "checkpoint": checkpoint, "publication_performed": False, "automatic_retry": False})
    except Exception as exc:
        retain(home / "result.json", {"status": "RECONCILIATION_REQUIRED", "workbook": plan["workbook"],
            "plan": plan_binding, "failure_type": type(exc).__name__, "publication_performed": False,
            "automatic_retry": False})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("action", choices=("run", "reconstruct-child"))
    p.add_argument("--plan", required=True); p.add_argument("--plan-sha256", required=True)
    a = p.parse_args(); selected = {"path": str(Path(a.plan).absolute()), "sha256": a.plan_sha256}
    result = run(selected) if a.action == "run" else reconstruction_child(selected)
    if result: print(json.dumps(result))


if __name__ == "__main__":
    main()
