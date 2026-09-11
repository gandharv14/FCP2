import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

root=Path(__file__).resolve().parents[1]
script=root/'scripts/recalc/maintain_table_runner.py'
if not script.is_file():script=root/'proposal/scripts/recalc/maintain_table_runner.py'
spec=importlib.util.spec_from_file_location('maintain_table_runner',script)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

@pytest.fixture
def case(tmp_path,monkeypatch):
    root=tmp_path/'protected';root.mkdir(mode=0o755)
    install=root/'install';install.mkdir();config=root/'config';config.mkdir()
    monkeypatch.setattr(m,'INSTALL',install);monkeypatch.setattr(m,'CONFIG',config)
    monkeypatch.setattr(m,'JOURNAL',root/'install-journal')
    monkeypatch.setattr(m,'TABLE_JOURNAL',root/'table-journal')
    monkeypatch.setattr(m,'ARTIFACTS',root/'artifacts')
    monkeypatch.setattr(m,'WINDOW_LOCK',root/'offline.lock')
    def protected(path,directory=False):
        # Only root UID/parent protection is replaced for the synthetic layout.
        p=Path(path)
        if p.is_symlink() or (not p.is_dir() if directory else not p.is_file()):raise m.MaintenanceError('unsafe fixture deployment')
        if p.stat().st_mode&0o022:raise m.MaintenanceError('writable fixture deployment')
    monkeypatch.setattr(m,'protected',protected)
    monkeypatch.setattr(m.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=501))
    paths=m.targets();paths['observer'].write_text('# Existing ordinary observer\n');paths['observer'].chmod(0o755)
    public=config/'existing-public.pem';public.write_bytes(b'EXISTING PUBLIC KEY');public.chmod(0o644)
    private=config/'existing-private.pem';private.write_bytes(b'PRIVATE MUST NEVER BE OPENED BY INSTALLER');private.chmod(0o600)
    base={'schema_version':'excel-runner-config/v1','automation_user':'existing-user',
          'runner_implementation':str(paths['observer']),'runner_implementation_sha256':m.hashed(paths['observer']),
          'receipt_public_key':str(public),'receipt_private_key':str(private),'receipt_public_key_sha256':m.hashed(public),
          'isolation_controls':{k:True for k in m.CONTROLS},'unchanged_field':'preserve exact value'}
    attestation={'schema_version':'excel-isolation-attestation/v1',**{k:True for k in m.CONTROLS},
                 **{k:base[k] for k in ('runner_implementation','runner_implementation_sha256','receipt_public_key','receipt_public_key_sha256')},
                 'sandbox_runner_sha256':'unchanged-wrapper-hash'}
    paths['config'].write_bytes(m.canonical(base));paths['attestation'].write_bytes(m.canonical(attestation))
    pipeline=tmp_path/'reviewed';pipeline.mkdir()
    for role,relative in m.SOURCES.items():
        p=pipeline/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# Reviewed '+role+' implementation\n')
    bundle=m.prepare_bundle(pipeline);plan=m.make_plan(bundle,allowed_invoker_uid=501,permitted_versions=['16.112.2'])
    original={role:p.read_bytes() if p.exists() else None for role,p in paths.items()}
    safe_read=m.read_bytes
    def no_private(path):
        if Path(path).resolve()==private.resolve():raise AssertionError('private key must never be opened')
        return safe_read(path)
    monkeypatch.setattr(m,'read_bytes',no_private)
    return dict(root=root,plan=plan,bundle=bundle,paths=paths,original=original,public=public,private=private,base=base,attestation=attestation)


