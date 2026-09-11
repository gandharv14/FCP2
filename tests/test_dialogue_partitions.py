import copy,json
from pathlib import Path
from dataclasses import asdict,replace
import pytest
from scripts.batch import harbor_dialogue_partition_plan as D,harbor_dialogue_partition_review as R
from scripts.batch import harbor_instruction_producer as P
from scripts.batch.harbor_admission import record
from test_harbor_instruction_producer import setup,dialogue_fixture
from test_harbor_semantic_readiness import bind_json,set_call
from test_harbor_model_review_provenance import fixture as journal
ROOT=Path.cwd()

@pytest.fixture
def planned(setup,tmp_path):
    task,_,_,_=dialogue_fixture(setup)
    x=P.skill_module(ROOT,'additional-assumptions-dialogue','extract_claims')
    disclosure=P.read(record(task.stage/'tests/disclosure.json'));entry=disclosure['agent_records'][0]
    records=[{**copy.deepcopy(entry),'cells':['S!'+col+'3']} for col in ('C','D','E')]
    disclosure.update(agent_records=records,records=records)
    (task.stage/'tests/disclosure.json').write_text(json.dumps(disclosure))
    task=replace(task,stage_tree=P.tree(task.stage))
    claims=x.extract(task.stage)
    projection=D.build(claims,root=ROOT,max_claims=1)
    run=tmp_path/'worker';home=run/'dialogue-partition';rounddir=home/'round-01';rounddir.mkdir(parents=True)
    plan=bind_json(rounddir/'plan.json',dict(claims=claims,projection=projection));before,identity=P.task_context(task)
    initial=bind_json(run/'initial.json',{});implementation=record(ROOT/'scripts/batch/harbor_dialogue_partition_continuation.py')
    continuation=bind_json(home/'permit.json',dict(schema_version='harbor-dialogue-partition-continuation/v1',worker_run=str(run),root=str(ROOT),initial=initial,implementation=implementation,workbook=identity['workbook'],original_source=identity['original'],source=before['bindings']['source_file'],global_limit=2))
    bind_json(run/'dialogue-partition-continuation.json',dict(schema_version='harbor-dialogue-partition-continuation/v1',permit=continuation))
    checkpoint=bind_json(run/'dialogue-init-complete.json',dict(step='dialogue-init',status='completed',selection={'fixture':'selection'},lease={}))
    authority=bind_json(rounddir/'authority.json',dict(schema_version=R.AUTHORITY,plan=plan,global_round=1,global_limit=2,workbook=identity['workbook'],original_source=identity['original'],source=before['bindings']['source_file'],answer_key=record(task.stage/'tests/answer_key.json'),task_dir=str(task.stage),scope_evidence=None,policy=asdict(task.policy),initial=initial,previous_round=None,continuation=continuation,implementation=implementation,tracker=bind_json(rounddir/'tracker-before.json',before),predecessor=checkpoint,selection={'fixture':'selection'},lease={}))
    return task,claims,projection,plan,authority

def test_global_scaffold_slices_are_exact_and_do_not_restart_cast(planned):
    task,claims,projection,plan,authority=planned
    assert ''.join(p['template'] for p in projection['partitions'])==projection['template']
    assert [x for p in projection['partitions'] for x in p['slot_ids']]==projection['slots']['slot_order']
    assert projection['partitions'][1]['slots']['slots']['c002-junior']==projection['slots']['slots']['c002-junior']
    assert len(projection['seams'])==2
    D.validate(projection,claims,root=ROOT)

@pytest.mark.parametrize('fault',['missing','duplicate','cast','scope','order','seam','bound'])
def test_plan_mutation_rejected(planned,fault):
    _,claims,p,_,_=planned;p=copy.deepcopy(p)
    if fault=='missing':p['partitions'].pop()
    elif fault=='duplicate':p['partitions'][1]=p['partitions'][0]
    elif fault=='cast':p['partitions'][1]['slots']['slots']['c002-junior']['speaker']='CEO'
    elif fault=='scope':p['partitions'][1]['claims']['claims'][0]['row_label']='Other'
    elif fault=='order':p['partitions'].reverse()
    elif fault=='seam':p['seams']=[]
    else:p['max_bytes']=2
    with pytest.raises(ValueError):D.validate(p,claims,root=ROOT)

