import json
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from dataclasses import dataclass
import pytest
from scripts.fleet import environment_checkpoint_continue as E


@pytest.fixture
def boundary(tmp_path):
    def put(name,value):return E.keep(tmp_path/name,value)
    source=put('source.json',{'source':'retained'});policy=dict(source=source,phase1=put('phase1.json',{}),inventory=put('inventory.json',{}),captures=put('captures.json',{}),authorization=put('auth.json',{}))
    old={k:put('original-fields/'+k+'.json',{})for k in E.FIELDS};old.update(schema_version=E.SCHEMA,entry=E.binding(E.__file__),original_job=put('author-job.json',{'config':{'original':'config'}}),source=source,workbook='0215',home=str(tmp_path/'continuations/old'),operations=str(tmp_path/'operations/old'),owner='owner',profile_recovery=put('profile.json',policy));prior=put('previous.json',old)
    stop=put('stop.json',{'exact':'failure'});correction=put('correction.json',dict(policy,stop=stop));newpolicy=put('next-policy.json',dict(policy,producer_correction=correction))
    descriptor=put('descriptor.json',{'original':'refresh'});lease={'exact':'actual lease'}
    ri=put('continuations/old/refresh/intent.json',dict(action='refresh',arguments=dict(checkpoint=old['checkpoint'])))
    rr=put('continuations/old/refresh/result.json',dict(status='IMPORTED',step='normalization_phase1',descriptor=descriptor,lease=lease))
    rg=put('continuations/old/refresh/resource.json',dict(returncode=0,failure=None,intent=ri));ro=put('continuations/old/refresh/outcome.json',dict(status='COMPLETED_OPERATION',resource=rg,result=rr))
    fi=put('continuations/old/bridge/intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=rr,captures=policy['captures'])))
    fr=put('continuations/old/bridge/result.json',dict(status='UNRESOLVED',step='source_profile',descriptor=descriptor,lease=lease,evidence=[stop]))
    fg=put('continuations/old/bridge/resource.json',dict(returncode=0,failure=None,intent=fi));fo=put('continuations/old/bridge/outcome.json',dict(status='COMPLETED_OPERATION',resource=fg,result=fr))
    result=put('continuations/old/result.json',dict(status='UNRESOLVED'))
    job=put('job.json',dict(driver=old['entry'],inputs=[prior],source=source,workbook='0215',version='code',command=['python','-I','-B',old['entry']['path'],'run','--plan',prior['path'],'--plan-sha256',prior['sha256']]))
    wi=put('worker/intent.json',dict(job=job,owner='owner',claim_id='claim'));claim=put('worker/claim.json',dict(owner='owner',claim_id='claim',workbook='0215',source_sha256=source['sha256'],version='code',fence=3))
    outer=put('worker/outcome.json',dict(intent=wi,status='RECONCILIATION_REQUIRED',returncode=0,failure_type=None,automatic_retry=False,publication_performed=False,result=result))
    rows=[]
    for i,f in enumerate(list(tmp_path.rglob('*.json'))):
        copy=tmp_path/'copies'/str(i);copy.parent.mkdir(exist_ok=True);copy.write_bytes(f.read_bytes());rows.append(dict(native=E.binding(f),copy=E.binding(copy)))
    stopped=put('stopped.json',dict(processes=[],at=123,files=rows));proof=put('proof.json',dict(schema_version='fleet-profile-fragment-remaining/v1',previous=prior,outer=outer,refresh=ro,failed=fo,stopped=stopped))
    p=dict(old,profile_recovery=newpolicy,profile_remaining=proof,home=str(tmp_path/'continuations/new'),operations=str(tmp_path/'operations/new'))
    return SimpleNamespace(p=p,old=old,proof=proof,put=put,root=tmp_path,rr=rr,fo=fo,fr=fr,claim=claim,stopped=stopped)


def test_exact_failed_profile_preserves_completed_refresh(boundary):
    c=boundary;old,proof,cp,value=E.profile_remaining_identity(c.p)
    assert old==c.old and cp==c.rr and value['status']=='IMPORTED'


@pytest.mark.parametrize('change',['owner','source','home','lease','uncertainty','claim_version','live_process','missing_custody'])
def test_rejects_foreign_or_uncertain_profile_boundary(boundary,change):
    c=boundary;p=dict(c.p)
    if change in ('owner','source'):p[change]='foreign'
    elif change=='home':p['home']=c.old['home']
    else:
        target=c.fr if change=='lease'else c.fo if change=='uncertainty'else c.claim if change=='claim_version'else c.stopped
        v=E.checked(target)
        if change=='lease':v['lease']={'foreign':'lease'}
        elif change=='uncertainty':v['status']='UNKNOWN'
        elif change=='claim_version':v['version']='foreign'
        elif change=='live_process':v['processes']=[{'pid':1}]
        else:v['files']=[]
        Path(target['path']).write_text(json.dumps(v))
    with pytest.raises((ValueError,KeyError,TypeError)):E.profile_remaining_identity(p)


def test_owning_remaining_branch_never_invokes_completed_refresh(boundary,monkeypatch):
    c=boundary;p=c.p;calls=[]
    @dataclass
    class Worker:campaign: str='same-campaign'
    def run(action,args,*a,**kw):
        calls.append((action,args));assert action=='step'and args['checkpoint']==c.rr
        return c.fo
    C=SimpleNamespace(_campaign=lambda x:None,run=run)
    def successful(row,env):return E.checked(row)['result'],E.checked(E.checked(row)['result'])
    def dispatch(job):E.keep(Path(p['operations'])/'hold.json',{'outcome':c.fo})
    D=SimpleNamespace(fleet_slot=lambda *a:nullcontext(()),successful=successful,run=dispatch)
    monkeypatch.setattr(E,'active',lambda p:None)
    original={str(f):f.read_bytes()for f in c.root.rglob('*')if f.is_file()}
    result=E._run_selected({'path':'selected','sha256':'exact'},(p,D,(C,None,None,None,None,None,None,'originalguard'),Worker(),None,None,None,None))
    assert E.checked(result)['status']=='UNRESOLVED' and len(calls)==1
    assert not(Path(p['home'])/'refresh').exists() and not(Path(p['home'])/'legacy.json').exists()
    assert all(Path(f).read_bytes()==raw for f,raw in original.items())
