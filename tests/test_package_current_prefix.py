from dataclasses import asdict,replace
from pathlib import Path
import copy
import pytest
from scripts import harbor_package_current_prefix as H,harbor_campaign as C
from scripts.batch import harbor_environment_refresh as F,harbor_environment_worker as W,harbor_normalization_partitioned as P,harbor_partition_code_applicability as A
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_transition_codec import encode
from test_harbor_admission import setup
from test_harbor_workbook_bootstrap import selected as bootstrap_selected
from test_harbor_environment_worker import selected,worker,responses
from test_normalization_partitioned import respond

@pytest.fixture
def prefix(worker,monkeypatch,tmp_path):
    s,lease=worker;responses(monkeypatch);result=W.advance(s,lease,dispatch=True,credential='synthetic')
    old=result['selection'];lease=result['lease'];c=old.content
    prior=F.keep(tmp_path/'research-result.json',{**result,'lease':asdict(lease),'selection':encode(old)})
    ns=W.normalization.Selection(c.workbook,c.bindings,c.normalization_run,c.model_policy,s.normalization_budget)
    W.normalization.prepare(ns);new=replace(ns,run=tmp_path/'fresh'/ns.run.name)
    original_codes=A.profile(P.codes())
    with monkeypatch.context() as m:
        m.setattr(P,'codes',lambda:original_codes)
        prepared=P.prepare(new,s.campaign,tmp_path/'parts',prior_plan=record(ns.run/'production-selection.json'))
    respond(monkeypatch,new);P.dispatch(prepared,0,1,credential='synthetic');emitted=read(P.emit_phase1(prepared));r=new.run
    b={**c.bindings,'normalization_partitioned_review':emitted['normalization_partitioned_review'],'normalization_draft':record(r/'draft.json'),'normalizer':record(r/'normalize_0001.py'),'normalization_prepared_manifest':prepared,'normalized_phase1':emitted['phase1'],'normalized_final':record(r/'normalized.json'),'normalization_report':record(r/'normalization_report.json'),'normalization_exclusions':record(r/'exclusions.json')}
    current=replace(old,content=replace(c,normalization_run=r,normalization_prepared=prepared,normalization_call=None,bindings=b))
    proof=F.prepare(s,lease,prior,current,'normalization_phase1',tmp_path/'refresh');lease,_,committed=F.commit(s,lease,proof)
    controls=C.Controls(tmp_path/'actual-workbook.lock');descriptor=C._descriptor(s,lease,controls,(proof,committed));descriptor.update(schema_version='harbor-environment-coordinator-refresh/v1',refresh=proof)
    descriptor=F.keep(tmp_path/'descriptor.json',descriptor)
    for step in ('source_profile','normalization_phase2'):
        result=F.advance(s,lease,proof,tmp_path/step);assert result['status']=='STEP_COMPLETE';lease=result['lease']
    checkpoint=F.keep(tmp_path/'phase2.json',{**result,'descriptor':descriptor,'selection':encode(result['selection']),'lease':C.encode(lease)})
    before=read(record(s.bootstrap.policy.tracker_root/'0001.json'))
    return s,descriptor,checkpoint,before,result['selection'],proof


def test_full_current_prefix_and_original_history_are_preserved(prefix,monkeypatch,tmp_path):
    s,d,c,b,current,f=prefix;tracker=s.bootstrap.policy.tracker_root/'0001.json';old=tracker.read_bytes()
    monkeypatch.setattr(W.executor,'_open',lambda *a,**k:pytest.fail('no model calls'))
    result=H.inspect(s,d,c,b,current)
    assert len(result['current_complete_prefix_results'])==4 and len(result['current_original_prefix_results'])==2
    assert result['tracker_mutations']==0 and result['semantic_budget_reset'] is False
    assert tracker.read_bytes()==old
    receipt=F.keep(tmp_path/'receipt.json',result)
    assert H.validate(receipt,s,d,c,b,current)==result


