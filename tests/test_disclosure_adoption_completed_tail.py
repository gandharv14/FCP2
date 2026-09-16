"""Retain actual successful phases after a historical reader stops before child."""
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
from scripts.fleet import disclosure_adoption_continue as S,disclosure_review_continue as M
from scripts.batch import harbor_disclosure_round_adoption as A,harbor_disclosure_review_round as Q
from scripts.batch.harbor_admission import record,read
from test_disclosure_adoption_resume import keep
from test_disclosure_adoption_renewal import renewed,case,instruction_setup,worker,selected,setup


def test_actual_changed_instruction_uses_archived_bytes_only_inside_original_round(renewed,tmp_path,monkeypatch):
    w,s,current,certificate,plan,result,old,before,after,build,instruction_archive=renewed
    home=tmp_path/'cold';home.mkdir();keep(home/'round-renewal.json',read(certificate))
    prior=read(certificate)['historical_instruction'];Path(prior['path']).write_text('Actual independently reviewed NAT instruction\n')
    assert record(prior['path'])!=prior
    def original_resume(*args):
        assert Q.life.checked_record(prior)==prior
        assert Q.read(before)==read(before)
        assert Q.record(prior['path'])==prior
        assert Path(prior['path']).read_text().startswith('Actual independently')
        return 'original history validated'
    monkeypatch.setattr(M,'resume_state',original_resume);monkeypatch.setitem(sys.modules,'scripts.batch.harbor_disclosure_round_adoption',A)
    p=dict(history=record(A.history.__file__),adoption=record(A.__file__),context=dict(plan=plan),round_result=result)
    adapted=S.completed_review_context(M,p,read(certificate)['authority'],home)
    call=adapted.__wrapped__.__globals__['resume_state']
    assert call()== 'original history validated'
    assert Q.record(prior['path'])!=prior
    with pytest.raises(ValueError):Q.life.checked_record(prior)
    Path(instruction_archive['path']).write_text('tampered archive')
    with pytest.raises(ValueError):call()


