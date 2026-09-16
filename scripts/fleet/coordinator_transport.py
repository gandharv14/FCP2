"""A separate compressed SSH alias to the unchanged loopback coordinator.

No coordinator, credential, database, workbook, or old tunnel is replaced.
"""
from pathlib import Path
import hashlib,json,shlex,subprocess

SCHEMA='fleet-coordinator-ssh-alias/v1'
HOSTS={'hhu-server',*(f'hhu-server-{i}'for i in range(2,9))}
PRIMARY='http://127.0.0.1:8765'
ALIAS='http://127.0.0.1:8766'


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path)
    require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)), 'canonical transport binding required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'transport evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def command(host):
    require(host in HOSTS,'authorized transport host required')
    return ['gcloud','compute','ssh',host,'--project=saleseng','--zone=us-central1-a',
            '--tunnel-through-iap','--quiet','--','-N','-T','-C','-o','ExitOnForwardFailure=yes',
            '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
            '-R','127.0.0.1:8766:127.0.0.1:8765']


def identity(pid):
    require(type(pid)is int and pid>0,'exact transport PID required')
    result=subprocess.run(['/bin/ps','-p',str(pid),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=3)
    require(result.returncode==0,'selected transport process is absent')
    return result.stdout.strip()


def validate(row,*,host,coordinator):
    p=read(row)
    require(set(p)=={'schema_version','helper','coordinator','host','url','process','health'}
        and p['schema_version']==SCHEMA and p['helper']==binding(__file__)
        and p['host']==host and host in HOSTS and p['coordinator']==coordinator and p['url']==ALIAS,
        'closed same-authority transport selection required')
    c=read(coordinator)
    require(c['url']==PRIMARY and c['command'][3]=='serve'
        and c['command'][c['command'].index('--database')+1]==c['database']
        and binding(c['command'][2])['sha256']==c['code_sha256']
        and c['command'][2]in identity(c['pid']),'original coordinator process differs')
    proc=read(p['process']);require(set(proc)=={'host','pid','pgid','command','identity','started_at'}
        and proc['host']==host and proc['command']==command(host)
        and proc['pgid']==proc['pid']and identity(proc['pid'])==proc['identity'],
        'compressed alias process changed')
    words=shlex.split(proc['identity']);expected=command(host)[1:]
    require(len(words)>len(expected)and words[-len(expected):]==expected
        and any('gcloud'in word for word in words[:-len(expected)]),'alias command differs')
    health=read(p['health'])
    require(set(health)=={'status','host','url','coordinator','at','received_at','seconds','age_seconds','workbooks','events','publication_limit','models_dispatched','mutations'}
        and health['status']=='AUTHENTICATED_SAME_AUTHORITY_ALIAS'and health['host']==host
        and health['url']==ALIAS and health['coordinator']==coordinator
        and health['publication_limit']==1 and health['models_dispatched']==health['mutations']==0
        and 0<=health['age_seconds']<=30 and health['seconds']>=0,
        'original authenticated alias proof differs')
    return p['url']
