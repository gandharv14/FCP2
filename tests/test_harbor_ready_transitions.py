"""Transaction mechanics with a fixed fake I/O boundary, not financial proof.

Owning normalization/profile/NAT/runtime tests separately exercise real validators.
These tests never interpret a supplied PASS record as production readiness.
"""
import copy
import importlib
import json
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from scripts.batch import harbor_ready_transitions as r
from scripts.batch import harbor_lane_notes as n
from scripts.batch import harbor_tracker as s
from scripts.batch import harbor_tracker_lifecycle as l
from scripts.batch.harbor_complete_readiness import ReadinessSelection
from scripts.batch.harbor_readiness import Selection
from test_harbor_tracker_lifecycle import created,acquire,authority


def fixture(tmp_path,monkeypatch,*,failure=False):
    path,initial=created(tmp_path);a=acquire(path,initial);t=a['tracker'];t['execution_state']='running'
    # Deliberate nonfinancial mechanical setup, using the live lifecycle creation
    # and lease journal. Production callbacks are never taken from this fixture.
    for row in t['lane_state'].values():row.update(state='passed',current_confidence='high',attempt=2)
    t['lane_state']['harbor-normalization-engineer']['phase']='phase2_complete'
    t['current_confidence'].update(level='high',contributors=list(s.LANES))
    t['repairs'].update(count=2,by_signature={'old':2},history=[{'consumed':2}])
    source=t['raw_source'];gen={'path':str(tmp_path/'gen'),'id':'a'*64,'manifest_sha256':'b'*64}
    t['bindings'].update(source_generation=gen,source_file=source,segmentation_generation=gen,curation=source)
    stage=tmp_path/'stage';stage.mkdir();(stage/'instruction.md').write_text('Synthetic stage')
    evidence=tmp_path/'proof.json';evidence.write_text('{"mechanical_fixture":true}')
    b=copy.deepcopy(t['bindings']);content=Selection('0245',stage,'d'*64,b,tmp_path/'norm',tmp_path/'nat',{}, {})
    selection=ReadinessSelection(content,None,None,None)
    proof=dict(canonical_invocations=[k for k,_ in __import__('scripts.batch.harbor_completed_lanes',fromlist=['STEPS']).STEPS],publication_ready=True,blockers=[],checks={'synthetic_boundary':{'checked':True}},evidence=[n.record(evidence)],stage_sha256='d'*64)
    def fixed(before,chosen):
        assert chosen==selection
        l.checked_record(proof['evidence'][0])
        return copy.deepcopy(proof)
    monkeypatch.setattr(r,'_proof',fixed)
    monkeypatch.setattr(r.time,'time_ns',lambda:30)
    if failure:
        result=dict(lane=n.SEGMENTATION,gate='segmentation',classification='cached_mismatch',diagnostic_sha256='c'*64,evidence=[n.record(evidence)],terminal=False)
        t,raw=n._failure_after(t,result,25,path.with_name('0245-agent-notes.md'))
        Path(t['agent_notes']['path']).write_bytes(raw)
    s.validate_tracker(t);l.validate_lifecycle(t);path.write_bytes(l.canonical(t))
    state=dict(revision=t['revision'],sha256=n.record(path)['sha256'],tracker=t)
    return r.ReadyTransitions(n.RootContext(tmp_path)),path,state,selection,proof


def kw(state):return dict(expected_revision=state['revision'],expected_sha256=state['sha256'],owner='agent1',token=state['tracker']['leases']['task_agent']['token'])


def test_enqueue_preserves_all_consumed_counts_and_publishes_nothing(tmp_path,monkeypatch):
    f,p,start,selection,proof=fixture(tmp_path,monkeypatch)
    result=f.transition('0245','enqueue',selection,**kw(start));t=result['tracker']
    assert t['execution_state']=='publication_queued' and t['terminal_verdict']=='PUBLICATION_READY'
    assert t['promotion_ids']==start['tracker']['promotion_ids'] and t['repairs']==start['tracker']['repairs']
    assert all(row['attempt']==2 for row in t['lane_state'].values())
    assert n.validate_notes_pair(t,Path(t['agent_notes']['path']).read_bytes())
    assert f.require_synchronized('0245')


def test_clear_requires_proof_and_retains_exact_old_failure(tmp_path,monkeypatch):
    f,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True)
    old=copy.deepcopy(start['tracker']['promotion_failure'])
    result=f.transition('0245','clear',selection,**kw(start));t=result['tracker']
    assert t['promotion_failure'] is None and t['failure_history'][-1]['failure']==old
    assert t['failure_history'][:-1]==start['tracker']['failure_history']
    assert t['repairs']==start['tracker']['repairs'] and all(row['attempt']==2 for row in t['lane_state'].values())
    assert f.require_synchronized('0245')


