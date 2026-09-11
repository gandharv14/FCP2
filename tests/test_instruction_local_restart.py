"""Tests for the isolated reviewed 0184 restart authority, not live coordinator."""
import importlib.util,json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'diagnostics/requested-fixes-20260910/0184-local-restart-overlay/scripts/batch/harbor_instruction_local_restart.py'
spec=importlib.util.spec_from_file_location('isolated_restart',SOURCE)
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)


def write(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return R.record(p)


@pytest.fixture
def authority(tmp_path,monkeypatch):
    old=tmp_path/'old';stage=tmp_path/'old-stage';code=tmp_path/'code';run=tmp_path/'new'
    for p in (old,stage,code,run):p.mkdir();(p/'file').write_text('immutable')
    newstage=tmp_path/'new-stage';newstage.mkdir()
    selection=NS(content=NS(workbook='0018',stage=newstage,bindings={k:k for k in (
        'original_raw_source','source_file','source_generation','segmentation_generation','curation')}))
    monkeypatch.setattr(R.K,'decode',lambda x:selection)
    worker=NS(context=NS(tracker_root=tmp_path/'tracker'),run=run,root=code)
    permit=dict(schema_version=R.SCHEMA,maximum_restarts=1,workbook='0018',run=str(run),current_root=str(code),
        campaign='campaign',context='context',implementation=R.record(SOURCE),current_files=R.tree(code),
        history=dict(run=str(old),files=R.tree(old),stage=str(stage),stage_files=R.tree(stage)),evidence=[],selection={})
    binding=write(tmp_path/'permit.json',permit)
    marker=dict(schema_version=R.SCHEMA,workbook='0018',run=str(run),permit=binding)
    write(R.registry(worker,'0018'),marker);write(run/'local-restart.json',marker)
    descriptor=dict(initial=dict(selection={}),worker=dict(campaign='campaign',context='context'))
    return worker,descriptor,permit,marker


def test_marker_authority_survives_descriptor_without_optional_field(authority):
    w,d,p,m=authority
    assert R.validate_selected(w,d)==p


@pytest.mark.parametrize('change',['delete-marker','substitute-marker','old-history','old-stage','code','scope','other-run'])
def test_permanent_registry_rejects_ancestry_loss(authority,change):
    w,d,p,m=authority
    if change=='delete-marker':(w.run/'local-restart.json').unlink()
    elif change=='substitute-marker':write(w.run/'local-restart.json',{**m,'run':'elsewhere'})
    elif change=='old-history':(Path(p['history']['run'])/'file').write_text('changed')
    elif change=='old-stage':(Path(p['history']['stage'])/'file').write_text('changed')
    elif change=='code':(w.root/'file').write_text('changed')
    elif change=='scope':d['worker']['campaign']='different'
    else:w.run=w.run.parent/'other'
    with pytest.raises(ValueError):R.validate_selected(w,d)


def test_registry_reservation_cannot_repeat_in_fresh_namespace(tmp_path):
    path=tmp_path/'registry.json';first=R.exclusive(path,{'run':'a'})
    with pytest.raises(FileExistsError):R.exclusive(path,{'run':'b'})
    assert R.record(path)==first


def test_unregistered_marker_rejected(authority):
    w,d,_,_=authority;R.registry(w,'0018').unlink()
    with pytest.raises(ValueError):R.validate_selected(w,d)


def test_legacy_without_registry_or_marker_remains_supported(authority):
    w,d,_,_=authority;R.registry(w,'0018').unlink();(w.run/'local-restart.json').unlink()
    assert R.validate_selected(w,d) is None


@pytest.mark.parametrize('name',['role-request-01.json','request.json','response.txt','ambiguous_roles.json','attempt-01'])
def test_any_prepared_or_uncertain_instruction_call_precludes_restart(tmp_path,name):
    (tmp_path/'steps/disclosure-build').mkdir(parents=True)
    (tmp_path/name).write_text('{}')
    with pytest.raises(ValueError):R.zero_calls(tmp_path)


def test_actual_retained_timeout_and_stage_are_consistent():
    run=ROOT/'diagnostics/authoring-resume-20260910/authoring/0184/instruction'
    if not run.exists():pytest.skip('local retained campaign evidence unavailable')
    result=R.failed_build(run,run.parent/'environment/stages/0184-outputs')
    assert len(result['files'])==17 and len(result['stage_files'])==15


def test_campaign_scan_preserves_prior_calls_but_rejects_instruction_scope(tmp_path):
    w=NS(campaign=NS(root=tmp_path));s=NS(content=NS(workbook='0018'))
    before={'lane_state':{k:{'attempt':0,'state':'pending'} for k in (
        'harbor-disclosure-writer','harbor-instruction-naturalizer','harbor-dialogue-author')}}
    first=write(tmp_path/'normalization/attempt-01/plan.json',{'scope':{'lane':'harbor-normalization-engineer','workbook_id':'0018'}})
    assert R.campaign_history(w,s,before)==[first]
    write(tmp_path/'disclosure/attempt-01/plan.json',{'scope':{'lane':'harbor-disclosure-writer','workbook_id':'0018'}})
    with pytest.raises(ValueError):R.campaign_history(w,s,before)


def test_consumed_semantic_attempt_cannot_be_reset_without_plan(tmp_path):
    w=NS(campaign=NS(root=tmp_path));s=NS(content=NS(workbook='0018'))
    before={'lane_state':{k:{'attempt':0,'state':'pending'} for k in (
        'harbor-disclosure-writer','harbor-instruction-naturalizer','harbor-dialogue-author')}}
    before['lane_state']['harbor-disclosure-writer']['attempt']=1
    with pytest.raises(ValueError):R.campaign_history(w,s,before)


@pytest.fixture
def profile(tmp_path,monkeypatch):
    roots={k:tmp_path/k for k in ('old','base','lease','current','overlay')}
    names=[R.COORD,'scripts/batch/harbor_lease_takeover.py','xl_seg/evaluate.py']
    for root in roots.values():
        for name in names:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(R.ANCHOR+'\n' if name==R.COORD else 'original')
    def snap(root,path,**extra):
        return write(path,dict(root=str(root),files=[dict(relative=k,sha256=v) for k,v in R.tree(root).items()],**extra))
    old=snap(roots['old'],tmp_path/'old.json')
    predecessor=write(tmp_path/'predecessor.json',{'snapshot':R.read(old)})
    base=snap(roots['base'],tmp_path/'base.json',predecessor=predecessor)
    t='scripts/batch/harbor_lease_takeover.py'
    for key in ('lease','overlay','current'):(roots[key]/t).write_text('reviewed lease')
    overlay=write(tmp_path/'overlay.json',dict(root=str(roots['overlay']),files=[dict(relative=t,**R.record(roots['overlay']/t))]))
    lease=snap(roots['lease'],tmp_path/'lease.json',overlay=overlay)
    (roots['current']/R.COORD).write_text(R.HOOK+R.ANCHOR+'\n')
    p=roots['current']/R.SELF;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('helper')
    monkeypatch.setattr(R,'__file__',str(p))
    initial=dict(code={str(roots['old']/t):R.record(roots['old']/t)['sha256']})
    return roots,old,base,lease,initial


def test_closed_profile_accepts_only_reviewed_deltas(profile):
    roots,old,base,lease,initial=profile
    assert len(R.code_profile(old,base,roots['current'],initial,lease))==4


@pytest.mark.parametrize('change',['evaluator','extra-file','incomplete-old-code','coordinator'])
def test_profile_rejects_unreviewed_code_or_detached_inventory(profile,change):
    roots,old,base,lease,initial=profile
    if change=='incomplete-old-code':initial['code']={}
    elif change=='extra-file':(roots['current']/'extra.py').write_text('unreviewed')
    elif change=='coordinator':(roots['current']/R.COORD).write_text('unreviewed')
    else:(roots['current']/'xl_seg/evaluate.py').write_text('different arithmetic')
    with pytest.raises(ValueError):R.code_profile(old,base,roots['current'],initial,lease)


def test_actual_authorize_serializes_both_prefixes_before_registry(tmp_path,monkeypatch):
    """Real immutable 0184 input shapes; intercept before any canonical mutation."""
    from dataclasses import replace
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_repair as repair,harbor_lease_takeover as T
    base=ROOT/'diagnostics/requested-fixes-20260910'
    dp=base/'environment-from-normalization-v5/0184/instruction-start/descriptor.json'
    if not dp.exists():pytest.skip('local retained campaign unavailable')
    descriptor=R.record(dp);old=R.read(descriptor);worker=C.worker_decode(old['worker'])
    prior=R.record(dp.parent/'result.json');selection=C.codec.decode(R.read(prior)['selection'])
    tracker=R.record(worker.run/'initial-tracker.json');before=R.read(tracker)
    lease=C.base.bootstrap.Lease(**R.read(R.record(worker.run/'initial.json'))['lease'])
    new=replace(worker,run=tmp_path/'fresh-worker',root=tmp_path/'code')
    directory=tmp_path/'authority';stage=tmp_path/'stages/0184-outputs';stage.parent.mkdir()
    monkeypatch.setattr(R,'code_profile',lambda *a:{'checked':'profile'})
    monkeypatch.setattr(R.W,'active',lambda *a:(None,before,tracker))
    monkeypatch.setattr(T,'continuation_chain',lambda *a,**kw:None)
    monkeypatch.setattr(R,'campaign_history',lambda *a:[])
    monkeypatch.setattr(R,'registry',lambda worker,wb:tmp_path/'isolated-tracker/instruction-local-restarts'/(wb+'.json'))
    plan=R.read(R.record(worker.run/'steps/disclosure-build/command-plan.json'))
    monkeypatch.setattr(R.W.producer,'disclosure_command_plan',lambda task:plan)
    seen=[]
    def prefix(*a,**kw):
        seen.append(str(a[1].content.stage));return [{'stage':seen[-1]}]
    monkeypatch.setattr(repair,'prefix',prefix)
    original=R.exclusive
    class ReservedBoundary(Exception):pass
    def intercept(path,value):
        if Path(path).parent.name=='instruction-local-restarts':
            permit=R.read(value['permit'])
            assert permit['original_prefix']==[{'stage':str(selection.content.stage)}]
            assert permit['cloned_prefix']==[{'stage':str(stage)}]
            assert permit['maximum_restarts']==1
            assert R.tree(stage)==R.tree(selection.content.stage)
            raise ReservedBoundary
        return original(path,value)
    monkeypatch.setattr(R,'exclusive',intercept)
    before_files=R.tree(worker.run)
    with pytest.raises(ReservedBoundary):
        R.authorize(descriptor,prior,worker=new,selection=selection,lease=lease,
          old_snapshot=R.record(base/'0184-instruction-code.json'),
          base_snapshot=R.record(base/'0184-indexed-methods-snapshot.json'),
          lease_snapshot=R.record(base/'0184-external-guard-takeover-snapshot-v2.json'),
          failure_outcome=R.record(base/'0184-successful-renewal-operations/001-disclosure-build/outcome.json'),
          directory=directory,stage=stage)
    assert R.tree(worker.run)==before_files
    assert not new.run.exists()