@pytest.fixture
def completed(planned,tmp_path):
    task,claims,projection,plan,authority=planned;a,p=R.selected(authority,task.policy,root=ROOT)
    def call(kind,index,payload,response):
        out=tmp_path/(kind+str(index));out.mkdir();c,_,_=journal(out,'naturalization');set_call(c,payload,response)
        s=P.read(c.submitted);s.update(scope=asdict(R.scope(a,kind,index)),semantic_ordinal=1,semantic_limit=1,budget_evidence=[authority],response_mode='json_object')
        object.__setattr__(c,'submitted',bind_json(Path(c.submitted['path']),s))
        r=P.read(c.response);r['id']='actual-'+kind+str(index);object.__setattr__(c,'response',bind_json(Path(c.response['path']),r))
        complete=P.read(c.completed);complete['response']=c.response;complete['response_mode']='json_object';object.__setattr__(c,'completed',bind_json(Path(c.completed['path']),complete));return c
    writers=[];paras=[];outputs=[]
    for i,part in enumerate(projection['partitions']):
        values={'schema_version':'dialogue-slot-prose/v1','slots':{sid:('Which source should I use?' if meta['kind']=='junior' else 'Take Revenue from the Opening balance row, using the S tab.') for sid,meta in part['slots']['slots'].items()}}
        w=call('writer',i,R.author_payload(a,p,i,'writer',task.policy,root=ROOT),values);writers.append(w)
        actual=R.validate_author(w,authority,i,'writer',task.policy,root=ROOT)
        q=call('paraphraser',i,R.author_payload(a,p,i,'paraphraser',task.policy,root=ROOT,incoming=actual['filled']),values);paras.append(q);outputs.append(values)
    composer=P.skill_module(ROOT,'additional-assumptions-dialogue','compose_draft');text=composer.strip_slot_scaffold(D.assemble(projection,outputs,root=ROOT));draft=tmp_path/'draft.md';draft.write_text(text);draft=record(draft)
    reviews=[]
    for i,part in enumerate(projection['partitions']):
        review=dict(agent_model='gpt-5.6-sol-high',round=1,accuracy=dict(verdict='pass',claims=[dict(record_id=x,must_say='entailed',findings=[]) for x in part['claim_ids']],extras=[],cell_refs_in_senior_turns=[]),naturalness=dict(verdict='pass',findings=[]),passed=True)
        response=dict(review=review,seams=[dict(seam_id=s['id'],passed=True,findings=[]) for s in projection['seams'] if s['owner']==i])
        reviews.append(call('review',i,R.review_payload(a,p,i,task.policy,text,root=ROOT),response))
    return task,claims,projection,plan,authority,writers,paras,reviews,draft

def test_real_calls_reconcile_truthful_report_and_exact_text(completed,tmp_path):
    task,claims,projection,plan,authority,writers,paras,reviews,draft=completed
    result=R.aggregate(authority,writers,paras,reviews,draft,task.policy,root=ROOT,task_dir=task.stage)
    assert result['passed'] is True and len(result['review']['accuracy']['claims'])==3
    assert len(result['writer_calls'])+len(result['paraphraser_calls'])+len(result['review_calls'])==9
    assert 'call' not in result

@pytest.mark.parametrize('fault',['missing','reused','scope','budget','authority','junior','coverage','seam','draft','response'])
def test_changed_or_incomplete_actual_calls_do_not_promote(completed,fault):
    task,claims,projection,plan,authority,ws,ps,rs,draft=completed
    if fault=='missing':rs.pop()
    elif fault=='reused':rs[1]=rs[0]
    elif fault=='draft':Path(draft['path']).write_text('changed')
    elif fault=='response':Path(rs[0].response['path']).write_text('{}')
    elif fault in ('scope','budget','authority'):
        c=ws[0];s=P.read(c.submitted)
        if fault=='scope':s['scope']['budget_id']='other'
        elif fault=='budget':s['semantic_limit']=2
        else:s['budget_evidence']=[]
        object.__setattr__(c,'submitted',bind_json(Path(c.submitted['path']),s))
    else:
        c=ps[0] if fault=='junior' else rs[1];raw=P.read(c.parsed)
        if fault=='junior':raw['slots']['c001-junior']='Changed question'
        elif fault=='coverage':raw['review']['accuracy']['claims']=[]
        else:raw['seams']=[]
        set_call(c,P.read(c.request),raw)
    with pytest.raises(ValueError):R.aggregate(authority,ws,ps,rs,draft,task.policy,root=ROOT,task_dir=task.stage)

