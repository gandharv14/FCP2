"""Authenticate one SSH forwarding leaf before retiring a dead transport."""
from pathlib import Path
import hashlib,json,os,pwd,re,signal,subprocess,time


def require(ok,message):
    if not ok:raise ValueError(message)


def identity(pid,port,*,proc=Path('/proc'),listener=None,uid=None):
    require(type(pid)is int and pid>1 and port==8765,'exact coordinator forwarding endpoint required')
    if listener is None:
        q=subprocess.run(['ss','-H','-lntp',f'sport = :{port}'],capture_output=True,text=True,check=True)
        listener=q.stdout.strip()
    require(len(listener.splitlines())==1 and re.search(r'127\.0\.0\.1:8765\s',listener)
        and re.findall(r'pid=(\d+)',listener)==[str(pid)] and '"sshd"'in listener,
        'listener is not the exact sole SSH forwarding session')
    directory=proc/str(pid);stat=(directory/'stat').read_text().rsplit(')',1)[1].split()
    argv=[x.decode()for x in(directory/'cmdline').read_bytes().split(b'\0')if x]
    user='gmahajan_labelbox_com';expected_uid=pwd.getpwnam(user).pw_uid if uid is None else uid
    status=(directory/'status').read_text();uids=re.search(r'^Uid:\s+(.+)$',status,re.M).group(1).split()
    require(argv==['sshd: '+user] and all(int(x)==expected_uid for x in uids),
        'SSH process is not the unprivileged forwarding leaf')
    children=[]
    for p in proc.iterdir():
        if not p.name.isdigit():continue
        try:parts=(p/'stat').read_text().rsplit(')',1)[1].split()
        except(FileNotFoundError,ProcessLookupError):continue
        if int(parts[1])==pid:children.append(int(p.name))
    require(children==[],'SSH session owns a child; refusing transport retirement')
    fd=re.findall(r'fd=(\d+)',listener);require(len(fd)==1,'unique forwarding socket required')
    socket=os.readlink(directory/'fd'/fd[0]);require(re.fullmatch(r'socket:\[\d+\]',socket),'forwarding fd changed')
    return dict(pid=pid,port=port,ppid=int(stat[1]),start_ticks=int(stat[19]),boot_id=(proc/'sys/kernel/random/boot_id').read_text().strip(),
        argv=argv,uid=expected_uid,socket=socket,listener=listener,children=[])


def retire(expected):
    """No force, listener daemon signal, workbook signal or automatic replay."""
    require(identity(expected['pid'],expected['port'])==expected,'stale tunnel session identity changed')
    os.kill(expected['pid'],signal.SIGTERM)
    return dict(status='STALE_FORWARDING_LEAF_TERM_SENT',identity=expected,at=time.time(),models=0,workbook_signals=0)
