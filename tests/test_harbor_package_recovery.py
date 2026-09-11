"""Real tiny current gates and tracker CAS; command/service fixtures only."""
from dataclasses import asdict,replace
from pathlib import Path
import copy
import importlib
import json
import sys
from types import SimpleNamespace
import pytest
from scripts import harbor_package_recovery as R
from scripts import harbor_campaign as C
from scripts.batch import harbor_environment_refresh as F
from scripts.batch import harbor_environment_worker as W
from scripts.batch import harbor_environment_producer as P
from scripts.batch.harbor_admission import record,read
from scripts.batch.harbor_transition_codec import encode
from test_direct_audit_packaging import completed
from test_historical_audit_source_refresh import direct
from test_harbor_admission import setup
from test_harbor_environment_worker import worker,responses
from test_harbor_workbook_bootstrap import selected
from test_harbor_normalization_producer import mock_review


def local_commands(monkeypatch):
    real=P.subprocess.run
    def local(argv,**kwargs):
        if len(argv)>1 and Path(argv[1]).name in {'xl_variable_mcp.py','xl_input_mask.py','xl_output_task.py'}:
            rc=importlib.import_module(Path(argv[1]).stem).main(list(map(str,argv[2:])))
            return SimpleNamespace(returncode=rc or 0)
        return real(argv,**kwargs)
    monkeypatch.setattr(P.subprocess,'run',local)

@pytest.fixture
def scenario(worker,completed,monkeypatch,tmp_path):
    s,lease=worker;b,upgrade,original,_,_,_=completed
    b={**b,'audit_validator_upgrade':upgrade,'audit_upgrade_original_code':original}
    c=W._content(s,b,read(s.prepared)['normalization_records']);sel=W._selection(c)
    r=W._gate(s,lease,'research',sel);assert r['status']=='STEP_COMPLETE';lease=r['lease'];W._checkpoint(s,'research',sel,r['evidence'])
    mock_review(monkeypatch,W.normalization.Selection(c.workbook,b,c.normalization_run,c.model_policy,s.normalization_budget))
    c,receipt=W._normalization(s,c,dispatch=True,credential='synthetic');sel=replace(sel,content=c)
    r=W._gate(s,lease,'normalization_phase1',sel);assert r['status']=='STEP_COMPLETE';lease=r['lease'];W._checkpoint(s,'normalization_phase1',sel,r['evidence'])
    prior=R.keep(tmp_path/'phase1.json',{**r,'lease':asdict(lease),'selection':encode(sel)})
    refresh=F.prepare(s,lease,prior,sel,'normalization_phase1',tmp_path/'refresh');lease,_,committed=F.commit(s,lease,refresh)
    controls=C.Controls(tmp_path/'actual-workbook.lock')
    descriptor=C._descriptor(s,lease,controls,(refresh,committed));descriptor.update(schema_version='harbor-environment-coordinator-refresh/v1',refresh=refresh)
    (tmp_path/'coordinator').mkdir()
    descriptor=R.keep(tmp_path/'coordinator/descriptor.json',descriptor)
    for phase in ('source_profile','normalization_phase2'):
        r=F.advance(s,lease,refresh,tmp_path/phase);assert r['status']=='STEP_COMPLETE';lease=r['lease']
    checkpoint=R.keep(tmp_path/'phase2.json',{**r,'descriptor':descriptor,'lease':C.encode(lease),'selection':encode(r['selection'])})
    current=r['selection'];local_commands(monkeypatch)
    command=P.command
    def fail(work,label,argv):
        if label!='package':return command(work,label,argv)
        args=list(map(str,argv))
        for flag in ('--audit-validator-upgrade-receipt','--audit-upgrade-original-code'):
            i=args.index(flag);del args[i:i+2]
        R.keep(work/'package.intent.json',dict(argv=[sys.executable,*args],entry=dict(path=str(R.ROOT/'xl_output_task.py'),sha256=R.BEFORE_PACKAGER),model_calls_permitted=False))
        (work/'package.log').write_text('RuntimeError: no lbx_api_key found; use --no-variable-source-audit to opt out\n')
        R.keep(work/'package.result.json',dict(returncode=1,log=record(work/'package.log')))
        raise ValueError('selected old packaging defect')
    with monkeypatch.context() as m:
        m.setattr(P,'command',fail)
        with pytest.raises(ValueError,match='selected old'):P.package(current.content,current.public_profiles,s.run/'packaging',s.taxonomy)
    path=s.run/'packaging/selection.json';value=read(record(path));value['code']['sha256']=R.BEFORE_PRODUCER;path.write_bytes(W.executor.canonical(value))
    guard=tmp_path/'failed-guard';guard.mkdir();(guard/'step.log').write_text('retained known command failure\n')
    intent=R.keep(guard/'intent.json',dict(schema_version='harbor-coordinator-step/v1',action='step',arguments=dict(descriptor=descriptor,checkpoint=checkpoint,captures=None),controls=C.encode(controls),dispatch=False,code=record(C.__file__),executable=record(Path(sys.executable).resolve()),memory_limit_bytes=R.MEMORY,timeout_seconds=R.SECONDS))
    resource=R.keep(guard/'resource.json',dict(intent=intent,returncode=1,metrics={'peak_rss_bytes':1234},log=record(guard/'step.log'),failure=None))
    outcome=R.keep(guard/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None))
    return SimpleNamespace(s=s,lease=lease,current=current,descriptor=descriptor,checkpoint=checkpoint,outcome=outcome,controls=controls,home=tmp_path)