@pytest.fixture
def tail(tmp_path,monkeypatch):
    source=keep(tmp_path/'source',{});tracker=keep(tmp_path/'tracker',{});instruction=keep(tmp_path/'instruction',{})
    inputs=keep(tmp_path/'inputs',dict(instruction=instruction));rp=keep(tmp_path/'round',dict(inputs=inputs));rr=keep(tmp_path/'result',{})
    cfg=keep(tmp_path/'config',dict(run_root=str(tmp_path/'authoring')));original_job=keep(tmp_path/'job',dict(config=cfg))
    home=tmp_path/'old';descriptor=keep(tmp_path/'descriptor',{});initial=keep(tmp_path/'adopted',dict(status='STEP_COMPLETE',next_step='disclosure-commit',descriptor=descriptor))
    outcome=keep(tmp_path/'adopted-out',dict(result=initial));cp=keep(tmp_path/'completed-step',dict(outcome=outcome))
    old=dict(schema_version=S.SCHEMA,helper=record(S.__file__),owner='first',fence=3,output=str(home),source=source,context=dict(plan=rp),round_result=rr,original_job=original_job,completed_step=cp)
    row=keep(tmp_path/'old-plan',old);keep(home/'intent.json',dict(schema_version=S.SCHEMA,plan=row,automatic_retry=False))
    workerjob=keep(tmp_path/'worker-job',dict(driver=old['helper'],source=source,command=['python','--plan',row['path'],'--sha256',row['sha256']]))
    wi=keep(tmp_path/'worker-intent',dict(owner=old['owner'],job=workerjob));outer=keep(tmp_path/'outer',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,intent=wi))
    ops=tmp_path/'authoring/dispatcher-v2/old';outcomes=[];prior=initial
    for index,step in enumerate(['nat-init','nat-writer','nat-review','nat-commit']):
        path=ops/f'{index:03d}';intent=keep(path/'intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=prior)))
        guard=keep(path/'resource.json',dict(returncode=0,failure=None,intent=intent));res=keep(path/'result.json',dict(status='STEP_COMPLETE',next_step=step,descriptor=descriptor));out=keep(path/'outcome.json',dict(status='COMPLETED_OPERATION',result=res,resource=guard));outcomes.append(out);prior=res
    path=ops/'004';intent=keep(path/'intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=prior)));log=path/'step.log';log.write_text('resume_state\nscripts.batch.harbor_tracker.Conflict: changed evidence: '+instruction['path']+'\n')
    guard=keep(path/'resource.json',dict(returncode=1,failure=None,intent=intent,log=record(log)));failed=keep(path/'outcome.json',dict(status='RECONCILIATION_REQUIRED',result=None,resource=guard));keep(path/'aggregate-bootstrap.json',dict(schema_version=S.SCHEMA,plan=row,helper=old['helper'],kind='instruction',intent=intent,automatic_retry=False));keep(ops/'hold.json',dict(outcome=failed))
    proof=keep(tmp_path/'proof',dict(schema_version='fleet-disclosure-adoption-completed-tail/v1',plan=row,worker_outcome=outer,outcomes=outcomes,failed_outcome=failed,tracker=tracker))
    monkeypatch.setattr(S,'completed_step',lambda *a,**k:read(cp))
    return {**old,'helper':record(S.__file__),'owner':'second','fence':4,'output':str(tmp_path/'next'),'completed_tail':proof},ops,tracker


@pytest.mark.parametrize('fault',[None,'tracker','outcome','failed_intent','sidecar','later','source','fence'])
def test_exact_completed_prefix_and_no_later_effect(tail,fault):
    p,ops,tracker=tail
    if fault=='tracker':Path(tracker['path']).write_text('changed')
    elif fault=='outcome':(ops/'002/result.json').write_text('{}')
    elif fault=='failed_intent':(ops/'004/intent.json').write_text('{}')
    elif fault=='sidecar':(ops/'004/aggregate-bootstrap.json').write_text('{}')
    elif fault=='later':(ops/'005').mkdir()
    elif fault=='source':p['source']={**p['source'],'sha256':'f'*64}
    elif fault=='fence':p['fence']=8
    if fault:
        with pytest.raises((ValueError,FileNotFoundError)):S.completed_tail(p)
    else:
        proof,old,invocation=S.completed_tail(p)
        assert len(proof['outcomes'])==4 and old['_binding']==proof['plan']
        assert invocation[0]['path'].endswith('004/intent.json')
        Path(tracker['path']).write_text('later authentic checkpoint is checked separately')
        assert S.completed_tail(p,continued=True)
    assert not Path(p['output']).exists()

from test_disclosure_partition_review import reviewed,ROOT
from scripts.batch import harbor_disclosure_partition_review as R

@pytest.mark.parametrize('fault',[None,'archive','unsubmitted','missing_duplicate'])
def test_real_full_partition_source_rederivation_uses_submitted_archive(reviewed,tmp_path,fault):
    plan,inputs,projection,args,policy,calls=reviewed
    archive=tmp_path/'budget-034.snapshot';archive.write_bytes(Path(inputs['instruction']['path']).read_bytes());arch=record(archive)
    home=tmp_path/'actual-round';run=keep(tmp_path/'run',dict(output=str(home),modules=dict(review=record(R.__file__))))
    result=keep(tmp_path/'round-result',dict(actual_review_calls=1,review_plan=plan))
    submitted=keep(tmp_path/'submitted',dict(budget_evidence=[arch]if fault!='unsubmitted'else[]))
    keep(home/'batches/0000/complete.json',dict(index=0,plan=plan,call=dict(submitted=submitted)))
    instruction=inputs['instruction'];rows=[dict(original=instruction,archive=arch),dict(original=instruction,archive=instruction)]
    if fault=='missing_duplicate':rows=rows[1:]
    receipt=dict(round_plan=run,round_result=result,historical_instruction=instruction,historical_records=rows)
    p=dict(context=dict(plan=run),round_result=result)
    Path(instruction['path']).write_text('A later independently reviewed NAT text\n')
    if fault=='archive':archive.write_text('tampered')
    old=(R.validate_plan,R.full_body,R.checked_record)
    if fault:
        with pytest.raises(ValueError):
            with S.archived_instruction_runtime(A,p,receipt,A.historical_round):R.validate_plan(plan,policy=policy,root=ROOT,current_inputs=True)
    else:
        with S.archived_instruction_runtime(A,p,receipt,A.historical_round):
            checked=R.validate_plan(plan,policy=policy,root=ROOT,current_inputs=True)
            assert checked[0]==inputs
            assert Path(instruction['path']).read_text().startswith('A later')
        with pytest.raises(ValueError):R.validate_plan(plan,policy=policy,root=ROOT,current_inputs=True)
    assert (R.validate_plan,R.full_body,R.checked_record)==old

@pytest.mark.parametrize('fault',[None,'missing','altered','member'])
def test_original_typed_stage_tree_rebuilt_from_complete_archived_members(tmp_path,fault):
    from scripts.batch import harbor_readiness as static
    stage=tmp_path/'stage';stage.mkdir();(stage/'instruction.md').write_text('original reviewed instruction');(stage/'tests').mkdir();(stage/'tests/test.py').write_text('original independent tests')
    digest,_=static.publication._tree_hash(stage)
    prefix=[dict(kind='environment',result=dict(bindings={'staged_bundle':dict(path=str(stage),sha256=digest)},evidence=[],passed=True))]
    evidence,directories=Q.prefix_records(prefix);archives=[]
    for i,row in enumerate(directories[0]['files']):
        path=tmp_path/f'budget-{i:03d}.snapshot';path.write_bytes(Path(row['path']).read_bytes());archives.append(dict(original=row,archive=record(path)))
    pb=keep(tmp_path/'prefix',prefix);authority=keep(tmp_path/'authority',dict(prefix=pb,prefix_directories=directories));plan=keep(tmp_path/'plan',dict(review_round=authority));result=keep(tmp_path/'result',dict(review_plan=plan))
    receipt=dict(historical_records=archives);p=dict(round_result=result)
    (stage/'instruction.md').write_text('later NAT text');(stage/'later.json').write_text('actual later task artifact')
    if fault=='missing':receipt['historical_records']=archives[1:]
    elif fault=='altered':Path(archives[0]['archive']['path']).write_text('different source')
    elif fault=='member':directories[0]['files'].pop();authority=keep(tmp_path/'authority2',dict(prefix=pb,prefix_directories=directories));plan=keep(tmp_path/'plan2',dict(review_round=authority));p['round_result']=keep(tmp_path/'result2',dict(review_plan=plan))
    reader=S.archived_prefix_reader(A,p,receipt,Q,Q.prefix_records)
    if fault:
        with pytest.raises((ValueError,KeyError)):reader(prefix)
    else:
        assert reader(prefix)==(evidence,directories)
        with pytest.raises(ValueError):Q.prefix_records(prefix)

@pytest.mark.parametrize('fault',[None,'missing_member','altered_archive'])
def test_distinct_instruction_archive_still_revalidates_original_stage_tree(tmp_path,fault):
    from scripts.batch import harbor_readiness as static
    stage=tmp_path/'stage';stage.mkdir();(stage/'instruction.md').write_text('reviewed text');(stage/'test.txt').write_text('independent test')
    digest,_=static.publication._tree_hash(stage)
    prefix=[dict(kind='environment',result=dict(bindings={'staged_bundle':dict(path=str(stage),sha256=digest)},evidence=[],passed=True))]
    expected=Q.prefix_records(prefix);records=[];instruction=record(stage/'instruction.md')
    for i,row in enumerate(expected[1][0]['files']):
        a=tmp_path/f'budget-{i:03d}.snapshot';a.write_bytes(Path(row['path']).read_bytes());records.append(dict(original=row,archive=record(a)))
    pb=keep(tmp_path/'prefix',prefix);authority=keep(tmp_path/'authority',dict(prefix=pb,prefix_directories=expected[1]));plan=keep(tmp_path/'plan',dict(review_round=authority));result=keep(tmp_path/'result',dict(review_plan=plan));run=keep(tmp_path/'run',{})
    receipt=dict(round_plan=run,round_result=result,historical_instruction=instruction,historical_records=records)
    p=dict(context=dict(plan=run),round_result=result)
    (stage/'instruction.md').write_text('actual reviewed NAT text');(stage/'new-file').write_text('actual later stage')
    if fault=='missing_member':receipt['historical_records']=records[1:]
    if fault=='altered_archive':Path(records[0]['archive']['path']).write_text('changed')
    original=Q.prefix_records
    if fault:
        with pytest.raises((ValueError,KeyError)):
            with S.archived_instruction_runtime(A,p,receipt,A.historical_round):Q.prefix_records(prefix)
    else:
        with S.archived_instruction_runtime(A,p,receipt,A.historical_round):assert Q.prefix_records(prefix)==expected
        assert (stage/'instruction.md').read_text()=='actual reviewed NAT text'
    assert Q.prefix_records is original

@pytest.fixture
def direct_tail(tail,tmp_path,monkeypatch):
    p,ops,tracker=tail;proof=read(p['completed_tail']);old=read(proof['plan']);home=Path(old['output'])
    initial_out=read(read(old['completed_step'])['outcome']);initial=initial_out['result'];descriptor=read(initial)['descriptor']
    checkpoint=keep(tmp_path/'original-permit-result',dict(status='RETRY_READY'));resume=dict(descriptor=descriptor,checkpoint=checkpoint)
    old.pop('completed_step');old.update(resume=keep(tmp_path/'resume',resume),archived_history=True,semantic_archive={})
    oldrow=keep(tmp_path/'direct-old-plan',old)
    (home/'intent.json').unlink();keep(home/'intent.json',dict(schema_version=S.SCHEMA,plan=oldrow,automatic_retry=False))
    job=keep(tmp_path/'direct-worker-job',dict(driver=old['helper'],source=old['source'],command=['python','--plan',oldrow['path'],'--sha256',oldrow['sha256']]))
    wi=keep(tmp_path/'direct-worker-intent',dict(owner=old['owner'],job=job));outer=keep(tmp_path/'direct-outer',dict(read(proof['worker_outcome']),intent=wi))
    first_intent=keep(home/'first-step/intent.json',dict(action='step',arguments=resume));first_guard=keep(home/'first-step/resource.json',dict(returncode=0,failure=None,intent=first_intent))
    first=keep(home/'first-step/outcome.json',dict(status='COMPLETED_OPERATION',result=initial,resource=first_guard))
    path=ops/'004';guard=read(read(proof['failed_outcome'])['resource']);log=Path(guard['log']['path']);log.write_text('validate(worker,before)\nhistory.completed_run\nprefix_records\nscripts.batch.harbor_admission.AdmissionError: typed prefix directory changed\n')
    guard['log']=record(log);(path/'resource.json').unlink();resource=keep(path/'resource.json',guard)
    (path/'outcome.json').unlink();failed=keep(path/'outcome.json',dict(status='RECONCILIATION_REQUIRED',result=None,resource=resource))
    (ops/'hold.json').unlink();keep(ops/'hold.json',dict(outcome=failed))
    side=read(record(path/'aggregate-bootstrap.json'));side['plan']=oldrow;(path/'aggregate-bootstrap.json').unlink();keep(path/'aggregate-bootstrap.json',side)
    selected=keep(tmp_path/'direct-proof',dict(proof,schema_version='fleet-disclosure-adoption-completed-tail/v2',plan=oldrow,worker_outcome=outer,first_outcome=first,failed_outcome=failed))
    monkeypatch.setattr(S,'resume_state',lambda *a,**k:resume)
    return {**old,'helper':record(S.__file__),'owner':'second','fence':4,'output':str(tmp_path/'next'),'completed_tail':selected},ops,first


@pytest.mark.parametrize('fault',[None,'first_guard','first_intent','log','later','fence'])
def test_direct_completed_consumer_retains_original_prefix_without_invented_intermediate_selection(direct_tail,fault):
    p,ops,first=direct_tail
    if fault=='first_guard':Path(read(first)['resource']['path']).write_text('{}')
    elif fault=='first_intent':Path(read(read(first)['resource'])['intent']['path']).write_text('{}')
    elif fault=='log':(ops/'004/step.log').write_text('other failure')
    elif fault=='later':(ops/'005').mkdir()
    elif fault=='fence':p['fence']+=1
    if fault:
        with pytest.raises((ValueError,KeyError)):S.completed_tail(p)
    else:
        value,old,_=S.completed_tail(p)
        assert value['first_outcome']==first and 'completed_step'not in old
    assert not Path(p['output']).exists()

@pytest.mark.parametrize('fault',[None,'foreign','tampered'])
def test_nested_original_aggregate_reuses_exact_archive_scope(tmp_path,fault):
    from scripts.batch import harbor_readiness as static
    stage=tmp_path/'stage';stage.mkdir();(stage/'instruction.md').write_text('reviewed')
    digest,_=static.publication._tree_hash(stage)
    prefix=[dict(kind='environment',result=dict(bindings={'staged_bundle':dict(path=str(stage),sha256=digest)},evidence=[],passed=True))]
    expected=Q.prefix_records(prefix);row=expected[1][0]['files'][0];archive=tmp_path/'budget-001.snapshot';archive.write_bytes(Path(row['path']).read_bytes())
    records=[dict(original=row,archive=record(archive))];pb=keep(tmp_path/'prefix',prefix);authority=keep(tmp_path/'authority',dict(prefix=pb,prefix_directories=expected[1]));plan=keep(tmp_path/'plan',dict(review_round=authority));result=keep(tmp_path/'result',dict(review_plan=plan));run=keep(tmp_path/'run',{})
    receipt=dict(round_plan=run,round_result=result,historical_instruction=row,historical_records=records)
    p=dict(context=dict(plan=run),round_result=result,archived_history=True)
    (stage/'instruction.md').write_text('later reviewed NAT text');original=A.historical_round;prefix_fn=Q.prefix_records
    with S.historical_runtime(A,p):
        with A.historical_round(receipt):
            assert Q.prefix_records(prefix)==expected
            nested=receipt if fault!='foreign'else dict(receipt,round_result={})
            if fault=='tampered':archive.write_text('tampered actual archive')
            if fault:
                with pytest.raises(ValueError):
                    with A.historical_round(nested):pass
            else:
                with A.historical_round(nested):assert Q.prefix_records(prefix)==expected
    assert A.historical_round is original and Q.prefix_records is prefix_fn
