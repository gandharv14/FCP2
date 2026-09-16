import json
import sys
from types import SimpleNamespace
from pathlib import Path
import pytest
from scripts.fleet import worker as w
from scripts.fleet import migration as m


@pytest.fixture
def available_disk(monkeypatch):
    # The local corpus shares a nearly full disk. These process-custody tests
    # isolate the remote disk observation; production reserve remains unchanged.
    monkeypatch.setattr(w.shutil, "disk_usage", lambda _: SimpleNamespace(free=20*1024**3))


class Client:
    def __init__(self, source, fail_heartbeat=False):
        self.source = source; self.calls = []; self.fail_heartbeat = fail_heartbeat
    def call(self, action, **kw):
        self.calls.append(action)
        if action == "claim":
            return {"workbook": "0001", "owner": kw["owner"], "token": "PRIVATE_CLAIM_TOKEN",
                "fence": 1, "source_sha256": self.source["sha256"], "state": "active"}
        if action == "heartbeat" and self.fail_heartbeat:
            raise ConnectionError("PRIVATE_CLAIM_TOKEN")
        return {}


def job(tmp_path, script):
    tmp_path = tmp_path.resolve()
    source = tmp_path / "source.xlsx"; source.write_bytes(b"original")
    driver = tmp_path / "driver.py"; driver.write_text(script)
    selected = {"schema_version": w.SCHEMA, "workbook": "0001", "source": m.binding(source),
        "version": "f"*64, "driver": m.binding(driver), "inputs": [],
        "command": [sys.executable, str(driver)], "cwd": str(tmp_path), "run": str(tmp_path / "worker"),
        "result": str(tmp_path / "result.json"), "memory_limit_bytes": 8*1024**3,
        "timeout_seconds": 10, "minimum_disk_bytes": 12*1024**3}
    return selected, m.retain(tmp_path / "job.json", selected)


def test_success_is_only_publication_ready_and_never_logs_token(tmp_path, available_disk):
    selected, bound = job(tmp_path, 'import json\nfrom pathlib import Path\nPath("result.json").write_text(json.dumps({"status":"PUBLICATION_READY"}))\n')
    client = Client(selected["source"])
    result = w.execute(bound, client=client, owner="vm/0", url="http://127.0.0.1:8765", token_file="/private/runtime-token")
    outcome = m.checked(result)
    assert outcome["status"] == "PUBLICATION_READY" and not outcome["publication_performed"]
    assert client.calls[0] == "claim" and client.calls[-1] == "finish"
    assert "PRIVATE_CLAIM_TOKEN" not in "".join(p.read_text() for p in Path(selected["run"]).rglob("*") if p.is_file())
    with pytest.raises(ValueError, match="already exists"):
        w.execute(bound, client=client, owner="vm/0", url="http://127.0.0.1:8765", token_file="/private/runtime-token")
    assert client.calls.count("claim") == 1


def test_heartbeat_loss_terminates_group_and_retains_uncertainty(tmp_path, available_disk):
    selected, bound = job(tmp_path, "import time\ntime.sleep(30)\n")
    client = Client(selected["source"], fail_heartbeat=True)
    result = w.execute(bound, client=client, owner="vm/0", url="http://127.0.0.1:8765", token_file="/private/runtime-token")
    outcome = m.checked(result)
    assert outcome["status"] == "RECONCILIATION_REQUIRED"
    assert outcome["failure_type"] == "ConnectionError" and not outcome["automatic_retry"]
    assert outcome["metrics"]["elapsed_seconds"] < 15


def test_malformed_result_retains_outcome(tmp_path, available_disk):
    selected, bound = job(tmp_path, 'from pathlib import Path\nPath("result.json").write_text("broken json")\n')
    result = w.execute(bound, client=Client(selected["source"]), owner="vm/0", url="http://127.0.0.1:8765", token_file="/private/runtime-token")
    outcome = m.checked(result)
    assert outcome["status"] == "RECONCILIATION_REQUIRED" and outcome["result"]