def test_substantive_rejection_is_retained_without_fake_pass(completed):
    task,claims,projection,plan,authority,ws,ps,rs,draft=completed
    c=rs[0];raw=P.read(c.parsed);raw['review']['accuracy']['claims'][0]['must_say']='contradicted';raw['review']['accuracy']['verdict']='fail';raw['review']['passed']=False;set_call(c,P.read(c.request),raw)
    result=R.aggregate(authority,ws,ps,rs,draft,task.policy,root=ROOT,task_dir=task.stage)
    assert result['passed'] is False and result['review']['accuracy']['claims'][0]['must_say']=='contradicted'

def test_actual_apply_and_publication_copy_revalidate_all_claims(completed,tmp_path):
    import shutil
    task,claims,projection,plan,authority,ws,ps,rs,draft=completed
    result=R.aggregate(authority,ws,ps,rs,draft,task.policy,root=ROOT,task_dir=task.stage)
    binding=bind_json(tmp_path/'aggregate.json',result);cb=bind_json(tmp_path/'claims.json',claims)
    validator=P.skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
    a=P.read(authority)
    (task.stage/'environment/0245.xlsx').write_bytes(Path(a['source']['path']).read_bytes())
    (task.stage/'environment/Dockerfile').write_text('FROM python:3.12\nWORKDIR /app\nCOPY 0245.xlsx /app/0245.xlsx\n')
    validator.apply(task.stage,Path(draft['path']),claims,result['review'],require_review_pass=True,skip_smoke=True)
    notes=record(task.stage/'environment/additional-assumptions.md');a=P.read(authority)
    args=dict(claims=cb,reviewed_claims=cb,draft=draft,notes=notes,policy=task.policy,workbook=a['workbook'],original_sha256=a['original_source']['sha256'],source=a['source'],answer_key=record(task.stage/'tests/answer_key.json'),task_dir=task.stage,root=ROOT)
    assert R.validate_aggregate(binding,**args)['claims']==3
    copied=tmp_path/'published'/task.stage.name;shutil.copytree(task.stage,copied)
    copydraft=tmp_path/'copied-draft.md';copydraft.write_bytes(Path(draft['path']).read_bytes())
    args.update(task_dir=copied,draft=record(copydraft),notes=record(copied/'environment/additional-assumptions.md'),answer_key=record(copied/'tests/answer_key.json'))
    assert R.validate_aggregate(binding,**args)['review']==result['review']
    (copied/'tests/answer_key.json').write_text('{}')
    with pytest.raises(ValueError):R.validate_aggregate(binding,**args)


