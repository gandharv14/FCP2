import copy
from pathlib import Path
from types import SimpleNamespace as NS
from contextlib import nullcontext
import pytest
from scripts.fleet import retained_warning_continue as M


def case(tmp_path):
    def put(name,value):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);return M.keep(p,value)
    oldhelper=tmp_path/'old.py';oldhelper.write_text('# original\n')
    home=tmp_path/'old';home.mkdir()
    handoff=put('old/handoff/result.json',{'status':'RETRY_READY'})
    intent=put('old/step-00/intent.json',dict(models=False,action='step',arguments={'checkpoint':handoff}))
    log=home/'step-00/step.log';log.write_text('scripts.batch.harbor_admission.AdmissionError: ordinary warning lacks authenticated renewal history\n')
    guard=put('old/step-00/resource.json',dict(returncode=1,failure=None,intent=intent,log=M.binding(log)))
    failed=put('old/step-00/outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=guard))
    for name in ('preparation.json','live-renewal/complete.json','renew-resource.json'):put('old/'+name,{'exact':name})
    old=dict(helper=M.binding(oldhelper),output=str(home),owner='old-owner',fence=2,live_renewal={'plan':{}},source_sha256='source',snapshot={})
    prior=put('prior.json',old)
    selected={**old,'helper':M.binding(M.__file__),'output':str(tmp_path/'new'),'owner':'new-owner','fence':3,
              'resume':dict(prior_plan=prior,failed_outcome=failed,runtime={},runtime_profile={})}
    return selected,old,put


def test_retains_actual_completed_renewal_and_warning(tmp_path):
    p,old,_=case(tmp_path)
    assert M.live_resume(p)==old

@pytest.mark.parametrize('change',['code','source','renewal','fence','output','nested','missing_permit','different_failure','unknown_guard','model'])
def test_live_resume_rejects_foreign_or_uncertain_state(tmp_path,change):
    p,old,put=case(tmp_path)
    if change=='code':p['snapshot']={'other':1}
    elif change=='source':p['source_sha256']='other'
    elif change=='renewal':p['live_renewal']={'plan':{'other':1}}
    elif change=='fence':p['fence']=2
    elif change=='output':p['output']=old['output']
    elif change=='nested':
        old['resume']={};p['resume']['prior_plan']=put('nested.json',old)
    elif change=='missing_permit':(Path(old['output'])/'preparation.json').unlink()
    else:
        outcome=M.read(p['resume']['failed_outcome']);guard=M.read(outcome['resource'])
        if change=='different_failure':
            log=tmp_path/'other.log';log.write_text('different error\n');guard['log']=M.binding(log)
        elif change=='unknown_guard':guard['failure']='TimeoutExpired'
        elif change=='model':
            intent=M.read(guard['intent']);intent['models']=True;guard['intent']=put('changed-intent.json',intent)
        outcome['resource']=put('changed-guard.json',guard)
        # Rebind the actual fixed outcome path to expose the specific changed evidence.
        out=Path(p['resume']['failed_outcome']['path']);out.unlink();p['resume']['failed_outcome']=M.keep(out,outcome)
    with pytest.raises(ValueError):M.live_resume(p)


def test_reader_change_is_exact_full_module_ast(tmp_path,monkeypatch):
    old=tmp_path/'original.py'
    old.write_text("def historical_lease(worker,before):\n    if True:\n        proof=first.validate_history(worker,before,p)\n        return proof['renewal_history']\ndef owning():\n    return 'unchanged'\n")
    selected=tmp_path/'selected.py'
    extra="        if worker.lease_renewal is None:\n            require('lease_renewal' not in proof and 'renewal_history' not in proof,\n                    'ordinary warning invented renewal history')\n            return proof\n"
    selected.write_text(old.read_text().replace('        proof=first.validate_history(worker,before,p)\n','        proof=first.validate_history(worker,before,p)\n'+extra))
    def profile():
        p=dict(entrypoint=M.binding(M.__file__),runtime={'selected':'runtime'},patches=[],dependencies=[],guard_modules=[],child_plans=[],journal_roots=[],symbols=[dict(module_name='original',original=M.binding(old),selected=M.binding(selected),names=['historical_lease'])])
        f=tmp_path/('profile-%d.json'%len(list(tmp_path.glob('profile-*'))));return M.keep(f,p)
    entered=[]
    monkeypatch.setattr(M,'module',lambda *a:NS(installed=lambda row:nullcontext()))
    p=dict(helper=M.binding(M.__file__),resume=dict(runtime={'selected':'runtime'},runtime_profile=profile()))
    manager=M.live_resume_runtime(p,NS(__name__='original',__file__=str(old)));manager.__exit__(None,None,None)
    selected.write_text(selected.read_text().replace("return 'unchanged'","return 'altered'"));p['resume']['runtime_profile']=profile()
    with pytest.raises(ValueError,match='only authenticated'):M.live_resume_runtime(p,NS(__name__='original',__file__=str(old)))


def test_run_reuses_completed_authority_without_renewal_or_Q(tmp_path,monkeypatch):
    old=tmp_path/'old';old.mkdir();new=tmp_path/'new'
    def put(p,v):p.parent.mkdir(parents=True,exist_ok=True);return M.keep(p,v)
    prior=put(tmp_path/'prior.json',dict(output=str(old)))
    put(old/'preparation.json',{'completed':True});put(old/'live-renewal/complete.json',{'schema_version':'fleet-validated-continuation-lease/v1','completed':True})
    selected=dict(schema_version=M.DIRECT_SCHEMA,output=str(new),resume=dict(prior_plan=prior,failed_outcome={},runtime_profile={}),
        live_renewal={},completion=None,old_result={},snapshot={},takeover_plan=None,config={},workbook='test')
    profile=dict(context={'path':'/original/permit','sha256':'p'},takeover=None)
    selection=NS(content=NS(dialogue_scope_receipt=None));w=NS();lease=NS()
    checkpoint=put(tmp_path/'handoff-template.json',dict(next_step='dialogue-review',descriptor={}))
    calls=[]
    C=NS();I=NS(_prior=lambda *a:None,run=lambda *a,**k:calls.append(a)or{'phase':len(calls)-1})
    def successful(outcome,env):
        index=outcome['phase'];value={'status':'PUBLICATION_READY' if index==3 else 'STEP_COMPLETE',
           'next_step':None if index==3 else M.PHASES[index+1],'descriptor':{}}
        return {},value
    D=NS(fleet_slot=lambda *a:nullcontext([]),successful=successful,publication_request=lambda *a:None)
    env=(C,I,None,NS(),None,{},tmp_path,None)
    prepmod=NS(handoff=lambda *a:checkpoint)
    monkeypatch.setattr(M,'context',lambda row:(selected,prepmod,{},D,env,w,selection,lease,None,profile,nullcontext()))
    monkeypatch.setattr(M,'active',lambda *a:None)
    monkeypatch.setattr(M,'preparation_active',lambda *a:(None,{'leases':{'task_agent':{'expires_at':M.time.time_ns()+7200*10**9}}},None))
    monkeypatch.setattr(M,'create_output',lambda path,root:Path(path).mkdir()or Path(path))
    monkeypatch.setattr(M,'install_child_bootstrap',lambda *a:None)
    def forbidden(*a,**k):raise AssertionError('completed authority replayed')
    monkeypatch.setattr(M,'live_renew_guard',forbidden);monkeypatch.setattr(M,'prepare_direct',forbidden)
    result=M.read(M.run({}))
    assert result['status']=='PUBLICATION_READY' and len(calls)==4
    assert M.read(M.binding(new/'preparation.json'))['permit']==profile['context']
    assert not (new/'live-renewal').exists()


def test_takeover_routing_resume_preserves_completed_authority(tmp_path):
    from scripts.fleet import retained_takeover_history as H
    p,old,put=case(tmp_path)
    current=Path(H.__file__).read_text()
    tree=__import__('ast').parse(current)
    route=next(n for n in __import__('ast').walk(tree)if isinstance(n,__import__('ast').FunctionDef)and n.name=='route')
    clause=route.body[1];lines=current.splitlines(keepends=True)
    original=''.join(lines[:clause.lineno-1]+lines[clause.end_lineno:])
    original_file=tmp_path/'original-history.py';original_file.write_text(original)
    before=dict(schema_version='fleet-retained-takeover-history/v1',helper=M.binding(original_file),authority={'unchanged':'actual'})
    prior_history=dict(helper=M.binding(original_file),profile=put('history-before.json',before))
    next_history=dict(helper=M.binding(H.__file__),profile=put('history-after.json',dict(before,helper=M.binding(H.__file__))))
    old['takeover_history']=prior_history
    p['takeover_history']=next_history
    prior=put('prior-takeover.json',old)
    failed=M.read(p['resume']['failed_outcome']);guard=M.read(failed['resource'])
    log=Path(guard['log']['path']);log.write_text('ValueError: takeover history may validate only original historical worker\n')
    guard['log']=M.binding(log);failed['resource']=put('guard-takeover.json',guard)
    out=Path(p['resume']['failed_outcome']['path']);out.unlink();fb=M.keep(out,failed)
    p['resume']=dict(prior_plan=prior,failed_outcome=fb,takeover_history=next_history)
    assert M.live_resume(p)==old
    with M.live_resume_runtime(p,None):pass
    bad=tmp_path/'bad-history.py';bad.write_text(current.replace('selected==worker','selected.run==worker.run'))
    foreign=dict(helper=M.binding(bad),profile=put('foreign-history.json',dict(before,helper=M.binding(bad))))
    with pytest.raises(ValueError,match='only exact'):M.takeover_resume_delta(prior_history,foreign)
    changed=dict(next_history,profile=put('changed-authority.json',dict(before,helper=next_history['helper'],authority={'changed':True})))
    with pytest.raises(ValueError,match='authority changed'):M.takeover_resume_delta(prior_history,changed)
