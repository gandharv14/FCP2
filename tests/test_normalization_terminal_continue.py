"""Full inventory migration keeps prior code, job controls and request history."""
import json
from pathlib import Path
import pytest
from scripts.fleet import normalization_continue as M


def fixture(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    names={'scripts/fleet/advance.py':b'original driver\n',
           'scripts/batch/harbor_normalization_producer.py':b'original producer\n',
           'scripts/batch/harbor_normalization_partitioned.py':b'old partitions\n'}
    for name,raw in names.items():
        for root in(old,new):
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    (new/'scripts/batch/harbor_normalization_partitioned.py').write_bytes(b'bounded partitions\n')
    (new/'scripts/batch/harbor_normalization_terminal_repair.py').write_bytes(b'explicit rejected-call allowance\n')
    def snapshot(root):
        rows=[dict(relative=str(p.relative_to(root)),sha256=M.binding(p)['sha256']) for p in sorted(root.rglob('*')) if p.is_file()]
        return M.retain(tmp_path/(root.name+'-snapshot.json'),dict(root=str(root),files=rows))
    os=snapshot(old);ns=snapshot(new)
    oc=M.retain(tmp_path/'old-config.json',dict(workbook='0078',code=os,controls={'same':'immutable'},intake='original'))
    nc=M.retain(tmp_path/'new-config.json',{**M.checked(oc),'code':ns})
    job=dict(driver=M.binding(old/'scripts/fleet/advance.py'),config=oc,models=True,outcome='original',operations='original')
    nj=M.retain(tmp_path/'new-job.json',{**job,'config':nc,'driver':M.binding(new/'scripts/fleet/advance.py')})
    s=M.retain(tmp_path/'successor.json',dict(schema_version='normalization-terminal-code-successor/v1',
        original_snapshot=os,snapshot=ns,original_config=oc,config=nc,job=nj,
        changed_files=['scripts/batch/harbor_normalization_partitioned.py','scripts/batch/harbor_normalization_terminal_repair.py']))
    plan=dict(code_root=str(new),terminal_rejection={'permit':{'path':'retained','sha256':'a'*64},'successor':s})
    return plan,job,old,new


def reseal(plan,key,value,tmp_path):
    old=M.checked(plan['terminal_rejection']['successor']);old[key]=value
    plan['terminal_rejection']['successor']=M.retain(tmp_path/'updated-successor.json',old)


def test_exact_successor_preserves_old_inputs(tmp_path):
    p,j,o,n=fixture(tmp_path);before={str(x):x.read_bytes() for x in o.rglob('*') if x.is_file()}
    new=M.successor(p,j)
    assert new['outcome']==j['outcome'] and new['operations']==j['operations']
    assert all(Path(k).read_bytes()==v for k,v in before.items())


@pytest.mark.parametrize('field,value',[('controls',{'changed':True}),('intake','new'),('workbook','0010')])
def test_current_config_cannot_change_original_authority(tmp_path,field,value):
    p,j,_,_=fixture(tmp_path);s=M.checked(p['terminal_rejection']['successor'])
    c=M.checked(s['config']);c[field]=value
    reseal(p,'config',M.retain(tmp_path/'foreign-config.json',c),tmp_path)
    with pytest.raises(ValueError,match='only the current code'):M.successor(p,j)


def test_recomputed_inventory_cannot_change_original_producer(tmp_path):
    p,j,_,n=fixture(tmp_path);s=M.checked(p['terminal_rejection']['successor']);v=M.checked(s['snapshot'])
    target=n/'scripts/batch/harbor_normalization_producer.py';target.write_bytes(b'changed prompt or source parser')
    for row in v['files']:
        if row['relative'].endswith('harbor_normalization_producer.py'):row['sha256']=M.binding(target)['sha256']
    ns=M.retain(tmp_path/'tampered-snapshot.json',v);c=M.checked(s['config']);c['code']=ns
    nc=M.retain(tmp_path/'tampered-config.json',c);new=M.checked(s['job']);new['config']=nc
    s.update(snapshot=ns,config=nc,job=M.retain(tmp_path/'tampered-job.json',new))
    s['changed_files'].append('scripts/batch/harbor_normalization_producer.py')
    p['terminal_rejection']['successor']=M.retain(tmp_path/'tampered-successor.json',s)
    with pytest.raises(ValueError,match='unreviewed terminal'):M.successor(p,j)


def test_extra_uninventoried_file_rejected(tmp_path):
    p,j,_,n=fixture(tmp_path);(n/'foreign.py').write_text('foreign')
    with pytest.raises(ValueError,match='full code inventory'):M.successor(p,j)


def test_job_cannot_skip_original_outcome(tmp_path):
    p,j,_,_=fixture(tmp_path);s=M.checked(p['terminal_rejection']['successor']);new=M.checked(s['job']);new['outcome']='new'
    reseal(p,'job',M.retain(tmp_path/'changed-job.json',new),tmp_path)
    with pytest.raises(ValueError,match='dispatcher or original'):M.successor(p,j)


def test_actual_descriptor_reader_uses_only_same_byte_historical_locator(tmp_path):
    import ast,types
    source=Path('scripts/harbor_campaign.py').read_text();node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='selected')
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    for root in(old,new):(root/'harbor_campaign.py').write_text(source)
    snapshot=lambda root:M.retain(tmp_path/(root.name+'.json'),{'root':str(root),'files':[]})
    p=M.retain(tmp_path/'successor.json',{'original_snapshot':snapshot(old),'snapshot':snapshot(new)})
    origin=M.retain(tmp_path/'origin.json',{'original':'source'})
    descriptor=M.retain(tmp_path/'descriptor.json',dict(schema_version='harbor-environment-coordinator/v1',
        worker='worker',initial_lease='lease',controls='controls',origin=[origin],code=M.binding(old/'harbor_campaign.py')))
    class Worker:pass
    class Lease:pass
    w=Worker();l=Lease();controls=object();seen=[]
    base=types.ModuleType('fixture_selected_coordinator');base.__file__=str(new/'harbor_campaign.py')
    base.__dict__.update(read=M.checked,record=M.binding,require=M.require,
        decode={'worker':w,'lease':l,'controls':controls}.__getitem__,
        worker=types.SimpleNamespace(Selection=Worker),bootstrap=types.SimpleNamespace(Lease=Lease),
        executor=types.SimpleNamespace(checked_bytes=lambda row:seen.append(M.checked(row))),
        _controls=lambda value:seen.append(value))
    exec(compile(ast.Module(body=[node],type_ignores=[]),base.__file__,'exec'),base.__dict__)
    with pytest.raises(ValueError,match='coordinator code changed'):base.selected(descriptor)
    before=base.record
    assert M.historical_worker(base,descriptor,{'terminal_rejection':{'successor':p}})==(w,l,controls)
    assert base.record is before and seen==[controls,{'original':'source'}]
    with pytest.raises(ValueError,match='coordinator code changed'):base.selected(descriptor)
    (new/'harbor_campaign.py').write_text(source+'\n# changed\n')
    with pytest.raises(ValueError,match='identical selected'):M.historical_worker(base,descriptor,{'terminal_rejection':{'successor':p}})


