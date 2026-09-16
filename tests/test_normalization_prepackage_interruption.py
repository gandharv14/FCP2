import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import normalization_imported_continue as N
from scripts.fleet import normalization_prepackage_interruption as I

@pytest.fixture
def case(tmp_path,monkeypatch):
    def put(name,value):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return N.binding(p)
    home=tmp_path/'old';ops=tmp_path/'old-authoring';source=put('source.json',{'raw':'source'})
    descriptor=put('descriptor.json',{'scope':'original'});last=put('phase2.json',dict(descriptor=descriptor));done=put('done.json',dict(result=last));remaining=put('remaining.json',dict(completed=done))
    helper=put('old-helper.py',{'code':'original'});old=dict(schema_version=N.SCHEMA2,helper=helper,home=str(home),fence=7,owner='original',workbook='0316',source=source,remaining=remaining,checkpoint=last,takeover_completion=done)
    previous=put('previous.json',old);job=put('job.json',dict(driver=helper,inputs=[previous],workbook='0316',source=source,result=str(home/'result.json'),run=str(tmp_path/'worker'),version='code'))
    launch=put('launch.json',dict(remote=dict(job=job)))
    put(home/'intent.json',dict(plan=previous,imported=last,takeover=done,emission_repeated=False,refresh_repeated=False,automatic_retry=False))
    put(home/'authoring-job.json',{})
    put(ops/'job.json',{})
    put(ops/'000/intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=last,captures=None),dispatch=True))
    for name in ('declared-runtime-adapter.json','dependency.json'):put(ops/'000'/name,{})
    (ops/'000/step.log').write_bytes(b'')
    wi=put('worker/intent.json',dict(job=job,owner='original',claim_id='claim'))
    put('worker/claim.json',dict(owner='original',claim_id='claim',fence=7,version='code',source_sha256=source['sha256'],workbook='0316'))
    put('worker/outcome.json',dict(intent=wi,status='RECONCILIATION_REQUIRED',automatic_retry=False,returncode=None,result=None,publication_performed=False,failure_type='RuntimeError'))
    files=[Path(job['path']),*[p for root in(home,ops,tmp_path/'worker')for p in root.rglob('*')if p.is_file()]];copies=[]
    for i,p in enumerate(files):
        cp=tmp_path/'copies'/str(i);cp.parent.mkdir(exist_ok=True);cp.write_bytes(p.read_bytes());copies.append(dict(native=N.binding(p),copy=N.binding(cp)))
    wb=put('workbook.json',dict(state='hold'));e=put('evidence.json',dict(schema_version='fleet-stopped-author-no-operation-evidence/v2',workbook=wb,launch=launch,copies=copies))
    plan=put('release-plan.json',dict(arguments=dict(evidence=e,workbook='0316',owner='original',fence=7,quarantines={},claim_id='claim')))
    receipt=put('receipt.json',dict(after_reservation=dict(state='released',claim_id='claim')))
    release=put('released.json',dict(status='STOPPED_AUTHOR_RECONCILED',unknown_operations_released=False,plan=plan,receipt=receipt))
    proof=put('interruption.json',dict(schema_version=I.SCHEMA,previous=previous,release=release,evidence=e))
    q=dict(old,helper=N.binding(N.__file__),home=str(tmp_path/'new'),fence=8,interruption=proof,interruption_helper=N.binding(I.__file__),pair_cache={'selected':'bound separately'},docker_profile={'selected':'bound separately'},docker_helper={'selected':'bound separately'})
    called=[]
    def identity(actual,p):assert actual==old;called.append(True);return ('prepared','checkpoint','outer')
    monkeypatch.setattr(N,'module',lambda *a:SimpleNamespace(identity=identity))
    return SimpleNamespace(q=q,put=put,home=home,ops=ops,release=release,evidence=e,proof=proof,called=called)

def test_preserves_original_identity_and_all_prepackage_files(case):
    assert I.validate(N,case.q,{})==('prepared','checkpoint','outer')
    assert case.called==[True]

@pytest.mark.parametrize('change',['owner','fence','home','new-file','partial-log','changed-copy','unreleased','different-job'])
def test_changed_or_partial_effect_boundary_refused(case,change):
    q=dict(case.q)
    if change=='owner':q['owner']='foreign'
    elif change=='fence':q['fence']=10
    elif change=='home':q['home']=str(case.home)
    elif change=='new-file':(case.ops/'000/result.json').write_text('{}')
    elif change=='partial-log':(case.ops/'000/step.log').write_text('partial action')
    elif change=='changed-copy':Path(N.read(case.evidence)['copies'][0]['copy']['path']).write_text('changed')
    elif change=='unreleased':
        r=N.read(case.release);r['status']='NOT_STARTED';case.put(Path(case.release['path']),r)
    else:
        e=N.read(case.evidence);Path(e['copies'][0]['native']['path']).write_text('{}')
    with pytest.raises(ValueError):I.validate(N,q,{})

def test_exact_historical_clock_delta_refuses_other_changes(tmp_path):
    old=tmp_path/'old.py';new=tmp_path/'new.py'
    source="def takeover_context():\n    from types import FunctionType\n    selected=FunctionType(f.__code__,{**f.__globals__,'checked':historical},f.__name__,f.__defaults__)(p)\n    T.validate_completion(completion)\n"
    old.write_text(source)
    expected=source.replace('from types import FunctionType','from types import FunctionType,SimpleNamespace').replace("    selected=", "    observed=stop['observed_at']\n    require(type(observed)is int and observed>0,'completed takeover observation time required')\n    clock=SimpleNamespace(time_ns=lambda:observed)\n    selected=").replace("'checked':historical}","'checked':historical,'time':clock}")
    new.write_text(expected);I.historical_clock_delta(N,N.binding(old),N.binding(new))
    for changed in (expected.replace('lambda:observed','lambda:123'),expected.replace('T.validate_completion(completion)','pass')):
        new.write_text(changed)
        with pytest.raises(ValueError):I.historical_clock_delta(N,N.binding(old),N.binding(new))
