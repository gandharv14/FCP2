#!/usr/bin/python3
"""Standalone protected quarantine dispatcher proposal. No pipeline imports.

This separate entry point leaves the ordinary runner implementation unchanged.
Deployment must serialize ordinary and quarantine Excel sessions externally too.
"""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import traceback
import uuid

CONFIG=Path('/etc/harbor/excel-table-quarantine.json')
PROFILE='excel-table-quarantine/v1'
REQUEST='table-recalc-quarantine-request/v1'
CLAIMS='excel-table-quarantine-claims/v1'
RECEIPT='excel-table-quarantine-receipt/v1'
LIMIT=8*1024*1024
SHARED_WINDOW_LOCK=Path('/var/run/fcp2-recalc-window.lock')
CONTROLS=('dedicated_session','network_disabled','macros_disabled','add_ins_disabled','link_updates_disabled','prompts_suppressed')

class DispatchError(RuntimeError):pass

def canonical(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def same(a,b,label):
    if canonical(a)!=canonical(b):raise DispatchError(label+' mismatch')
def keys(value,expected):
    if not isinstance(value,dict) or set(value)!=set(expected):raise DispatchError('unexpected/missing fields')
def regular(path):
    p=Path(path);m=p.lstat()
    if stat.S_ISLNK(m.st_mode) or not stat.S_ISREG(m.st_mode) or m.st_nlink!=1:raise DispatchError('unsafe regular file')
    return m

def file_hash(path):
    regular(path);h=hashlib.sha256()
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as handle:
        for data in iter(lambda:handle.read(1024*1024),b''):h.update(data)
    return h.hexdigest()

def protected(path,directory=False):
    p=Path(path);m=p.lstat()
    if p.is_symlink() or m.st_uid!=0 or m.st_mode&0o022 or (not stat.S_ISDIR(m.st_mode) if directory else not stat.S_ISREG(m.st_mode)):
        raise DispatchError('unprotected deployment path')
    for parent in p.resolve().parents:
        m=parent.stat()
        if m.st_uid!=0 or m.st_mode&0o022 or not stat.S_ISDIR(m.st_mode):raise DispatchError('unprotected deployment parent')

def read_json(path):
    if regular(path).st_size>LIMIT:raise DispatchError('JSON evidence exceeds bound')
    def unique(items):
        result={}
        for k,v in items:
            if k in result:raise DispatchError('duplicate JSON field')
            result[k]=v
        return result
    return json.loads(Path(path).read_text(),object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(DispatchError('nonfinite JSON')))

def sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def write_new(path,data,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    except BaseException:
        # Incomplete evidence remains reserved; never silently retry the ID.
        raise
    sync_dir(Path(path).parent)

def copy_observed(source,destination,expected=None):
    regular(source);fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        out=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(out,'wb') as target:shutil.copyfileobj(stream,target);target.flush();os.fsync(target.fileno())
    actual=file_hash(destination)
    if expected is not None:same(actual,expected,'copied source')
    return actual

def load_deployment(path=CONFIG):
    protected(path);config=read_json(path)
    keys(config,('schema_version','base_config','base_module','base_module_sha256','dispatcher_sha256',
                 'selected_policy_code','journal_root','artifact_root','allowed_invoker_uid','permitted_engine_versions'))
    same(config['schema_version'],'excel-table-quarantine-config/v1','config schema')
    protected(__file__);same(file_hash(__file__),config['dispatcher_sha256'],'protected dispatcher')
    module=Path(config['base_module']);protected(module)
    same(file_hash(module),config['base_module_sha256'],'protected observer module')
    spec=importlib.util.spec_from_file_location('_protected_excel_base',module)
    base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    base_config=base.load_config(Path(config['base_config']))
    same(config['base_module_sha256'],base_config['runner_implementation_sha256'],'ordinary runner dependency')
    keys(config['selected_policy_code'],('trusted_producer_sha256','profile_sha256','observation_sha256','snapshot_receipt_sha256','health_sha256'))
    if any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in config['selected_policy_code'].values()):raise DispatchError('bad protected policy digests')
    same(config['selected_policy_code']['trusted_producer_sha256'],config['dispatcher_sha256'],'selected producer')
    same(config['selected_policy_code']['observation_sha256'],config['base_module_sha256'],'selected observer')
    for field in ('journal_root','artifact_root'):protected(Path(config[field]),directory=True)
    if Path(config['journal_root']).resolve()==Path(config['artifact_root']).resolve():raise DispatchError('journal and published artifacts must be distinct')
    if type(config['allowed_invoker_uid']) is not int or config['allowed_invoker_uid']<=0:raise DispatchError('dedicated invoking UID required')
    versions=config['permitted_engine_versions']
    if not isinstance(versions,list) or not versions or any(not isinstance(v,str) or not v or v=='*' for v in versions):raise DispatchError('explicit permitted engine versions required')
    return config,base,base_config

def validate_request(request, *, config, base, base_config, invoking_uid, now_ns=None):
    keys(request,('schema_version','profile','request_id','workbook','created_at_ns','expires_at_ns','source_path',
                  'output_path','source_sha256','source_size_bytes','original_observation','original_health_sha256',
                  'original_snapshot_sha256','code_identity','public_key_sha256','engine','engine_version','scope',
                  'source_approval','task_approval','request_sha256'))
    same(request['request_sha256'],digest({k:v for k,v in request.items() if k!='request_sha256'}),'request digest')
    for field,value in {'schema_version':REQUEST,'profile':PROFILE,'scope':'quarantine_only','source_approval':False,'task_approval':False,'engine':'excel-macos'}.items():same(request[field],value,field)
    try:same(str(uuid.UUID(request['request_id'])),request['request_id'],'canonical request ID')
    except (ValueError,TypeError,AttributeError) as exc:raise DispatchError('bad request UUID') from exc
    if not isinstance(request['workbook'],str) or not re.fullmatch('[A-Za-z0-9_-]+',request['workbook']):raise DispatchError('bad workbook identity')
    start,end=request['created_at_ns'],request['expires_at_ns'];now=time.time_ns() if now_ns is None else now_ns
    if type(start) is not int or type(end) is not int or not 0<start<=now<=end or not 0<end-start<=86400*10**9:raise DispatchError('request execution interval invalid')
    same(request['code_identity'],config['selected_policy_code'],'protected selected pipeline policy')
    same(request['public_key_sha256'],base_config['receipt_public_key_sha256'],'protected receipt key')
    if request['engine_version'] not in config['permitted_engine_versions']:raise DispatchError('unapproved engine version')
    same(invoking_uid,config['allowed_invoker_uid'],'dedicated invoker')
    source=Path(request['source_path']);m=regular(source)
    if source.name=='excel-staged.xlsx' or request['workbook']+'.xlsx' in {'original.xlsx','excel-staged.xlsx'}:
        raise DispatchError('reserved evidence filename')
    if not source.is_absolute() or m.st_uid!=invoking_uid or m.st_mode&0o022:raise DispatchError('source ownership/permissions invalid')
    same(m.st_size,request['source_size_bytes'],'original size')
    same(file_hash(source),request['source_sha256'],'original bytes')
    final=Path(config['artifact_root'])/request['request_id']
    same(str(final/(request['workbook']+'.xlsx')),request['output_path'],'protected final output path')
    if final.exists() or final.is_symlink():raise DispatchError('published output already exists')
    base.validate_xlsx(source)
    before=base.observe_data_tables(source)
    if not before.get('tables'):raise DispatchError('complete tables required')
    same(before,request['original_observation'],'independently observed original')
    # Health and semantic hashes are bound as policy-selected request fields,
    # never independently asserted by this stdlib producer.
    for field in ('original_health_sha256','original_snapshot_sha256'):
        if not isinstance(request[field],str) or not re.fullmatch('[0-9a-f]{64}',request[field]):raise DispatchError('bad derived request digest')
    return source,final,before

@contextlib.contextmanager
def exclusive_session(root):
    path=Path(root)/'session.lock';fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink!=1:raise DispatchError('unsafe session lock')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:os.close(fd)

def reserve(root,request,request_file_sha256):
    directory=Path(root)/request['request_id']
    try:directory.mkdir(mode=0o700)
    except FileExistsError as exc:raise DispatchError('request ID already reserved: replay or interrupted execution') from exc
    sync_dir(root)
    write_new(directory/'reservation.json',canonical({'schema_version':'table-quarantine-reservation/v1',
        'request_id':request['request_id'],'request_sha256':request['request_sha256'],
        'request_file_sha256':request_file_sha256,'reserved_at_ns':time.time_ns()}))
    return directory

def observe_excel(base, excel_app, original_copy, retained_staging, timeout):
    """Observe Excel's actual saved copy; no pipeline code or cache synthesizer."""
    if base._excel_running():raise DispatchError('Excel session is not dedicated')
    user=os.environ.get('SUDO_USER');account=pwd.getpwnam(user or '')
    if account.pw_uid==0:raise DispatchError('Excel cannot execute as root')
    root=base._excel_container_staging_root(account)
    directory=Path(tempfile.mkdtemp(prefix='quarantine-',dir=root));workbook=directory/original_copy.name
    script=None;process=None;prefix=['/usr/bin/sudo','-n','-u',user,'--']
    try:
        copy_observed(original_copy,workbook,file_hash(original_copy))
        os.chmod(directory,0o700);os.chown(directory,account.pw_uid,account.pw_gid);os.chown(workbook,account.pw_uid,account.pw_gid)
        # Use the exact protected ordinary runner AppleScript: macros/events/
        # add-ins/link updates guarded, full rebuild, save, close and quit.
        fd,name=tempfile.mkstemp(suffix='.applescript');script=Path(name)
        with os.fdopen(fd,'w') as stream:stream.write(base._AUTOMATION_SCRIPT)
        os.chmod(script,0o644)
        process=subprocess.Popen(prefix+['/usr/bin/osascript',str(script),str(workbook),str(excel_app)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:_,stderr=process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=10)
            raise DispatchError('Excel observation timed out') from exc
        if process.returncode:raise DispatchError('Excel observation failed: '+stderr.strip())
        base.validate_xlsx(workbook)
        copy_observed(workbook,retained_staging,file_hash(workbook))
    finally:
        if process is not None and process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=10)
        if script is not None:script.unlink(missing_ok=True)
        if base._excel_running():
            subprocess.run(prefix+['/usr/bin/osascript','-e','tell application "Microsoft Excel" to quit'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        shutil.rmtree(directory,ignore_errors=True)
    if base._excel_running():raise DispatchError('Excel did not exit isolated session')
    return base.excel_version(excel_app)

def run_request(request_path, *, config, base, base_config, invoking_uid,
                automation=observe_excel, network_check=None, network_enforcer=None, signer=None):
    """Only main supplies protected configuration. Dependencies injectable in tests."""
    if platform.system()!='Darwin' or os.geteuid()!=0:raise DispatchError('protected dispatcher requires macOS root')
    request_path=Path(request_path);meta=regular(request_path)
    if meta.st_uid!=invoking_uid or meta.st_mode&0o077:raise DispatchError('request must be owner-only for invoker')
    request_file_hash=file_hash(request_path);request=read_json(request_path)
    source,final,before=validate_request(request,config=config,base=base,base_config=base_config,invoking_uid=invoking_uid)
    controls=base_config.get('isolation_controls')
    keys(controls,CONTROLS)
    if any(v is not True for v in controls.values()):raise DispatchError('strict true controls required')
    timeout=base_config.get('timeout_seconds',300)
    if type(timeout) is not int or not 1<=timeout<=3600:raise DispatchError('invalid timeout')
    excel=Path(base_config['excel_app']);base.validate_root_directory(excel)
    if excel.name!='Microsoft Excel.app':raise DispatchError('wrong Excel application')
    check=base.no_default_network_route if network_check is None else network_check
    enforce=base.enforce_pf_network_isolation if network_enforcer is None else network_enforcer
    sign=base.sign_payload if signer is None else signer
    with exclusive_session(config['journal_root']):
        # Admission may have waited behind another table session. Recheck the
        # interval and all original bindings before reserving or opening Excel.
        source,final,before=validate_request(request,config=config,base=base,base_config=base_config,invoking_uid=invoking_uid)
        journal=reserve(config['journal_root'],request,request_file_hash)
        work=Path(tempfile.mkdtemp(prefix='.'+request['request_id']+'-',dir=config['artifact_root']))
        # Root owns work 0700; retained evidence is not visible before the
        # completed bundle is atomically renamed. Failed work remains private.
        try:
            same(file_hash(request_path),request_file_hash,'request before execution')
            # Preserve the original basename while Excel observes the copy.
            original_copy=work/source.name;copy_observed(source,original_copy,request['source_sha256'])
            staged=work/'excel-staged.xlsx';output=work/(request['workbook']+'.xlsx')
            started=time.time_ns()
            if not check():raise DispatchError('network isolation is not enforced')
            with enforce():
                version=automation(base,excel,original_copy,staged,timeout)
                same(version,request['engine_version'],'actual Excel version')
                same(file_hash(original_copy),request['source_sha256'],'protected original copy')
                retained_original=work/'original.xlsx'
                if original_copy!=retained_original:os.rename(original_copy,retained_original)
                original_copy=retained_original
                actual=base.observe_data_tables(staged)
                for field in ('geometry_sha256','primitive_inputs_sha256','formula_identity_sha256'):same(actual[field],before[field],'staging '+field)
                copy_observed(original_copy,output,request['source_sha256'])
                base.graft_observed_data_tables(staged,output)
                after=base.observe_data_tables(output)
                for field in ('geometry_sha256','primitive_inputs_sha256','formula_identity_sha256'):same(after[field],before[field],'output '+field)
                same(actual['results_sha256'],after['results_sha256'],'all observed table results')
                same(file_hash(source),request['source_sha256'],'original after Excel')
                same(file_hash(request_path),request_file_hash,'request after Excel')
                completed=time.time_ns()
                if not request['created_at_ns']<=started<=completed<=request['expires_at_ns']:raise DispatchError('execution exceeded request lifetime')
                # Observation source_sha256 fields reflect actual file bytes;
                # private staging locations are intentionally not in digests.
                payload={'schema_version':CLAIMS,'profile':PROFILE,'scope':'quarantine_only','source_approval':False,'task_approval':False,
                    'request_sha256':request['request_sha256'],'request_id':request['request_id'],'workbook':request['workbook'],
                    'source_sha256':request['source_sha256'],'staging_sha256':file_hash(staged),'output_sha256':file_hash(output),
                    'source_path':request['source_path'],'staging_path':str(final/'excel-staged.xlsx'),'output_path':request['output_path'],
                    'selected_policy_code':config['selected_policy_code'],'original_observation_sha256':digest(before),
                    'staging_observation_sha256':digest(actual),'output_observation_sha256':digest(after),
                    'engine':'excel-macos','engine_version':version,'started_at_ns':started,'completed_at_ns':completed,
                    'calculation_complete':True,'isolation_enforced':True,'isolation_controls':controls,
                    'network_isolation_mechanism':'macos-pf-anchor','network_isolation_rules_sha256':base.PF_RULES_SHA256,
                    'public_key_sha256':base_config['receipt_public_key_sha256']}
                receipt={'schema_version':RECEIPT,'signature_algorithm':'ed25519','signed_payload':payload,'signature_base64':sign(payload,base_config)}
            write_new(work/'producer-receipt.json',canonical(receipt));write_new(work/'request.json',canonical(request))
            write_new(work/'table-observations.json',canonical({'original':before,'staging':actual,'output':after}))
            for name in ('original.xlsx','excel-staged.xlsx',output.name):
                same(file_hash(work/name),{'original.xlsx':request['source_sha256'],'excel-staged.xlsx':payload['staging_sha256'],output.name:payload['output_sha256']}[name],'prepublication bytes')
            # User-readable, owner-only files under an undeletable root-owned
            # bundle. Any later owner modification invalidates signed hashes.
            for path in work.iterdir():os.chown(path,invoking_uid,-1);os.chmod(path,0o400)
            os.chmod(work,0o755);sync_dir(work)
            if final.exists() or final.is_symlink():raise DispatchError('publication conflict')
            os.rename(work,final);sync_dir(final.parent)
            write_new(journal/'completed.json',canonical({'request_sha256':request['request_sha256'],'receipt_sha256':digest(receipt),'bundle':str(final),'completed_at_ns':completed}))
            return {'status':'quarantined','bundle':str(final),'receipt':str(final/'producer-receipt.json'),
                    'receipt_sha256':digest(receipt),'output_sha256':payload['output_sha256'],'scope':'quarantine_only'}
        except BaseException as exc:
            if not (journal/'failed.json').exists():
                write_new(journal/'failed.json',canonical({'request_sha256':request['request_sha256'],'error_type':type(exc).__name__,'error_text':str(exc)[:8192],
                    'traceback':traceback.format_exc()[-32768:],'reserved_id_remains_consumed':True}))
            raise

def validate_inherited_window_lock(fd):
    if type(fd) is not int or fd<0:raise DispatchError('inherited offline lock FD required')
    expected=SHARED_WINDOW_LOCK.lstat();actual=os.fstat(fd)
    if (SHARED_WINDOW_LOCK.is_symlink() or expected.st_uid!=0 or expected.st_mode&0o022
            or not stat.S_ISREG(expected.st_mode) or actual.st_nlink!=1
            or (actual.st_dev,actual.st_ino)!=(expected.st_dev,expected.st_ino)):
        raise DispatchError('inherited shared offline lock is invalid')
    # An inherited flock shares the parent's open-file description. A forged
    # separately opened FD cannot acquire this while the parent owns the lock.
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError as exc:raise DispatchError('offline lock is not inherited') from exc


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--request',required=True);parser.add_argument('--offline-lock-fd',required=True,type=int);args=parser.parse_args(argv)
    if os.geteuid()!=0 or platform.system()!='Darwin':raise DispatchError('macOS root required')
    validate_inherited_window_lock(args.offline_lock_fd)
    config,base,base_config=load_deployment()
    account=pwd.getpwnam(base_config['automation_user'])
    try:uid=int(os.environ['SUDO_UID'])
    except (KeyError,ValueError) as exc:raise DispatchError('sudo invoker missing') from exc
    if uid!=account.pw_uid or os.environ.get('SUDO_USER')!=account.pw_name or uid!=config['allowed_invoker_uid']:raise DispatchError('invoker is not the dedicated automation account')
    def interrupted(signum,frame):raise KeyboardInterrupt('quarantine dispatcher interrupted')
    old={sig:signal.signal(sig,interrupted) for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
    try:print(json.dumps(run_request(args.request,config=config,base=base,base_config=base_config,invoking_uid=uid),sort_keys=True))
    finally:
        for sig,handler in old.items():signal.signal(sig,handler)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,OSError,ValueError,KeyboardInterrupt) as exc:print('table quarantine runner: '+str(exc),file=os.sys.stderr);raise SystemExit(1)
