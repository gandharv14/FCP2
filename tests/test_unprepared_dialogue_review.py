from pathlib import Path
from types import SimpleNamespace as NS
import json
import pytest
from scripts.fleet import unprepared_dialogue_review as R


def require(ok,message):
    if not ok:raise ValueError(message)


@pytest.fixture
def scope(tmp_path):
    root=tmp_path/'producer';step=tmp_path/'steps/dialogue-review/semantic-round-02';step.mkdir(parents=True)
    first=NS(prepared=NS(scope=('book','sha','dialogue','dialogue-review'),ordinal=1,limit=2))
    task=NS(run=root);worker=NS(campaign=NS(root=tmp_path/'models'))
    E=NS(SemanticScope=lambda *a:a,location=lambda *a:tmp_path/'models/scope/attempt-01')
    P=NS(DIALOGUE='dialogue',task_context=lambda t:({},dict(workbook='book',original={'sha256':'sha'})))
    W=NS(task=lambda *a:task,request_saved=lambda p:first,step_directory=lambda *a:step,producer=P,executor=E)
    U=NS(require=require)
    return U,W,worker,first,root


def test_only_unprepared_second_review_can_continue(scope):
    U,W,worker,first,root=scope
    assert R.absent(U,W,worker,None,None).run==root


@pytest.mark.parametrize('path',['producer/requests/dialogue-review/attempt-02','producer/dialogue/review-call-02.json','models/scope/attempt-02'])
@pytest.mark.parametrize('kind',['directory','file','symlink'])
def test_any_prepared_or_uncertain_second_review_rejects(scope,tmp_path,path,kind):
    U,W,worker,first,root=scope;p=tmp_path/path;p.parent.mkdir(parents=True,exist_ok=True)
    if kind=='directory':p.mkdir()
    elif kind=='file':p.write_text('{}')
    else:p.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError,match='prepared or submitted'):R.absent(U,W,worker,None,None)


@pytest.mark.parametrize('field,value',[('scope',('foreign','sha','dialogue','dialogue-review')),('ordinal',2),('limit',3)])
def test_original_review_scope_and_budget_must_be_exact(scope,field,value):
    U,W,worker,first,root=scope;setattr(first.prepared,field,value)
    with pytest.raises(ValueError,match='logical scope'):R.absent(U,W,worker,None,None)


@pytest.fixture
def sealed(tmp_path):
    from scripts.fleet import instruction_semantic_continue as U
    from dataclasses import dataclass
    @dataclass
    class Lease:
        owner:str='owner'
    old={'output':str(tmp_path/'old'),'helper':{'path':'helper','sha256':'a'}}
    directory=tmp_path/'run/dispatcher-v2/old/001';directory.mkdir(parents=True)
    step=tmp_path/'step';step.mkdir();worker=NS(run=tmp_path/'worker');worker.run.mkdir();(worker.run/'initial.json').write_text('{}')
    i=tmp_path/'I.py';i.write_text('I');c=tmp_path/'C.py';c.write_text('C')
    C=NS(__file__=str(c),encode=lambda x:x,decode=lambda x:Lease())
    I=NS(__file__=str(i),SCHEMA='instruction')
    W=NS(step_directory=lambda *a:step)
    checkpoint={'path':'checkpoint','sha256':'b'};value=dict(descriptor={'path':'descriptor','sha256':'c'},checkpoint={'path':'previous','sha256':'d'},selection={},lease={})
    p=dict(semantic_context=dict(plan={'path':'plan','sha256':'e'}))
    def save(path,v):path.write_text(json.dumps(v));return U.binding(path)
    controls={};intent=save(directory/'intent.json',dict(schema_version=I.SCHEMA,action='step',arguments=dict(checkpoint=checkpoint,descriptor=value['descriptor']),code=U.binding(i),base_code=U.binding(c),controls=controls,memory_limit_bytes=8589934592,models=True,commands=True,containers=True))
    (directory/'step.log').write_text('in dialogue_review_context\noperands.project\nscripts.batch.harbor_admission.AdmissionError: source method copied formula changed\n')
    resource=save(directory/'resource.json',dict(returncode=1,failure=None,intent=intent,log=U.binding(directory/'step.log')))
    failed=save(directory/'outcome.json',dict(status='RECONCILIATION_REQUIRED',result=None,automatic_retry=False,resource=resource));p['failed_guard']=failed
    import sys
    save(directory/'semantic-bootstrap.json',dict(schema_version='fleet-retained-semantic-child/v1',plan=p['semantic_context']['plan'],helper=old['helper'],kind='instruction',intent=intent,original_command=[sys.executable,'-B',str(i),'_child',intent['path'],intent['sha256']],seconds=3300,memory_bytes=8589934592,automatic_retry=False))
    save(directory/'dependency.json',{})
    save(step/'intent.json',dict(step='dialogue-review',previous=value['checkpoint'],selection=value['selection'],lease={'owner':'owner'},initial=U.binding(worker.run/'initial.json')))
    import hashlib
    save(step/'exception.json',dict(type='AdmissionError',message_sha256=hashlib.sha256(b'source method copied formula changed').hexdigest(),automatic_replay=False))
    utility=NS(read=U.read,binding=U.binding,require=U.require,retained_seconds=lambda *a:3300)
    env=C,I,None,W,None,dict(run_root=str(tmp_path/'run')),tmp_path,controls
    return utility,p,old,env,checkpoint,value,worker,directory,step,save


def test_exact_original_preparation_stop_is_retained(sealed):
    U,p,old,env,checkpoint,value,worker,directory,step,save=sealed
    assert R.stopped(U,p,old,env,checkpoint,value,worker)==step


@pytest.mark.parametrize('change',['request','complete','pending','foreign_exception','uncertain_guard','guard_extra','changed_predecessor','wrong_step','foreign_bootstrap'])
def test_sealed_review_cannot_hide_effects_or_change_its_predecessor(sealed,change):
    U,p,old,env,checkpoint,value,worker,directory,step,save=sealed
    if change in ['request','complete','pending']:save(step/(change+'.json'),{})
    elif change=='foreign_exception':save(step/'exception.json',{})
    elif change=='guard_extra':save(directory/'submitted.json',{})
    elif change=='uncertain_guard':
        resource=U.read(U.binding(directory/'resource.json'));resource['returncode']=None
        bound=save(directory/'resource.json',resource);v=U.read(p['failed_guard']);v['resource']=bound;p['failed_guard']=save(directory/'outcome.json',v)
    elif change in ['changed_predecessor','wrong_step']:
        v=U.read(U.binding(step/'intent.json'));v['previous' if change=='changed_predecessor'else'step']='foreign';save(step/'intent.json',v)
    else:save(directory/'semantic-bootstrap.json',{})
    with pytest.raises(ValueError):R.stopped(U,p,old,env,checkpoint,value,worker)