def prepare(s):return R.prepare(s.descriptor,s.checkpoint,s.outcome,s.lease,s.home/'recovery')


def test_actual_current_package_owning_environment_cas_preserves_history(scenario,monkeypatch):
    s=scenario;p=prepare(s);before=read(record(s.s.bootstrap.policy.tracker_root/'0001.json'))
    originals={r['path']:Path(r['path']).read_bytes() for r in read(p)['failed_evidence']}
    assert R.execute(p)['status']=='PREPARED'
    monkeypatch.setattr(W.executor,'_open',lambda *a,**k:pytest.fail('models forbidden'))
    r=R.execute(p,dispatch_commands=True)
    assert r['status']=='ENVIRONMENT_READY'
    after=read(record(s.s.bootstrap.policy.tracker_root/'0001.json'))
    assert after['revision']==before['revision']+1
    assert after['failure_history']==before['failure_history'] and after['repairs']==before['repairs']
    assert all(after['lane_state'][k]['attempt']==v['attempt'] for k,v in before['lane_state'].items())
    assert all(Path(p).read_bytes()==raw for p,raw in originals.items())
    assert not r['publication_ready']


def test_completed_package_resumes_without_rerunning_commands(scenario,monkeypatch):
    s=scenario;p=prepare(s)
    def fault(at):
        if at=='after_package':raise RuntimeError('process loss')
    with pytest.raises(RuntimeError):R.execute(p,dispatch_commands=True,fault=fault)
    monkeypatch.setattr(P,'package',lambda *a,**k:pytest.fail('completed package may not rerun'))
    assert R.execute(p)['status']=='ENVIRONMENT_READY'