def test_remaining_global_round_keeps_rejected_calls_and_requires_new_identities(completed,tmp_path):
    task,claims,projection,plan,authority,ws,ps,rs,draft=completed
    raw=P.read(rs[0].parsed);raw['review']['accuracy']['claims'][0]['must_say']='contradicted';raw['review']['accuracy']['verdict']='fail';raw['review']['passed']=False;set_call(rs[0],P.read(rs[0].request),raw)
    old=R.aggregate(authority,ws,ps,rs,draft,task.policy,root=ROOT,task_dir=task.stage);prior=bind_json(tmp_path/'rejected.json',old)
    a=P.read(authority);directory=Path(authority['path']).parent.parent/'round-02';directory.mkdir()
    a.update(global_round=2,previous_round=prior,tracker=bind_json(directory/'tracker-before.json',P.read(a['tracker'])))
    next_authority=bind_json(directory/'authority.json',a);a,p=R.selected(next_authority,task.policy,root=ROOT)
    new=[[],[],[]]
    def call(kind,index,payload,response):
        out=tmp_path/('r2'+kind+str(index));out.mkdir();c,_,_=journal(out,'naturalization');set_call(c,payload,response)
        submitted=P.read(c.submitted);submitted.update(scope=asdict(R.scope(a,kind,index)),semantic_ordinal=1,semantic_limit=1,budget_evidence=[next_authority],response_mode='json_object');object.__setattr__(c,'submitted',bind_json(Path(c.submitted['path']),submitted))
        response=P.read(c.response);response['id']='r2-'+kind+str(index);object.__setattr__(c,'response',bind_json(Path(c.response['path']),response))
        complete=P.read(c.completed);complete.update(response=c.response,response_mode='json_object');object.__setattr__(c,'completed',bind_json(Path(c.completed['path']),complete));return c
    for i in range(len(projection['partitions'])):
        payload=R.author_payload(a,p,i,'writer',task.policy,root=ROOT)
        assert 'previous_partition_draft' in json.loads(payload['messages'][1]['content'])
        w=call('writer',i,payload,P.read(ws[i].parsed));new[0].append(w)
        filled=R.validate_author(w,next_authority,i,'writer',task.policy,root=ROOT)['filled']
        new[1].append(call('paraphraser',i,R.author_payload(a,p,i,'paraphraser',task.policy,root=ROOT,incoming=filled),P.read(ps[i].parsed)))
        review=P.read(rs[i].parsed);review['review']['round']=2;review['review']['passed']=True;review['review']['accuracy']['verdict']='pass'
        for row in review['review']['accuracy']['claims']:row['must_say']='entailed'
        new[2].append(call('review',i,R.review_payload(a,p,i,task.policy,P.text(draft),root=ROOT),review))
    result=R.aggregate(next_authority,*new,draft,task.policy,root=ROOT,task_dir=task.stage)
    assert result['round']==2 and result['passed'] and P.read(prior)==old
    a['global_round']=3;bad=bind_json(tmp_path/'round3.json',a)
    with pytest.raises(ValueError):R.selected(bad,task.policy,root=ROOT)

def test_owning_style_warning_policy_remains_intact(completed):
    task,claims,projection,plan,authority,ws,ps,rs,draft=completed
    call=rs[0];raw=P.read(call.parsed)
    raw['review']['naturalness']=dict(verdict='fail',findings=['The dialogue is repetitive.']);raw['review']['passed']=False
    set_call(call,P.read(call.request),raw)
    result=R.aggregate(authority,ws,ps,rs,draft,task.policy,root=ROOT,task_dir=task.stage)
    assert result['passed'] is True
    assert result['review']['passed'] is False and result['review']['naturalness']['verdict']=='fail'
    assert result['review']['naturalness']['findings']==['The dialogue is repetitive.']


def test_duplicate_response_identity_cannot_hide_behind_distinct_requests(completed):
    task,claims,projection,plan,authority,ws,ps,rs,draft=completed
    call=rs[0];response=P.read(call.response);response['id']=P.read(ws[0].response)['id']
    object.__setattr__(call,'response',bind_json(Path(call.response['path']),response))
    complete=P.read(call.completed);complete['response']=call.response;object.__setattr__(call,'completed',bind_json(Path(call.completed['path']),complete))
    with pytest.raises(ValueError):R.aggregate(authority,ws,ps,rs,draft,task.policy,root=ROOT,task_dir=task.stage)

@pytest.mark.parametrize('fault',['foreign_namespace','marker','initial','tracker_attempt','source','checkpoint','implementation'])
def test_canonical_global_reservation_cannot_be_reset(planned,tmp_path,fault):
    task,claims,projection,plan,authority=planned;a=P.read(authority)
    if fault=='foreign_namespace':authority=bind_json(tmp_path/'copied-authority.json',a)
    elif fault=='marker':Path(a['continuation']['path']).parent.parent.joinpath('dialogue-partition-continuation.json').unlink()
    else:
        if fault=='initial':a['initial']=bind_json(tmp_path/'other-initial.json',{})
        elif fault=='tracker_attempt':
            tracker=P.read(a['tracker']);tracker['lane_state']['harbor-dialogue-author']['attempt']=1;a['tracker']=bind_json(Path(a['tracker']['path']),tracker)
        elif fault=='source':a['source']=a['original_source']
        elif fault=='checkpoint':a['selection']={'different':'selection'}
        else:a['implementation']=a['initial']
        authority=bind_json(Path(authority['path']),a)
    with pytest.raises((ValueError,FileNotFoundError)):R.selected(authority,task.policy,root=ROOT)