@pytest.mark.parametrize('change',['owner','token','revision','hash','expired','evidence','notes','incomplete_invocations','reordered_invocations'])
def test_reject_before_mutation(tmp_path,monkeypatch,change):
    f,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True);args=kw(start)
    if change=='owner':args['owner']='foreign'
    elif change=='token':args['token']='foreign'
    elif change=='revision':args['expected_revision']-=1
    elif change=='hash':args['expected_sha256']='0'*64
    elif change=='expired':monkeypatch.setattr(r.time,'time_ns',lambda:120)
    elif change=='evidence':Path(proof['evidence'][0]['path']).write_text('changed')
    elif change=='notes':Path(start['tracker']['agent_notes']['path']).write_text('changed')
    elif change=='incomplete_invocations':proof['canonical_invocations'].pop()
    else:proof['canonical_invocations'].reverse()
    raw=p.read_bytes()
    with pytest.raises(ValueError):f.transition('0245','clear',selection,**args)
    assert p.read_bytes()==raw


class Interrupted(Exception):pass
@pytest.mark.parametrize('action',['enqueue','clear'])
@pytest.mark.parametrize('where',['prepared','notes_replaced','tracker_replaced'])
def test_exact_interruption_recovery_no_new_lease_or_budget(tmp_path,monkeypatch,action,where):
    f,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=action=='clear')
    def fault(point):
        if point==where:raise Interrupted(point)
    with pytest.raises(Interrupted):f.transition('0245',action,selection,**kw(start),_fault=fault)
    journal=n.record(next((tmp_path/'0245-notes-transactions').glob('*/transaction.json')))
    monkeypatch.setattr(r.time,'time_ns',lambda:999)
    result=f.recover_ready('0245',journal,owner='agent1',token=kw(start)['token'])
    assert result['tracker']['leases']==start['tracker']['leases']
    assert result['tracker']['repairs']==start['tracker']['repairs']
    assert f.require_synchronized('0245')
    assert f.recover_ready('0245',journal,owner='agent1',token=kw(start)['token'])==result


@pytest.mark.parametrize('change',['after','evidence','notes','tracker','token','marker'])
def test_interrupted_transaction_tamper_rejected(tmp_path,monkeypatch,change):
    f,p,start,selection,proof=fixture(tmp_path,monkeypatch)
    def stop(point):
        if point=='prepared':raise Interrupted()
    with pytest.raises(Interrupted):f.transition('0245','enqueue',selection,**kw(start),_fault=stop)
    journal=n.record(next((tmp_path/'0245-notes-transactions').glob('*/transaction.json')));d=n.read(journal);token=kw(start)['token']
    if change=='after':
        after=n.read(d['after']);after['repairs']['by_signature']['old']=0
        Path(d['after']['path']).write_bytes(l.canonical(after));d['after']=n.record(Path(d['after']['path']));Path(journal['path']).write_bytes(l.canonical(d));journal=n.record(Path(journal['path']))
    elif change=='evidence':Path(proof['evidence'][0]['path']).write_text('changed')
    elif change=='notes':p.with_name('0245-agent-notes.md').write_text('foreign')
    elif change=='tracker':p.write_text('{}')
    elif change=='token':token='wrong'
    else:Path(journal['path']).with_name('complete.json').write_text('{}')
    raw=p.read_bytes()
    with pytest.raises(ValueError):f.recover_ready('0245',journal,owner='agent1',token=token)
    assert p.read_bytes()==raw


def test_ordinary_operation_cannot_resume_stopped(tmp_path,monkeypatch):
    f,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True)
    t=start['tracker'];t.update(execution_state='STOPPED',terminal_verdict='STOPPED',final_status='STOPPED');t['promotion_failure']['fixability']='exhausted'
    raw=n.render_notes(t);t['agent_notes']['sha256']=s.sha(raw);Path(t['agent_notes']['path']).write_bytes(raw);p.write_bytes(l.canonical(t));start['sha256']=n.record(p)['sha256']
    with pytest.raises(ValueError,match='terminal state'):f.transition('0245','clear',selection,**kw(start))
    with pytest.raises(ValueError,match='released leases'):f.transition('0245','resume',selection,authority=authority(),expected_revision=t['revision'],expected_sha256=start['sha256'])


