import inspect,json,sys
from pathlib import Path
from types import SimpleNamespace as S
import pytest
from scripts.fleet import package_recovery_continue as M


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)


def history_modules(tmp_path):
    selected=inspect.getsource(M.historical_environment)
    old=selected.replace("and start['selection']==value['selection']", "and start['selection']==value['selection']and start['lease']==hv['lease']")
    old=old.replace("    startup_lease_history(p,env[0],hv['lease'],start['lease'])\n",'')
    prefix='from pathlib import Path\n'
    a=tmp_path/'old.py';a.write_text(prefix+old)
    b=tmp_path/'new.py';b.write_text(prefix+inspect.getsource(M.startup_lease_history)+'\n'+selected)
    return M.module('old_history_test',M.binding(a)),M.module('new_history_test',M.binding(b))


def test_exact_history_scope_is_restored_on_exception_and_rejects_nested(tmp_path):
    old,new=history_modules(tmp_path);original=old.historical_environment
    with pytest.raises(RuntimeError,match='downstream'):
        with M.selected_startup_history(old,new):
            assert old.historical_environment is not original and old.historical_environment.__globals__ is old.__dict__
            with pytest.raises(ValueError):
                with M.selected_startup_history(old,new):pass
            raise RuntimeError('downstream')
    assert old.historical_environment is original and not hasattr(old,'startup_lease_history')


def test_history_delta_rejects_any_other_function_change(tmp_path):
    old,new=history_modules(tmp_path)
    path=Path(new.__file__);path.write_text(path.read_text()+'\ndef unreviewed(): return True\n')
    with pytest.raises(ValueError,match='other completed'):M.startup_history_delta(old,new)


