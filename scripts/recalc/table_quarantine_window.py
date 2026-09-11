#!/usr/bin/python3
"""Protected standalone offline window proposal; no pipeline imports as root."""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import time

DISPATCHER=Path('/Library/Application Support/FCP2/recalc/table_quarantine_runner.py')
LOCK=Path('/var/run/fcp2-recalc-window.lock')

def trusted(path):
    path=Path(path);m=path.lstat()
    if path.is_symlink() or not stat.S_ISREG(m.st_mode) or m.st_uid!=0 or m.st_mode&0o022:raise RuntimeError('unprotected runner path')
    for parent in path.resolve().parents:
        m=parent.stat()
        if m.st_uid!=0 or m.st_mode&0o022:raise RuntimeError('unprotected runner parent')

def stop_group(process):
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:pass
    try:process.wait(timeout=45)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=10)
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass

@contextlib.contextmanager
def offline_interfaces():
    original=subprocess.check_output(['/sbin/ifconfig','-l','-u'],text=True).split();downed=[]
    try:
        for interface in original:
            if interface=='lo0':continue
            subprocess.run(['/sbin/ifconfig',interface,'down'],check=True,capture_output=True);downed.append(interface)
        time.sleep(2)
        if set(subprocess.check_output(['/sbin/ifconfig','-l','-u'],text=True).split())-{'lo0'}:raise RuntimeError('network interfaces remain active')
        yield
    finally:
        failed=[]
        for interface in downed:
            if subprocess.run(['/sbin/ifconfig',interface,'up'],capture_output=True).returncode:failed.append(interface)
        if failed:raise RuntimeError('network restore failed for '+', '.join(failed))

FINALIZE = r'''
import hashlib,json,os,sys,tempfile
from pathlib import Path
import xl_table_recalc_quarantine as q
request_path,bundle_path,destination,producer_sha,key_path=sys.argv[1:]
request=json.loads(Path(request_path).read_text());bundle=Path(bundle_path)
receipt=json.loads((bundle/'producer-receipt.json').read_text())
result=q.assemble_result(request,receipt,source=request['source_path'],staged=bundle/'excel-staged.xlsx',
    output=request['output_path'],producer_sha256=producer_sha,public_key=key_path)
path=Path(destination)
# Temporary evidence is retained on failure/interruption; final path is absent
# until every byte has been durably written. link is atomic and never replaces.
fd,name=tempfile.mkstemp(prefix='.'+path.name+'.pending-',dir=path.parent)
with os.fdopen(fd,'wb') as handle:
    handle.write(q.canonical(result));handle.flush();os.fsync(handle.fileno())
os.link(name,path,follow_symlinks=False)
os.unlink(name)
dirfd=os.open(path.parent,os.O_RDONLY)
try:os.fsync(dirfd)
finally:os.close(dirfd)
print(json.dumps({'status':'quarantine_evidence_verified','result_path':str(path.absolute()),
    'result_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bundle_path':str(bundle)}))
'''


DIAGNOSTICS = r'''
import base64,hashlib,json,os,stat,sys,tempfile
from pathlib import Path
root=Path(sys.argv[1]);phase=sys.argv[2]
if phase=='create':
    os.mkdir(root,0o700)
    fd=os.open(root.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
else:
    if phase not in {'producer','derived-validation'}:raise ValueError('unknown diagnostic phase')
    m=root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(m.st_mode) or m.st_uid!=os.getuid() or m.st_mode&0o077:raise ValueError('unsafe diagnostic directory')
    record=json.load(sys.stdin);files={}
    for stream in ('stdout','stderr'):
        data=base64.b64decode(record.pop(stream+'_base64'),validate=True);path=root/(phase+'.'+stream+'.txt')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as handle:handle.write(data);handle.flush();os.fsync(handle.fileno())
        files[stream]={'path':path.name,'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data)}
    record.update(schema_version='private-quarantine-diagnostics/v1',phase=phase,files=files)
    data=(json.dumps(record,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd,name=tempfile.mkstemp(prefix='.'+phase+'.manifest-',dir=root)
    with os.fdopen(fd,'wb') as handle:handle.write(data);handle.flush();os.fsync(handle.fileno())
    os.link(name,root/(phase+'.manifest.json'),follow_symlinks=False);os.unlink(name)
    fd=os.open(root,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
'''

def save_private_diagnostics(prefix,pipeline,directory,phase,*,stdout='',stderr='',returncode=None,request_file_sha256=None):
    def encoded(value):
        return base64.b64encode(value if isinstance(value,bytes) else value.encode('utf-8')).decode('ascii')
    record={'stdout_base64':encoded(stdout),'stderr_base64':encoded(stderr),'returncode':returncode,
            'request_file_sha256':request_file_sha256,'recorded_at_ns':time.time_ns()}
    saved=subprocess.run(prefix+['-c',DIAGNOSTICS,str(directory),phase],cwd=pipeline,
                         input=json.dumps(record),capture_output=True,text=True)
    if saved.returncode:raise RuntimeError('could not persist private diagnostics at '+str(directory))


