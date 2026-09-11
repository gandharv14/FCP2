"""A consumption exemption is valid only for the exact completed old authors."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

@pytest.fixture
def sample(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/consume_0061_review_readonly.py'
    spec=importlib.util.spec_from_file_location('reader0061',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    (old/'producer.py').write_text('old');(new/'producer.py').write_text('new')
    b=m.binding;oldcode=b(old/'producer.py');newcode=b(new/'producer.py')
    snap=tmp_path/'snapshot.json';snap.write_text(__import__('json').dumps(dict(root=str(old),files=[dict(relative='producer.py',sha256=oldcode['sha256'])])))
    def artifact(name):
        p=tmp_path/name;p.write_text(name);return b(p)
    payload=artifact('payload');budget=artifact('budget');pre=artifact('pre');semantic=artifact('semantic')
    call=NS(**{k:artifact(k) for k in ('request','submitted','response','completed','parsed')})
    from dataclasses import make_dataclass
    Call=make_dataclass('Call',list(call.__dict__));call=Call(**call.__dict__)
    scope=NS(workbook_id='0061',original_sha256='original',budget_id='dialogue-writer',lane='dialogue')
    prepared=NS(ordinal=2,limit=2,physical_repair=None,request=payload,scope=scope,response_mode='json_object',budget_evidence=(budget,))
    context=dict(schema_version='harbor-instruction-request/v1',kind='dialogue-writer',workbook='0061',original={'sha256':'original'},stage_tree='tree',request=payload,evidence=[pre,semantic],budget_evidence=[budget],ordinal=2,limit=2,response_mode='json_object',code=[oldcode])
    cb=artifact('context');request=NS(context=cb,prepared=prepared)
    original=m.checked
    monkeypatch.setattr(m,'checked',lambda x:context if x==cb else original(x))
    saved=tmp_path/'request.json';saved.write_text('saved')
    w=NS(root=new,campaign='campaign',dialogue_pre_review_repair=pre,dialogue_semantic_retry=semantic)
    task=NS(stage_tree='tree')
    P=NS(DIALOGUE='dialogue',task_context=lambda _:({},dict(workbook='0061',original={'sha256':'original'})),code_evidence=lambda _:[newcode],observed=lambda *a:call,_accepted_author=lambda *a,**kw:(call,{},'filled',{}))
    W=NS(executor=NS(checked_bytes=lambda x:Path(x['path']).read_bytes()),step_directory=lambda *a:tmp_path,request_saved=lambda _:request)
    permit=dict(old_snapshot=b(snap),history=[getattr(call,k) for k in ('request','submitted','response','completed','parsed')])
    def run():return m.historical_author(P,W,None,task,w,permit,request,'dialogue-writer',{},'',{},tmp_path)
    return NS(run=run,context=context,prepared=prepared,permit=permit,P=P,call=call)

def test_exact_completed_author_is_retained(sample):
    call,filled,proof=sample.run();assert call==sample.call and filled=='filled';assert proof['call']['completed']==sample.call.completed

@pytest.mark.parametrize('mutation',['ordinal','kind','stage','code','history','accepted','uncertain'])
def test_fresh_detached_or_changed_author_cannot_use_exemption(sample,mutation):
    if mutation=='ordinal':sample.prepared.ordinal=1
    elif mutation=='kind':sample.context['kind']='dialogue-review'
    elif mutation=='stage':sample.context['stage_tree']='other'
    elif mutation=='code':sample.context['code']=[]
    elif mutation=='history':sample.permit['history']=sample.permit['history'][:-1]
    elif mutation=='accepted':sample.P._accepted_author=lambda *a,**kw:(None,{},'filled',{})
    else:sample.P.observed=lambda *a:(_ for _ in ()).throw(ValueError('incomplete; no replay'))
    with pytest.raises((AssertionError,ValueError)):sample.run()
