"""Prospective sidecar: actual independent response review and publication gates.

Execute outside the immutable author snapshot. Every owning module is loaded
from that snapshot; this adapter is itself an explicit root-selected binding.
No model, publication or consumed attempt is implicitly retried.
"""
from __future__ import annotations

from dataclasses import asdict
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tarfile
import uuid


def require(ok, message):
    if not ok:
        raise ValueError(message)


def binding(path):
    path = Path(path).absolute()
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), "regular exact evidence required")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def checked(row):
    require(set(row) == {"path", "sha256"} and binding(row["path"]) == row, "selected sidecar evidence changed")
    return json.loads(Path(row["path"]).read_text())


def retain(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return binding(path)


def module(name, selected):
    require(binding(selected["path"]) == selected, "selected helper changed")
    spec = importlib.util.spec_from_file_location(name, selected["path"])
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def owning_ready(ready_binding, code_root):
    root = Path(code_root)
    require(root.is_absolute() and root.is_dir(), "owning immutable code root required")
    # Entry process starts with stdlib only; do not reuse caller-loaded modules.
    for name, loaded in tuple(sys.modules.items()):
        if name.startswith(("scripts.", "xl_")) and getattr(loaded, "__file__", None):
            require(Path(loaded.__file__).resolve().is_relative_to(root), "foreign owning runtime already imported")
    sys.path.insert(0, str(root))
    ready = checked(ready_binding)
    require(ready["status"] == "PUBLICATION_READY" and ready["publication_performed"] is False,
            "actual completed authoring readiness required")
    job = checked(ready["job"])
    require(job["config"] == ready["config"], "ready config differs from actual job")
    driver = module("response_review_owning_dispatcher", job["driver"])
    env = driver.setup(job)
    checkpoint, value = driver.successful(ready["outcome"], env)
    require(env[6] == root and checkpoint == ready["checkpoint"] and value["status"] == "PUBLICATION_READY",
            "owning publication-ready checkpoint changed")
    request = driver.publication_request(env, value)
    require(request.selection.content.workbook == ready["workbook"], "ready workbook differs")
    return ready, job, driver, env, value, request


def review(plan_binding):
    plan = checked(plan_binding)
    require(set(plan) == {"schema_version", "helper", "code_root", "ready", "guidance_index", "authorization", "directory"}
            and plan["schema_version"] == "harbor-response-review-worker/v1"
            and plan["helper"] == binding(__file__), "exact root-selected response review plan required")
    directory = Path(plan["directory"])
    require(directory.is_absolute() and not directory.exists(), "response review already attempted; reconcile without replay")
    ready, job, driver, env, value, request = owning_ready(plan["ready"], plan["code_root"])
    content = request.selection.content
    require(directory == Path(ready["config"]["path"]).parent / "response-review-final",
            "one canonical response review budget per authoring configuration required")
    require(not directory.is_relative_to(content.stage) and not directory.is_relative_to(Path(plan["code_root"])),
            "private reviewer evidence must be outside task and code")
    from scripts.batch import harbor_response_guidance as G
    from scripts.batch import harbor_model_executor as E
    from scripts.fleet import model_gate
    require(model_gate.configured() and os.environ.get("HARBOR_FLEET_WORKBOOK") == content.workbook,
            "response review requires current exclusive fleet worker custody")
    authorization = checked(plan["authorization"])
    require(authorization.get("response_guidance_must_be_source_grounded") is True,
            "root response verification authorization required")
    index = checked(plan["guidance_index"])
    require(index["schema_version"] == "harbor-response-guidance-index/v1", "private guidance index required")
    context_binding = index["contexts"][content.workbook]
    context = G.validate_context(context_binding)
    require(context["source"]["sha256"] == content.bindings["original_raw_source"]["sha256"],
            "response context differs from original task")
    policy = content.model_policy
    writer_call = content.naturalization_writer_call
    require(isinstance(policy, G.Policy) and isinstance(writer_call, G.Call), "actual naturalization author provenance required")
    writer_request = checked(content.naturalization_writer_request)
    writer_body = json.loads(writer_request["messages"][1]["content"])
    require(writer_body["private_response_guidance"]["context_binding"] == context_binding
            and writer_body["private_response_guidance"]["index_binding"] == plan["guidance_index"],
            "actual instruction author did not receive selected response evidence")
    authors = [(writer_call, writer_request, policy)]
    for call in request.selection.semantic.dialogue_writer_calls:
        authors.append((call, checked(call.request), policy))
    instruction, key = content.stage / "instruction.md", content.stage / "tests/answer_key.json"
    directory.mkdir(parents=True, mode=0o700)
    intent = retain(directory / "intent.json", {"plan": plan_binding, "workbook": content.workbook,
                    "source": context["source"], "instruction": binding(instruction), "answer_key": binding(key),
                    "ready_checkpoint": ready["checkpoint"], "attempt_id": str(uuid.uuid4()),
                    "limit": 1, "ordinal": 1, "automatic_retry": False})
    try:
        request_binding = retain(directory / "request.json", G.review_request(context_binding, instruction, key, policy=policy))
        scope = E.SemanticScope(content.workbook, context["source"]["sha256"],
                                "harbor-response-guidance-review", "response-guidance-final")
        prepared = E.PreparedCall(request_binding, policy, scope, 1, 1, checked(intent)["attempt_id"],
                                 (plan["authorization"], context_binding, intent))
        worker, _ = driver.selected(env, value)
        campaign = E.Campaign(directory / "model-campaign", worker.campaign.slot_root, worker.campaign.model_slots)
        retain(directory / "prepared.json", {"call": asdict(prepared), "campaign": {
            "root": str(campaign.root), "slot_root": str(campaign.slot_root), "model_slots": campaign.model_slots}})
        from xl_task_build import read_env_key
        credential = read_env_key(env[7].env_file)
        require(bool(credential), "configured model credential unavailable")
        executed = E.execute(prepared, campaign, credential=credential)
        require(executed.call is not None, "actual review did not complete; reconcile retained model outcome")
        call_binding = retain(directory / "call.json", asdict(executed.call))
        receipt = G.qualify(context_binding, instruction, key, review_call=executed.call,
                            author_calls=authors, policy=policy)
        receipt_binding = retain(directory / "qualification.json", receipt)
        G.revalidate(receipt_binding, instruction=instruction, answer_key=key, task_root=content.stage)
        return retain(directory / "result.json", {"schema_version": "harbor-response-review-result/v1",
             "status": "PUBLICATION_READY", "response_review_status": "QUALIFIED",
             "workbook": content.workbook, "plan": plan_binding,
             "ready": plan["ready"], "qualification": receipt_binding, "call": call_binding,
             "execution": executed.outcome, "publication_performed": False, "automatic_retry": False})
    except Exception as exc:
        retain(directory / "result.json", {"schema_version": "harbor-response-review-result/v1",
             "status": "RECONCILIATION_REQUIRED", "plan": plan_binding, "failure_type": type(exc).__name__,
             "publication_performed": False, "automatic_retry": False})
        raise


def publication_gate(action, native_plan_binding=None, **collection):
    """Invoked inside the native publisher child, before its first mutation."""
    path, digest = os.environ.get("FCP_RESPONSE_PUBLICATION_PROFILE"), os.environ.get("FCP_RESPONSE_PUBLICATION_PROFILE_SHA256")
    require(path and digest, "mandatory response qualification publication profile missing")
    profile = checked({"path": path, "sha256": digest})
    require(set(profile) == {"schema_version", "helper", "action", "native_plan", "review_result"}
            and profile["schema_version"] == "harbor-response-publication-profile/v1"
            and profile["helper"] == binding(__file__) and profile["action"] == action,
            "exact response-qualified publication profile required")
    native = checked(profile["native_plan"])
    result = checked(profile["review_result"])
    require(result["status"] == "PUBLICATION_READY" and result.get("response_review_status") == "QUALIFIED"
            and result["publication_performed"] is False,
            "actual qualified response review required")
    sidecar = checked(result["plan"])
    require(sidecar["helper"] == binding(__file__) and sidecar["ready"] == result["ready"],
            "response review result detached from owning ready task")
    root = Path(sidecar["code_root"])
    sys.path.insert(0, str(root))
    from scripts.batch import harbor_response_guidance as G
    require(Path(G.__file__).resolve().is_relative_to(root), "foreign response verifier imported")
    receipt = checked(result["qualification"])
    instruction, key = Path(receipt["instruction"]["path"]), Path(receipt["answer_key"]["path"])
    require(receipt["workbook_id"] == result["workbook"] == native["workbook"], "publication workbook differs from response review")
    context = G.validate_context(receipt["context"])
    require(context["source"]["sha256"] == native["source_sha256"], "publication source differs from response review")
    G.revalidate(result["qualification"], instruction=instruction, answer_key=key, task_root=instruction.parent)
    if action == "remote":
        require(native_plan_binding == profile["native_plan"] and native["ready"] == result["ready"],
                "response review does not bind this native publication")
        # Reconstruct selection through its owning dispatcher again before P.
        _, _, _, _, _, request = owning_ready(result["ready"], str(root))
        require(request.selection.content.stage / "instruction.md" == instruction,
                "native selected instruction differs from reviewed stage")
    else:
        require(action == "collect" and set(collection) == {"archive", "manifest_binding", "output_root", "journal"},
                "closed response-qualified collection selection required")
        require(str(collection["archive"]) == native["archive"] and collection["manifest_binding"] == native["manifest"]
                and str(collection["output_root"]) == native["output_root"] and str(collection["journal"]) == native["journal"],
                "collection differs from root-selected response-qualified plan")
        manifest = checked(native["manifest"])
        require(binding(native["archive"])["sha256"] == manifest["archive_sha256"], "response-qualified archive changed")
        forbidden = {receipt["context"]["sha256"], context["source"]["sha256"], context["response"]["sha256"]}
        with tarfile.open(native["archive"], "r:gz") as archive:
            for member_name, selected in (("task/instruction.md", receipt["instruction"]),
                                           ("task/tests/answer_key.json", receipt["answer_key"])):
                member = archive.getmember(member_name)
                require(member.isfile() and hashlib.sha256(archive.extractfile(member).read()).hexdigest() == selected["sha256"],
                        "archive instruction/key differs from independently reviewed bytes")
        require(not any(row["sha256"] in forbidden for name, row in manifest["files"].items() if name.startswith("task/")),
                "private response evidence entered collected task")
    return result["qualification"]


def build_publisher(native_publisher, output):
    """Exact two-site additive adapter; retain original implementation unchanged."""
    original = Path(native_publisher["path"])
    require(binding(original) == native_publisher, "native publisher changed")
    source = original.read_text()
    helper = binding(__file__)
    loader = '\n\ndef _response_gate(*args, **kwargs):\n    import importlib.util\n    helper = ' + repr(helper) + '\n    require(binding(helper["path"]) == helper, "selected response gate changed")\n    spec = importlib.util.spec_from_file_location("selected_response_publication_gate", helper["path"])\n    adapter = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(adapter)\n    return adapter.publication_gate(*args, **kwargs)\n\n'
    remote = 'def remote_child(plan_binding):\n    plan = checked(plan_binding)'
    collect = 'def collect(archive, manifest_binding, output_root, journal):\n    """Atomic collection only after complete package and independent report checks."""'
    require(source.count(remote) == source.count(collect) == 1 and "def _response_gate(" not in source,
            "unrecognized native publisher gate locations")
    source = source.replace(remote, loader + remote + '\n    _response_gate("remote", plan_binding)')
    source = source.replace(collect, collect + '\n    _response_gate("collect", archive=archive, manifest_binding=manifest_binding, output_root=output_root, journal=journal)')
    output = Path(output)
    require(output.is_absolute() and not output.exists(), "fresh publisher adapter path required")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(source); stream.flush(); os.fsync(stream.fileno())
    return retain(output.with_suffix(".provenance.json"), {"schema_version": "harbor-response-publisher-adapter/v1",
         "native_publisher": native_publisher, "helper": helper, "publisher": binding(output),
         "mutation_sites": ["remote_child prepublication", "collect precollection"], "all_native_gates_preserved": True})


def evidence_files(review_result_binding):
    """Exact required private replay closure for hash-preserving VM collection."""
    result = checked(review_result_binding)
    require(result["status"] == "PUBLICATION_READY" and result.get("response_review_status") == "QUALIFIED",
            "qualified response review required")
    receipt = checked(result["qualification"])
    context = checked(receipt["context"])
    rows = [review_result_binding, result["plan"], result["qualification"], result["call"],
            receipt["context"], receipt["instruction"], receipt["answer_key"], receipt["implementation"],
            context["source"], context["response"]]
    for call in [receipt["review_call"], *[item["call"] for item in receipt["author_calls"]]]:
        rows.extend(row for row in call.values() if row is not None)
    selected = {}
    for row in rows:
        require(binding(row["path"]) == row, "response replay evidence changed")
        selected[row["path"]] = row
    return list(selected.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("review", "build-publisher", "evidence-files"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    selected = {"path": str(Path(args.plan).absolute()), "sha256": args.plan_sha256}
    if args.action == "review":
        result = review(selected)
    elif args.action == "build-publisher":
        require(args.output, "publisher output required")
        result = build_publisher(selected, args.output)
    else:
        result = evidence_files(selected)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
