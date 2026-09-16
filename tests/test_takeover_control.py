from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import takeover_control as T

def fixture():
    p=dict(workbook='0177',expected_owner='old',expected_fence=3,source_sha256='s',version='v')
    w=dict(workbook='0177',owner='old',fence=3,source_sha256='s',version='v',state='hold')
    return p,dict(workbooks=[w],operations=[])

@pytest.mark.parametrize('state',['active','hold'])
def test_uncertain_or_active_operation_cannot_takeover(state):
    p,s=fixture();s['operations']=[dict(workbook='0177',state=state)]
    with pytest.raises(ValueError,match='reconcile first'):T.gate(s,p)

def test_unrelated_quarantine_does_not_block_safe_native_custody():
    p,s=fixture();s['operations']=[dict(workbook='0413',state='hold')]
    assert T.gate(s,p)==s['workbooks'][0]

@pytest.mark.parametrize('change',[dict(state='ready'),dict(state='active'),dict(fence=4),dict(source_sha256='other')])
def test_changed_custody_cannot_takeover(change):
    p,s=fixture();s['workbooks'][0].update(change)
    with pytest.raises(ValueError,match='custody changed'):T.gate(s,p)

def test_uncertain_transport_is_retained_and_not_replayed(tmp_path,monkeypatch):
    p,status=fixture();database=tmp_path/'fleet.sqlite3';database.touch()
    p['coordinator_receipt']={};receipt=dict(url='unused',private_directory=str(tmp_path),database=str(database))
    monkeypatch.setattr(T,'load',lambda _:p);monkeypatch.setattr(T,'checked',lambda _:receipt)
    monkeypatch.setattr(T,'Client',lambda *a:SimpleNamespace(call=lambda _:status))
    calls=[]
    def uncertain(*args):calls.append(args);raise TimeoutError('response lost')
    monkeypatch.setattr(T,'transport',uncertain);journal=tmp_path/'operation'
    with pytest.raises(TimeoutError):T.run({},journal,execute=True)
    assert (journal/'intent.json').exists()and(journal/'reconciliation.json').exists()
    with pytest.raises(ValueError,match='reconcile without replay'):T.run({},journal,execute=True)
    assert len(calls)==1

def test_root_takeover_uses_same_database_mutex_as_authoring(tmp_path,monkeypatch):
    p,status=fixture();database=tmp_path/'fleet.sqlite3';database.touch()
    p['coordinator_receipt']={};receipt=dict(url='unused',private_directory=str(tmp_path),database=str(database))
    monkeypatch.setattr(T,'load',lambda _:p);monkeypatch.setattr(T,'checked',lambda _:receipt)
    monkeypatch.setattr(T,'Client',lambda *a:SimpleNamespace(call=lambda _:status))
    with T.lock(T.admission_path(receipt)):
        with pytest.raises(BlockingIOError):T.run({},tmp_path/'operation',execute=True)
    assert not(tmp_path/'operation').exists()

@pytest.mark.parametrize('fault',[None,'source','version','config'])
def test_authenticated_native_source_and_code_must_match_central_custody(fault):
    p,_=fixture();before=dict(workbook_id='0177',bindings=dict(original_raw_source=dict(sha256='s')))
    snapshot=dict(root='/snapshots/v');records=dict(snapshot=snapshot,config=dict(code='snapshot'))
    profile=dict(original_snapshot='snapshot',selection=dict(config='config'))
    if fault=='source':before['bindings']['original_raw_source']['sha256']='other'
    if fault=='version':p['version']='other'
    if fault=='config':records['config']['code']='different';records['different']=dict(root='/snapshots/other')
    if fault:
        with pytest.raises(ValueError):T.native_identity(p,profile,before,records.__getitem__)
    else:T.native_identity(p,profile,before,records.__getitem__)

def test_preflight_uses_owning_nested_configured_takeover_directory(tmp_path):
    home=tmp_path/'authoring'/'authoring'
    original=SimpleNamespace(_layout=lambda selected:(None,home,None,None))
    wrong=tmp_path/'authoring'/'lease-takeovers'/'new'
    with pytest.raises(ValueError,match='dedicated root'):
        T.native_directory(dict(native_directory=str(wrong)),object(),original)
    correct=home/'lease-takeovers'/'new'
    assert T.native_directory(dict(native_directory=str(correct)),object(),original)==correct
    correct.mkdir(parents=True)
    with pytest.raises(ValueError,match='reconcile original journal'):
        T.native_directory(dict(native_directory=str(correct)),object(),original)

