import ast
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import traceback
from pathlib import Path
from types import SimpleNamespace as N
import pytest

DRIVER=Path(__file__).parents[1]/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v7.py'

def load():
    names={'publication_lock_failure','publish_or_wait','validate_publication_wait','publication_observation'}
    nodes=[n for n in ast.parse(DRIVER.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
    def binding(p):return dict(path=str(p),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
    def checked(b):
        assert binding(b['path'])==b
        return json.loads(Path(b['path']).read_text())
    def retain(p,v):
        with Path(p).open('x') as f:json.dump(v,f)
        return binding(p)
    def require(v,m):
        if not v:raise ValueError(m)
    ns=dict(Path=Path,errno=errno,traceback=traceback,hashlib=hashlib,binding=binding,checked=checked,retain=retain,require=require,__file__=str(DRIVER),SCHEMA='harbor-queue-dispatch/v6',WAIT_SCHEMA='harbor-publication-lock-wait/v1')
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(DRIVER),'exec'),ns)
    return ns

def fixture(tmp_path):
    ns=load();ops=tmp_path/'dispatcher-v2/old';ops.mkdir(parents=True);directory=ops/'003';lock=tmp_path/'lock'
    @contextlib.contextmanager
    def locks(request):
        fd=os.open(lock,os.O_RDWR|os.O_CREAT,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield (fd,)
        finally:os.close(fd)
    def run(request,directory,**kw):
        with locks(request):raise AssertionError('busy lock must prevent entry')
    P=N(locks=locks,run=run);env=(None,None,P,None,None,{'run_root':str(tmp_path)})
    state=dict(directory=str(directory),tracker={'sha256':'unchanged'},request={'exact':True},output=None,publication=None)
    ns['publication_observation']=lambda *a:dict(state)
    outcome=ns['retain'](tmp_path/'outcome.json',{'genuine':True});checkpoint={'path':'enqueue','sha256':'exact'}
    job=dict(driver=ns['binding'](DRIVER),schema_version=ns['SCHEMA'],queue={'exact':1},config={'exact':2},models=False,commands=True,containers=True,publish=True,outcome=outcome,operations=str(ops))
    jb=ns['retain'](ops/'job.json',job)
    fd=os.open(lock,os.O_RDWR|os.O_CREAT,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:assert ns['publish_or_wait'](jb,outcome,checkpoint,env,None,directory,ops,(fd,)) is None
    finally:os.close(fd)
    wait=ns['binding'](ops/'publication-lock-wait.json')
    ns['successful']=lambda *a:(checkpoint,{'status':'PUBLICATION_READY'})
    ns['publication_request']=lambda *a:'request'
    nextjob=dict(job,operations=str(tmp_path/'new'),publication_wait=wait)
    return ns,env,nextjob,wait,state

def test_actual_flock_contention_is_durable_and_has_no_intent(tmp_path):
    ns,env,job,wait,state=fixture(tmp_path)
    value=ns['checked'](wait)
    assert value['before']==value['after'] and not value['automatic_retry']
    assert not Path(state['directory']).exists()
    assert 'BlockingIOError' in ns['checked'](value['exception'])['traceback']
    assert ns['validate_publication_wait'](wait,job,env)=='request'

@pytest.mark.parametrize('field',['queue','config','models','commands','containers','publish','outcome'])
def test_resume_cannot_change_authority(tmp_path,field):
    ns,env,job,wait,state=fixture(tmp_path);job[field]='foreign'
    with pytest.raises(ValueError):ns['validate_publication_wait'](wait,job,env)

def test_changed_tracker_after_wait_rejects(tmp_path):
    ns,env,job,wait,state=fixture(tmp_path);state['tracker']={'sha256':'changed'}
    with pytest.raises(ValueError):ns['validate_publication_wait'](wait,job,env)

def test_unrelated_blocking_error_is_not_publication_wait():
    ns=load()
    @contextlib.contextmanager
    def locks(r):yield ()
    try:raise BlockingIOError(errno.EAGAIN,'unrelated transport')
    except BlockingIOError as exc:assert not ns['publication_lock_failure'](exc,N(locks=locks))

def test_changed_state_during_contention_is_not_wait(tmp_path):
    ns=load();calls=[]
    @contextlib.contextmanager
    def locks(r):raise BlockingIOError(errno.EAGAIN,'busy');yield
    def run(*a,**k):
        with locks(None):pass
    def observation(*a):calls.append(1);return {'revision':len(calls)}
    ns['publication_observation']=observation
    with pytest.raises(ValueError):ns['publish_or_wait']({}, {}, {}, (None,None,N(run=run,locks=locks)),None,tmp_path/'003',tmp_path,())
    assert not (tmp_path/'publication-lock-wait.json').exists()

@pytest.mark.parametrize('change',['intent','symlink','revision','publication_lease','bytes'])
def test_observation_rejects_any_started_or_changed_publication(tmp_path,change):
    ns=load();tracker=tmp_path/'tracker.json';tracker.write_text('{}')
    raw=tracker.read_bytes();value={'revision':5,'leases':{'publication':None}}
    request=N(lease=N(revision=5,sha256=hashlib.sha256(raw).hexdigest()),output=tmp_path/'output',publication_root=tmp_path/'release')
    P=N(_paths=lambda *a:(tracker,None,None),_tracker=lambda *a:(tracker.read_bytes(),value),encode=lambda *a:{'exact':True},_checkpoint=lambda *a:None,_tree=lambda *a:None)
    directory=tmp_path/'publication'
    assert ns['publication_observation']((None,None,P),request,directory)['tracker']==ns['binding'](tracker)
    if change=='intent':directory.mkdir();(directory/'intent.json').write_text('{}')
    elif change=='symlink':directory.symlink_to(tmp_path/'missing')
    elif change=='revision':value['revision']=6
    elif change=='publication_lease':value['leases']['publication']={'started':True}
    else:tracker.write_text('{"changed":true}')
    with pytest.raises(ValueError):ns['publication_observation']((None,None,P),request,directory)
