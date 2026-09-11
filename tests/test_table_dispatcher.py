import base64
import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT=Path(__file__).resolve().parents[1]
PROPOSAL=ROOT/'proposal' if (ROOT/'proposal/scripts/recalc/table_quarantine_runner.py').is_file() else ROOT
sys.path.insert(0,str(PROPOSAL))
import xl_table_recalc_quarantine as q
from scripts.recalc import excel_runner as base
spec=importlib.util.spec_from_file_location('table_dispatcher',PROPOSAL/'scripts/recalc/table_quarantine_runner.py');d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
spec=importlib.util.spec_from_file_location('table_fixture',Path.cwd()/'tests/test_table_recalc_observation.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)

@pytest.fixture
def case(tmp_path,monkeypatch):
    key=Ed25519PrivateKey.generate();pub=tmp_path/'pub.pem';pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo));pub.chmod(0o600)
    protected=q.recalc._trusted_public_key;monkeypatch.setattr(q.recalc,'_trusted_public_key',lambda p:protected(p,require_root_owner=False))
    uid=os.getuid();journal=tmp_path/'journal';journal.mkdir();artifacts=tmp_path/'artifacts';artifacts.mkdir();excel=tmp_path/'Microsoft Excel.app';excel.mkdir()
    producer=q.file_hash(d.__file__);source=f.fixture(tmp_path/'original.xlsx');identity=str(uuid.uuid4());output=artifacts/identity/'0001.xlsx'
    request=q.make_request(source,workbook='0001',output=output,producer_sha256=producer,public_key=pub,engine_version='16.test',created_at_ns=time.time_ns()-10**9,request_id=identity)
    path=tmp_path/'request.json';path.write_bytes(q.canonical(request));path.chmod(0o600)
    config={'artifact_root':str(artifacts),'journal_root':str(journal),'allowed_invoker_uid':uid,'permitted_engine_versions':['16.test'],'selected_policy_code':q.code_identity(producer)}
    base_config={'receipt_public_key_sha256':q.file_hash(pub),'isolation_controls':{k:True for k in d.CONTROLS},'timeout_seconds':5,'excel_app':str(excel)}
    events=[]
    def automation(base,app,original,stage,timeout):
        events.append('automation');shutil.copyfile(original,stage)
        f.edit(stage,lambda t,p:f.set_value(f.cells(t)['E3'],'19'))
        return '16.test'
    @contextlib.contextmanager
    def network():
        events.append('isolate')
        try:yield
        finally:events.append('restore')
    def signer(payload,config):
        events.append('sign');return base64.b64encode(key.sign(d.canonical(payload))).decode()
    # Pure synthetic fixture: never call real platform, network, Excel or signer.
    monkeypatch.setattr(d.platform,'system',lambda:'Darwin');monkeypatch.setattr(d.os,'geteuid',lambda:0)
    monkeypatch.setattr(base,'validate_root_directory',lambda p:None)
    args={'config':config,'base':base,'base_config':base_config,'invoking_uid':uid,'automation':automation,'network_check':lambda:True,'network_enforcer':network,'signer':signer}
    return {'args':args,'request':request,'request_path':path,'source':source,'output':output,'key':key,'pub':pub,'events':events,'config':config,'tmp':tmp_path}

def run(case):return d.run_request(case['request_path'],**case['args'])
def replace_request(case,change):
    req=copy.deepcopy(case['request']);change(req);req['request_sha256']=q.digest({k:v for k,v in req.items() if k!='request_sha256'});case['request_path'].write_bytes(q.canonical(req));return req

def test_standalone_observations_match_pipeline_and_atomic_bundle(case):
    result=run(case);bundle=Path(result['bundle']);receipt=d.read_json(bundle/'producer-receipt.json')
    args={'source':case['source'],'staged':bundle/'excel-staged.xlsx','output':case['output'],'producer_sha256':q.file_hash(d.__file__),'public_key':case['pub']}
    packaged=q.assemble_result(case['request'],receipt,**args)
    assert q.validate_result(packaged,case['request'],**args)['source_approval'] is False
    assert packaged['derived_evidence']['authority']=='pipeline_derived_not_runner_attested'
    assert not any('snapshot' in k or 'health' in k for k in receipt['signed_payload'])
    assert case['events']==['isolate','automation','sign','restore']
    assert sorted(p.name for p in bundle.iterdir())==['0001.xlsx','excel-staged.xlsx','original.xlsx','producer-receipt.json','request.json','table-observations.json']
    assert (Path(case['config']['journal_root'])/case['request']['request_id']/'completed.json').is_file()
    assert result['scope']=='quarantine_only'

