"""A completed phase two cannot be replayed or replaced by another stopped job."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import normalization_imported_continue as N

@pytest.fixture
def case(tmp_path,monkeypatch):
    def put(path,value):
        path=tmp_path/path;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return N.binding(path)
    def code(path,text):
        p=tmp_path/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);return N.binding(p)
    old_helper=code('old.py','# original imported owner\n')
    old_history=code('old-history.py','# original history\n')
    history=code('new-history.py','# original history\ndef phase1_projection():\n pass\n')
    home=tmp_path/'dispatcher/old';ops=tmp_path/'dispatcher/old-authoring'
    source=put('source.json',{'source':'exact'});descriptor=put('descriptor.json',{'original':'refresh'})
    imported=put('imported.json',dict(descriptor=descriptor,status='IMPORTED'))
    tracker=put('tracker.json',{'revision':12});tp=put('takeover.json',dict(selection=dict(tracker=tracker)))
    final=put('normalized.json',{'phase':2});prepared=put('prepared.json',dict(mutable_witnesses=[dict(key='normalized_final',original=final,copy=final)]))
    old=dict(schema_version=N.SCHEMA,helper=old_helper,history_helper=old_history,home=str(home),fence=6,owner='same-owner',source=source,workbook='0316',checkpoint=imported,takeover_profile=tp)
    previous=put('previous.json',old);q=dict(old,schema_version=N.SCHEMA2,helper=N.binding(N.__file__),history_helper=history,home=str(tmp_path/'dispatcher/new'),fence=7)
    lease=dict(type='Lease',value={k:dict(type='scalar',value=v)for k,v in dict(owner='same-owner',token='token',revision=12,sha256=tracker['sha256']).items()})
    def success(root,prior,step):
        intent=put(root/'intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=prior,captures=None)))
        guard=put(root/'resource.json',dict(returncode=0,failure=None,intent=intent))
        result=put(root/'result.json',dict(status='STEP_COMPLETE',step=step,descriptor=descriptor,lease=lease))
        return put(root/'outcome.json',dict(status='COMPLETED_OPERATION',resource=guard,result=result)),result
    bridge,br=success(home/'bridge-step',imported,'source_profile')
    completed,last=success(ops/'000',br,'normalization_phase2')
    intent=put(ops/'001/intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=last,captures=None)))
    log=code(ops/'001/step.log',"normalization_projection\nF.validate(w,prepared,read(current))\nscripts.batch.harbor_tracker.Conflict: changed evidence: "+final['path']+'\n')
    guard=put(ops/'001/resource.json',dict(returncode=1,failure=None,intent=intent,log=log))
    failed=put(ops/'001/outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=guard))
    job=put('job.json',dict(driver=old_helper,inputs=[previous],source=source,workbook='0316',version='code',command=['python','-I','-B',old_helper['path'],'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]))
    wi=put('worker/intent.json',dict(job=job,owner='same-owner',claim_id='claim'))
    put('worker/claim.json',dict(owner='same-owner',fence=6,version='code',workbook='0316',source_sha256=source['sha256'],claim_id='claim'))
    outer=put('worker/outcome.json',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,publication_performed=False,intent=wi))
    rows=[]
    for i,f in enumerate(list(tmp_path.rglob('*.json'))+list(tmp_path.rglob('*.log'))):
        copy=tmp_path/'copies'/str(i);copy.parent.mkdir(exist_ok=True);copy.write_bytes(f.read_bytes());rows.append(dict(native=N.binding(f),copy=N.binding(copy)))
    stopped=put('stopped.json',dict(at=123,processes=[],files=rows))
    proof=put('remaining.json',dict(schema_version='fleet-imported-phase2-remaining/v1',previous=previous,outer=outer,stopped=stopped,completed=completed,failed=failed))
    q['remaining']=proof;calls=[]
    def identity(actual,p):
        assert actual==old;calls.append('original-identity');return prepared,dict(descriptor=descriptor),{'original':'outer'}
    monkeypatch.setattr(N,'module',lambda *a:SimpleNamespace(identity=identity))
    return SimpleNamespace(q=q,put=put,calls=calls,proof=proof,completed=completed,failed=failed,stopped=stopped,tracker=tracker,source=source)

def test_retains_original_prefix_and_allows_later_tracker_only(case):
    before=Path(case.source['path']).read_bytes();assert N.remaining_identity(case.q,{})[2]=={'original':'outer'}
    Path(case.tracker['path']).write_text('{"revision":13}')
    assert N.remaining_identity(case.q,{})[2]=={'original':'outer'}
    assert Path(case.source['path']).read_bytes()==before and len(case.calls)==2

@pytest.mark.parametrize('change',['owner','fence','source','namespace','phase','uncertain','live-process','missing-evidence','archive-tamper','foreign-mutable'])
def test_refuses_changed_boundary(case,change):
    q=dict(case.q);proof=N.read(case.proof)
    if change=='owner':q['owner']='another-owner'
    elif change=='fence':q['fence']=9
    elif change=='source':q['source']={'different':'source'}
    elif change=='namespace':q['home']=N.read(proof['previous'])['home']
    elif change in ('phase','uncertain'):
        row=case.completed if change=='phase'else case.failed;value=N.read(row)
        if change=='phase':
            result=N.read(value['result']);value['result']=case.put(Path(value['result']['path']),dict(result,step='research'))
        else:value['status']='UNKNOWN'
        proof['completed'if change=='phase'else'failed']=case.put(Path(row['path']),value)
        q['remaining']=case.put('remaining2.json',proof)
    elif change in ('live-process','missing-evidence'):
        stop=N.read(case.stopped)
        if change=='live-process':stop['processes']=[{'pid':123}]
        else:stop['files']=[]
        proof['stopped']=case.put('stopped2.json',stop);q['remaining']=case.put('remaining2.json',proof)
    elif change=='archive-tamper':Path(N.read(case.stopped)['files'][0]['copy']['path']).write_text('{}')
    else:Path(case.source['path']).write_text('{}')
    with pytest.raises((ValueError,KeyError)):N.remaining_identity(q,{})

@pytest.mark.parametrize('remaining,initial,expected',[(False,True,True),(True,True,False),(True,False,False)])
def test_completed_phase2_uses_historical_takeover_then_current_phase2_custody(monkeypatch,remaining,initial,expected):
    # The original takeover must remain fully validated, but its after snapshot is
    # historical once the retained phase-two gate has advanced the tracker.
    class Boundary(Exception):pass
    q=dict(previous='p',history_helper='h',environment_helper='e',takeover_runtime='r',takeover_profile='tp',takeover_completion='tc')
    if remaining:q['remaining']='exact completed phase2 certificate'
    monkeypatch.setattr(N,'read',lambda row:q if row=='q'else {'helper':'n'})
    monkeypatch.setattr(N,'identity',lambda q,p:('prepared','cp','outer'))
    observed=[]
    def takeover(runtime,profile,completion,*,current):
        observed.append((runtime,profile,completion,current));raise Boundary
    modules={'n':object(),'h':object(),'e':SimpleNamespace(takeover_context=takeover),'r':object()}
    monkeypatch.setattr(N,'module',lambda name,row:modules[row])
    with pytest.raises(Boundary):
        with N.context('q',initial=initial):pass
    assert observed==[(modules['r'],'tp','tc',expected)]