from test_root_lease_takeover import setup as takeover_setup


def test_initial_lease_requires_real_completed_replacement(takeover_setup,tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from scripts.batch import harbor_lease_takeover as T
    from scripts.batch.harbor_workbook_bootstrap import Lease
    s,d,b,old=takeover_setup
    (s.campaign_root/'work/0245/instruction-worker/worker.lock').unlink()
    (s.campaign_root/'work/0245/instruction-worker').rmdir()
    (s.campaign_root/'work/0245/environment-worker').mkdir()
    s=replace(s,worker_kind='environment');new,completion=T.replace_expired(s,d,clock=lambda:120)
    job=M.retain(tmp_path/'current-job.json',{'config':None})
    successor=M.retain(tmp_path/'lease-successor.json',{'job':job,'original_config':None})
    plan=dict(workbook='0245',unresolved=s.checkpoint,tracker=M.binding(s.tracker['path']),
        terminal_rejection={'successor':successor},lease_continuation={'takeover':M.binding(T.__file__),'completion':completion})
    base=SimpleNamespace(decode=lambda value:Lease(**value),bootstrap=SimpleNamespace(Lease=Lease))
    assert M.initial_lease(plan,base)==new
    assert M.checked(s.checkpoint)['lease']['token']==old.token
    bad={**plan,'tracker':{**plan['tracker'],'sha256':'f'*64}}
    with pytest.raises(ValueError,match='detached'):M.initial_lease(bad,base)
    bad={**plan,'workbook':'0010'}
    with pytest.raises(ValueError,match='detached'):M.initial_lease(bad,base)