def persistent_runtime_precheck(prefix,pipeline):
    checked=subprocess.run(prefix+['-c','import sys,openpyxl,xl_table_recalc_quarantine; assert sys.version_info[:2] == (3,12)'],cwd=pipeline,capture_output=True,text=True)
    if checked.returncode:raise RuntimeError('persistent Python 3.12 precheck failed; networking was not changed')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',required=True);parser.add_argument('--pipeline-python',required=True)
    parser.add_argument('--pipeline',required=True);parser.add_argument('--derived-result',required=True)
    parser.add_argument('--diagnostics-dir',help='fresh owner-only output directory; defaults beside derived result')
    args=parser.parse_args(argv)
    if os.geteuid()!=0:parser.error('run locally through sudo after reviewed installation')
    trusted(__file__);trusted(DISPATCHER)
    spec=importlib.util.spec_from_file_location('_protected_quarantine_dispatcher',DISPATCHER)
    dispatcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(dispatcher)
    config,base,base_config=dispatcher.load_deployment()
    account=pwd.getpwnam(base_config['automation_user'])
    if str(account.pw_uid)!=os.environ.get('SUDO_UID') or account.pw_name!=os.environ.get('SUDO_USER') or account.pw_uid!=config['allowed_invoker_uid']:parser.error('dedicated sudo invoker required')
    if Path('/dev/console').stat().st_uid!=account.pw_uid:parser.error('dedicated invoker is not console user')
    python=Path(args.pipeline_python).absolute();pipeline=Path(args.pipeline).absolute()
    prefix=['/usr/bin/sudo','-n','-u',account.pw_name,'--',str(python)]
    # No interface operation or private-key use before persistent runtime check.
    persistent_runtime_precheck(prefix,pipeline)
    if Path(args.derived_result).exists() or Path(args.derived_result).is_symlink():parser.error('derived result already exists')
    diagnostics=Path(args.diagnostics_dir or (str(Path(args.derived_result).absolute())+'.diagnostics')).absolute()
    request_file_sha=dispatcher.file_hash(args.request)
    save_private_diagnostics(prefix,pipeline,diagnostics,'create')
    fd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    process=None;old_handlers={}
    try:
        m=os.fstat(fd)
        if m.st_uid!=0 or m.st_mode&0o022 or not stat.S_ISREG(m.st_mode) or m.st_nlink!=1:raise RuntimeError('shared Excel session lock is unsafe')
        fcntl.flock(fd,fcntl.LOCK_EX)
        def interrupted(signum,frame):raise KeyboardInterrupt('offline window interrupted')
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):old_handlers[sig]=signal.signal(sig,interrupted)
        with offline_interfaces():
            try:
                process=subprocess.Popen(['/usr/bin/python3','-I',str(DISPATCHER),'--request',str(Path(args.request).absolute()),'--offline-lock-fd',str(fd)],pass_fds=(fd,),start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    stdout,stderr=process.communicate(timeout=base_config.get('timeout_seconds',300)+180)
                except BaseException:
                    stop_group(process)
                    stdout,stderr=process.communicate()
                    save_private_diagnostics(prefix,pipeline,diagnostics,'producer',stdout=stdout,stderr=stderr,returncode=process.returncode,request_file_sha256=request_file_sha)
                    raise
                save_private_diagnostics(prefix,pipeline,diagnostics,'producer',stdout=stdout,stderr=stderr,returncode=process.returncode,request_file_sha256=request_file_sha)
                if process.returncode:raise RuntimeError('quarantine producer failed; diagnostics: '+str(diagnostics))
                status=json.loads(stdout)
            finally:
                if process is not None:stop_group(process)
        # Derived checks execute as the user, only after networking is restored.
        # Protected producer observations are retained even if this step fails.
        finalized=subprocess.Popen(prefix+['-c',FINALIZE,str(Path(args.request).absolute()),status['bundle'],str(Path(args.derived_result).absolute()),config['dispatcher_sha256'],base_config['receipt_public_key']],cwd=pipeline,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:
            try:final_stdout,final_stderr=finalized.communicate(timeout=180)
            except BaseException:
                stop_group(finalized);final_stdout,final_stderr=finalized.communicate()
                save_private_diagnostics(prefix,pipeline,diagnostics,'derived-validation',stdout=final_stdout,stderr=final_stderr,returncode=finalized.returncode,request_file_sha256=request_file_sha)
                raise
        finally:stop_group(finalized)
        save_private_diagnostics(prefix,pipeline,diagnostics,'derived-validation',stdout=final_stdout,stderr=final_stderr,returncode=finalized.returncode,request_file_sha256=request_file_sha)
        if finalized.returncode:raise RuntimeError('pipeline evidence revalidation failed; diagnostics: '+str(diagnostics)+'; retained bundle: '+status['bundle'])
        print(final_stdout.decode('utf-8').strip())
        return 0
    finally:
        for sig,handler in old_handlers.items():signal.signal(sig,handler)
        os.close(fd)

if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,OSError,ValueError,KeyboardInterrupt) as exc:print('table quarantine window: '+str(exc),file=sys.stderr);raise SystemExit(1)