def fixture(tmp_path,monkeypatch):
    proc=tmp_path/'proc';proc.mkdir();monkeypatch.setattr(M,'Path',lambda value:proc if str(value)=='/proc'else Path(value))
    helper=M.binding(Path(M.__file__).absolute());previous=put(tmp_path/'previous.json',{})
    old=dict(home=str(tmp_path/'old'),operations=str(tmp_path/'old-ops'),helper=helper,owner='same-owner',fence=9,workbook='0496',source={'path':'/source','sha256':'source'})
    cp=put(Path(old['home'])/'instruction-start/result.json',{})
    startout=put(Path(old['home'])/'instruction-start/outcome.json',{})
    startintent=put(Path(old['home'])/'instruction-start/intent.json',{})
    desc=put(tmp_path/'descriptor.json',{})
    value=dict(status='INSTRUCTION_READY',checkpoint=None,descriptor=desc,selection={'selected':True},lease={'old':True})
    directory=Path(old['operations'])/'000'
    intent=put(directory/'intent.json',dict(action='step',arguments=dict(descriptor=desc,checkpoint=cp)))
    put(directory/'declared-runtime-adapter.json',{});(directory/'step.log').write_bytes(b'');put(Path(old['operations'])/'job.json',{})
    run=tmp_path/'worker';run.mkdir();(run/'worker.log').write_bytes(b'')
    job=put(tmp_path/'job.json',dict(driver=helper,workbook='0496',version='original',source=old['source'],result=str(Path(old['home'])/'result.json'),run=str(run),command=[sys.executable,'-I','-B',helper['path'],'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]))
    wi=put(run/'intent.json',dict(job=job,owner=old['owner'],claim_id='claim',automatic_retry=False))
    claim=put(run/'claim.json',dict(claim_id='claim',owner=old['owner'],fence=9,source_sha256='source',version='original',workbook='0496'))
    outer=put(run/'outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',intent=wi,returncode=None,failure_type='RuntimeError',log=M.binding(run/'worker.log'),metrics={},result=None,publication_performed=False,automatic_retry=False))
    process=put(tmp_path/'process.json',dict(pid=998877,start_ticks='4',job=job,automatic_retry=False))
    tracker=put(tmp_path/'tracker.json',{'revision':38})
    stop=put(tmp_path/'stop.json',dict(schema_version='fleet-interrupted-package-first-step/v1',previous=previous,worker_outcome=outer,process=process,claim=claim,tracker=tracker,files={x.name:M.binding(x)for x in directory.iterdir()}))
    p=dict(previous=previous,interrupted=intent,start_outcome=startout,stopped=stop)
    worker=S(run=tmp_path/'unused-task');calls=[]
    O=S(historical_adapter=lambda row,o,e,a,i:calls.append(i))
    def successful(row,env):assert row==startout;return cp,value
    D=S(successful=successful);base=S(decode=lambda x:S(sha256=tracker['sha256']))
    I=S(selected=lambda row:(worker,None,None),codec=S(decode=lambda x:x));W=S(active=lambda *a:calls.append('active'))
    env=(base,I,None,W)
    return p,old,O,D,env,None,value,worker,calls


def test_exact_intent_only_interruption_preserves_completed_start(tmp_path,monkeypatch):
    p,old,O,D,env,A,value,worker,calls=fixture(tmp_path,monkeypatch)
    cp,actual=M.interrupted_initial_step(p,old,O,D,env,A,live=True)
    assert actual==value and calls[-1]=='active' and len(calls)==3
    worker.run.mkdir()
    # Historical validation keeps the old immutable stop; current ownership is
    # checked separately after the selected renewal and original W transition.
    M.interrupted_initial_step(p,old,O,D,env,A,live=False)
    with pytest.raises(ValueError,match='custody advanced'):M.interrupted_initial_step(p,old,O,D,env,A,live=True)


@pytest.mark.parametrize('fault',['dependency','outcome','log','checkpoint','status','claim','tracker','process'])
def test_partial_effect_or_foreign_custody_cannot_resume(tmp_path,monkeypatch,fault):
    p,old,O,D,env,A,value,worker,calls=fixture(tmp_path,monkeypatch)
    directory=Path(p['interrupted']['path']).parent;stop=M.read(p['stopped'])
    if fault in ('dependency','outcome'):put(directory/(fault+'.json'),{})
    elif fault=='log':(directory/'step.log').write_text('a child wrote output')
    elif fault=='checkpoint':value['checkpoint']={'invented':'predecessor'}
    elif fault=='status':value['status']='STEP_COMPLETE'
    elif fault=='tracker':Path(stop['tracker']['path']).write_text('{"revision":39}')
    elif fault=='process':
        q=tmp_path/'proc/998877';q.mkdir();fields=['S']+['0']*20;fields[19]='4';(q/'stat').write_text('998877 (test) '+' '.join(fields))
    else:
        claim=M.read(stop['claim']);claim['fence']=10;stop['claim']=put(Path(stop['claim']['path']),claim);p['stopped']=put(Path(p['stopped']['path']),stop)
    with pytest.raises(ValueError):M.interrupted_initial_step(p,old,O,D,env,A,live=True)
    assert not worker.run.exists()


def takeover_fixture(tmp_path,monkeypatch):
    from contextlib import contextmanager
    shared={'lifecycle':{'path':'/L','sha256':'L'},'takeover':{'path':'/T','sha256':'T'}}
    snapshot={'path':'/snapshot','sha256':'code'};source={'path':'/source','sha256':'source'}
    tracker=put(tmp_path/'original-tracker.json',{'revision':38});prior=put(tmp_path/'T3.json',{})
    original_tp=put(tmp_path/'T3-profile.json',dict(shared=shared,original_snapshot=snapshot))
    integration=put(tmp_path/'integration.json',dict(recovery={'path':'/recovery','sha256':'R'}))
    original=put(tmp_path/'package-plan.json',dict(integration=integration))
    old=dict(previous=original,takeover_profile=original_tp,takeover_completion=prior)
    tp=put(tmp_path/'T4-profile.json',dict(shared=shared,original_snapshot=snapshot,selection=dict(owner='same-owner',prior_completion=prior,checkpoint=prior)))
    descriptor=put(tmp_path/'descriptor.json',{});resource=put(tmp_path/'resource.json',{});result=put(tmp_path/'start.json',{})
    outcome=put(tmp_path/'outcome.json',dict(resource=resource));stop=put(tmp_path/'stop.json',dict(tracker=tracker))
    bridge={'path':'/B','sha256':'B'};home=tmp_path/'remaining'
    takeover_stop=put(tmp_path/'T4-stop.json',dict(before=tracker))
    completion=put(tmp_path/'T4.json',dict(stopped_owner_proof=takeover_stop))
    profile=put(tmp_path/'handoff-profile.json',dict(completion=completion,initial_start=True,implementation=bridge,takeover_plan=tp,outcome=outcome,result=result,descriptor=descriptor,guard=resource,source=source,workbook='0496',directory=str(home/'checkpoint-takeover'),prior_takeover_chain=dict(original_tracker=tracker)))
    p=dict(checkpoint_takeover=dict(helper=bridge,profile=profile),start_outcome=outcome,source=source,workbook='0496',home=str(home),owner='same-owner',stopped=stop)
    seen=[]
    @contextmanager
    def takeover(R,selected):seen.append(selected);yield 'original-T';seen.append('restored')
    R=S(source_runtime=lambda row:S(takeover=takeover));monkeypatch.setattr(M,'module',lambda name,row:R)
    D=S(successful=lambda row,env:(result,dict(descriptor=descriptor)))
    return p,old,D,seen


def test_remaining_start_uses_original_completed_takeover_scope(tmp_path,monkeypatch):
    p,old,D,seen=takeover_fixture(tmp_path,monkeypatch)
    with M.remaining_takeover(p,old,D,(),None)as T:assert T=='original-T'
    assert len(seen)==2 and seen[-1]=='restored'


@pytest.mark.parametrize('fault',['source','guard','outcome','descriptor','directory','owner','snapshot','shared','prior','tracker'])
def test_remaining_takeover_rejects_changed_start_or_takeover_ancestry(tmp_path,monkeypatch,fault):
    p,old,D,seen=takeover_fixture(tmp_path,monkeypatch);profile=M.read(p['checkpoint_takeover']['profile']);tp=M.read(profile['takeover_plan'])
    if fault in ('source','guard','outcome','descriptor'):profile[fault]={'path':'/foreign','sha256':'foreign'}
    elif fault=='directory':profile['directory']=str(tmp_path/'foreign')
    elif fault=='tracker':profile['prior_takeover_chain']['original_tracker']={'path':'/foreign','sha256':'foreign'}
    else:
        if fault=='owner':tp['selection']['owner']='foreign'
        elif fault=='prior':tp['selection']['checkpoint']={'path':'/foreign','sha256':'foreign'}
        elif fault=='snapshot':tp['original_snapshot']={'path':'/foreign','sha256':'foreign'}
        else:tp['shared']={'foreign':True}
        profile['takeover_plan']=put(Path(profile['takeover_plan']['path']),tp)
    p['checkpoint_takeover']['profile']=put(Path(p['checkpoint_takeover']['profile']['path']),profile)
    with pytest.raises(ValueError,match='detached'): 
        with M.remaining_takeover(p,old,D,(),None):pass
    assert not seen


def failed_custody_fixture(tmp_path,monkeypatch):
    p,old,O,D,env,A,value,worker,calls=fixture(tmp_path,monkeypatch)
    before=M.read(p['stopped']);outer=M.read(before['worker_outcome']);outer.update(returncode=1,failure_type=None)
    outcome=put(Path(before['worker_outcome']['path']),outer)
    handoff=put(Path(old['home'])/'checkpoint-takeover/handoff.json',{})
    failed=put(Path(old['home'])/'first-step/outcome.json',{})
    put(Path(old['home'])/'first-step/intent.json',{})
    # The old fixture's interrupted namespace is unrelated to this actual
    # remaining worker's unused future dispatcher namespace.
    old['operations']=str(tmp_path/'unused-next-dispatcher')
    f=put(tmp_path/'failed-successor.json',dict(handoff=handoff,outcome=failed))
    stopped=put(tmp_path/'failed-stop.json',dict(schema_version='fleet-failed-package-first-step-stop/v1',
        previous=p['previous'],worker_outcome=outcome,process=before['process'],claim=before['claim'],tracker=before['tracker']))
    selected=dict(previous=p['previous'],failure=f,stopped=stopped)
    return selected,dict(tracker=before['tracker']),old,O,D,env,A,calls


def test_failed_package_custody_accepts_only_stopped_actual_first_step(tmp_path,monkeypatch):
    p,proof,old,O,D,env,A,calls=failed_custody_fixture(tmp_path,monkeypatch)
    M.failed_start_custody('row',p,proof,old,O,D,env,A)
    assert calls==[M.binding(Path(old['home'])/'first-step/intent.json')]


@pytest.mark.parametrize('fault',['uncertain','claim','tracker','later_dispatcher','later_result','process','job'])
def test_failed_package_custody_rejects_unknown_or_later_effects(tmp_path,monkeypatch,fault):
    p,proof,old,O,D,env,A,calls=failed_custody_fixture(tmp_path,monkeypatch);s=M.read(p['stopped'])
    if fault=='uncertain':
        v=M.read(s['worker_outcome']);v.update(returncode=None,failure_type='TimeoutError');s['worker_outcome']=put(Path(s['worker_outcome']['path']),v)
    elif fault=='claim':
        v=M.read(s['claim']);v['fence']+=1;s['claim']=put(Path(s['claim']['path']),v)
    elif fault=='tracker':put(Path(s['tracker']['path']),{'changed':'current'})
    elif fault=='later_dispatcher':Path(old['operations']).mkdir()
    elif fault=='later_result':put(Path(old['home'])/'result.json',{'status':'later'})
    elif fault=='job':old['helper']={'path':'/another-helper','sha256':'foreign'}
    else:
        process=M.read(s['process']);q=tmp_path/'proc'/str(process['pid']);q.mkdir();fields=['S']+['0']*20;fields[19]=str(process['start_ticks']);(q/'stat').write_text('1 (worker) '+' '.join(fields));(q/'cmdline').write_bytes(b'python\0')
    p['stopped']=put(Path(p['stopped']['path']),s)
    with pytest.raises(ValueError):M.failed_start_custody('row',p,proof,old,O,D,env,A)


def test_failed_refresh_scope_is_optional_and_pins_original_package_root(tmp_path,monkeypatch):
    from contextlib import contextmanager
    calls=[];root=tmp_path/'root';refresh=root/'scripts/batch/harbor_environment_refresh.py';lanes=root/'scripts/batch/harbor_completed_lanes.py'
    for path in (refresh,lanes):path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# original\n')
    selected=dict(helper=M.binding(Path(M.__file__).absolute()),refresh=M.binding(refresh),lanes=M.binding(lanes));F=S()
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_environment_refresh',F)
    @contextmanager
    def installed(actual,**kwargs):
        assert actual is F and kwargs==dict(refresh=selected['refresh'],lanes=selected['lanes'])
        calls.append('entered')
        try:yield
        finally:calls.append('restored')
    monkeypatch.setattr(M,'module',lambda name,row:S(installed=installed));env=(None,)*6+(root,)
    with M.failed_refresh_scope({},env):assert calls==[]
    with pytest.raises(RuntimeError):
        with M.failed_refresh_scope(dict(pair_scope=selected),env):raise RuntimeError('original downstream exception')
    assert calls==['entered','restored']
    for key in ('refresh','lanes'):
        bad=dict(selected);bad[key]=selected['helper']
        with pytest.raises(ValueError,match='original selected package'):
            with M.failed_refresh_scope(dict(pair_scope=bad),env):pass