@pytest.mark.parametrize('failure',['automation','network','signature','geometry','primitive','source','expiry'])
def test_failure_restores_isolation_retains_reservation_and_never_publishes(case,failure):
    original=case['args']['automation']
    def bad_automation(*a):
        result=original(*a);stage=a[3]
        if failure=='automation':raise RuntimeError('fixture failure')
        if failure=='geometry':f.edit(stage,lambda t,p:f.cells(t)['D3'].find('{*}f').set('ref','D3:F4'))
        if failure=='primitive':f.edit(stage,lambda t,p:f.set_value(f.cells(t)['A1'],'7'))
        if failure=='source':f.edit(case['source'],lambda t,p:f.set_value(f.cells(t)['A1'],'8'))
        if failure=='expiry':case['request']['expires_at_ns']=0 # actual request local remains; use patched clock
        return result
    case['args']['automation']=bad_automation
    if failure=='network':case['args']['network_check']=lambda:False
    if failure=='signature':case['args']['signer']=lambda *a:(_ for _ in ()).throw(RuntimeError('sign fixture failure'))
    if failure=='expiry':
        req=replace_request(case,lambda r:r.update(expires_at_ns=time.time_ns()+10**9))
        def expire(*a):
            value=original(*a);time.sleep(1.05);return value
        case['args']['automation']=expire
    with pytest.raises((RuntimeError,ValueError)):
        run(case)
    assert not case['output'].parent.exists()
    reservation=Path(case['config']['journal_root'])/case['request']['request_id']
    assert (reservation/'reservation.json').is_file() and (reservation/'failed.json').is_file()
    if failure!='network':assert case['events'][-1]=='restore'
    with pytest.raises((RuntimeError,ValueError)):run(case)


def test_completed_id_replay_and_rehashed_changed_same_id_are_rejected(case):
    run(case);calls=case['events'].count('automation')
    with pytest.raises(RuntimeError):run(case)
    replace_request(case,lambda r:r.update(original_health_sha256='b'*64))
    with pytest.raises(RuntimeError):run(case)
    assert case['events'].count('automation')==calls


def test_interrupted_reservation_cannot_be_replayed(case):
    directory=d.reserve(case['config']['journal_root'],case['request'],d.file_hash(case['request_path']))
    assert not (directory/'completed.json').exists()
    with pytest.raises(RuntimeError,match='replay or interrupted'):run(case)
    assert case['events']==[]

@pytest.mark.parametrize('field,value',[('code_identity',{}),('source_approval',1),('output_path','/tmp/escape.xlsx'),('source_sha256','b'*64),('engine_version','unapproved'),('original_observation',{}),('request_id','bad'),('created_at_ns',True)])
def test_untrusted_request_cannot_select_profile_or_paths(case,field,value):
    replace_request(case,lambda r:r.update({field:value}))
    with pytest.raises((RuntimeError,ValueError)):run(case)
    assert case['events']==[]


def test_changed_unsigned_derived_evidence_is_independently_rejected(case):
    bundle=Path(run(case)['bundle']);receipt=d.read_json(bundle/'producer-receipt.json')
    args=dict(source=case['source'],staged=bundle/'excel-staged.xlsx',output=case['output'],producer_sha256=q.file_hash(d.__file__),public_key=case['pub'])
    result=q.assemble_result(case['request'],receipt,**args)
    result['derived_evidence']['original_snapshot_sha256']='b'*64;result['result_sha256']=q.digest({k:v for k,v in result.items() if k!='result_sha256'})
    with pytest.raises(ValueError,match='unsigned derived'):q.validate_result(result,case['request'],**args)


