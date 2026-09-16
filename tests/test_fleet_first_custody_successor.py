import copy,json
from pathlib import Path
import pytest
from scripts.fleet import first_custody_successor as X
from scripts.fleet.migration import checked,binding,retain


def fixture(w,tmp_path):
    profile=X.PROFILES[w];job=checked(profile['job']);h=checked(profile['history']);research=checked(h['research'])
    a=dict(schema_version='harbor-fleet-retained-native-authority/v1',kind='normalization-hold',workbook=w,job=profile['job'],source=job['source'],tracker=h['tracker'],checkpoint=h['research'],descriptor=research['descriptor'],inspection=profile['inspection'],history=profile['history'])
    p=dict(job=profile['job'],authority=retain(tmp_path/'authority.json',a),host=profile['host'],handoff=h['stopped_proof'])
    return p,job,a


@pytest.mark.parametrize('w',['0073','0173','0303','0077'])
def test_actual_retained_normalization_authority(w,tmp_path):
    p,j,a=fixture(w,tmp_path);assert X.authority(p,j)==a


@pytest.mark.parametrize('field,value',[('host','hhu-server-8'),('history',None),('inspection',None),('checkpoint',None),('tracker',None),('kind','existing-review')])
def test_wrong_custody_or_historical_kind_rejected(field,value,tmp_path):
    p,j,a=fixture('0173',tmp_path)
    if field=='host':p[field]=value
    else:
        a[field]=value;p['authority']=retain(tmp_path/'changed.json',a)
    with pytest.raises((ValueError,TypeError,KeyError)):X.authority(p,j)


@pytest.mark.parametrize('field,value',[('timeout_seconds',86401),('version','f'*64),('workbook','0029'),('command',['python','--replay']),('driver',{'path':'/wrong','sha256':'f'*64})])
def test_altered_job_cannot_reuse_prepared_authority(field,value,tmp_path):
    p,j,a=fixture('0173',tmp_path);j[field]=value
    with pytest.raises((ValueError,KeyError)):X.authority(p,j)


def test_semantic_budget_evidence_decodes_without_native_import():
    b={'type':'Budget','value':{'limit':{'type':'scalar','value':2},'evidence':{'type':'tuple','value':[]}}}
    assert X.decoded_budget(b)=={'limit':2,'evidence':[]}
    with pytest.raises(ValueError):X.decoded_budget({'type':'Unknown','value':{}})


def test_original_authorities_delegate_without_broadening(tmp_path,monkeypatch):
    p={'authority':retain(tmp_path/'a.json',{'kind':'source-only'})};seen=[]
    monkeypatch.setattr(X,'original_authority',lambda p,j:seen.append((p,j)) or 'old')
    assert X.authority(p,{'workbook':'0006'})=='old' and len(seen)==1


@pytest.mark.parametrize('field,value',[('models_dispatched',1),('tracker_modified',True),('semantic_budget_reset',True),('status','RETRY_READY')])
def test_probe_semantics_fail_even_for_newly_bound_receipt(field,value,tmp_path,monkeypatch):
    p,j,a=fixture('0173',tmp_path);probe=checked(a['inspection']);probe[field]=value
    a['inspection']=retain(tmp_path/'altered-probe.json',probe);profile={**X.PROFILES['0173'],'inspection':a['inspection']}
    monkeypatch.setitem(X.PROFILES,'0173',profile);p['authority']=retain(tmp_path/'altered-authority.json',a)
    with pytest.raises(ValueError,match='non-model probe'):X.authority(p,j)


@pytest.mark.parametrize('w,relative',[('0006','control/first-custody-001/0006-launch-template.json'),('0190','holds-0190/hhu-server-7-custody-001/launch-template.json')])
def test_original_native_authorities_still_validate(w,relative):
    path=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912'/relative
    if not path.exists():pytest.skip('optional retained custody fixture')
    p=checked(binding(path));assert X.authority(p,checked(p['job']))['workbook']==w


def dialogue_fixture(tmp_path):
    profile=X.PROFILES['0029'];job=checked(profile['job']);h=checked(profile['history'])
    path=Path(profile['job']['path']).parent/'authority.json';a=checked(binding(path))
    return dict(authority=binding(path),job=profile['job'],host=profile['host'],handoff=h['stopped_proof'],new_owner=h['owner']),job,a


def test_actual_0029_source_backed_retained_pre_review(tmp_path):
    p,j,a=dialogue_fixture(tmp_path);assert X.authority(p,j)==a


@pytest.mark.parametrize('field,value',[('claims',42),('passed_prefix_checks',9),('publication_ready',True),('task_evidence_mutated',True)])
def test_0029_incomplete_proof_rejected(field,value,tmp_path,monkeypatch):
    p,j,a=dialogue_fixture(tmp_path);probe=checked(a['inspection']);probe[field]=value
    a['inspection']=retain(tmp_path/'changed-probe.json',probe);h=checked(a['history']);h['linux_probe']=a['inspection'];a['history']=retain(tmp_path/'changed-plan.json',h)
    profile={**X.PROFILES['0029'],'inspection':a['inspection'],'history':a['history']};monkeypatch.setitem(X.PROFILES,'0029',profile)
    p['authority']=retain(tmp_path/'changed-authority.json',a)
    with pytest.raises(ValueError,match='non-model proof'):X.authority(p,j)
