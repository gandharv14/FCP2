import json
from pathlib import Path
import pytest
from scripts.batch import harbor_disclosure_review_resume as R
from scripts.batch import harbor_disclosure_payload_repair as P

PAYLOAD='scripts/batch/harbor_disclosure_payload_repair.py'
EVIDENCE='scripts/batch/harbor_disclosure_evidence.py'
PRODUCER='scripts/batch/harbor_instruction_producer.py'
ADAPTER='scripts/batch/harbor_disclosure_review_resume.py'

def trees(tmp_path,changed):
    old=tmp_path/'old';new=tmp_path/'new';files=[]
    for name in {PAYLOAD,EVIDENCE,PRODUCER,ADAPTER,'.pipeline-code.json',*changed}:
        for root,text in [(old,'original'),(new,'selected' if name in changed else 'original')]:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        files.append(dict(relative=name,sha256=R.record(old/name)['sha256']))
    snapshot=tmp_path/'snapshot.json';snapshot.write_text(json.dumps(dict(root=str(old),files=files)))
    return R.record(snapshot),new,old

def test_exact_record_projection_accepts_only_selected_serialization_closure(tmp_path):
    changed={PAYLOAD,EVIDENCE,PRODUCER,ADAPTER,'.pipeline-code.json'}
    snapshot,new,_=trees(tmp_path,changed)
    assert R.changes(snapshot,new,projection=P.RECORD_PROJECTION)==sorted(changed)

@pytest.mark.parametrize('projection',[None,P.TABLE_VERSION,'unknown'])
def test_record_evidence_cannot_use_implicit_or_old_projection(tmp_path,projection):
    snapshot,new,_=trees(tmp_path,{PAYLOAD,EVIDENCE})
    with pytest.raises(ValueError,match='explicit lossless records'):
        R.changes(snapshot,new,projection=projection)

def test_record_evidence_requires_payload_change(tmp_path):
    snapshot,new,_=trees(tmp_path,{EVIDENCE})
    with pytest.raises(ValueError,match='explicit lossless records'):
        R.changes(snapshot,new,projection=P.RECORD_PROJECTION)

@pytest.mark.parametrize('extra',['xl_seg/evaluate.py','scripts/batch/harbor_readiness.py'])
def test_even_previously_allowed_instruction_changes_are_not_record_serialization(tmp_path,extra):
    snapshot,new,_=trees(tmp_path,{PAYLOAD,EVIDENCE,extra})
    with pytest.raises(ValueError,match='unrelated instruction changes'):
        R.changes(snapshot,new,projection=P.RECORD_PROJECTION)

def test_original_snapshot_tamper_stays_rejected(tmp_path):
    snapshot,new,old=trees(tmp_path,{PAYLOAD,EVIDENCE});(old/EVIDENCE).write_text('tampered')
    with pytest.raises(ValueError,match='historical code bytes changed'):
        R.changes(snapshot,new,projection=P.RECORD_PROJECTION)

def test_old_physical_payload_route_keeps_original_projection_contract(tmp_path):
    snapshot,new,_=trees(tmp_path,{PAYLOAD})
    assert R.changes(snapshot,new)==[PAYLOAD]