def test_changed_code_and_relaxed_memory_rejected_before_claim(tmp_path):
    selected, _ = job(tmp_path, "pass\n")
    selected["memory_limit_bytes"] *= 2
    with pytest.raises(ValueError, match="guards"):
        w.validate(selected)
    selected["memory_limit_bytes"] //= 2
    Path(selected["driver"]["path"]).write_text("print('changed')\n")
    with pytest.raises(ValueError, match="hash differs"):
        w.validate(selected)


def test_explicit_scoped_memory_allowance_and_expiry(tmp_path):
    from scripts.batch import harbor_continuation_allowance as A
    import time
    selected, _ = job(tmp_path, 'pass\n')
    directory = tmp_path.resolve()/'allowance'
    directory.mkdir()
    authorization = m.retain(directory/'user.json', {'authorization': 'bounded resource increase'})
    now = time.time_ns()
    allowance = A.issue(directory, expected_previous=None,
        selected_scope=A.scope(selected['workbook'], selected['source']['sha256']),
        limits=dict(takeovers=6, lease_duration_ns=14400*10**9, stage_seconds=7200,
                    memory_bytes=16*1024**3, review_attempts=4, transport_retries=0),
        authorization=authorization, reason='Prior segmentation memory guard exhausted',
        expires_at_ns=now+3600*10**9, approve=lambda _: True, clock=lambda: now)
    selected['operational_allowance'] = allowance
    selected['memory_limit_bytes'] = 16*1024**3
    w.validate(selected)
    selected['memory_limit_bytes'] += 1
    with pytest.raises(ValueError, match='memory allowance'): w.validate(selected)
    selected['memory_limit_bytes'] -= 1
    selected['workbook'] = '0002'
    with pytest.raises(A.life.Conflict, match='another source/workbook'): w.validate(selected)
    selected['workbook'] = '0001'
    from unittest.mock import patch
    with patch.object(w.time, 'time_ns', return_value=now+3601*10**9):
        with pytest.raises(A.life.Conflict, match='currently effective'): w.validate(selected)


@pytest.mark.parametrize('change',["path","hash","duplicate_plan","duplicate_hash","duplicate_input",
    "equals_plan","equals_hash","equals_fresh_hash"])
def test_stale_or_ambiguous_plan_rejected_before_claim_and_intent(tmp_path,change):
    selected, _ = job(tmp_path, "pass\n")
    current = m.retain(tmp_path / "current-plan.json", {"version": "new"})
    selected["inputs"] = [current]
    selected["command"] += ["--plan", current["path"], "--sha256", current["sha256"]]
    if change == "path": selected["command"][-3] = str(tmp_path / "old-plan.json")
    elif change == "hash": selected["command"][-1] = "0"*64
    elif change == "duplicate_plan": selected["command"] += ["--plan", current["path"]]
    elif change == "duplicate_hash": selected["command"] += ["--sha256", current["sha256"]]
    elif change == "equals_plan": selected["command"] += ["--plan=/old-plan.json"]
    elif change == "equals_hash": selected["command"] += ["--sha256="+"0"*64]
    elif change == "equals_fresh_hash": selected["command"] += ["--plan-sha256="+"0"*64]
    else: selected["inputs"].append(current)
    bound=m.retain(tmp_path / "invalid-job.json", selected);client=Client(selected["source"])
    with pytest.raises(ValueError, match="plan"):
        w.execute(bound,client=client,owner="unused",url="http://127.0.0.1:1",token_file="/never-read")
    assert client.calls == [] and not Path(selected["run"]).exists()


def test_known_coordinator_http_and_guard_diagnostics_are_distinct():
    value=w.failure_diagnostics(RuntimeError('coordinator HTTP 500: coordinator error; reconcile uncertain mutations before retry'),'heartbeat')
    assert value['http_status']==500 and value['stage']=='heartbeat'
    assert value['response']=={'error':'coordinator error; reconcile uncertain mutations before retry'}
    guard=w.failure_diagnostics(RuntimeError('memory observation unavailable'),'memory_observation')
    assert guard['category']=='resource_guard'and 'http_status'not in guard