def test_real_system_python_can_load_cli_without_pipeline_imports():
    completed=subprocess.run(['/usr/bin/python3','-I',d.__file__,'--help'],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0 and '--request' in completed.stdout
    source=Path(d.__file__).read_text();assert 'import xl_' not in source and 'openpyxl' not in source


def test_secure_config_path_validation_is_not_bypassed(tmp_path):
    path=tmp_path/'config.json';path.write_text('{}')
    with pytest.raises(RuntimeError,match='unprotected'):d.load_deployment(path)


def test_atomic_reservation_race_allows_one_process(tmp_path):
    journal=tmp_path/'journal';journal.mkdir();identity=str(uuid.uuid4())
    script=tmp_path/'race.py';script.write_text('''import importlib.util,sys
spec=importlib.util.spec_from_file_location('d',sys.argv[1]);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
try:d.reserve(sys.argv[2],{'request_id':sys.argv[3],'request_sha256':'a'*64},'b'*64)
except (d.DispatchError,FileExistsError):raise SystemExit(3)
''')
    argv=[sys.executable,'-I',str(script),d.__file__,str(journal),identity]
    children=[subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(4)]
    try:codes=[p.wait(timeout=10) for p in children]
    finally:
        for child in children:
            if child.poll() is None:child.kill();child.wait(timeout=5)
    assert sorted(codes)==[0,3,3,3]

def test_abrupt_process_exit_leaves_durable_no_replay_record(case):
    script=case['tmp']/'crash.py';script.write_text('''import importlib.util,sys,os,json
spec=importlib.util.spec_from_file_location('d',sys.argv[1]);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
r=d.read_json(sys.argv[3]);d.reserve(sys.argv[2],r,d.file_hash(sys.argv[3]));os._exit(7)
''')
    completed=subprocess.run([sys.executable,'-I',str(script),d.__file__,case['config']['journal_root'],str(case['request_path'])],timeout=10)
    assert completed.returncode==7
    with pytest.raises(RuntimeError,match='replay or interrupted'):run(case)
    assert case['events']==[]


def test_crash_after_atomic_bundle_commit_never_reexecutes(case,monkeypatch):
    real=d.write_new
    def fail(path,data,*args,**kwargs):
        if Path(path).name=='completed.json':raise OSError('simulated journal-completion failure')
        return real(path,data,*args,**kwargs)
    monkeypatch.setattr(d,'write_new',fail)
    with pytest.raises(OSError):run(case)
    assert (case['output'].parent/'producer-receipt.json').is_file()
    assert (case['output'].parent/'excel-staged.xlsx').is_file()
    assert (case['output'].parent/'table-observations.json').is_file()
    with pytest.raises(RuntimeError):run(case)
    assert case['events'].count('automation')==1


@pytest.mark.parametrize('signal_failure',[False,True])
def test_offline_window_restores_only_interfaces_it_downed(tmp_path,monkeypatch,signal_failure):
    spec=importlib.util.spec_from_file_location('window',PROPOSAL/'scripts/recalc/table_quarantine_window.py');window=importlib.util.module_from_spec(spec);spec.loader.exec_module(window)
    calls=[];probes=iter(['lo0 en0 bridge0','lo0'])
    monkeypatch.setattr(window.subprocess,'check_output',lambda *a,**k:next(probes))
    def call(argv,**kwargs):
        calls.append(argv)
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr(window.subprocess,'run',call);monkeypatch.setattr(window.time,'sleep',lambda n:None)
    if signal_failure:
        with pytest.raises(KeyboardInterrupt):
            with window.offline_interfaces():raise KeyboardInterrupt()
    else:
        with window.offline_interfaces():pass
    assert calls==[['/sbin/ifconfig','en0','down'],['/sbin/ifconfig','bridge0','down'],['/sbin/ifconfig','en0','up'],['/sbin/ifconfig','bridge0','up']]


def test_pipeline_runtime_failure_precedes_interface_changes(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('window',PROPOSAL/'scripts/recalc/table_quarantine_window.py');window=importlib.util.module_from_spec(spec);spec.loader.exec_module(window)
    calls=[]
    def call(argv,**kwargs):calls.append(argv);return type('Result',(),{'returncode':1})()
    monkeypatch.setattr(window.subprocess,'run',call)
    with pytest.raises(RuntimeError,match='networking was not changed'):
        window.persistent_runtime_precheck(['/usr/bin/sudo','-n','-u','synthetic','--','/missing/python'],tmp_path)
    assert len(calls)==1 and not any('ifconfig' in s for s in calls[0])


def test_system_python_loads_protected_window_without_pipeline_imports():
    path=PROPOSAL/'scripts/recalc/table_quarantine_window.py'
    result=subprocess.run(['/usr/bin/python3','-I',str(path),'--help'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and '--pipeline-python' in result.stdout


def test_lock_fd_rejects_wrong_file(tmp_path,monkeypatch):
    expected=tmp_path/'lock';expected.touch();other=tmp_path/'other';other.touch()
    monkeypatch.setattr(d,'SHARED_WINDOW_LOCK',expected)
    fd=os.open(other,os.O_RDWR)
    try:
        with pytest.raises(RuntimeError):d.validate_inherited_window_lock(fd)
    finally:os.close(fd)

@pytest.mark.parametrize('change',[None,'producer','observer','uid','versions','roots','schema'])
def test_protected_deployment_contract_checks_all_selected_code_and_roots(tmp_path,monkeypatch,change):
    # A deliberately tiny protected-dependency fixture tests the loader contract;
    # it cannot run Excel and is never installed. Only filesystem ownership is
    # relaxed, analogous to fixture public-key ownership in signature tests.
    dependency=tmp_path/'base.py';dependency.write_text("def load_config(path):\n    return {'runner_implementation_sha256':'PLACEHOLDER'}\n")
    # Avoid a self-hash recursion in the fixture function by reading its own
    # actual bytes, exactly as the real base loader reads configured bytes.
    dependency.write_text("import hashlib\ndef load_config(path):\n    return {'runner_implementation_sha256':hashlib.sha256(open(__file__,'rb').read()).hexdigest()}\n")
    journal=tmp_path/'j';journal.mkdir();artifacts=tmp_path/'a';artifacts.mkdir()
    config={'schema_version':'excel-table-quarantine-config/v1','base_config':str(tmp_path/'base.json'),'base_module':str(dependency),'base_module_sha256':d.file_hash(dependency),'dispatcher_sha256':d.file_hash(d.__file__),
            'selected_policy_code':{'trusted_producer_sha256':d.file_hash(d.__file__),'observation_sha256':d.file_hash(dependency),'profile_sha256':'b'*64,'snapshot_receipt_sha256':'c'*64,'health_sha256':'d'*64},
            'journal_root':str(journal),'artifact_root':str(artifacts),'allowed_invoker_uid':501,'permitted_engine_versions':['16.test']}
    if change=='producer':config['selected_policy_code']['trusted_producer_sha256']='f'*64
    elif change=='observer':config['selected_policy_code']['observation_sha256']='f'*64
    elif change=='uid':config['allowed_invoker_uid']=True
    elif change=='versions':config['permitted_engine_versions']=['*']
    elif change=='roots':config['artifact_root']=config['journal_root']
    elif change=='schema':config['schema_version']='ordinary'
    path=tmp_path/'config.json';path.write_bytes(d.canonical(config));checked=[]
    monkeypatch.setattr(d,'protected',lambda path,directory=False:checked.append((Path(path),directory)))
    if change is None:
        accepted,base_module,base_config=d.load_deployment(path)
        assert accepted==config and len(checked)>=5
    else:
        with pytest.raises(RuntimeError):d.load_deployment(path)

def test_original_basename_is_preserved_during_excel_observation(case):
    renamed=case['source'].with_name('0001.xlsx');case['source'].rename(renamed);case['source']=renamed
    request=q.make_request(renamed,workbook='0001',output=case['output'],producer_sha256=q.file_hash(d.__file__),public_key=case['pub'],engine_version='16.test',created_at_ns=time.time_ns()-10**9,request_id=case['request']['request_id'])
    case['request']=request;case['request_path'].write_bytes(q.canonical(request))
    original=case['args']['automation']
    def observe(*args):
        assert args[2].name=='0001.xlsx'
        return original(*args)
    case['args']['automation']=observe
    result=run(case)
    assert (Path(result['bundle'])/'original.xlsx').read_bytes()==renamed.read_bytes()
    assert case['output'].is_file()


def test_inherited_shared_lock_description_passes_but_separate_open_rejects(tmp_path,monkeypatch):
    path=tmp_path/'shared.lock';path.touch()
    class RootWitness:
        def lstat(self):
            fields=list(path.lstat());fields[4]=0;return os.stat_result(fields)
        def is_symlink(self):return False
    # Only ownership metadata is synthetic; kernel flock and descriptor identity
    # are real. An inherited dup shares the parent's locked open description.
    monkeypatch.setattr(d,'SHARED_WINDOW_LOCK',RootWitness())
    parent=os.open(path,os.O_RDWR);inherited=os.dup(parent);other=os.open(path,os.O_RDWR)
    try:
        d.fcntl.flock(parent,d.fcntl.LOCK_EX)
        d.validate_inherited_window_lock(inherited)
        with pytest.raises(RuntimeError,match='not inherited'):d.validate_inherited_window_lock(other)
    finally:
        for fd in (other,inherited,parent):os.close(fd)

@pytest.mark.parametrize('returncode',[0,1])
def test_full_private_diagnostics_survive_success_and_failure(tmp_path,returncode):
    spec=importlib.util.spec_from_file_location('window',PROPOSAL/'scripts/recalc/table_quarantine_window.py');window=importlib.util.module_from_spec(spec);spec.loader.exec_module(window)
    directory=tmp_path/'private-diag';prefix=[sys.executable]
    window.save_private_diagnostics(prefix,tmp_path,directory,'create')
    for phase in ('producer','derived-validation'):
        stdout='retained full stdout\n'+('x'*9000);stderr='exact fixture failure at Model!D9\n'+('y'*12000)
        window.save_private_diagnostics(prefix,tmp_path,directory,phase,stdout=stdout,stderr=stderr,returncode=returncode,request_file_sha256='b'*64)
        manifest=json.loads((directory/(phase+'.manifest.json')).read_text())
        assert manifest['returncode']==returncode and manifest['request_file_sha256']=='b'*64
        for stream,expected in [('stdout',stdout),('stderr',stderr)]:
            path=directory/manifest['files'][stream]['path']
            assert path.read_text()==expected and path.stat().st_mode&0o077==0
            assert d.file_hash(path)==manifest['files'][stream]['sha256']
    assert directory.stat().st_mode&0o077==0
    with pytest.raises(RuntimeError):window.save_private_diagnostics(prefix,tmp_path,directory,'producer',stdout='replacement')


def test_failure_journal_retains_actual_diagnostic_cause(case):
    def failed(*args):raise RuntimeError('synthetic formula encoding mismatch at D9')
    case['args']['automation']=failed
    with pytest.raises(RuntimeError):run(case)
    path=Path(case['config']['journal_root'])/case['request']['request_id']/'failed.json';record=json.loads(path.read_text())
    assert 'encoding mismatch at D9' in record['error_text']
    assert 'RuntimeError' in record['traceback'] and 'encoding mismatch at D9' in record['traceback']
    assert record['request_sha256']==case['request']['request_sha256'] and path.stat().st_mode&0o077==0

@pytest.mark.parametrize('failure',[None,'interrupt_before_publish','existing_destination'])
def test_actual_derived_finalizer_is_atomic_and_keeps_failed_temp(case,monkeypatch,failure):
    bundle=Path(run(case)['bundle']);destination=case['tmp']/'derived-result.json'
    spec=importlib.util.spec_from_file_location('window',PROPOSAL/'scripts/recalc/table_quarantine_window.py');window=importlib.util.module_from_spec(spec);spec.loader.exec_module(window)
    monkeypatch.setattr(sys,'argv',['finalize',str(case['request_path']),str(bundle),str(destination),q.file_hash(d.__file__),str(case['pub'])])
    if failure=='existing_destination':destination.write_text('retain existing evidence')
    if failure=='interrupt_before_publish':
        original=os.link
        def interrupted(*args,**kwargs):raise KeyboardInterrupt('interrupted before final commit')
        monkeypatch.setattr(os,'link',interrupted)
    if failure:
        with pytest.raises((KeyboardInterrupt,FileExistsError)):
            exec(compile(window.FINALIZE,'<protected finalizer>','exec'),{})
        assert list(case['tmp'].glob('.derived-result.json.pending-*'))
        if failure=='existing_destination':assert destination.read_text()=='retain existing evidence'
        else:assert not destination.exists()
    else:
        exec(compile(window.FINALIZE,'<protected finalizer>','exec'),{})
        result=json.loads(destination.read_text())
        assert result['derived_evidence']['authority']=='pipeline_derived_not_runner_attested'
        assert not list(case['tmp'].glob('.derived-result.json.pending-*'))
        assert destination.stat().st_mode&0o077==0

def test_private_diagnostics_preserve_non_utf8_bytes(tmp_path):
    spec=importlib.util.spec_from_file_location('window',PROPOSAL/'scripts/recalc/table_quarantine_window.py');window=importlib.util.module_from_spec(spec);spec.loader.exec_module(window)
    directory=tmp_path/'raw-diag';window.save_private_diagnostics([sys.executable],tmp_path,directory,'create')
    raw=b'raw stderr\xff\x00\n'
    window.save_private_diagnostics([sys.executable],tmp_path,directory,'producer',stdout=b'raw\xfe',stderr=raw,returncode=1)
    assert (directory/'producer.stderr.txt').read_bytes()==raw
    assert (directory/'producer.stdout.txt').read_bytes()==b'raw\xfe'
