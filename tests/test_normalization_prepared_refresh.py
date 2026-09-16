from pathlib import Path
from copy import deepcopy
import sys
import pytest
from scripts.fleet import normalization_remaining as N, normalization_prepared_refresh as P


@pytest.fixture
def selected(tmp_path,monkeypatch):
    i=iter(range(1000))
    def put(value,name=None):return N.keep(tmp_path/(name or str(next(i))) ,value)
    tracker=put({'revision':4});lease={'revision':4};source=put({'source':True});research=put({'step':'research'})
    original=tmp_path/'original.py';original.write_text('')
    selected=tmp_path/'refresh.py';selected.write_text('def prepare(*a): raise AssertionError("must not repeat")\ndef validate(*a):return None\ndef commit(*a):return None\ndef _history_checkpoint(*a):return None\ndef encode(x):return x\n')
    F=N.module('selected_refresh_fixture',N.binding(selected));import scripts.batch
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_environment_refresh',F);monkeypatch.setattr(scripts.batch,'harbor_environment_refresh',F,raising=False)
    helper=tmp_path/'prior.py';helper.write_text('def validate_single(*a):return None\n')
    home=tmp_path/'old';home.mkdir();refresh=home/'refresh';refresh.mkdir();prefix=refresh/'prefix';prefix.mkdir()
    old=dict(single_author=put({'research':research}),completed_emission=put({}),completed_helper=N.binding(helper),helper=N.binding(original),single_helper=N.binding(original),home=str(home),fence=3,owner='owner',source=source,workbook='0316',tracker=tracker)
    symbols=[dict(module_name='scripts.batch.harbor_review_context',original=N.binding(original),selected=N.binding(original),names=['normalization_review']),dict(module_name='scripts.batch.harbor_environment_refresh',original=N.binding(original),selected=N.binding(selected),names=['unchanged','prepare'])]
    profile=dict(symbols=symbols,dependencies=[],entrypoint=old['helper'],read_call={'args':[dict(home=str(home),single_helper=old['single_helper'])]})
    old['runtime_profile']=put(profile);previous=put(old)
    current=put({'content':'exact'},'old/current.json');legacy=put({'worker':'same'},'old/legacy.json');lr=put(lease,'old/lease.json')
    before=put(N.read(tracker),'old/refresh/prefix/before.json')
    value=dict(code=N.binding(selected),selection=N.read(current),prior=research,before=before,last='normalization_phase1')
    prepared=put(value,'old/refresh/prefix/prepared.json')
    intent=put(dict(action='refresh',arguments=dict(legacy=legacy,checkpoint=research,current=current,lease=lr)))
    log=refresh/'step.log';log.write_text('scripts.batch.harbor_admission.AdmissionError: refresh owner/worker changed\n')
    guard=put(dict(returncode=1,failure=None,intent=intent,log=N.binding(log)),'old/refresh/resource.json')
    job=put(dict(driver=old['helper'],source=source,workbook='0316',version='original',inputs=[previous],command=['python','-I','-B',old['helper']['path'],'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]))
    wi=put(dict(owner='owner',claim_id='claim-id',job=job),'worker-intent.json');claim=put(dict(owner='owner',fence=3,claim_id='claim-id',workbook='0316',version='original',source_sha256=source['sha256']),'claim.json');outer=put(dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,automatic_retry=False,publication_performed=False,result=None,intent=wi))
    rows=[previous,prepared,claim,guard,intent,N.binding(log),outer,wi,job,current,legacy,lr,value['code'],before]
    stop=put(dict(at=1,claims=0,models=0,processes=[],files=[dict(native=r,copy=r)for r in rows]))
    q=dict(schema_version=P.SCHEMA,previous=previous,prepared=prepared,refresh_guard=guard,outer=outer,claim=claim,stopped=stop)
    p=dict(old,home=str(tmp_path/'new'),fence=4,prepared_refresh=put(q),prepared_helper=N.binding(P.__file__))
    future=deepcopy(profile);future.update(symbols=symbols[:1],dependencies=[{k:v for k,v in symbols[1].items()if k!='names'}]);future['read_call']['args'][0]['home']=p['home'];p['runtime_profile']=put(future)
    return p,q,put,F,lease


def test_actual_prepared_receipt_passes_coherent_identity_without_repeat(selected):
    p,q,put,F,lease=selected
    assert P.validate(N,p,None,None,None,lease)[0]==q


@pytest.mark.parametrize('field,value',[('fence',5),('owner','foreign'),('source',{'path':'/foreign','sha256':'0'*64}),('home','/foreign/namespace')])
def test_prepared_refresh_rejects_changed_authority(selected,field,value):
    p,q,put,F,lease=selected;p[field]=value
    with pytest.raises(ValueError):P.validate(N,p,None,None,None,lease)


def test_prepared_refresh_rejects_cas_already_completed(selected):
    p,q,put,F,lease=selected
    Path(q['prepared']['path']).with_name('committed.json').write_text('{}')
    with pytest.raises(ValueError,match='pre-CAS'):P.validate(N,p,None,None,None,lease)


def test_prepared_refresh_rejects_partial_module_identity(selected,monkeypatch):
    p,q,put,F,lease=selected
    namespace={};exec('def validate(*a):pass',namespace);monkeypatch.setattr(F,'validate',namespace['validate'])
    with pytest.raises(ValueError,match='selected identity'):P.validate(N,p,None,None,None,lease)


def test_retained_prepare_uses_existing_receipt_and_restores_original(selected,monkeypatch):
    from types import SimpleNamespace
    p,q,put,F,lease=selected;original=F.prepare;F.W=SimpleNamespace(_active=lambda *a:(None,{}))
    from scripts import harbor_campaign as C
    monkeypatch.setattr(C,'encode',lambda v:v)
    with P.installed(N,p,None,None,'worker',lease,initial=True):
        assert F.prepare('worker',lease,N.read(q['prepared'])['prior'],{'content':'exact'},'normalization_phase1',Path(p['home'])/'refresh/prefix')==q['prepared']
        with pytest.raises(ValueError):F.prepare('worker',lease,N.read(q['prepared'])['prior'],{'content':'wrong'},'normalization_phase1',Path(p['home'])/'refresh/prefix')
    assert F.prepare is original


def test_committed_history_requires_exact_cas_and_lease(selected):
    from scripts import harbor_campaign as C
    p,q,put,F,_=selected;value=N.read(q['prepared']);before=N.read(value['before'])
    initial=C.encode(C.bootstrap.Lease(owner='owner',token='token',revision=before['revision'],sha256='a'*64))
    current=C.encode(C.bootstrap.Lease(owner='owner',token='token',revision=before['revision']+1,sha256='b'*64))
    Path(p['home'],'refresh').mkdir(parents=True)
    receipt=dict(prepared=q['prepared'],revision=before['revision']+1,tracker_sha256='b'*64)
    committed=put(receipt,'old/refresh/prefix/committed.json')
    descriptor=put(dict(schema_version='harbor-environment-coordinator-refresh/v1',refresh=q['prepared'],initial_lease=current))
    cp=dict(status='IMPORTED',publication_ready=False,selection=value['selection'],step=value['last'],lease=current,descriptor=descriptor,evidence=[q['prepared'],committed])
    checkpoint=put(cp,'new/refresh/result.json');row=dict(checkpoint=checkpoint,committed=committed)
    P.validate_committed(N,p,q,value,initial,row)
    for key,change in [('status','STEP_COMPLETE'),('selection',{'changed':True}),('evidence',[]),('lease',C.encode(C.bootstrap.Lease(owner='foreign',token='token',revision=5,sha256='b'*64)))]:
        Path(checkpoint['path']).unlink();changed=put(dict(cp,**{key:change}),'new/refresh/result.json')
        with pytest.raises(ValueError):P.validate_committed(N,p,q,value,initial,dict(row,checkpoint=changed))
    Path(checkpoint['path']).unlink();checkpoint=put(cp,'new/refresh/result.json')
    Path(committed['path']).unlink();bad=put(dict(receipt,revision=999),'old/refresh/prefix/committed.json')
    with pytest.raises(ValueError):P.validate_committed(N,p,q,value,initial,dict(checkpoint=checkpoint,committed=bad))