def test_actual_transaction_preserves_keys_config_and_rolls_back(case):
    plan=case['plan'];private_before=m.private_metadata(case['private']);public_before=m.hashed(case['public'])
    # Exercise serialized plan ordering, as in the actual CLI.
    plan=json.loads(m.canonical(plan));result=m.apply_plan(plan)
    assert result['status']=='installed'
    base=m.load(case['paths']['config']);attestation=m.load(case['paths']['attestation'])
    assert {k:v for k,v in base.items() if k!='runner_implementation_sha256'}=={k:v for k,v in case['base'].items() if k!='runner_implementation_sha256'}
    assert {k:v for k,v in attestation.items() if k!='runner_implementation_sha256'}=={k:v for k,v in case['attestation'].items() if k!='runner_implementation_sha256'}
    assert m.private_metadata(case['private'])==private_before and m.hashed(case['public'])==public_before
    table=m.load(case['paths']['table_config'])
    assert table['selected_policy_code']['snapshot_receipt_sha256']==case['bundle']['sources']['recalc']['sha256']
    assert (Path(result['transaction'])/'observer.before').read_bytes()==case['original']['observer']
    assert m.recover(Path(result['transaction']).name)['status']=='rolled_back'
    assert m.private_metadata(case['private'])==private_before and m.hashed(case['public'])==public_before
    for role,path in case['paths'].items():assert (path.read_bytes() if path.exists() else None)==case['original'][role]


@pytest.mark.parametrize('count',range(1,7))
def test_interruption_after_every_replace_is_recoverable(case,monkeypatch,count):
    write=m.write_atomic;seen=[];tx=str(uuid.uuid4())
    def interrupted(path,*args,**kwargs):
        write(path,*args,**kwargs)
        if Path(path) in case['paths'].values():
            seen.append(path)
            if len(seen)==count:raise KeyboardInterrupt('simulated abrupt interruption after replace')
    monkeypatch.setattr(m,'write_atomic',interrupted)
    with pytest.raises(KeyboardInterrupt):m.apply_plan(case['plan'],transaction_id=tx)
    monkeypatch.setattr(m,'write_atomic',write)
    record=m.load(m.JOURNAL/tx/'transaction.json')
    assert record['state']=='interrupted' and 'after replace' in record['error_text']
    m.recover(tx)
    for role,path in case['paths'].items():assert (path.read_bytes() if path.exists() else None)==case['original'][role]


@pytest.mark.parametrize('role',['observer','config','attestation'])
def test_expected_existing_cas_rejects_changed_deployment_before_writes(case,role):
    path=case['paths'][role];path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises((m.MaintenanceError,json.JSONDecodeError)):m.apply_plan(case['plan'])
    assert not m.JOURNAL.exists()


@pytest.mark.parametrize('role',m.SOURCES)
def test_changed_reviewed_source_rejects_before_writes(case,role):
    Path(case['bundle']['sources'][role]['path']).write_text('# changed source\n')
    with pytest.raises(m.MaintenanceError):m.apply_plan(case['plan'])
    assert not m.JOURNAL.exists()


def test_recovery_rejects_unknown_new_bytes_without_partial_restore(case):
    result=m.apply_plan(case['plan']);case['paths']['observer'].write_text('# unrelated newer installation\n')
    before={role:path.read_bytes() for role,path in case['paths'].items()}
    with pytest.raises(m.MaintenanceError,match='unknown current bytes'):m.recover(Path(result['transaction']).name)
    assert {role:path.read_bytes() for role,path in case['paths'].items()}==before


def test_altered_backup_rejects_before_restoring_any_file(case):
    result=m.apply_plan(case['plan']);root=Path(result['transaction']);(root/'observer.before').write_text('altered')
    before={role:path.read_bytes() for role,path in case['paths'].items()}
    with pytest.raises(m.MaintenanceError):m.recover(root.name)
    assert {role:path.read_bytes() for role,path in case['paths'].items()}==before


def test_private_key_metadata_change_stops_recovery_without_reading_key(case):
    result=m.apply_plan(case['plan']);case['private'].chmod(0o640)
    with pytest.raises(m.MaintenanceError):m.recover(Path(result['transaction']).name)


def test_public_key_change_stops_recovery(case):
    result=m.apply_plan(case['plan']);case['public'].write_bytes(b'changed public key')
    with pytest.raises(m.MaintenanceError):m.recover(Path(result['transaction']).name)


@pytest.mark.parametrize('versions',[[],['*'],['16.*'],[''],['16.112.2','16.112.2']])
def test_invalid_version_policy_rejected(case,versions):
    with pytest.raises(m.MaintenanceError):m.make_plan(case['bundle'],allowed_invoker_uid=501,permitted_versions=versions)


