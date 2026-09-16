import ast,copy,json
from pathlib import Path
import pytest
from scripts.fleet import smoke_refresh_successor_layout as V
from scripts.fleet import publish_smoke_refresh_successor as P
from scripts.fleet import smoke_publication_refresh_successor as S
from scripts.fleet.migration import binding,checked,retain

@pytest.fixture
def layout(tmp_path):
    root=tmp_path.resolve();code=root/('c'*64);(code/'scripts/batch').mkdir(parents=True)
    rows=[]
    for name in ('xl_mcp_oracle.py','scripts/batch/harbor_environment_worker.py'):
        p=code/name;p.write_text('# exact immutable fixture\n');rows.append(dict(relative=name,sha256=binding(p)['sha256']))
    snapshot=retain(root/'snapshot.json',dict(root=str(code),files=rows))
    original_snapshot=retain(root/'old-snapshot.json',dict(root='/retained/original',files=[]))
    config=retain(root/'original-config.json',dict(workbook='0270',code=original_snapshot,controls={'env_file':'native','guard':8589934592},run_root='/retained/original-attempt',intake={'attempt':1,'limit':2}))
    parent=retain(root/'parent.json',dict(root=str(root/V.PRIOR_VERSION),files=[]))
    profile=retain(root/'profile.json',dict(parent_successor=parent,schema_version='0270-oracle-gate-successor/v1',models_replayed=False,semantic_budget_reset=False,original_snapshot=original_snapshot))
    plan=retain(root/'plan.json',dict(schema_version='0270-oracle-refresh-continuation/v1',native_lease={'path':'/retained/lease.json','sha256':'a'*64},workbook='0270',profile=profile,snapshot=snapshot))
    source=root/'source.xlsx';source.write_bytes(b'immutable source')
    runtime=V.REMOTE/'releases'/V.OLD_RELEASE
    worker=dict(path=str(runtime/'code/scripts/fleet/worker.py'),sha256='a'*64)
    manifest_copy=retain(root/'runtime.json',dict(release=V.OLD_RELEASE,files={'scripts/fleet/worker.py':{'sha256':worker['sha256']}}))
    manifest=dict(path=str(runtime/'code/FLEET_CODE_MANIFEST.json'),sha256=manifest_copy['sha256'])
    job=retain(root/'job.json',dict(workbook='0270',version=code.name,source=binding(source),cwd=str(runtime/'code'),command=[str(runtime/'venv/bin/python')],inputs=[plan,profile,snapshot]))
    probe=retain(root/'probe.json',dict(status='PASS',models_dispatched=0,result={'plan':plan}))
    native=retain(root/'native.json',dict(schema_version='harbor-fleet-successor-runtime-probe/v1',runtime_release=V.OLD_RELEASE,executable=str(runtime/'venv/bin/python'),runtime_manifest=manifest,runtime_worker=worker,authoring_snapshot=snapshot,models_dispatched=0,python_version='3.12.3'))
    prior=retain(root/'prior.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False))
    v=dict(schema_version=V.SCHEMA,workbook='0270',source=binding(source),prior_outcome=prior,root_plan=plan,worker_job=job,authoring_snapshot=snapshot,authoring_version=code.name,profile=profile,native_probe=probe,original_config=config,runtime_release=V.OLD_RELEASE,runtime_manifest=manifest,runtime_manifest_copy=manifest_copy,runtime_python=str(runtime/'venv/bin/python'),runtime_worker=worker,runtime_probe=native)
    b=retain(root/'layout.json',v);return root,b,v

def test_distinct_runtime_and_authoring_versions_keep_original_semantics(layout):
    root,b,v=layout;assert V.validate(b,expected_sha256=b['sha256'])==v
    old=checked(v['original_config']);new={**old,'code':v['authoring_snapshot']};nb=retain(root/'newconfig.json',new)
    assert V.configuration(v,nb)==new and checked(v['original_config'])==old
    assert v['runtime_release']!=v['authoring_version']

@pytest.mark.parametrize('field,value',[('workbook','0190'),('authoring_version',V.OLD_RELEASE),('runtime_release','foreign'),('runtime_python','/usr/bin/python3'),('runtime_worker',{'path':'/unreviewed','sha256':'f'*64})])
def test_rebound_layout_cannot_change_closed_authority(layout,field,value):
    root,b,v=layout;v[field]=value;bad=retain(root/'bad.json',v)
    with pytest.raises((ValueError,KeyError)):V.validate(bad,expected_sha256=bad['sha256'])

def test_unreviewed_new_layout_hash_is_rejected(layout):
    root,b,v=layout
    with pytest.raises(ValueError,match='unreviewed'):V.validate(b,expected_sha256='f'*64)

@pytest.mark.parametrize('section,field,value',[('profile','semantic_budget_reset',True),('profile','models_replayed',True),('native_probe','models_dispatched',1),('native_probe','status','HOLD'),('runtime_probe','python_version','different')])
def test_rebound_proof_cannot_waive_budget_or_runtime_boundary(layout,section,field,value):
    root,b,v=layout;proof=checked(v[section]);proof[field]=value;v[section]=retain(root/'badproof.json',proof);bad=retain(root/'bad.json',v)
    with pytest.raises(ValueError):V.validate(bad,expected_sha256=bad['sha256'])

@pytest.mark.parametrize('field,value',[('workbook','0190'),('run_root','/new-attempt'),('intake',{'attempt':0,'limit':3}),('controls',{'guard':17179869184}),('extra_authority',True)])
def test_config_only_code_delta_never_resets_budget_or_paths(layout,field,value):
    root,b,v=layout;config=checked(v['original_config']);config['code']=v['authoring_snapshot'];config[field]=value
    with pytest.raises(ValueError):V.configuration(v,retain(root/'changed-config.json',config))

def test_immutable_snapshot_tamper_rejected(layout):
    root,b,v=layout;Path(checked(v['authoring_snapshot'])['root'],'xl_mcp_oracle.py').write_text('changed')
    with pytest.raises(ValueError,match='code bytes'):V.validate(b,expected_sha256=b['sha256'])

def test_independent_publisher_embeds_exact_same_layout_validation():
    import inspect
    for name in ('validate','configuration'):
        assert ast.dump(ast.parse(inspect.getsource(getattr(P,name))))==ast.dump(ast.parse(inspect.getsource(getattr(V,name))))

def test_new_publication_schema_cannot_masquerade_as_old_release(layout,monkeypatch):
    root,b,v=layout;publisher=binding(Path(P.__file__).resolve())
    monkeypatch.setattr(S,'LAYOUT_SHA256',b['sha256']);monkeypatch.setattr(S,'PUBLISHER',publisher['sha256'])
    selection=dict(schema_version='harbor-0270-refresh-successor-publication-selection/v1',host='hhu-server',workbook='0270',release=v['authoring_version'],expected_fence=6,publisher=publisher,worker_job=v['worker_job'],worker_process={'path':'/future/process','sha256':'d'*64},verifier={},history={},remote_journal='/future/journal',successor_layout=b)
    assert S.selected_layout(selection)==v
    selection['release']=V.OLD_RELEASE
    with pytest.raises(ValueError):S.selected_layout(selection)

def test_pack_collect_carries_exact_lineage_and_is_idempotent(layout,monkeypatch,tmp_path):
    from test_fleet_publication import qualified_fixture
    root,b,v=layout;taskroot=root/'task-fixture';taskroot.mkdir();_,output,report,pub=qualified_fixture(taskroot)
    original=P.validate
    # The packaging call is a Linux operation; this fixture executes on the
    # root machine, so only its interpreter/remote-path check is substituted.
    monkeypatch.setattr(P,'validate',lambda selected,**kw:V.validate(selected,expected_sha256=b['sha256']))
    package=P.pack(output,report,pub,root/'export',b)
    manifest=checked(package['manifest']);assert manifest['successor_layout']==b
    assert 'evidence/successor-layout.json' in manifest['files']
    first=P.collect(Path(package['archive']['path']),package['manifest'],root/'final',root/'collect1',b)
    assert checked(first)['successor_layout']==b
    second=P.collect(Path(package['archive']['path']),package['manifest'],root/'final',root/'collect2',b)
    assert checked(second)['idempotent_existing_output']
    bad=copy.deepcopy(manifest);bad['successor_layout']={'path':'/foreign','sha256':'e'*64}
    with pytest.raises(ValueError,match='lineage'):
        P.collect(Path(package['archive']['path']),retain(root/'bad-package.json',bad),root/'other',root/'collect3',b)
    assert not (root/'other').exists()

@pytest.mark.parametrize('argv,expected',[
 ([b'python',b'guard.py',b'--job',b'/original/authoring/dispatcher-v2/recovery/job.json'],True),
 ([b'python',b'--job=/original/authoring/dispatcher-v2/recovery/job.json'],True),
 ([b'python',b'/original/authoring/dispatcher-v2/recovery'],True),
 ([b'python',b'-c',b"print('/original/authoring/dispatcher-v2/recovery/job.json')"],False),
 ([b'python',b'--job',b'/original/authoring/dispatcher-v2/recovery-other/job.json'],False),
 ([b'sh',b'-c',b'python /original/authoring/dispatcher-v2/recovery/job.json'],False),
])
def test_orphan_namespace_matches_token_boundaries_even_without_outer_worker(argv,expected):
    from scripts.fleet import smoke_refresh_successor_launch as L
    namespace='/original/authoring/dispatcher-v2/recovery'
    assert S.owned_argv(argv,[namespace]) is expected
    assert L.owned_argv(argv,[namespace]) is expected

def test_remote_publication_spawner_retains_intent_before_failed_launch(tmp_path,monkeypatch):
    import subprocess
    from test_fleet_launch_job import spawn_payload
    payload=spawn_payload(tmp_path)
    def failed(*args,**kwargs):
        assert (tmp_path/'worker.intent.json').is_file()
        raise OSError('synthetic spawn failure')
    monkeypatch.setattr(subprocess,'Popen',failed)
    with pytest.raises(OSError):S.remote_spawn(payload)
    with pytest.raises(ValueError,match='already attempted'):S.remote_spawn(payload)

def test_publication_and_worker_use_identical_tested_once_only_spawn():
    from scripts.fleet import launch_job as L
    assert ast.dump(ast.parse(S.function_source('remote_spawn')))==ast.dump(ast.parse(L.function_source('remote_spawn')))

def test_remote_probes_include_original_guarded_operations_and_exact_worker_run():
    from scripts.fleet import smoke_refresh_successor_launch as L
    source=S.function_source('remote_probe')
    assert 'owning_job["operations"]' in source and 'job["run"]' in source
    assert 'original["run_root"]' in source and 'dispatcher-v2' in source
    assert 'owned_argv(arguments, needles)' in source
    assert 'owned_argv(args,needles)' in L.function_source('remote_preflight')

def test_collector_rejects_rebound_other_workbook_before_output(layout,monkeypatch):
    from test_fleet_publication import qualified_fixture
    root,b,v=layout;taskroot=root/'other-fixture';taskroot.mkdir();_,output,report,pub=qualified_fixture(taskroot)
    monkeypatch.setattr(P,'validate',lambda selected,**kw:V.validate(selected,expected_sha256=b['sha256']))
    package=P.pack(output,report,pub,root/'export-other',b)
    manifest=checked(package['manifest']);manifest['workbook']='0190'
    with pytest.raises(ValueError,match='workbook differs'):
        P.collect(Path(package['archive']['path']),retain(root/'other-manifest.json',manifest),root/'must-not-exist',root/'no-journal',b)
    assert not (root/'must-not-exist').exists()

def test_controlled_publication_rejects_wrong_source_before_claim(layout,monkeypatch):
    root,b,v=layout
    monkeypatch.setattr(P,'validate',lambda selected,**kw:V.validate(selected,expected_sha256=b['sha256']))
    plan=retain(root/'wrong-source-plan.json',dict(schema_version='harbor-fleet-remote-publication/v1',workbook='0270',source_sha256='f'*64,journal=str(root/'no-operation'),successor_layout=b,code_root=checked(v['authoring_snapshot'])['root']))
    with pytest.raises(ValueError,match='publication source differs'):
        P.controlled(plan,action='remote',url='http://127.0.0.1:1',token_file='/never-read',owner='host:publisher',version=v['authoring_version'])
    assert not (root/'no-operation').exists()

@pytest.mark.parametrize('schema,lease',[
    ('0270-oracle-gate-continuation/v1','native_lease'),
    ('0270-oracle-refresh-continuation/v1','lease_renewal'),
])
def test_refresh_layout_rejects_expired_takeover_recipe_even_when_all_outer_bindings_match(layout,schema,lease):
    root,b,v=layout;plan=checked(v['root_plan']);native=plan.pop('native_lease')
    plan.update(schema_version=schema,**{lease:native})
    selected=retain(root/'changed-plan.json',plan);v['root_plan']=selected
    job=checked(v['worker_job']);job['inputs'][0]=selected;v['worker_job']=retain(root/'changed-job.json',job)
    probe=checked(v['native_probe']);probe['result']['plan']=selected;v['native_probe']=retain(root/'changed-probe.json',probe)
    changed=retain(root/'changed-layout.json',v)
    with pytest.raises(ValueError,match='retained-refresh'):V.validate(changed,expected_sha256=changed['sha256'])

@pytest.mark.parametrize('fence',[4,5,7,True])
def test_refresh_publication_requires_exact_next_worker_fence(layout,monkeypatch,fence):
    root,b,v=layout;publisher=binding(Path(P.__file__).resolve())
    monkeypatch.setattr(S,'LAYOUT_SHA256',b['sha256']);monkeypatch.setattr(S,'PUBLISHER',publisher['sha256'])
    selection=dict(schema_version='harbor-0270-refresh-successor-publication-selection/v1',host='hhu-server',workbook='0270',release=v['authoring_version'],expected_fence=fence,publisher=publisher,worker_job=v['worker_job'],worker_process={'path':'/future/process','sha256':'d'*64},verifier={},history={},remote_journal='/future/journal',successor_layout=b)
    with pytest.raises(ValueError,match='custody'):S.selected_layout(selection)
