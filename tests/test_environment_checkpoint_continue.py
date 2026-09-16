"""A completed takeover reads its archive without rewriting current custody."""
import json,types
import pytest
from scripts.fleet import environment_checkpoint_continue as E

def put(p,v):
    p.write_text(json.dumps(v));return E.binding(p)

@pytest.mark.parametrize('defect',[None,'archive','current','selection'])
def test_historical_tracker_read_is_exact_and_read_only(tmp_path,defect):
    before=put(tmp_path/'before.json',dict(revision=1));current=put(tmp_path/'tracker.json',dict(revision=2))
    original=dict(path=current['path'],sha256=before['sha256']);selection=dict(tracker=original,workbook='0498')
    profile=put(tmp_path/'profile.json',dict(schema_version='harbor-allowance-takeover-direct/v1',selection=selection))
    stop=put(tmp_path/'stop.json',dict(selection=selection if defect!='selection'else{},before=before if defect!='archive'else current,observed_at=120))
    # The actual takeover archives after.json separately from the live tracker.
    archived_after=put(tmp_path/'after.json',dict(revision=2))
    completion=put(tmp_path/'completion.json',dict(stopped_owner_proof=stop,after=archived_after if defect!='current'else before))
    rt=types.ModuleType('test_original_direct_runtime');rt.checked=lambda row:json.dumps(E.checked(row)).encode()
    calls=[];rt.T=types.SimpleNamespace(validate_completion=lambda row:calls.append(row),_selection=lambda row:row)
    exec("def direct(p):\n checked(p['selection']['tracker'])\n return p,T,None,None\n",rt.__dict__)
    saved=(tmp_path/'tracker.json').read_bytes()
    if defect:
        with pytest.raises(ValueError):E.takeover_context(rt,profile,completion)
    else:
        E.takeover_context(rt,profile,completion);assert calls==[completion]
    assert (tmp_path/'tracker.json').read_bytes()==saved


def test_selected_package_function_and_guard_restore_on_child_failure(tmp_path,monkeypatch):
    import sys
    from scripts import harbor_campaign as C,harbor_instruction_campaign as I
    old=C.worker.environment.readiness.publication
    original=old._reject_external_package_artifacts;guard=C._run_in_process_group
    cached=types.ModuleType('test_retained_release_reference');cached.inspect=original
    monkeypatch.setitem(sys.modules,cached.__name__,cached)
    selected=lambda *args:None
    release=types.SimpleNamespace(_reject_external_package_artifacts=selected)
    hits=[]
    def run(producer,**kwargs):
        def child(command,**options):hits.append((command,options));raise RuntimeError('fixture guard stopped')
        return types.SimpleNamespace(declared_runner=child)
    runner=types.SimpleNamespace(select_run=run)
    q={k:put(tmp_path/(k+'.json'),{})for k in ('helper','runner','release','original_release','policy')}
    p={'package_recovery':put(tmp_path/'package.json',{}),'entry':E.binding(E.__file__)}
    monkeypatch.setattr(E,'package_policy',lambda *args:q)
    monkeypatch.setattr(E,'module',lambda name,row: runner if row==q['runner']else release)
    D=types.SimpleNamespace(__file__=__file__)
    with pytest.raises(RuntimeError,match='fixture guard'):
        with E.package_runtime({},p,D,(C,I)):
            assert old._reject_external_package_artifacts is selected and cached.inspect is selected
            # Real V8 renew reads this exact base object from its env argument.
            C._run_in_process_group(['python','-I','-B',str(__file__),'renew-child','intent','sha'],timeout_seconds=420)
    assert old._reject_external_package_artifacts is original and cached.inspect is original
    assert C._run_in_process_group is guard and hits[0][1]['timeout_seconds']==420


