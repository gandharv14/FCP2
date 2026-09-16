"""Publication linkage checks complement the owning migration/history suites."""
from pathlib import Path
import copy
import pytest
from scripts.fleet import publish as P
from scripts.fleet import continuation_history as H
from scripts.batch import harbor_source_only_continuation as M
from scripts import harbor_campaign as C


def fixture(tmp_path, monkeypatch):
    root=tmp_path.resolve(); home=root/'continued';home.mkdir()
    def keep(name,value): return P.retain(root/name,value)
    dummy=keep('original.json', {'retained':True})
    source={'path':'/original/source.xlsx','sha256':'a'*64}
    history=dict(workbook='0006',roots=[],known_ids=['0006'],source=source,config=dummy,descriptor=dummy,checkpoint=dummy,tracker=dummy,prior_prepared=dummy)
    hb=keep('history.json',history)
    worker=keep('worker.json',dict(origin=[dummy],initial_lease={'exact':'lease'},worker={'retained':'worker'}))
    receipt=keep('receipt.json',dict(**{'predecessor_'+k:dummy for k in ('config','descriptor','checkpoint','tracker','prepared')},new_worker=worker,new_prepared=dummy,takeover=dummy,source=source))
    descriptor=P.retain(home/'descriptor.json',{**P.checked(worker),'origin':[worker,receipt]})
    checkpoint=P.retain(home/'result.json',dict(status='IMPORTED',step='source',descriptor=descriptor,lease={'exact':'lease'},selection=C.content_codec.encode(None),evidence=[receipt,dummy,dummy],publication_ready=False))
    config=P.retain(home/'config.json',dict(workbook='0006',run_root=str(home),continuation_history=hb,source_continuation=receipt))
    # Only deeper owning semantic validators are stubbed: each has separate
    # real source and immutable takeover tests. These tests exercise all new
    # cross-receipt publication and environment links using actual bound bytes.
    monkeypatch.setattr(H,'validate',lambda *a:history)
    monkeypatch.setattr(M,'layout',lambda *a:None)
    plan=dict(workbook='0006',source_sha256='a'*64,code_root=str(C.ROOT),continuation_history=hb,source_continuation=dict(config=config,receipt=receipt,descriptor=descriptor,checkpoint=checkpoint))
    return plan,home


def test_exact_continued_lineage_is_distinct_from_fresh(tmp_path,monkeypatch):
    plan,home=fixture(tmp_path,monkeypatch)
    assert P.history_binding(plan)==plan['continuation_history']
    auth=dict(workbook='0006',owner='newowner',token='synthetic',fence=2)
    monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY','1')
    monkeypatch.setenv('HARBOR_FLEET_MODEL_JOURNAL','obsolete')
    env=P.publication_environment(plan,auth,action='remote',url='http://127.0.0.1:8765',token_file=tmp_path/'token')
    assert env['HARBOR_FLEET_CONTINUATION_HISTORY_PATH']==plan['continuation_history']['path']
    assert all(k not in env for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_READ_ONLY','HARBOR_FLEET_MODEL_JOURNAL'))


@pytest.mark.parametrize('defect',['workbook','history','descriptor','checkpoint','fresh','source'])
def test_changed_cross_boundary_authority_rejected(tmp_path,monkeypatch,defect):
    plan,home=fixture(tmp_path,monkeypatch)
    if defect=='workbook':plan['workbook']='0190'
    elif defect=='source':plan['source_sha256']='b'*64
    elif defect=='fresh':plan['history']=plan['continuation_history']
    elif defect=='history':
        value=P.checked(plan['continuation_history']);value['checkpoint']={'path':'/foreign','sha256':'f'*64}
        plan['continuation_history']=P.retain(tmp_path/'foreign-history.json',value)
    else:
        key=defect; path=Path(plan['source_continuation'][key]['path']);value=P.checked(plan['source_continuation'][key])
        if key=='descriptor':value['origin']=[]
        else:value['step']='normalization_phase1'
        path.write_text(__import__('json').dumps(value));plan['source_continuation'][key]=P.binding(path)
    with pytest.raises(ValueError):P.history_binding(plan)


def test_untyped_continuation_fails_before_claim(tmp_path):
    with pytest.raises(ValueError,match='typed continued history'):
        P.history_binding(dict(source_continuation={},workbook='0006'))
