import importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
ROOT=Path(__file__).resolve().parents[1]
OVER=ROOT/'diagnostics/requested-fixes-20260910/0184-local-command-overlay'

@pytest.fixture
def module(monkeypatch):
    import scripts.batch as package
    modules={}
    for name in ('harbor_instruction_local_restart','harbor_instruction_local_command'):
        p=OVER/'scripts/batch'/f'{name}.py';spec=importlib.util.spec_from_file_location('scripts.batch.'+name,p)
        m=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,m);monkeypatch.setattr(package,name,m,raising=False);spec.loader.exec_module(m);modules[name]=m
    return modules['harbor_instruction_local_command'],modules['harbor_instruction_local_restart']


def test_remaining_commands_only_and_no_capability_means_no_execution(module,tmp_path,monkeypatch):
    M,R=module
    original={'before_role_resolution':[[sys.executable,'-B','/old/disclose.py','--runs-root','/run','select']],
      'after_zero_or_validated_roles':[[sys.executable,'-B','/old/disclose.py','--runs-root','/run',name] for name in ('detect','context','write','faithcheck','verify')]}
    old=tmp_path/'plan.json';old.write_text(json.dumps(original));permit=dict(old_root='/old',plan=M.record(old),completed=['retained'],failed='retained-failure')
    monkeypatch.setattr(M,'validate',lambda *a:permit)
    w=NS(root=Path('/new'),run=tmp_path/'run');w.run.mkdir();(w.run/'local-command-resume.json').write_text(json.dumps({'permit':'bound'}))
    monkeypatch.setattr(M.W,'active',lambda *a:(None,{},{}));monkeypatch.setattr(M.W,'task',lambda *a:NS())
    monkeypatch.setattr(M.W.producer,'disclosure_command_plan',lambda t:json.loads(json.dumps(original).replace('/old','/new')))
    calls=[];monkeypatch.setattr(M.W,'_guarded',lambda w,t,d,n,argv,fd:calls.append((n,argv[-1])))
    monkeypatch.setattr(M.W,'refreshed',lambda s:s);out=tmp_path/'new-step';out.mkdir()
    assert M.execute(w,'selection','lease',out,commands=False,lock_fd=1)==('selection','lease','COMMANDS_REQUIRED')
    assert calls==[]
    assert M.execute(w,'selection','lease',out,commands=True,lock_fd=1)==('selection','lease',None)
    assert calls==[('after-02','write'),('after-03','faithcheck'),('after-04','verify')]
    reused=json.loads((out/'reused-command-evidence.json').read_text())
    assert reused['completed']==['retained'] and reused['executed_again'] is False


def test_actual_authorize_reaches_serialized_permit_without_reserving(module,tmp_path,monkeypatch):
    M,R=module
    from dataclasses import replace
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_repair as repair
    b=ROOT/'diagnostics/requested-fixes-20260910';dp=b/'0184-local-restart-authorization/descriptor.json'
    if not dp.exists():pytest.skip('local campaign evidence unavailable')
    descriptor=M.record(dp);d=M.read(descriptor);old=C.worker_decode(d['worker']);new=replace(old,root=tmp_path/'code')
    prior=M.record(b/'0184-local-restart-operations/result.json');pv=M.read(prior);selection=C.codec.decode(pv['selection']);lease=C.base.decode(pv['lease'])
    tracker=M.record(old.run/'initial-tracker.json');before=M.read(tracker)
    monkeypatch.setattr(M,'profile',lambda *a:(old.root,{'approved':'profile'}))
    monkeypatch.setattr(M.W,'active',lambda *a:(None,before,tracker))
    plan=M.read(M.record(old.run/'steps/disclosure-build/command-plan.json'))
    monkeypatch.setattr(M.W.producer,'disclosure_command_plan',lambda *a:plan)
    monkeypatch.setattr(M.W,'role_inventory',lambda *a:{'cases':[],'case_count':0})
    renderer=NS(write_disclosure=lambda *a:{'faults':[]},agent_records=lambda r:r,
        render_section=lambda r:'unchanged section',Book=lambda p:None,
        metadata_literal_warnings=lambda *a:[{'kind':'verified_cell_locator'}])
    monkeypatch.setattr(M.W.producer,'skill_module',lambda *a:renderer)
    monkeypatch.setattr(repair,'prefix',lambda *a,**kw:[{'kind':'all prior gates','passed':True}])
    exclusive=R.exclusive
    class Boundary(Exception):pass
    def intercept(path,value):
        if path==M.marker(new):
            p=M.read(value['permit']);assert len(p['completed'])==15
            assert p['maximum_continuations']==1 and p['dry_run']['faults']==[]
            assert p['section_sha256']==M.W.executor.sha(b'unchanged section')
            assert p['lease']==__import__('dataclasses').asdict(lease)
            raise Boundary
        return exclusive(path,value)
    monkeypatch.setattr(R,'exclusive',intercept)
    history=R.tree(old.run/'steps/disclosure-build')
    with pytest.raises(Boundary):M.authorize(new,selection,lease,
        snapshot=M.record(b/'0184-local-restart-snapshot.json'),descriptor=descriptor,prior_result=prior,
        failure=M.record(b/'0184-restarted-disclosure-build/outcome.json'),directory=tmp_path/'authority')
    assert not M.marker(new).exists() and R.tree(old.run/'steps/disclosure-build')==history
