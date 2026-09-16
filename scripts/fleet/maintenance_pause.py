"""Bounded process-tree pause for a root-selected coordinator maintenance window.

No task artifacts or budgets are changed. Existing wall clocks keep running.
Only `freeze` signals; `status` is read-only and `thaw` can only resume retained
identities that were running before this maintenance operation.
"""
from pathlib import Path
import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager


def require(ok, message):
    if not ok: raise ValueError(message)


def binding(path):
    p=Path(path)
    require(not any(q.is_symlink() for q in (p,*p.parents)), 'maintenance symlink')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def checked(row):
    require(binding(row['path'])==row,'maintenance binding changed')
    return json.loads(Path(row['path']).read_bytes())


def retain(path,value):
    raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(Path(path).parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(path)


def process(pid):
    p=Path('/proc')/str(pid)
    try:
        data=(p/'stat').read_text().rsplit(')',1)[1].split()
        selected={}
        for raw in (p/'environ').read_bytes().split(b'\0'):
            key,_,value=raw.partition(b'=')
            if key in (b'HARBOR_FLEET_OWNER',b'HARBOR_FLEET_WORKBOOK'):
                selected[key.decode()]=value.decode()
        return dict(pid=pid,ppid=int(data[1]),start_ticks=data[19],state=data[0],fleet=selected,
                    command=[v.decode() for v in (p/'cmdline').read_bytes().split(b'\0') if v])
    except (FileNotFoundError,ProcessLookupError):return None
    except PermissionError:return None


def inventory():
    result={}
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            value=process(int(p.name))
            if value is not None:result[value['pid']]=value
    return result


def same(actual, expected):
    return actual is not None and actual['pid']==expected['pid'] and actual['start_ticks']==expected['start_ticks']


def descendants(rows, known):
    """Retain discovered identities even if an intermediate parent exits."""
    result=dict(known)
    changed=True
    while changed:
        changed=False
        for pid,row in rows.items():
            parent=result.get(row['ppid'])
            if parent is not None and same(rows.get(parent['pid']),parent) and pid not in result:
                result[pid]=row;changed=True
    return result


def owner_coverage(rows, known, roots):
    owners={x['owner']:x['workbook'] for x in roots}
    for pid,row in rows.items():
        owner=row.get('fleet',{}).get('HARBOR_FLEET_OWNER')
        if owner not in owners or row['state'] in ('Z','X'):continue
        require(row['fleet'].get('HARBOR_FLEET_WORKBOOK')==owners[owner], 'maintenance owner/workbook mismatch')
        require(pid in known and same(row,known[pid]), 'matching owner has an orphan or unselected HTTP process')


def status(home):
    intent=checked(binding(home/'intent.json'));p,_,roots=load(intent['plan'])
    frozen=checked(binding(home/'frozen.json')) if (home/'frozen.json').exists() else None
    rows=inventory();remaining=max(0,intent['deadline_monotonic']-time.monotonic())
    watcher=checked(binding(home/'watchdog-ready.json')) if (home/'watchdog-ready.json').exists() else None
    known={x['pid']:x for x in frozen['processes']} if frozen else {}
    covered=True
    try:owner_coverage(rows,known,roots)
    except ValueError:covered=False
    alive=same(rows.get(watcher['pid']),watcher) if watcher else False
    ready=bool(frozen and alive and covered and remaining>1 and not (home/'thaw-intent.json').exists()
        and all(not same(rows.get(pid),x) or rows[pid]['state'] in ('T','t','Z','X') for pid,x in known.items()))
    return dict(status='FROZEN' if ready else 'NOT_FROZEN',at=time.time(),remaining_pause_seconds=remaining,
        deadline_epoch=intent['deadline_epoch'],watchdog_alive=alive,owner_coverage=covered,
        files=[binding(q) for q in sorted(home.glob('*.json'))])


@contextmanager
def lock(home):
    fd=os.open(home/'mutation.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:os.close(fd)


def signal_identity(expected, number):
    """A pidfd pins the kernel process even across exit/PID reuse."""
    try:fd=os.pidfd_open(expected['pid'])
    except ProcessLookupError:return False
    try:
        if not same(process(expected['pid']),expected):return False
        try:signal.pidfd_send_signal(fd,number)
        except ProcessLookupError:return False
        return True
    finally:os.close(fd)


def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','helper','directory','maximum_pause_seconds','targets'},'maintenance plan fields differ')
    require(p['schema_version']=='fleet-maintenance-pause/v1' and p['helper']==binding(Path(__file__).absolute()),'maintenance implementation differs')
    require(type(p['maximum_pause_seconds']) is int and 1<=p['maximum_pause_seconds']<=45,'maintenance pause must fit 60s watchdog ceiling')
    require(type(p['targets']) is list and 1<=len(p['targets'])<=32,'bounded exact targets required')
    home=Path(p['directory']);require(home.is_absolute() and not any(q.is_symlink() for q in (home,*home.parents)),'maintenance namespace differs')
    targets=[]
    for row in p['targets']:
        receipt=checked(row);value=receipt.get('remote',receipt)
        require(type(value.get('pid')) is int and value['pid']>1 and str(value.get('start_ticks','')).isdigit()
                and type(value.get('command')) is list and value['command'] and all(type(x) is str for x in value['command']), 'exact launched process receipt required')
        require('--job' in value['command'] and '--job-sha256' in value['command'],'target must be selected fleet worker')
        command=value['command'];job=value['job'];require(binding(job['path'])==job, 'selected worker job changed')
        require(command.count('--job')==command.count('--job-sha256')==1
                and command[command.index('--job')+1]==job['path']
                and command[command.index('--job-sha256')+1]==job['sha256'], 'launched worker job differs')
        require(command.count('--owner')==1, 'launched owner is missing')
        owner=command[command.index('--owner')+1];workbook=checked(job)['workbook']
        require(type(owner) is str and owner and type(workbook) is str and len(workbook)==4 and workbook.isdigit(), 'launched owner/workbook invalid')
        targets.append(dict(pid=value['pid'],start_ticks=str(value['start_ticks']),command=value['command'],owner=owner,workbook=workbook))
    require(len({x['pid'] for x in targets})==len(targets),'duplicate maintenance roots')
    return p,home,targets


def thaw(home, reason):
    """A terminal marker prevents a late freezer from stopping more children."""
    with lock(home):
        terminal=home/'thaw-intent.json'
        if not terminal.exists():retain(terminal,dict(reason=reason,at=time.time()))
        for path in sorted((home/'stops').glob('*.json'),reverse=True):
            value=json.loads(path.read_bytes())
            require(value['initial_state'] not in ('T','t','Z','X'),'preexisting stop cannot be owned')
            done=home/'resumed'/path.name
            if done.exists():continue
            sent=signal_identity(value['identity'],signal.SIGCONT)
            retain(done,dict(identity=value['identity'],continued=sent,at=time.time()))
        if not (home/'thawed.json').exists():retain(home/'thawed.json',dict(status='THAWED',at=time.time(),reason=reason))
    return binding(home/'thawed.json')


def watchdog(row):
    p,home,_=load(row);intent=checked(binding(home/'intent.json'))
    require(intent['plan']==row,'watchdog plan differs')
    retain(home/'watchdog-ready.json',dict(pid=os.getpid(),start_ticks=process(os.getpid())['start_ticks'],deadline=intent['deadline_monotonic']))
    while time.monotonic()<intent['deadline_monotonic'] and not (home/'thaw-request.json').exists():time.sleep(.05)
    return thaw(home,'requested' if (home/'thaw-request.json').exists() else 'watchdog-deadline')



def inspect(row):
    p,home,roots=load(row)
    require(not home.exists(),'maintenance already exists: use status or thaw')
    rows=inventory()
    for expected in roots:
        require(same(rows.get(expected['pid']),expected) and rows[expected['pid']]['command']==expected['command'], 'root process no longer matches selected launch')
    known=descendants(rows,{r['pid']:r for r in roots});owner_coverage(rows,known,roots)
    return dict(status='MAINTENANCE_SELECTION_VALIDATED_NOT_PAUSED',plan=row,at=time.time(),roots=roots,
                processes=list(known.values()),signals_sent=0,maximum_pause_seconds=p['maximum_pause_seconds'])


def admission_census(row):
    """Read-only proof that idle supervisors cannot admit during maintenance."""
    p=checked(row)
    require(set(p)=={'schema_version','helper','host','active_claims'}
            and p['schema_version']=='fleet-admission-drain-census/v1'
            and p['helper']==binding(Path(__file__).absolute()),'closed admission census required')
    require(p['host']in['hhu-server']+['hhu-server-'+str(i)for i in range(2,9)],'unknown census host')
    expected={w['workbook']:w for w in p['active_claims']}
    require(len(expected)==len(p['active_claims'])and all(w['state']=='active'for w in expected.values()),
            'unique active claims required')
    rows=inventory();workers=[];pools=[];roots=[]
    for process in rows.values():
        if process['state']in('Z','X'):continue
        argv=process['command']
        # Frozen supervisors are often named supervisor.py. Authenticate the
        # actual generation/config/entry rather than infer safety from a name.
        if 'slot'in argv and '--run'in argv:
            require(argv.count('slot')==1 and all(argv.count(flag)==1 for flag in
                    ('--run','--config','--config-sha256','--generation','--slot')),'unknown fleet slot controller')
            directory=Path(argv[argv.index('--run')+1]);stop=binding(directory/'stop.json');state=checked(stop)
            require(state.get('force')is not True,'force stop is not an admission drain')
            generation=argv[argv.index('--generation')+1]
            require(generation and '/'not in generation and generation not in {'.','..'},'invalid supervisor generation')
            config={'path':argv[argv.index('--config')+1],'sha256':argv[argv.index('--config-sha256')+1]}
            cfg=checked(config);selected=binding(directory/'generations'/generation/'generation.json');original=checked(selected)
            require(original['config']==config and original['host']==cfg['host']==p['host']
                    and original['generation']==generation and binding(original['supervisor']['path'])==original['supervisor']
                    and argv.count(original['supervisor']['path'])==1
                    and argv.index(original['supervisor']['path'])+1==argv.index('slot'),
                    'supervisor entry/config differs from selected generation')
            current=checked(binding(directory/'current.json'));index=argv[argv.index('--slot')+1]
            require(index in {'0','1'}and current['generation']==generation and current['config']==config,
                    'orphan or unselected supervisor generation')
            matches=[x for x in current['slots']if x['slot']==int(index)]
            require(len(matches)==1 and matches[0]['command']==argv
                    and matches[0]['identity']['pid']==process['pid']
                    and str(matches[0]['identity']['start'])==process['start_ticks'],
                    'supervisor native process identity differs')
            pools.append(dict(pid=process['pid'],start_ticks=process['start_ticks'],stop=stop,generation=selected,config=config))
        if not any(arg.endswith('/worker.py')for arg in argv):continue
        require(all(argv.count(flag)==1 for flag in('--job','--job-sha256','--owner')),'unrecognized live worker')
        job={'path':argv[argv.index('--job')+1],'sha256':argv[argv.index('--job-sha256')+1]};value=checked(job)
        owner=argv[argv.index('--owner')+1];claim=expected.get(value['workbook'])
        require(re.match('^'+re.escape(p['host'])+r'(?=[:/])',owner)is not None
                and claim is not None and claim['owner']==owner and claim['version']==value['version']
                and claim['source_sha256']==value['source']['sha256'],'held, unknown or foreign worker survives')
        saved=binding(Path(value['run'])/'claim.json');native=checked(saved)
        require(all(native[k]==claim[k]for k in('workbook','owner','fence','source_sha256','version')),
                'native worker fenced claim differs')
        selected=dict(pid=process['pid'],start_ticks=process['start_ticks'],command=argv,job=job,
                      owner=owner,workbook=value['workbook'],claim=saved)
        workers.append(selected);roots.append(dict(process,owner=owner,workbook=value['workbook']))
    require({w['workbook']for w in workers}==set(expected)and len(workers)==len(expected),
            'active claim lacks exactly one native worker')
    known=descendants(rows,{w['pid']:w for w in roots});owner_coverage(rows,known,roots)
    for process in rows.values():
        owner=process.get('fleet',{}).get('HARBOR_FLEET_OWNER')
        if owner and process['state']not in('Z','X'):
            require(process['pid']in known,'orphan fleet HTTP mutator survives outside selected workers')
    return dict(schema_version='fleet-admission-drain-census/v1',status='ADMISSIONS_DRAINED',plan=row,
                helper=p['helper'],host=p['host'],at=time.time(),workers=workers,pools=pools,
                processes_signalled=0,model_calls=0)


def freeze(row):
    p,home,roots=load(row)
    require(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'Linux pidfd support required')
    require(not home.exists(),'existing maintenance operation: inspect/thaw, never replay freeze')
    for expected in roots:
        actual=process(expected['pid']);require(same(actual,expected) and actual['command']==expected['command'],'root process identity differs')
    home.mkdir(mode=0o700);(home/'stops').mkdir();(home/'resumed').mkdir()
    deadline=time.monotonic()+p['maximum_pause_seconds']
    retain(home/'intent.json',dict(plan=row,at=time.time(),deadline_epoch=time.time()+p['maximum_pause_seconds'],deadline_monotonic=deadline,automatic_retry=False))
    watcher=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).absolute()),'watchdog','--plan',row['path'],'--sha256',row['sha256']],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
    try:
        until=time.monotonic()+2
        while not (home/'watchdog-ready.json').exists():
            require(watcher.poll() is None and time.monotonic()<until,'watchdog failed to arm');time.sleep(.02)
        initial=inventory();known=descendants(initial,{r['pid']:r for r in roots});owner_coverage(initial,known,roots)
        stopped=set();preserved=[];sequence=0;stable=0
        while stable<2:
            require(time.monotonic()<deadline-1,'insufficient maintenance freeze runway')
            require(watcher.poll() is None,'maintenance watchdog exited')
            before=set(known);rows=inventory();known=descendants(rows,known)
            # Roots are inserted first; every subsequent child follows its parent.
            for pid,expected in known.items():
                if pid in stopped:continue
                with lock(home):
                    require(not (home/'thaw-intent.json').exists() and time.monotonic()<deadline-1,'maintenance already thawing')
                    current=process(pid)
                    if not same(current,expected):stopped.add(pid);continue
                    if current['state'] in ('T','t','Z','X'):
                        preserved.append(dict(identity=expected,initial_state=current['state']));stopped.add(pid);continue
                    # Durable before SIGSTOP: watchdog can safely continue this
                    # exact previously-running identity even if freezer exits.
                    sequence+=1;record=dict(identity=expected,initial_state=current['state'],at=time.time())
                    retain(home/'stops'/('%04d.json'%sequence),record)
                    signal_identity(expected,signal.SIGSTOP);stopped.add(pid)
            time.sleep(.03)
            rows=inventory();known=descendants(rows,known);owner_coverage(rows,known,roots)
            all_stopped=all(not same(rows.get(pid),x) or rows[pid]['state'] in ('T','t','Z','X') for pid,x in known.items())
            stable=stable+1 if set(known)==before and all_stopped else 0
        with lock(home):
            require(not (home/'thaw-intent.json').exists() and time.monotonic()<deadline-1,'maintenance window expired')
            result=retain(home/'frozen.json',dict(status='FROZEN',plan=row,frozen_at=time.time(),remaining_pause_seconds=deadline-time.monotonic(),deadline_epoch=checked(binding(home/'intent.json'))['deadline_epoch'],deadline_monotonic=deadline,processes=list(known.values()),preserved=preserved,watchdog=binding(home/'watchdog-ready.json'),wall_clocks_paused=False))
        return result
    except BaseException:
        thaw(home,'freeze-failed');raise


def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['inspect','freeze','thaw','status','watchdog','admission-census']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);v=a.parse_args();row=dict(path=v.plan,sha256=v.sha256)
    if v.action=='admission-census':result=admission_census(row)
    elif v.action=='inspect':result=inspect(row)
    elif v.action=='freeze':result=freeze(row)
    elif v.action=='watchdog':result=watchdog(row)
    else:
        p,home,_=load(row)
        require(checked(binding(home/'intent.json'))['plan']==row,'maintenance operation differs')
        if v.action=='thaw':
            with lock(home):
                if not (home/'thaw-request.json').exists():retain(home/'thaw-request.json',dict(at=time.time()))
            result=thaw(home,'explicit-root-request')
        else:result=status(home)
    print(json.dumps(result))


if __name__=='__main__':main()
