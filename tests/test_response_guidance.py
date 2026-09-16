import json
import time
import uuid
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.batch import harbor_response_guidance as G
from scripts.batch.harbor_model_review_provenance import Call, Policy


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return G.binding(path)


@pytest.fixture
def fixture(tmp_path):
    source = tmp_path / "4001.xlsx"
    with ZipFile(source, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="DCF" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Manufacturing valuation</t></is></c><c r="B1"><v>10</v></c><c r="C1"><f>B1*2</f><v>20</v></c></row></sheetData></worksheet>')
    response = tmp_path / "4001.md"
    response.write_text("Workbook: original.xlsx\n\n## What instructions were you given to build this model?\nBuild a manufacturing valuation model.\n")
    context = G.prepare("4001", source, response, original_filename="original.xlsx")
    selected = save(tmp_path / "context.json", context)
    task = tmp_path / "task"
    task.mkdir()
    instruction = task / "instruction.md"
    instruction.write_text("Compute DCF!C1 from the supplied manufacturing model inputs.")
    key = tmp_path / "answer_key.json"
    key.write_text('{"DCF!C1":20}')
    return tmp_path, context, selected, instruction, key, task


def call(root, request, result, *, attempt=None, response_id=None):
    now = time.time_ns()
    policy = Policy(project_id="test")
    req = save(root / "request.json", request)
    submitted = save(root / "submitted.json", {"model": policy.model, "reasoning_effort": policy.reasoning_effort,
        "endpoint": policy.endpoint, "project_id": policy.project_id, "semantic_budget_reset": False,
        "attempt_id": attempt or str(uuid.uuid4()), "request_sha256": req["sha256"],
        "submitted_at_ns": now, "response_mode": "json_object"})
    response = save(root / "response.json", {"model": policy.model, "id": response_id or str(uuid.uuid4()),
        "object": "chat.completion", "created": now // 10**9,
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(result)}}]})
    completed = save(root / "completed.json", {"response_sha256": response["sha256"], "completed_at_ns": now + 100,
        "response_mode": "json_object"})
    return Call(req, submitted, response, completed)


def review():
    refs = [{"sheet": "DCF", "cell": "A1", "value": "Manufacturing valuation"}]
    reasoning = "The instruction requests the workbook valuation output and excludes all computed numeric values."
    return {"verdict": "PASS", "findings": [],
            "checks": {k: {"passed": True, "reasoning": reasoning, "source_refs": refs} for k in G.CHECKS},
            "response_assessment": [{"claim": "Build a manufacturing valuation model.", "disposition": "supported",
                                      "reasoning": reasoning, "source_refs": refs}]}


def qualify_args(fixture, *, result=None, reuse=False):
    root, context, selected, instruction, key, task = fixture
    policy = Policy(project_id="test")
    writer_request = {"model": policy.model, "reasoning_effort": policy.reasoning_effort,
                      "messages": [{"role": "system", "content": "Write instructions"}, {"role": "user", "content": "Private workbook context"}]}
    writer = call(root / "writer", writer_request, {"preamble_body": "Compute outputs", "input_body": "Use model"})
    writer_attempt = json.loads(Path(writer.submitted["path"]).read_text())["attempt_id"]
    reviewer = call(root / "reviewer", G.review_request(selected, instruction, key, policy=policy),
                    review() if result is None else result, attempt=writer_attempt if reuse else None)
    return {"review_call": reviewer, "author_calls": [(writer, writer_request, policy)], "policy": policy}


def test_exact_pair_and_complete_workbook(fixture):
    root, context, selected, instruction, key, task = fixture
    assert G.validate_context(selected)["workbook"]["cell_count"] == 3
    assert context["workbook"]["sheets"][0]["cells"]["C1"]["formula"] == "B1*2"
    with pytest.raises(ValueError, match="correspond exactly"):
        G.prepare("4001", context["source"]["path"], context["response"]["path"], original_filename="different.xlsx")
    with pytest.raises(ValueError, match="cell bound"):
        G.workbook_evidence(context["source"]["path"], max_cells=2)