@pytest.mark.parametrize('at',['after_gate','after_result'])
def test_exact_post_cas_reconciliation_does_not_repeat_cas(scenario,monkeypatch,at):
    p=prepare(scenario)
    def fault(phase):
        if phase==at:raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):R.execute(p,dispatch_commands=True,fault=fault)
    before=(scenario.s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    monkeypatch.setattr(W,'_gate',lambda *a,**k:pytest.fail('no repeated CAS'))
    assert R.reconcile(p)['status']=='ENVIRONMENT_READY'
    assert (scenario.s.bootstrap.policy.tracker_root/'0001.json').read_bytes()==before

@pytest.mark.parametrize('damage',['stage','lease','checkpoint','log','outcome','argv','code','budget','source','profile'])
def test_changed_or_uncertain_root_evidence_rejects_before_effects(scenario,damage):
    s=scenario
    if damage=='stage':s.current.content.stage.mkdir(parents=True)
    elif damage=='lease':s.lease=replace(s.lease,token='foreign')
    else:
        path={'checkpoint':s.checkpoint['path'],'log':s.s.run/'packaging/package.log','outcome':s.outcome['path'],
              'argv':s.s.run/'packaging/package.intent.json','code':s.s.run/'packaging/selection.json',
              'budget':s.current.content.normalization_prepared['path'],'source':s.current.content.bindings['source_file']['path'],
              'profile':s.current.public_profiles.profiles['path']}[damage]
        Path(path).chmod(0o600);Path(path).write_bytes(Path(path).read_bytes()+b'changed')
    before=(s.s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    with pytest.raises((ValueError,KeyError)):prepare(s)
    assert (s.s.bootstrap.policy.tracker_root/'0001.json').read_bytes()==before


def test_partial_new_package_never_replays(scenario):
    p=prepare(scenario);(Path(p['path']).parent/'package').mkdir()
    with pytest.raises(ValueError,match='partial or unknown'):R.execute(p,dispatch_commands=True)


def test_completed_package_changed_task_is_rejected(scenario):
    p=prepare(scenario)
    def fault(at):raise RuntimeError('stop')
    with pytest.raises(RuntimeError):R.execute(p,dispatch_commands=True,fault=fault)
    (scenario.current.content.stage/'instruction.md').write_text('altered')
    with pytest.raises(ValueError):R.execute(p)


@pytest.mark.parametrize('changed',['argv','result','receipt'])
def test_rehashed_incomplete_completed_package_rejects(scenario,changed):
    p=prepare(scenario)
    def fault(at):raise RuntimeError('stop')
    with pytest.raises(RuntimeError):R.execute(p,dispatch_commands=True,fault=fault)
    home=Path(p['path']).parent/'package'
    path=home/{'argv':'package.intent.json','result':'package.result.json','receipt':'complete.json'}[changed]
    v=read(record(path))
    if changed=='argv':v['argv'][-1]='foreign'
    elif changed=='result':v['returncode']=1
    else:v['stage_sha256']='0'*64
    path.write_bytes(W.executor.canonical(v))
    with pytest.raises(ValueError):R.execute(p)


def test_no_post_cas_reconciliation_when_cas_was_not_attempted(scenario):
    p=prepare(scenario)
    def fault(at):raise RuntimeError('stop')
    with pytest.raises(RuntimeError):R.execute(p,dispatch_commands=True,fault=fault)
    with pytest.raises((ValueError,FileNotFoundError)):R.reconcile(p)


def test_guard_fixes_slot_limits_and_retains_child_outcome(scenario,monkeypatch):
    s=scenario;lease=R.keep(s.home/'lease.json',C.encode(s.lease));seen=[]
    def guard(argv,**kw):
        seen.append(kw)
        assert kw['memory_limit_bytes']==8*1024**3 and kw['timeout_seconds']==900 and kw['guardian_lock_fds']
        R.child(dict(path=argv[-2],sha256=argv[-1]));kw['metrics']['peak_rss_bytes']=123;return 0
    monkeypatch.setattr(C,'_run_in_process_group',guard)
    output=R.run('prepare',dict(descriptor=s.descriptor,checkpoint=s.checkpoint,outcome=s.outcome,lease=lease),s.controls,s.home/'guarded-prepare')
    assert read(output)['status']=='COMPLETED_OPERATION' and len(seen)==1
    prepared=read(read(output)['result']);assert read(prepared)['budget_reset'] is False


def test_guard_rejects_unshared_slot_before_child(scenario,monkeypatch):
    s=scenario;p=prepare(s)
    monkeypatch.setattr(C,'_run_in_process_group',lambda *a,**k:pytest.fail('no child'))
    with pytest.raises(ValueError):R.run('execute',dict(prepared=p),C.Controls(s.home/'other.lock'),s.home/'wrong-slot',dispatch_commands=True)


@pytest.mark.parametrize('kind',['absent','foreign','non_null'])
def test_only_recognized_legacy_absent_scope_receipt_is_equivalent(scenario,kind):
    s=scenario;path=s.s.run/'packaging/selection.json';v=read(record(path))
    if kind=='absent':v['content']['value'].pop('dialogue_scope_receipt')
    elif kind=='foreign':v['content']['value']['unknown']=dict(type='scalar',value=None)
    else:v['content']['value']['dialogue_scope_receipt']=encode({'path':'foreign','sha256':'0'*64})
    path.write_bytes(W.executor.canonical(v));original=path.read_bytes()
    if kind=='absent':assert read(prepare(s))['budget_reset'] is False
    else:
        with pytest.raises(ValueError):prepare(s)
    assert path.read_bytes()==original
