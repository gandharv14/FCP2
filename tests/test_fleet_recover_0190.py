import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from scripts.fleet import recover_0190 as R


def test_changed_retained_evidence_rejected(tmp_path):
    p=tmp_path/'prior.json';p.write_text('{}');binding=R.record(p)
    p.write_text('{"review_limit":3}')
    with pytest.raises(ValueError,match='changed'):R.checked(binding)


def test_gate_wrong_workbook_never_contacts_endpoint(monkeypatch,tmp_path):
    for name,value in {'URL':'http://127.0.0.1:1','TOKEN_FILE':str(tmp_path/'absent'),'WORKBOOK':'0191','OWNER':'x','CLAIM_TOKEN':'x','FENCE':'1'}.items():monkeypatch.setenv('HARBOR_FLEET_'+name,value)
    monkeypatch.setattr(R.urllib.request,'urlopen',lambda *a,**k:pytest.fail('must not contact'))
    with pytest.raises(ValueError,match='0190 fenced'):R.gate()


def test_actual_owning_reader_inspection_does_not_mutate_evidence(tmp_path):
    if not Path(R.SNAPSHOT['path']).exists():pytest.skip('retained 0190 evidence is an optional integration fixture')
    root=Path(__file__).resolve().parents[1]
    files=[p for p in R.HOME.rglob('*') if p.is_file() and not {'__pycache__','.pytest_cache'}&set(p.parts)]
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    run=subprocess.run([sys.executable,'-I','-B',str(root/'scripts/fleet/recover_0190.py'),'inspect','--output',str(tmp_path/'inspection')],capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stderr
    value=json.loads((tmp_path/'inspection/inspection.json').read_text())
    assert value['model_calls']==0 and value['original_review_ordinal']==1 and value['original_review_limit']==2
    assert value['unused_correction_rounds']==1 and value['same_review_must_not_replay']
    assert all(c['passed'] for c in value['prefix_checks'])
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in R.HOME.rglob('*') if p.is_file() and not {'__pycache__','.pytest_cache'}&set(p.parts)}
    assert before==after