def stopped(f,p,start):
    t=start['tracker'];t.update(execution_state='STOPPED',terminal_verdict='STOPPED',final_status='STOPPED');t['promotion_failure']['fixability']='exhausted'
    raw=n.render_notes(t);t['agent_notes']['sha256']=s.sha(raw);Path(t['agent_notes']['path']).write_bytes(raw);p.write_bytes(l.canonical(t));start['sha256']=n.record(p)['sha256']
    return start


def test_root_complete_resume_after_concrete_notes_release_preserves_budget(tmp_path,monkeypatch):
    f,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True);start=stopped(f,p,start)
    released=f.release_stopped('0245',authority=authority(),**kw(start))
    assert released['tracker']['leases']['task_agent'] is None
    resumed=f.transition('0245','resume',selection,authority=authority(),expected_revision=released['revision'],expected_sha256=released['sha256'])
    assert resumed['tracker']['execution_state']=='running' and resumed['tracker']['promotion_failure'] is None
    assert all(v is None for v in resumed['tracker']['leases'].values())
    assert resumed['tracker']['repairs']==start['tracker']['repairs']
    assert all(v['attempt']==2 for v in resumed['tracker']['lane_state'].values())


@pytest.mark.parametrize('where',['prepared','notes_replaced','tracker_replaced'])
def test_root_resume_interrupted_requires_root_again(tmp_path,monkeypatch,where):
    f,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True);start=stopped(f,p,start)
    released=f.release_stopped('0245',authority=authority(),**kw(start))
    def stop(point):
        if point==where:raise Interrupted()
    with pytest.raises(Interrupted):f.transition('0245','resume',selection,authority=authority(),expected_revision=released['revision'],expected_sha256=released['sha256'],_fault=stop)
    journal=n.record(next((tmp_path/'0245-notes-transactions').glob('*/transaction.json')))
    with pytest.raises(ValueError):f.recover_ready('0245',journal,authority=authority(False))
    result=f.recover_ready('0245',journal,authority=authority())
    assert result['tracker']['leases']==released['tracker']['leases'] and result['tracker']['repairs']==start['tracker']['repairs']


def test_codec_rejects_arbitrary_callback_and_class_injection():
    from scripts.batch import harbor_transition_codec as codec
    with pytest.raises(ValueError):codec.encode(lambda: True)
    with pytest.raises(ValueError):codec.decode({'type':'os.system','value':{}})
    with pytest.raises(ValueError):codec.decode({'type':'Policy','value':{'endpoint':{'type':'scalar','value':'https://foreign.example'}}})


@pytest.mark.parametrize('change',[None,'stage','source_tuple','seg_tuple','predecessor','pointer','old_manifest','task_bytes','source_bytes'])
def test_final_release_uses_real_strict_resolvers(tmp_path,change):
    from types import SimpleNamespace
    from test_release_publication import _real_release_fixture
    case=_real_release_fixture(tmp_path)
    publication=case['release_root'];release=case['release']
    selected=r.ReleaseSelection(publication,case['source_root'],case['segmentation_root'],publication,release['release_id'],n.record(publication/'current-release.json'),n.record(case['release_dir']/'release-manifest.json'))
    before={'workbook_id':case['workbook'],'bindings':{'source_generation':{'id':case['source_id']},'segmentation_generation':{'id':case['segmentation_id']}},'promotion_ids':{'release_id':None}}
    selection=SimpleNamespace(content=SimpleNamespace(stage=tmp_path/'real-task-stage'))
    if change=='stage':(selection.content.stage/'instruction.md').write_text('changed stage')
    elif change=='source_tuple':before['bindings']['source_generation']['id']='a'*64
    elif change=='seg_tuple':before['bindings']['segmentation_generation']['id']='b'*64
    elif change=='predecessor':before['promotion_ids']['release_id']='c'*64
    elif change=='pointer':Path(selected.pointer['path']).write_text('{}')
    elif change=='old_manifest':
        p=Path(selected.manifest['path']);p.chmod(0o600);p.write_text('{}')
    elif change=='task_bytes':
        p=publication/'task-generations'/case['task_id']/'instruction.md';p.chmod(0o600);p.write_text('changed task')
    elif change=='source_bytes':
        p=next((case['source_dir']/'source').glob('*.xlsx'));p.chmod(0o600);p.write_bytes(b'changed')
    if change is None:
        ids,evidence=r._release(before,selection,selected)
        assert ids['release_id']==release['release_id'] and ids['task_generation_id']==case['task_id'] and len(evidence)==2
    else:
        with pytest.raises(ValueError):r._release(before,selection,selected)