def test_new_corpus_requires_bound_index(fixture, monkeypatch):
    root, context, selected, *_ = fixture
    monkeypatch.delenv("FCP_RESPONSE_GUIDANCE_INDEX", raising=False)
    monkeypatch.delenv("FCP_RESPONSE_GUIDANCE_INDEX_SHA256", raising=False)
    assert G.selected_author_context("0010", "unused") is None
    assert G.selected_author_context("4001", context["source"]["sha256"]) is None
    monkeypatch.setenv("FCP_RESPONSE_GUIDANCE_INDEX", "missing")
    with pytest.raises(ValueError, match="requires hash-bound"):
        G.selected_author_context("4001", context["source"]["sha256"])
    index = save(root / "index.json", {"schema_version": "harbor-response-guidance-index/v1", "contexts": {"4001": selected}})
    assert G.selected_author_context("4001", context["source"]["sha256"], index_binding=index)["context_binding"] == selected
    with pytest.raises(ValueError, match="owning original source"):
        G.selected_author_context("4001", "wrong", index_binding=index)


def test_independent_review_and_final_byte_revalidation(fixture):
    root, context, selected, instruction, key, task = fixture
    receipt = G.qualify(selected, instruction, key, **qualify_args(fixture))
    receipt_binding = save(root / "qualification.json", receipt)
    assert G.revalidate(receipt_binding, instruction=instruction, answer_key=key, task_root=task)["passed"] is True
    instruction.write_text(instruction.read_text() + "\nThe answer is 20.")
    with pytest.raises(ValueError, match="differs from reviewed"):
        G.revalidate(receipt_binding, instruction=instruction, answer_key=key, task_root=task)


def test_same_author_attempt_cannot_review(fixture):
    _, _, selected, instruction, key, _ = fixture
    with pytest.raises(ValueError, match="reused a writer"):
        G.qualify(selected, instruction, key, **qualify_args(fixture, reuse=True))


@pytest.mark.parametrize("fault", ["hold", "no_refs", "invented_ref", "fake_claim"])
def test_unsubstantiated_or_failed_review_holds(fixture, fault):
    _, _, selected, instruction, key, _ = fixture
    result = review()
    if fault == "hold":
        result["verdict"] = "HOLD"
    elif fault == "no_refs":
        result["checks"]["source_match"]["source_refs"] = []
    elif fault == "invented_ref":
        result["checks"]["source_match"]["source_refs"][0]["value"] = "Carwash LBO"
    else:
        result["response_assessment"][0]["claim"] = "Build a premium car wash LBO model."
    with pytest.raises(ValueError):
        G.qualify(selected, instruction, key, **qualify_args(fixture, result=result))


def test_private_response_cannot_be_packaged(fixture):
    root, context, selected, instruction, key, task = fixture
    receipt = save(root / "qualification.json", G.qualify(selected, instruction, key, **qualify_args(fixture)))
    (task / "response.txt").write_bytes(Path(context["response"]["path"]).read_bytes())
    with pytest.raises(ValueError, match="copied into task"):
        G.revalidate(receipt, instruction=instruction, answer_key=key, task_root=task)


def test_fleet_wrapper_distributed_context_and_consumed_attempt(fixture):
    from scripts.fleet import response_guided_fresh as F
    root, context, selected, instruction, key, task = fixture
    inventory = save(root / "inventory.json", {"sources": [{"workbook_id": "4001", **context["source"]}]})
    plan = save(root / "plan.json", {"workbook": "4001", "inventory": inventory,
                "home": str(root / "authoring"), "code_root": str(root / "code"),
                "output_root": str(task), "publication_root": str(root / "releases")})
    pair = {"workbook_id": "4001", "staged_source": context["source"], "original_source": context["source"],
            "private_response": context["response"], "original_response": context["response"],
            "original_filename": "original.xlsx"}
    manifest = save(root / "manifest.json", {"corpus": "RawWorkbooks4", "pairs": [pair]})
    index = F.prepare_index(plan, manifest, root / "private-guidance")
    assert G.selected_author_context("4001", context["source"]["sha256"], index_binding=index)
    assert not (root / "authoring").exists()
    with pytest.raises(ValueError, match="already attempted"):
        F.prepare_index(plan, manifest, root / "private-guidance")


