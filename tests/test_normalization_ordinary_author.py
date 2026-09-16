"""Stopped ordinary authors retain all completed partition authority."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import normalization_remaining as N


def save(path,value):
    path.write_text(json.dumps(value));return N.binding(path)


@pytest.fixture
def ordinary(tmp_path):
    run=tmp_path/'normalization';run.mkdir()
    source=save(tmp_path/'source.json',{'original':True})
    job=save(tmp_path/'author-job.json',{'driver':'original'})
    cp=save(tmp_path/'checkpoint.json',{'status':'UNRESOLVED'})
    outcome=save(tmp_path/'step-outcome.json',{'status':'COMPLETED_OPERATION'})
    error=save(tmp_path/'unresolved.json',{'original_error':True})
    result=save(tmp_path/'result.json',dict(status='UNRESOLVED',workbook='0001',job=job,outcome=outcome,
        checkpoint=cp,automatic_retry=False,publication_performed=False))
    worker=save(tmp_path/'worker.json',dict(source=source,workbook='0001',result=result['path']))
    intent=save(tmp_path/'intent.json',dict(job=worker))
    outer=save(tmp_path/'outer.json',dict(status='RECONCILIATION_REQUIRED',returncode=0,failure_type=None,
        automatic_retry=False,publication_performed=False,intent=intent,result=result))
    evidence=[outer,intent,worker,result,job,outcome,cp,error]
    stop=save(tmp_path/'stopped.json',dict(at=1,claims=0,models=0,processes=[],
        files=[dict(native=r,copy=r)for r in evidence]))
    prepared=save(tmp_path/'prepared.json',{'original_calls':[1,2,3]})
    review={'cells':{'Sheet!B2':{'eligible':True}}};save(run/'normalization-review.json',review)
    calls=[]
    def aggregate(p):
        assert p==prepared;calls.append(p);return dict(review=review)
    P=SimpleNamespace(selection=lambda p:SimpleNamespace(run=run),aggregate=aggregate)
    p=dict(prior_outcome=outer,source=source,workbook='0001',stopped=stop,prepared=prepared)
    o=dict(job=job,outcome=outcome,checkpoint=cp,unresolved=error)
    return p,o,P,run,calls


def test_ordinary_failure_rechecks_original_aggregate_without_new_calls(ordinary):
    p,o,P,run,calls=ordinary
    assert N.ordinary_stopped(p,o,{},P)==dict(review=N.read(N.binding(run/'normalization-review.json')))
    assert calls==[p['prepared']]
    assert sorted(x.name for x in run.iterdir())==['normalization-review.json']


@pytest.mark.parametrize('change',['active-process','active-claim','active-model','missing-proof','duplicate-proof',
    'changed-bytes','foreign-source','foreign-job','different-status','uncertain-outer','different-checkpoint'])
def test_ordinary_stopped_custody_rejects_foreign_or_uncertain_state(ordinary,change,tmp_path):
    p,o,P,run,calls=ordinary
    outer=N.read(p['prior_outcome']);result=N.read(outer['result']);stop=N.read(p['stopped'])
    if change=='active-process':stop['processes']=[{'pid':1}]
    elif change=='active-claim':stop['claims']=1
    elif change=='active-model':stop['models']=1
    elif change=='missing-proof':stop['files'].pop()
    elif change=='duplicate-proof':stop['files'].append(stop['files'][0])
    elif change=='changed-bytes':Path(stop['files'][-1]['native']['path']).write_text('{}')
    elif change=='foreign-source':p['source']={'path':'/foreign','sha256':'f'*64}
    elif change=='foreign-job':o['job']=save(tmp_path/'other-job.json',{})
    elif change=='different-status':outer['status']='RUNNING'
    elif change=='uncertain-outer':outer['returncode']=None
    else:o['checkpoint']=save(tmp_path/'other-checkpoint.json',{})
    if change in {'different-status','uncertain-outer'}:p['prior_outcome']=save(tmp_path/'other-outer.json',outer)
    p['stopped']=save(tmp_path/'other-stop.json',stop)
    with pytest.raises(ValueError):N.ordinary_stopped(p,o,{},P)
    assert not calls


@pytest.mark.parametrize('name',['phase1-result.json','normalized.phase1.json','normalized.json',
    'normalization_report.json','exclusions.json','normalize_0001.py','source_profiles.json'])
def test_partial_emission_or_downstream_outputs_are_never_replayed(ordinary,name):
    p,o,P,run,calls=ordinary;(run/name).write_text('{}')
    with pytest.raises(ValueError,match='already exists'):N.ordinary_stopped(p,o,{},P)
    assert not calls


def test_original_aggregate_failure_is_not_waived(ordinary):
    p,o,P,run,calls=ordinary
    def reject(_):raise ValueError('actual completed review custody failed')
    P.aggregate=reject
    with pytest.raises(ValueError,match='actual completed review'):N.ordinary_stopped(p,o,{},P)


def test_review_bytes_must_equal_original_completed_aggregate(ordinary):
    p,o,P,run,calls=ordinary;(run/'normalization-review.json').write_text('{"cells":{}}')
    with pytest.raises(ValueError,match='actual completed calls'):N.ordinary_stopped(p,o,{},P)
    assert calls==[p['prepared']]


def test_closed_ordinary_context_cannot_mix_legacy_or_invented_authority(tmp_path):
    value={k:None for k in N.ORDINARY_FIELDS};value.update(schema_version=N.SCHEMA,helper=N.binding(str(Path(N.__file__).absolute())))
    for key in ['previous','previous_helper','failed_guard','pretend_review_pass']:
        row=save(tmp_path/(key+'.json'),{**value,key:{}})
        with pytest.raises(ValueError,match='closed remaining'):
            with N.context(row):raise AssertionError('must not enter')


@pytest.fixture
def prefix(tmp_path):
    from scripts.fleet import advance_authoring_v8 as D
    from types import SimpleNamespace
    root=tmp_path/'operations';root.mkdir();descriptor=save(tmp_path/'descriptor.json',{})
    def lease(rev):return dict(owner='original-owner',token='original-token',revision=rev,sha256=str(rev)*64)
    d=root/'000';d.mkdir();research=dict(status='STEP_COMPLETE',step='research',descriptor=descriptor,lease=lease(6))
    first=save(d/'result.json',research);prior=first
    for ordinal,status in [(1,'PENDING_MODEL'),(2,'UNRESOLVED')]:
        d=root/('%03d'%ordinal);d.mkdir()
        intent=save(d/'intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=prior)))
        (d/'step.log').write_text('')
        resource=save(d/'resource.json',dict(returncode=0,failure=None,intent=intent,log=N.binding(d/'step.log')))
        prior=save(d/'result.json',dict(status=status,step='normalization_phase1',descriptor=descriptor,lease=lease(6+ordinal)))
        save(d/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=prior))
    # Use the original guard-result validator; production additionally passes
    # the original setup environment to validate its code and typed controls.
    dispatcher=SimpleNamespace(successful=lambda row,env:D.successful(row))
    base=SimpleNamespace(decode=lambda value:SimpleNamespace(**value))
    return root,dispatcher,(base,),dict(operations=str(root),maximum_steps=100),dict(research=first,checkpoint=prior),research


def test_real_guard_chain_retains_partition_lease_advancement(prefix):
    root,D,env,job,o,research=prefix
    lease=N.ordinary_partition_prefix(D,env,job,o,research)
    assert (lease.owner,lease.token,lease.revision)==('original-owner','original-token',8)


@pytest.mark.parametrize('change',['owner','token','revision','stage','passed-stage','foreign-descriptor','wrong-prior','failed-guard','bound'])
def test_original_partition_guard_chain_rejects_changed_authority(prefix,change):
    root,D,env,job,o,research=prefix;d=root/'002';result=N.read(o['checkpoint'])
    if change in {'owner','token'}:result['lease'][change]='foreign'
    elif change=='revision':result['lease']['revision']=5
    elif change=='stage':result['step']='source_profile'
    elif change=='passed-stage':result['status']='STEP_COMPLETE'
    elif change=='foreign-descriptor':result['descriptor']={}
    elif change=='bound':job['maximum_steps']=1
    elif change=='wrong-prior':
        intent=N.read(N.binding(d/'intent.json'));intent['arguments']['checkpoint']=o['research'];save(d/'intent.json',intent)
    else:
        r=N.read(N.binding(d/'resource.json'));r['returncode']=1;save(d/'resource.json',r)
    if change not in {'bound','wrong-prior','failed-guard'}:
        o['checkpoint']=save(d/'result.json',result);value=N.read(N.binding(d/'outcome.json'));value['result']=o['checkpoint'];save(d/'outcome.json',value)
    with pytest.raises(ValueError):N.ordinary_partition_prefix(D,env,job,o,research)


def test_canonical_takeover_config_preserves_all_custody_fields(tmp_path):
 import json
 from scripts.fleet import normalization_remaining as N
 def save(p,v):p.write_text(json.dumps(v));return N.binding(p)
 oldintake=save(tmp_path/'old-intake.json',{'retained':'original'})
 newintake=save(tmp_path/'new-intake.json',{'retained':'actual admitted selection'})
 old=dict(run_root=str(tmp_path),code={'exact':'code'},controls={'exact':'slot'},workbook='0496',intake=oldintake)
 canonical=save(tmp_path/'config.json',old);current=save(tmp_path/'current.json',dict(old,intake=newintake));job=dict(config=current);tp=dict(selection=dict(config=canonical))
 N.ordinary_takeover_config(job,tp)
 for key in ('controls','code','run_root','workbook'):
  bad=save(tmp_path/('bad-'+key+'.json'),dict(old,**{key:'foreign'}))
  with pytest.raises(ValueError):N.ordinary_takeover_config(dict(config=bad),tp)
 foreign=save(tmp_path/'noncanonical.json',old)
 with pytest.raises(ValueError):N.ordinary_takeover_config(job,dict(selection=dict(config=foreign)))


def test_ordinary_journal_and_original_dispatcher_have_distinct_namespaces(tmp_path):
 from scripts.fleet import normalization_remaining as N
 config={'run_root':str(tmp_path)};base=tmp_path/'dispatcher-v2';base.mkdir();home=base/'remaining';home.mkdir()
 selected=N.dispatcher_directory(config,home)
 assert selected==base/'remaining-authoring'and not selected.exists()
 selected.mkdir()
 with pytest.raises(ValueError):N.dispatcher_directory(config,home)
 outside=tmp_path/'standalone-remaining';assert N.dispatcher_directory(config,outside)==base/'standalone-remaining'