@pytest.mark.parametrize('fault',[None,'worker','guard','code','missing','extra','symlink','duplicate','environment','original_config'])
def test_declared_selected_snapshot_retains_original_config_and_actual_guard(tmp_path,fault):
    import hashlib
    p,_=fixture();root=tmp_path/'v';root.mkdir();file=root/'worker.py';file.write_text('exact source')
    selected=dict(root=str(root),files=[dict(relative='worker.py',sha256=hashlib.sha256(file.read_bytes()).hexdigest())])
    old=dict(root='/original/old');p['selected_snapshot']='selected'
    before=dict(workbook_id='0177',bindings=dict(original_raw_source=dict(sha256='s')))
    records=dict(snapshot=old,config=dict(code='snapshot'),selected=selected,
        checkpoint=dict(descriptor='descriptor'),descriptor=dict(worker=dict(root=str(root)),code='original-I'),
        guard=dict(intent='intent'),intent=dict(action='step',arguments=dict(descriptor='descriptor',checkpoint='checkpoint'),code='original-I'))
    profile=dict(original_snapshot='snapshot',selection=dict(config='config',worker_kind='instruction',checkpoint='checkpoint',guard='guard'))
    if fault=='worker':records['descriptor']['worker']['root']='/other'
    if fault=='guard':records['intent']['arguments']['checkpoint']='foreign'
    if fault=='code':records['intent']['code']='foreign'
    if fault=='missing':file.unlink()
    if fault=='extra':(root/'extra.py').write_text('unexpected')
    if fault=='symlink':(root/'link.py').symlink_to(file)
    if fault=='duplicate':selected['files'].append(selected['files'][0])
    if fault=='environment':profile['selection']['worker_kind']='environment'
    if fault=='original_config':records['config']['code']='selected'
    if fault:
        with pytest.raises(ValueError):T.native_identity(p,profile,before,records.__getitem__)
    else:
        T.native_identity(p,profile,before,records.__getitem__)
        assert records['config']['code']=='snapshot'and records['snapshot']==old

@pytest.mark.parametrize('fault',[None,'config','guard','workbook','kind','cycle','unlisted','intent','extra'])
def test_consecutive_takeover_retains_original_instruction_code(tmp_path,fault):
    import hashlib,copy
    p,_=fixture();root=tmp_path/'v';root.mkdir();f=root/'worker.py';f.write_text('original')
    p['selected_snapshot']='selected'
    before=dict(workbook_id='0177',bindings=dict(original_raw_source=dict(sha256='s')))
    records=dict(snapshot=dict(root='/original/old'),config=dict(code='snapshot'),
        selected=dict(root=str(root),files=[dict(relative='worker.py',sha256=hashlib.sha256(f.read_bytes()).hexdigest())]),
        checkpoint=dict(descriptor='descriptor'),descriptor=dict(worker=dict(root=str(root)),code='original-I'),
        guard=dict(intent='intent'),intent=dict(action='step',arguments=dict(descriptor='descriptor',checkpoint='checkpoint'),code='original-I'))
    selection=dict(config='config',guard='guard',workbook='0177',worker_kind='instruction',checkpoint='checkpoint',prior_completion=None)
    records['stop1']=dict(selection=copy.deepcopy(selection))
    records['done1']=dict(schema_version='harbor-expired-lease-takeover/v1',stopped_owner_proof='stop1',after='after1',lease={})
    records['stop2']=dict(selection=dict(selection,checkpoint='done1',prior_completion='done1'))
    records['done2']=dict(schema_version='harbor-expired-lease-takeover/v1',stopped_owner_proof='stop2',after='after2',lease={})
    profile=dict(original_snapshot='snapshot',selection=dict(selection,checkpoint='done2',prior_completion='done2'))
    if fault in ('config','guard','workbook'):records['stop1']['selection'][fault]='foreign'
    elif fault=='kind':records['stop1']['selection']['worker_kind']='environment'
    elif fault=='cycle':records['stop1']['selection'].update(checkpoint='done2',prior_completion='done2')
    elif fault=='unlisted':records['stop2']['selection']['prior_completion']='foreign'
    elif fault=='intent':records['intent']['arguments']['checkpoint']='done2'
    elif fault=='extra':records['done1']['inferred']=True
    if fault:
        with pytest.raises(ValueError):T.native_identity(p,profile,before,records.__getitem__)
    else:T.native_identity(p,profile,before,records.__getitem__)

