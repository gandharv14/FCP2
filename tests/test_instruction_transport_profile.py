"""Closed instruction applicability; all transports use synthetic fixtures elsewhere."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from scripts import harbor_instruction_code_applicability as A
from scripts.batch import harbor_instruction_producer as P, harbor_model_executor as E
from scripts.batch.harbor_admission import record


def request():
    return NS(prepared=NS(scope=NS(lane='harbor-dialogue-author', budget_id='dialogue-writer'),
                          ordinal=2, limit=2, response_mode='json_object', attempt_id='unchanged-id'))


def current():
    return [record(P.__file__), record(E.__file__), *[record(p) for p in A.paths()]]


@pytest.mark.parametrize('project', ['profile', 'protocol_profile'])
def test_exact_two_historical_profiles_preserve_context_and_request(project):
    now=current(); req=request(); context=dict(kind='dialogue-writer',code=getattr(A,project)(now))
    before=deepcopy(context); identity=deepcopy(vars(req.prepared))
    A.validate(context,now,req)
    assert context==before and vars(req.prepared)==identity
    assert context['code'] != now


@pytest.mark.parametrize('field,value', [('ordinal',1),('limit',3),('response_mode','text')])
def test_no_scope_or_budget_broadening(field,value):
    now=current(); req=request();setattr(req.prepared,field,value)
    with pytest.raises(ValueError): A.validate(dict(kind='dialogue-writer',code=A.profile(now)),now,req)


@pytest.mark.parametrize('fault',['lane','budget','kind','structural','writer','unknown_code','missing','duplicate','changed_binding'])
def test_foreign_and_ambiguous_context_rejected(fault):
    now=current();req=request();context=dict(kind='dialogue-writer',code=A.protocol_profile(now))
    if fault=='lane':req.prepared.scope.lane='harbor-normalization-review'
    elif fault=='budget':req.prepared.scope.budget_id='dialogue-paraphraser'
    elif fault=='kind':context['kind']='dialogue-review'
    elif fault=='structural':context['structural_round']=2
    elif fault=='writer':context['writer_round']=2
    elif fault=='unknown_code':context['code'][0]['sha256']='f'*64
    elif fault=='missing':now.pop(0)
    elif fault=='duplicate':now.insert(0,dict(now[0]))
    else:now[0]['sha256']='f'*64
    with pytest.raises(ValueError): A.validate(context,now,req)


@pytest.mark.parametrize('index',[1,2,3,4])
def test_every_original_archive_is_hash_bound(index,monkeypatch,tmp_path):
    original=Path.read_bytes
    target=A.paths()[index]
    # record uses open: point the archive path to real changed bytes, not a read spy.
    mirror=tmp_path/'helper.py';mirror.write_text('')
    import shutil
    shutil.copytree(Path(A.__file__).parent/'harbor_instruction_code_v1',tmp_path/'harbor_instruction_code_v1')
    shutil.copytree(Path(A.__file__).parent/'harbor_instruction_code_transport_v2',tmp_path/'harbor_instruction_code_transport_v2')
    changed=tmp_path/target.parent.name/target.name;changed.write_bytes(original(changed)+b'\n')
    monkeypatch.setattr(A,'__file__',str(mirror))
    with pytest.raises(ValueError,match='archive changed'):A.paths()


@pytest.mark.parametrize('module',[P,E])
def test_unreviewed_live_delta_rejected(module,monkeypatch,tmp_path):
    now=current();path=tmp_path/'unreviewed.py';path.write_bytes(Path(module.__file__).read_bytes()+b'\n')
    monkeypatch.setattr(module,'__file__',str(path))
    with pytest.raises(ValueError,match='unreviewed'):A.profile(now)


def test_transport_reachable_but_plan_inspect_parser_and_protocol_unchanged():
    old=ast.parse(A.paths()[3].read_bytes());new=ast.parse(Path(E.__file__).read_bytes())
    def functions(tree):return {n.name:ast.dump(n,include_attributes=False) for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    before=functions(old);after=functions(new)
    assert all(after[k]==v for k,v in before.items() if k!='execute')
    assert set(after)-set(before)=={'transient_open_failure'}
    text=Path(E.__file__).read_text();prior=A.paths()[3].read_text()
    for start,end in [('        if root.exists():','        previous = []'),('        previous = []','        root.mkdir('), ("                    retain(root/'response.raw.json', raw)",'        except BaseException')]:
        assert text[text.index(start):text.index(end,text.index(start))]==prior[prior.index(start):prior.index(end,prior.index(start))]
    assert A.policy_evidence()==dict(prospective_transport_branch_reachable=True,json_dialogue_protocol_changed=False,sealed_journal_replay=False,request_identity_changed=False,semantic_budget_reset=False,max_transport_retries=3,historical_code_executed=False)

from test_dialogue_executor_protocol import pair


def test_retained_round_two_prospective_retry_and_sealed_reuse_keep_identity(pair,monkeypatch):
    from dataclasses import asdict
    c,make,calls=pair
    first=make();assert E.execute(first,c,credential='fixture-credential').status=='completed'
    second=make(ordinal=2,mode='json_object');req=NS(prepared=second)
    now=current();context=dict(kind='dialogue-writer',code=A.protocol_profile(now))
    before=deepcopy(context);identity=asdict(second);raw=E.checked_bytes(second.request)
    original=E._open;opens=[]
    def transient_then_complete(*args):
        opens.append(1)
        if len(opens)==1:raise TimeoutError('synthetic typed opening failure')
        return original(*args)
    monkeypatch.setattr(E,'_open',transient_then_complete);monkeypatch.setattr(E.time,'sleep',lambda _:None)
    A.validate(context,now,req)
    assert E.execute(second,c,credential='fixture-credential').status=='completed'
    root=E.location(second,c);sealed={p.name:p.read_bytes() for p in root.iterdir()}
    assert len(opens)==2 and len(calls)==2
    assert E.execute(second,c,credential='fixture-credential').reused and len(opens)==2
    assert sealed=={p.name:p.read_bytes() for p in root.iterdir()}
    assert identity==asdict(second) and raw==E.checked_bytes(second.request) and context==before
    assert not root.with_name('attempt-03').exists()

from test_harbor_instruction_producer import setup
from test_instruction_pretransport_recovery import ready


def test_full_owning_request_validator_accepts_exact_c5_context_without_rewrite(ready):
    from dataclasses import replace, asdict
    from scripts.batch import harbor_instruction_worker as W
    w,s,lease,exception,review,task,campaign,respond,calls=ready
    req=W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json')
    current_task=W.task(w,s,record(task.tracker['path']))
    context=P.read(req.context);context['code']=A.protocol_profile(P.code_evidence(current_task))
    retained=P.put(w.run/'retained-c5-context.json',context)
    req=replace(req,context=retained)
    before=asdict(req);raw=E.checked_bytes(retained);spent=len(calls)
    P.validate_request(current_task,req,'dialogue-writer')
    assert asdict(req)==before and E.checked_bytes(retained)==raw and len(calls)==spent