def test_package_dependency_failure_does_not_leak_selected_symbol(tmp_path,monkeypatch):
    from scripts import harbor_campaign as C,harbor_instruction_campaign as I
    old=C.worker.environment.readiness.publication
    original=old._reject_external_package_artifacts;guard=C._run_in_process_group
    q={k:put(tmp_path/(k+'.json'),{})for k in ('helper','runner','release','original_release','policy')}
    p={'package_recovery':put(tmp_path/'package.json',{}),'entry':E.binding(E.__file__)}
    runner=types.SimpleNamespace(select_run=lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('foreign producer')))
    release=types.SimpleNamespace(_reject_external_package_artifacts=lambda:None)
    monkeypatch.setattr(E,'package_policy',lambda *args:q)
    monkeypatch.setattr(E,'module',lambda name,row:runner if row==q['runner']else release)
    with pytest.raises(ValueError,match='foreign'):
        with E.package_runtime({},p,types.SimpleNamespace(__file__=__file__),(C,I)):pass
    assert old._reject_external_package_artifacts is original and C._run_in_process_group is guard


@pytest.mark.parametrize('fault',[None,'run_root','controls','intake-bytes','path','workbook'])
def test_takeover_retains_canonical_config_and_only_intake_placement(tmp_path,fault):
    active_intake=put(tmp_path/'active-intake.json',{'original':'admitted continuation'})
    canonical_intake=put(tmp_path/'canonical-intake.json',{'original':'canonical'})
    canonical=dict(run_root=str(tmp_path),intake=canonical_intake,controls={'bound':'same'},workbook='0315')
    canonical_row=put(tmp_path/'config.json',canonical)
    active=dict(canonical,intake=active_intake)
    if fault in {'run_root','controls','workbook'}:active[fault]='different'
    if fault=='intake-bytes':(tmp_path/'canonical-intake.json').write_text('{}')
    if fault=='path':canonical_row=put(tmp_path/'alias.json',canonical)
    job={'config':put(tmp_path/'active-config.json',active)}
    if fault:
        with pytest.raises(ValueError):E.takeover_config(types.SimpleNamespace(config=canonical_row),job)
    else:E.takeover_config(types.SimpleNamespace(config=canonical_row),job)


@pytest.mark.parametrize('different',[False,True])
def test_actual_takeover_serializer_optional_null_is_not_a_changed_selection(tmp_path,different):
    from scripts.batch import harbor_lease_takeover as T
    before=put(tmp_path/'before.json',{'revision':1});current=put(tmp_path/'tracker.json',{'revision':2})
    old=dict(path=current['path'],sha256=before['sha256'])
    selection=dict(campaign_root=str(tmp_path),workbook='0315',tracker=old,worker_kind='environment',guard={},checkpoint={},owner='owner',controls_migration=None)
    historical=T._selection_value(T._selection(selection));historical.pop('controls_migration',None)
    if different:historical['controls_migration']={'foreign':'policy'}
    profile=put(tmp_path/'profile.json',dict(schema_version='harbor-allowance-takeover-direct/v1',selection=selection))
    stop=put(tmp_path/'stop.json',dict(selection=historical,before=before,observed_at=120));completion=put(tmp_path/'completion.json',dict(stopped_owner_proof=stop,after=current))
    runtime=types.ModuleType('original');runtime.checked=E.checked;runtime.T=types.SimpleNamespace(_selection=T._selection,validate_completion=lambda _:None)
    exec("def direct(p):\n checked(p['selection']['tracker'])\n return p,T,None,None\n",runtime.__dict__)
    if different:
        with pytest.raises(ValueError):E.takeover_context(runtime,profile,completion)
    else:E.takeover_context(runtime,profile,completion)