@pytest.mark.parametrize('message',['coordinator HTTP 400: Bearer PRIVATE_CLAIM_TOKEN',
    'coordinator HTTP 500: coordinator error; reconcile uncertain mutations before retry\nPRIVATE_CLAIM_TOKEN',
    'https://user:PRIVATE_CLAIM_TOKEN@example.test/error'])
def test_unknown_exception_text_is_only_fingerprinted(message):
    value=w.failure_diagnostics(RuntimeError(message),'heartbeat')
    assert 'PRIVATE_CLAIM_TOKEN'not in json.dumps(value)
    assert value['message_bytes']==len(message.encode())
    if message.startswith('coordinator HTTP'):assert value['response_redacted']is True


def test_actual_heartbeat_and_finish_failures_retain_safe_sidecars(tmp_path,available_disk):
    selected,bound=job(tmp_path,'import time\ntime.sleep(30)\n')
    class Broken(Client):
        def call(self,action,**kw):
            if action in ('heartbeat','finish'):
                self.calls.append(action)
                raise RuntimeError('coordinator HTTP 500: coordinator error; reconcile uncertain mutations before retry')
            return super().call(action,**kw)
    client=Broken(selected['source'])
    result=w.execute(bound,client=client,owner='vm/0',url='http://127.0.0.1:8765',token_file='/private/runtime-token')
    root=Path(selected['run']);outcome=m.checked(result)
    failed=json.loads((root/'failure-diagnostics.json').read_text());finished=json.loads((root/'finish-diagnostics.json').read_text())
    assert failed['intent']==outcome['intent']and failed['stage']=='heartbeat'and failed['http_status']==500
    assert finished['outcome']==result and finished['stage']=='finish'and finished['http_status']==500
    assert (root/'finish-uncertain.json').exists()and client.calls.count('claim')==1
    assert set(outcome)=={'schema_version','intent','status','returncode','failure_type','metrics','result','log','automatic_retry','publication_performed'}
    assert 'PRIVATE_CLAIM_TOKEN'not in ''.join(p.read_text()for p in root.rglob('*')if p.is_file())

def test_selected_heartbeat_recovery_keeps_real_child_and_single_claim(tmp_path, available_disk):
    from scripts.fleet import heartbeat_reconciliation as H
    from scripts.fleet.coordinator import Coordinator
    selected, _ = job(tmp_path, 'import json,time\nfrom pathlib import Path\ntime.sleep(2)\nPath("result.json").write_text(json.dumps({"status":"PUBLICATION_READY"}))\n')
    authfile=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
    selected['heartbeat_reconciliation']=m.retain(tmp_path/'heartbeat-policy.json',dict(schema_version=H.SCHEMA,authorization=m.binding(authfile),**H.LIMITS))
    bound=m.retain(tmp_path/'selected-job.json',selected)
    c=Coordinator(tmp_path/'coordinator.sqlite');c.enroll('0001',selected['source']['sha256'],'source');c.reconcile('0001','original stopped')
    class Actual:
        def __init__(self):self.calls=[];self.failed=False
        def call(self,action,**kw):
            self.calls.append(action)
            value=getattr(c,action)(**kw)
            if action=='heartbeat' and not self.failed:
                self.failed=True;raise TimeoutError('committed heartbeat response lost')
            return value
    client=Actual();r=w.execute(bound,client=client,owner='vm/0',url='http://127.0.0.1:8765',token_file='/private/runtime-token')
    outcome=m.checked(r)
    assert outcome['status']=='PUBLICATION_READY' and outcome['failure_type'] is None
    assert client.calls.count('claim')==1 and client.calls.count('heartbeat')==2 and client.calls.count('status')==1
    assert (Path(selected['run'])/'heartbeat-reconciliation-001/custody.json').is_file()