def test_prospective_publisher_keeps_native_gates_and_requires_review(tmp_path, monkeypatch):
    from scripts.fleet import response_review as R
    native = Path(R.__file__).with_name("publish.py")
    output = tmp_path / "publisher.py"
    provenance = R.build_publisher(R.binding(native), output)
    document = R.checked(provenance)
    assert document["native_publisher"] == R.binding(native)
    assert document["all_native_gates_preserved"] is True
    assert '".fleet-collection.lock"' in output.read_text()
    assert 'client.call("begin", **auth, kind="publication"' in output.read_text()
    loaded = R.module("response_publisher_test", R.binding(output))
    monkeypatch.delenv("FCP_RESPONSE_PUBLICATION_PROFILE", raising=False)
    monkeypatch.delenv("FCP_RESPONSE_PUBLICATION_PROFILE_SHA256", raising=False)
    with pytest.raises(ValueError, match="mandatory response"):
        loaded.collect(tmp_path / "archive.tgz", {}, tmp_path / "output", tmp_path / "journal")
    assert not (tmp_path / "output").exists()
    with pytest.raises(ValueError, match="fresh publisher"):
        R.build_publisher(R.binding(native), output)


def test_strict_collection_requires_reviewed_archive_bytes(fixture, monkeypatch):
    import hashlib
    import tarfile
    from scripts.fleet import response_review as R
    root, context, selected, instruction, key, task = fixture
    receipt = save(root / "qualification.json", G.qualify(selected, instruction, key, **qualify_args(fixture)))
    sidecar_plan = save(root / "review-plan.json", {"helper": R.binding(R.__file__),
                        "code_root": str(Path(G.__file__).resolve().parents[2]), "ready": {"path": "unused", "sha256": "unused"}})
    result = save(root / "review-result.json", {"status": "PUBLICATION_READY", "response_review_status": "QUALIFIED", "publication_performed": False,
                  "plan": sidecar_plan, "workbook": "4001", "qualification": receipt,
                  "ready": {"path": "unused", "sha256": "unused"}})
    archive_path = root / "task.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(instruction, arcname="task/instruction.md")
        archive.add(key, arcname="task/tests/answer_key.json")
    manifest = save(root / "package.json", {"archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                    "files": {"task/instruction.md": {"sha256": G.binding(instruction)["sha256"]},
                              "task/tests/answer_key.json": {"sha256": G.binding(key)["sha256"]}}})
    native = save(root / "collect-plan.json", {"workbook": "4001", "source_sha256": context["source"]["sha256"],
                  "archive": str(archive_path), "manifest": manifest, "output_root": str(root / "outputs"), "journal": str(root / "journal")})
    profile = save(root / "profile.json", {"schema_version": "harbor-response-publication-profile/v1",
                   "helper": R.binding(R.__file__), "action": "collect", "native_plan": native, "review_result": result})
    monkeypatch.setenv("FCP_RESPONSE_PUBLICATION_PROFILE", profile["path"])
    monkeypatch.setenv("FCP_RESPONSE_PUBLICATION_PROFILE_SHA256", profile["sha256"])
    args = {"archive": archive_path, "manifest_binding": manifest, "output_root": root / "outputs", "journal": root / "journal"}
    assert R.publication_gate("collect", **args) == receipt
    with pytest.raises(ValueError, match="collection differs"):
        R.publication_gate("collect", **{**args, "output_root": root / "different"})
    altered = root / "altered.md"
    altered.write_text("Answer is 20.")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(altered, arcname="task/instruction.md")
        archive.add(key, arcname="task/tests/answer_key.json")
    # Even a newly self-consistent archive/manifest cannot use the old review.
    altered_manifest = save(root / "package-altered.json", {"archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(), "files": {}})
    native2 = save(root / "collect-plan-altered.json", {**R.checked(native), "manifest": altered_manifest})
    profile2 = save(root / "profile-altered.json", {**R.checked(profile), "native_plan": native2})
    monkeypatch.setenv("FCP_RESPONSE_PUBLICATION_PROFILE", profile2["path"])
    monkeypatch.setenv("FCP_RESPONSE_PUBLICATION_PROFILE_SHA256", profile2["sha256"])
    with pytest.raises(ValueError, match="archive instruction/key differs"):
        R.publication_gate("collect", **{**args, "manifest_binding": altered_manifest})


def test_dedicated_review_uses_actual_writer_and_bounded_executor(fixture, monkeypatch):
    from types import SimpleNamespace as S
    from scripts.fleet import response_review as R
    from scripts.batch import harbor_model_executor as E
    import xl_task_build
    root, context, selected, instruction, key, task = fixture
    # Match the production stage layout without changing the reviewed fixture.
    (task / "tests").mkdir()
    actual_key = task / "tests/answer_key.json"
    actual_key.write_bytes(key.read_bytes())
    index = save(root / "guidance-index.json", {"schema_version": "harbor-response-guidance-index/v1", "contexts": {"4001": selected}})
    policy = Policy(project_id="test")
    writer_request = {"model": policy.model, "reasoning_effort": policy.reasoning_effort,
       "messages": [{"role": "system", "content": "Write faithful instructions"},
                    {"role": "user", "content": json.dumps({"private_response_guidance": {"context_binding": selected, "index_binding": index}})}]}
    writer = call(root / "actual-writer", writer_request, {"preamble_body": "Compute manufacturing valuation", "input_body": "Use source inputs"})
    content = S(workbook="4001", stage=task, bindings={"original_raw_source": context["source"]},
                model_policy=policy, naturalization_writer_call=writer, naturalization_writer_request=writer.request)
    request = S(selection=S(content=content, semantic=S(dialogue_writer_calls=())))
    config = save(root / "authoring/config.json", {"workbook": "4001"})
    ready_binding = save(root / "ready.json", {"status": "PUBLICATION_READY", "checkpoint": selected, "config": config})
    ready = R.checked(ready_binding)
    controls = S(env_file=root / "unused.env")
    env = (None, None, None, None, None, None, None, controls)
    driver = S(selected=lambda *_: (S(campaign=S(slot_root=root / "slots", model_slots=1)), None))
    monkeypatch.setattr(R, "owning_ready", lambda *_: (ready, {}, driver, env, {}, request))
    monkeypatch.setattr(xl_task_build, "read_env_key", lambda _: "test-only")
    monkeypatch.setenv("HARBOR_FLEET_URL", "http://test.invalid")
    monkeypatch.setenv("HARBOR_FLEET_WORKBOOK", "4001")
    observed = []
    def execute(prepared, campaign, **kwargs):
        observed.append(prepared)
        assert (prepared.ordinal, prepared.limit) == (1, 1)
        assert prepared.scope.budget_id == "response-guidance-final"
        reviewed = call(root / "actual-review", R.checked(prepared.request), review())
        return E.ExecutionResult("completed", save(root / "model-outcome.json", {"completed": True}), reviewed)
    monkeypatch.setattr(E, "execute", execute)
    authorization = save(root / "authorization.json", {"response_guidance_must_be_source_grounded": True})
    plan = save(root / "review-plan.json", {"schema_version": "harbor-response-review-worker/v1",
               "helper": R.binding(R.__file__), "code_root": str(Path(G.__file__).parents[2]),
               "ready": ready_binding, "guidance_index": index, "authorization": authorization, "directory": str(root / "authoring/response-review-final")})
    result = R.checked(R.review(plan))
    assert result["status"] == "PUBLICATION_READY" and result["response_review_status"] == "QUALIFIED"
    assert len(observed) == 1
    with pytest.raises(ValueError, match="already attempted"):
        R.review(plan)
    assert len(observed) == 1
    closure = R.evidence_files(R.binding(root / "authoring/response-review-final/result.json"))
    assert {r["path"] for r in closure} >= {writer.request["path"], context["source"]["path"], str(instruction)}