@pytest.mark.parametrize('fault', [None,'owner','fence','source','version','host','historical_config','selected_config','snapshot','kind','job'])
def test_environment_successor_uses_actual_stopped_launch_without_rewriting_history(tmp_path,fault):
    import hashlib,copy
    p,_=fixture();p.update(host='hhu-server-7',selected_snapshot='selected',selected_environment='edge')
    root=tmp_path/'v';root.mkdir();f=root/'module.py';f.write_text('exact')
    selected=dict(root=str(root),files=[dict(relative='module.py',sha256=hashlib.sha256(f.read_bytes()).hexdigest())])
    old=dict(root='/historical/base');config=dict(code='original',workbook='0177',guard_seconds=3300)
    before=dict(workbook_id='0177',bindings=dict(original_raw_source=dict(sha256='s')))
    profile=dict(original_snapshot='original',selection=dict(config='config',worker_kind='environment'))
    records=dict(original=old,config=config,selected=selected,
        edge=dict(schema_version='fleet-launched-environment-code/v1',launch='launch'),
        launch=dict(automatic_retry=False,intent='intent',remote=dict(root_intent='intent',job='job')),
        intent=dict(plan='plan',remote=dict(job='job')),
        plan=dict(root_plan='continuation',job='job',host='hhu-server-7',new_owner='old',expected_fence=2),
        job=dict(workbook='0177',source=dict(sha256='s'),version='v'),
        continuation=dict(workbook='0177',terminal_rejection=dict(successor='successor'),code_root=str(root)),
        successor=dict(schema_version='normalization-terminal-code-successor/v1',original_snapshot='original',original_config='config',snapshot='selected',config='newconfig'),
        newconfig=dict(config,code='selected'))
    unchanged=copy.deepcopy(config)
    if fault=='owner':records['plan']['new_owner']='other'
    elif fault=='fence':records['plan']['expected_fence']=1
    elif fault=='source':records['job']['source']['sha256']='foreign'
    elif fault=='version':records['job']['version']='foreign'
    elif fault=='host':records['plan']['host']='hhu-server'
    elif fault=='historical_config':records['successor']['original_config']='foreign'
    elif fault=='selected_config':records['newconfig']['guard_seconds']=1
    elif fault=='snapshot':records['successor']['snapshot']='foreign'
    elif fault=='kind':profile['selection']['worker_kind']='instruction'
    elif fault=='job':records['intent']['remote']['job']='foreign'
    if fault:
        with pytest.raises(ValueError):T.native_identity(p,profile,before,records.__getitem__)
    else:T.native_identity(p,profile,before,records.__getitem__)
    assert config==unchanged

@pytest.mark.parametrize('fault',[None,'guard','failed','uncertain','result','predecessor','success','retry'])
def test_completed_dialogue_rejection_keeps_owning_predecessor_guard(tmp_path,fault):
    import hashlib
    p,_=fixture();root=tmp_path/'v';root.mkdir();f=root/'worker.py';f.write_text('unchanged')
    p.update(selected_snapshot='selected',completed_checkpoint='outcome')
    records=dict(snapshot=dict(root='/old'),config=dict(code='snapshot'),
        selected=dict(root=str(root),files=[dict(relative='worker.py',sha256=hashlib.sha256(f.read_bytes()).hexdigest())]),
        descriptor=dict(worker=dict(root=str(root)),code='original-I'),
        checkpoint=dict(descriptor='descriptor',status='DIALOGUE_REPAIR_REQUIRED',next_step='dialogue-review',publication_performed=False,evidence=['previous']),
        guard=dict(intent='intent',returncode=0,failure=None),
        intent=dict(action='step',arguments=dict(descriptor='descriptor',checkpoint='previous'),code='original-I'),
        outcome=dict(status='COMPLETED_OPERATION',resource='guard',result='checkpoint',automatic_retry=False))
    profile=dict(original_snapshot='snapshot',selection=dict(config='config',worker_kind='instruction',checkpoint='checkpoint',guard='guard'))
    before=dict(workbook_id='0177',bindings=dict(original_raw_source=dict(sha256='s')))
    if fault=='guard':records['outcome']['resource']='foreign'
    elif fault=='failed':records['guard']['returncode']=1
    elif fault=='uncertain':records['guard']['failure']='Timeout'
    elif fault=='result':records['outcome']['result']='foreign'
    elif fault=='predecessor':records['checkpoint']['evidence']=['foreign']
    elif fault=='success':records['checkpoint']['status']='PUBLICATION_READY'
    elif fault=='retry':records['outcome']['automatic_retry']=True
    if fault:
        with pytest.raises(ValueError):T.native_identity(p,profile,before,records.__getitem__)
    else:T.native_identity(p,profile,before,records.__getitem__)
