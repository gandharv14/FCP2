import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_package_completion as C
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_transition_codec import encode


def fixture(tmp):
    work=tmp/'packaging';work.mkdir();stage=tmp/'0123-outputs';stage.mkdir()
    entry=tmp/'xl_output_task.py';entry.write_text('# exact retained package command\n')
    content=Selection('0123',stage,'',{},tmp/'normalization',tmp/'naturalization',{}, {})
    taxonomy=tmp/'taxonomy.json';taxonomy.write_text('{}')
    tax=C.record(taxonomy);arguments=[entry,'0123','--require-cached-variable-source-audit','--no-naturalize']
    producer=SimpleNamespace(code_binding=lambda:C.record(entry),
        packaging_arguments=lambda *a:([],arguments),
        journal=SimpleNamespace(checked_bytes=lambda row:Path(row['path']).read_bytes()))
    def write(name,value):(work/name).write_text(json.dumps(value))
    write('selection.json',dict(schema_version='harbor-environment-production/v1',
        content=encode(content),profiles=encode(None),taxonomy=tax,code=C.record(entry),mode='plain'))
    (work/'package.log').write_text('completed original package\n')
    write('package.intent.json',dict(argv=[sys.executable,*map(str,arguments)],entry=C.record(entry),model_calls_permitted=False))
    write('package.result.json',dict(returncode=0,log=C.record(work/'package.log')))
    return producer,content,work,tax


def test_exact_command_receipt_never_executes_or_writes(tmp_path):
    p,c,w,t=fixture(tmp_path)
    before={x.name:x.read_bytes()for x in w.iterdir()}
    proof=C.command_receipt(p,c,None,w,t)
    assert set(proof)=={'selection','intent','result','log'}
    assert before=={x.name:x.read_bytes()for x in w.iterdir()}


@pytest.mark.parametrize('damage',['uncertain','nonzero','argv','entry','log','models','source','complete'])
def test_retained_package_requires_exact_successful_cached_only_command(tmp_path,damage):
    p,c,w,t=fixture(tmp_path)
    if damage=='uncertain':(w/'package.result.json').unlink()
    elif damage=='log':(w/'package.log').write_text('different log')
    elif damage=='complete':(w/'complete.json').write_text('{}')
    else:
        name='package.result.json'if damage=='nonzero'else 'selection.json'if damage=='source'else 'package.intent.json'
        v=json.loads((w/name).read_bytes())
        if damage=='nonzero':v['returncode']=1
        elif damage=='argv':v['argv'].remove('--require-cached-variable-source-audit')
        elif damage=='entry':v['entry']['sha256']='f'*64
        elif damage=='models':v['model_calls_permitted']=True
        else:v['content']['value']['workbook']['value']='0999'
        (w/name).write_text(json.dumps(v))
    before={x.name:x.read_bytes()for x in w.iterdir()}
    with pytest.raises((ValueError,FileNotFoundError)):C.command_receipt(p,c,None,w,t)
    assert before=={x.name:x.read_bytes()for x in w.iterdir()}


@pytest.fixture
def continued(tmp_path,monkeypatch):
    from dataclasses import replace
    from scripts import harbor_campaign as coordinator
    from scripts.batch import harbor_environment_refresh as F,harbor_environment_worker as W
    from scripts.batch.harbor_complete_readiness import ReadinessSelection
    p,c,w,t=fixture(tmp_path)
    s=SimpleNamespace(run=tmp_path,taxonomy=t,bootstrap=SimpleNamespace(workbook='0123'))
    selection=ReadinessSelection(c,None,None,None,None)
    folder=tmp_path/'refresh';folder.mkdir()
    prepared=F.keep(folder/'prepared.json',dict(last='normalization_phase2',results=[]))
    F.keep(folder/'committed.json',dict(prepared=prepared))
    descriptor=F.keep(tmp_path/'descriptor.json',dict(refresh=prepared))
    prior=F.keep(tmp_path/'prior.json',dict(status='IMPORTED',step='normalization_phase2',selection=encode(selection)))
    receipt=C.command_receipt(p,c,None,w,t)
    authority=F.keep(tmp_path/'authority.json',dict(schema_version='fleet-completed-package-selection/v1',
        helper=C.record(C.__file__),runner={},release={},original_release={},policy={},work=str(w),
        selection=receipt['selection'],command=receipt['intent'],result=receipt['result'],log=receipt['log'],failure={}))
    lease=object();before={'lane_state':{'harbor-normalization-engineer':{'phase':'phase2_complete'}}}
    monkeypatch.setattr(coordinator,'selected',lambda _: (s,lease,None))
    monkeypatch.setattr(coordinator,'_prior',lambda *args: (C.read(prior),lease))
    monkeypatch.setattr(W,'_active',lambda *args: (None,before))
    monkeypatch.setattr(F,'validate',lambda *args: (C.read(prepared),selection))
    monkeypatch.setattr(W,'environment',p)
    p.readiness=SimpleNamespace(publication=SimpleNamespace(_tree_hash=lambda stage:('f'*64,[])))
    monkeypatch.setattr(C,'validate',lambda *args: (replace(c,stage_sha256='f'*64),receipt))
    return coordinator,F,W,s,lease,descriptor,prior,authority,folder,selection


def test_completed_package_checkpoint_enters_original_refresh_gate_once(continued,monkeypatch):
    coordinator,F,W,s,lease,d,prior,authority,folder,selection=continued
    original={p.name:p.read_bytes()for p in (s.run/'packaging').iterdir()}
    row=C.checkpoint(coordinator,F,d,prior,authority=authority,directory=folder/'continuation')
    calls=[]
    def gate(worker,current,kind,selected):
        calls.append((worker,current,kind,selected))
        return {'status':'STEP_COMPLETE','lease':object()}
    monkeypatch.setattr(W,'_gate',gate)
    result=F.advance(s,lease,C.read(d)['refresh'],folder/'step')
    assert result['status']=='STEP_COMPLETE' and len(calls)==1 and calls[0][2]=='environment'
    assert calls[0][3].content.stage_sha256=='f'*64
    assert original=={p.name:p.read_bytes()for p in (s.run/'packaging').iterdir()}
    assert C.read(row)['evidence'][0]==authority
    with pytest.raises(ValueError,match='already attempted'):C.checkpoint(coordinator,F,d,prior,authority=authority,directory=folder/'continuation')


@pytest.mark.parametrize('defect',['foreign','receipt','prefix','validation'])
def test_package_recovery_rejects_before_checkpoint(continued,monkeypatch,defect):
    coordinator,F,W,s,lease,d,prior,authority,folder,selection=continued
    if defect=='foreign':directory=folder/'elsewhere'
    else:directory=folder/'continuation'
    if defect=='receipt':
        (s.run/'packaging/package.log').write_text('changed')
    elif defect=='prefix':
        monkeypatch.setattr(F,'validate',lambda *args: ({'last':'source_profile'},selection))
    elif defect=='validation':
        monkeypatch.setattr(C,'validate',lambda *args: (_ for _ in ()).throw(ValueError('static validation failed')))
    with pytest.raises(ValueError):C.checkpoint(coordinator,F,d,prior,authority=authority,directory=directory)
    assert not (folder/'continuation/environment.json').exists()
    assert not (folder/'continuation/package-completion.json').exists()