@pytest.mark.parametrize('what',['source','request','call','checkpoint','witness','profile','future'])
def test_changed_noncode_or_future_work_cannot_be_skipped(prefix,what):
    s,d,c,b,current,f=prefix
    if what=='future':
        F.keep(Path(f['path']).parent/'continuation/environment.json',{})
    else:
        m=read(current.content.normalization_prepared)
        call=P._prepared_call(current.content.normalization_prepared,0,1);campaign=P._current(current.content.normalization_prepared,allow_emitted=True)[2]
        paths={'source':current.content.bindings['source_file']['path'],'request':m['partitions'][0]['request']['path'],'call':P.E.location(call,campaign)/'response.raw.json','checkpoint':c['path'],'witness':read(f)['mutable_witnesses'][0]['copy']['path'],'profile':Path(f['path']).parent/'continuation/source_profile.json'}
        p=Path(paths[what]);p.chmod(0o600);p.write_bytes(p.read_bytes()+b'changed')
    with pytest.raises((ValueError,KeyError,SystemExit)):H.inspect(s,d,c,b,current)


def test_exact_gate_code_evidence_projection_only():
    current=P.codes();roles=A.profile(current);extra=A.code_records();noncode={'/root/source.xlsx':'a'*64}
    old={'passed':True,'gate':'normalization_phase1','evidence':[dict(path=k,sha256=v) for k,v in {**roles,**noncode}.items()]}
    new={'passed':True,'gate':'normalization_phase1','evidence':[dict(path=k,sha256=v) for k,v in {**current,**noncode}.items()]}
    assert H._result(old,new,roles)['original_evidence']==old['evidence']
    changed=copy.deepcopy(new);changed['passed']=False
    with pytest.raises(ValueError):H._result(old,changed,roles)
    changed=copy.deepcopy(new);changed['evidence'].append(dict(path='/root/unrelated.py',sha256='b'*64))
    with pytest.raises(ValueError):H._result(old,changed,roles)
    changed=copy.deepcopy(new);next(x for x in changed['evidence'] if x['path']=='/root/source.xlsx')['sha256']='c'*64
    with pytest.raises(ValueError):H._result(old,changed,roles)
    changed=copy.deepcopy(old);next(x for x in changed['evidence'] if x['path']==str(Path(P.E.__file__).resolve()))['sha256']='c'*64
    with pytest.raises(ValueError):H._result(changed,new,roles)


@pytest.fixture
def scenario(prefix,tmp_path):
    from types import SimpleNamespace
    import sys
    s,d,c,b,current,f=prefix
    controls=C.Controls(tmp_path/'actual-workbook.lock')
    guard=tmp_path/'failed-guard';guard.mkdir();(guard/'step.log').write_text('synthetic completed failure\n')
    intent=F.keep(guard/'intent.json',dict(schema_version='harbor-coordinator-step/v1',action='step',arguments=dict(descriptor=d,checkpoint=c,captures=None),controls=C.encode(controls),dispatch=False,code=record(C.__file__),executable=record(Path(sys.executable).resolve()),memory_limit_bytes=8*1024**3,timeout_seconds=900))
    resource=F.keep(guard/'resource.json',dict(intent=intent,returncode=1,metrics={'peak_rss_bytes':1234},log=record(guard/'step.log'),failure=None))
    outcome=F.keep(guard/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None))
    return SimpleNamespace(s=s,lease=C.decode(read(c)['lease']),current=current,descriptor=d,checkpoint=c,outcome=outcome,controls=controls,home=tmp_path)


from test_parallel_package_lease_bridge import continued


def test_package_context_consumes_fully_validated_partition_prefix_after_pause(continued,tmp_path):
    from scripts import harbor_package_recovery as R
    c=continued;tracker=c.s.bootstrap.policy.tracker_root/(c.s.bootstrap.workbook+'.json');before=read(record(tracker));raw=tracker.read_bytes()
    receipt=F.keep(tmp_path/'prefix-current.json',H.inspect(c.s,c.descriptor,c.checkpoint,before,c.current))
    selected=R.context(c.descriptor,c.checkpoint,c.lease,pause_completion=c.pause,prefix_receipt=receipt)
    assert selected[:3]==(c.s,before,c.current)
    assert tracker.read_bytes()==raw
    with pytest.raises(ValueError):R.context(c.descriptor,c.checkpoint,c.lease,pause_completion=c.pause,prefix_receipt={**receipt,'sha256':'0'*64})
    altered=read(receipt);altered['semantic_budget_reset']=True
    forged=F.keep(tmp_path/'forged-prefix.json',altered)
    with pytest.raises(ValueError):R.context(c.descriptor,c.checkpoint,c.lease,pause_completion=c.pause,prefix_receipt=forged)