def test_wrong_dedicated_uid_rejected(case):
    with pytest.raises(m.MaintenanceError):m.make_plan(case['bundle'],allowed_invoker_uid=502,permitted_versions=['16.112.2'])


def test_transaction_uuid_cannot_be_reused(case):
    tx=str(uuid.uuid4());m.apply_plan(case['plan'],transaction_id=tx);m.recover(tx)
    with pytest.raises(FileExistsError):m.apply_plan(case['plan'],transaction_id=tx)


def test_no_network_subprocess_or_key_generation_api():
    source=Path(m.__file__).read_text()
    assert 'import subprocess' not in source and 'genpkey' not in source
    assert 'sudoers' not in source and 'ifconfig' not in source


def test_system_python_isolated_help_loads_without_pipeline_dependencies():
    import subprocess
    result=subprocess.run(['/usr/bin/python3','-I',m.__file__,'--help'],capture_output=True,text=True)
    assert result.returncode==0 and 'recover' in result.stdout


def test_abrupt_process_exit_before_journal_progress_is_recoverable(case):
    import multiprocessing
    import os
    tx=str(uuid.uuid4());write=m.write_atomic
    def child():
        def crash(path,*args,**kwargs):
            write(path,*args,**kwargs)
            if Path(path)==case['paths']['observer']:os._exit(77)
        m.write_atomic=crash
        m.apply_plan(case['plan'],transaction_id=tx)
    process=multiprocessing.get_context('fork').Process(target=child)
    process.start();process.join(10)
    if process.is_alive():process.kill();process.join();pytest.fail('bounded fixture did not exit')
    assert process.exitcode==77
    record=m.load(m.JOURNAL/tx/'transaction.json')
    assert record['state']=='prepared' and record['completed']==[]
    assert case['paths']['observer'].read_bytes()!=case['original']['observer']
    m.recover(tx)
    for role,path in case['paths'].items():assert (path.read_bytes() if path.exists() else None)==case['original'][role]


def test_hash_review_and_parse_use_one_exact_read(case,tmp_path,monkeypatch):
    path=tmp_path/'plan.json';data=m.canonical(case['plan']);path.write_bytes(data)
    calls=[];read=m.read_bytes
    def once(p):
        calls.append(p);result=read(p)
        Path(p).write_text('{"attacker":"replacement after reviewed read"}')
        return result
    monkeypatch.setattr(m,'read_bytes',once)
    loaded=m.load_bound(path,m.hashlib.sha256(data).hexdigest())
    assert loaded==case['plan'] and calls==[path]


def test_post_install_check_binds_all_installed_files_and_existing_key(case):
    result=m.apply_plan(case['plan']);tx=Path(result['transaction']).name
    assert m.check_transaction(tx)['status']=='installed_files_verified'
    case['paths']['window'].write_text('unexpected newer window')
    with pytest.raises(m.MaintenanceError):m.check_transaction(tx)


def test_misconfigured_public_private_alias_rejects_without_opening_private(case):
    base=m.load(case['paths']['config']);base['receipt_public_key']=str(case['private'])
    case['paths']['config'].write_bytes(m.canonical(base))
    with pytest.raises(m.MaintenanceError,match='alias'):m.make_plan(case['bundle'],allowed_invoker_uid=501,permitted_versions=['16.112.2'])


@pytest.mark.parametrize('change',['destination','payload','key_reference'])
def test_rehashed_plan_cannot_select_arbitrary_mutation(case,change):
    plan=copy.deepcopy(case['plan'])
    if change=='destination':plan['files']['window']['path']=str(case['private'])
    elif change=='payload':plan['files']['window']['data_base64']=m.base64.b64encode(b'forged').decode()
    else:plan['expected_deployment']['private_key_metadata']['path']=str(case['public'])
    plan['plan_sha256']=m.digest({k:v for k,v in plan.items() if k!='plan_sha256'})
    with pytest.raises(m.MaintenanceError):m.apply_plan(plan)
    assert not m.JOURNAL.exists()