@pytest.mark.parametrize('fault',[None,'source','capabilities','version','config','takeover','namespace','environment','selection'])
def test_completed_context_retains_original_reader_and_current_checkpoint_responsibility(tmp_path,monkeypatch,fault):
    monkeypatch.setenv('HARBOR_FLEET_URL','fixture-original-url')
    monkeypatch.setenv('PATH',__import__('os').environ.get('PATH',''))
    home=tmp_path/'continuations/done';home.mkdir(parents=True)
    code=put(tmp_path/'code.json',{'root':str(tmp_path/'original-code')})
    config={'run_root':str(tmp_path),'code':code}
    config_row=put(tmp_path/'config.json',config)
    source=put(tmp_path/'source.json',{'original':True})
    old={'config':config_row,'driver':put(tmp_path/'driver.json',{}),'models':True,'commands':True,'containers':True,'publish':False}
    if fault=='capabilities':old['publish']=True
    job={'source':source,'workbook':'0315','version':'original-code'}
    if fault=='source':job['source']={}
    if fault=='version':job['version']='other'
    p={k:put(tmp_path/(k+'.json'),{})for k in E.FIELDS-{'schema_version','workbook','home','operations','owner','source'}}
    p.update(schema_version=E.SCHEMA,workbook='0315',source=source,owner='original-owner',home=str(home),operations=str(tmp_path/'operations'),original_job=put(tmp_path/'job.json',old),original_worker_job=put(tmp_path/'worker.json',job))
    if fault=='namespace':p['home']=str(tmp_path/'missing')
    selected=types.SimpleNamespace(workbook='0315',owner='original-owner',checkpoint=p['checkpoint'],config=config_row)
    if fault=='selection':selected.owner='foreign-owner'
    calls=[]
    def takeover(*args,**kwargs):
        calls.append(('original-takeover',kwargs))
        if fault=='takeover':raise ValueError('original validator rejected')
        return None,None,selected,None
    def canonical(s,o):
        calls.append(('original-config',o))
        if fault=='config':raise ValueError('original config rejected')
        E.takeover_config(s,o)
    env=(None,None,None,None,None,config,tmp_path/('other'if fault=='environment'else'original-code'),None)
    D=types.SimpleNamespace(setup=lambda o:env)
    original=types.SimpleNamespace(SCHEMA=E.SCHEMA,FIELDS=E.FIELDS,checked=E.checked,takeover_context=takeover,takeover_config=canonical,
        select_history=lambda *a,**kw:calls.append(('original-history',kw)),module=lambda name,row:D if row==old['driver']else types.SimpleNamespace(preload_inspect=lambda:None))
    monkeypatch.setattr(E,'module',lambda name,row:original)
    row=put(tmp_path/'plan.json',p)
    if fault:
        with pytest.raises(ValueError):E.completed_context(row,probe=True)
    else:
        result=E.completed_context(row,probe=True)
        assert result==(p,D,env)
        assert calls[0]==('original-takeover',{'current':False})
        assert calls[-1]==('original-history',{'probe':True})

@pytest.mark.parametrize('fault',[None,'controls','intake-bytes'])
def test_cold_profile_child_uses_same_canonical_intake_custody(tmp_path,monkeypatch,fault):
    canonical_intake=put(tmp_path/'canonical-intake.json',{'original':1})
    active_intake=put(tmp_path/'active-intake.json',{'continued':1})
    canonical=dict(run_root=str(tmp_path),intake=canonical_intake,controls={'original':True})
    config=put(tmp_path/'config.json',canonical)
    active=dict(canonical,intake=active_intake)
    if fault=='controls':active['controls']={'foreign':True}
    if fault=='intake-bytes':(tmp_path/'canonical-intake.json').write_text('{}')
    old=dict(config=put(tmp_path/'active-config.json',active),driver=put(tmp_path/'driver.json',{}))
    source=put(tmp_path/'source.json',{'source':1})
    job=dict(workbook='0215',source=source,version='code')
    p={k:{} for k in E.FIELDS};p.update(schema_version=E.SCHEMA,entry=E.binding(E.__file__),workbook='0215',owner='same-owner',source=source,
        original_job=put(tmp_path/'job.json',old),original_worker_job=put(tmp_path/'worker.json',job),profile_recovery={})
    s=types.SimpleNamespace(workbook='0215',owner='same-owner',checkpoint=p['checkpoint'],config=config)
    env=(None,None,None,None,None,active,tmp_path/'code',None)
    D=types.SimpleNamespace(setup=lambda o:env)
    monkeypatch.setattr(E,'module',lambda name,row:D if name=='original_environment_dispatcher' else types.SimpleNamespace(preload_inspect=lambda:None))
    monkeypatch.setattr(E,'takeover_context',lambda *a,**kw:(None,None,s,None))
    calls=[];monkeypatch.setattr(E,'select_history',lambda *a,**kw:calls.append('original-history'))
    row=put(tmp_path/'plan.json',p)
    if fault:
        with pytest.raises(ValueError):E.cold_context(row)
        assert calls==[]
    else:
        assert E.cold_context(row)==(p,D,env)
        assert calls==['original-history']
