"""Explicit, authenticated use of an existing sudo Docker command.

The caller retains this profile as evidence. No privilege or daemon settings are
changed, and original dependency/runtime receipts record the real wrapper path.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,os,shlex,shutil,stat

SCHEMA='fleet-existing-sudo-docker-command/v1'
MARKER='HARBOR_BOUND_DOCKER_COMMAND'
AUTHORIZATION_SHA256='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'


def require(ok,message):
    if not ok:raise ValueError(message)


def check(row):
    require(isinstance(row,dict)and set(row)=={'path','sha256'},'closed Docker command binding required')
    p=Path(row['path']);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),
                              'canonical Docker command input required')
    raw=p.read_bytes();require(hashlib.sha256(raw).hexdigest()==row['sha256'],'Docker command input changed')
    return p,raw


def wrapper(sudo,docker):
    return ('#!/bin/sh\nexec '+shlex.quote(sudo)+' -n -- '+shlex.quote(docker)+' "$@"\n').encode()


def _binary(row):
    p,_=check(row);s=p.stat()
    require(s.st_uid==0 and not stat.S_IMODE(s.st_mode)&0o022 and os.access(p,os.X_OK),
            'existing root-owned Docker/sudo binary required')


@contextmanager
def installed(row,*,entrypoint,workbook,source):
    _,raw=check(row);p=json.loads(raw)
    require(set(p)=={'schema_version','helper','entrypoint','workbook','source','command','sudo','docker','authorization',
                     'existing_sudo_only','persistent_privilege_changes'}and p['schema_version']==SCHEMA
        and p['entrypoint']==entrypoint and p['workbook']==workbook and p['source']==source
        and p['existing_sudo_only']is True and p['persistent_privilege_changes']is False,
        'explicit source-bound existing-sudo Docker selection required')
    path,raw=check(p['helper']);require(path==Path(__file__).absolute(),'Docker scope implementation differs')
    for item in (entrypoint,source,p['authorization']):check(item)
    require(p['authorization']['sha256']==AUTHORIZATION_SHA256,'Docker command authority is not the user deployment authorization')
    for item in (p['sudo'],p['docker']):_binary(item)
    require(p['sudo']['path']=='/usr/bin/sudo'and p['docker']['path']=='/usr/bin/docker',
            'only the existing native sudo and Docker executables are supported')
    command,raw=check(p['command']);require(command.name=='docker'and raw==wrapper(p['sudo']['path'],p['docker']['path']),
                                           'Docker command is not the declared existing-sudo invocation')
    require(command.stat().st_uid==os.getuid()and stat.S_IMODE(command.stat().st_mode)==0o500
        and not stat.S_IMODE(command.parent.stat().st_mode)&0o022,'private executable Docker command required')
    before=os.environ.get('PATH');marker=os.environ.get(MARKER);identity=dict(profile=row,entrypoint=entrypoint,workbook=workbook,source=source)
    if marker is not None:
        inherited=json.loads(marker);require(set(inherited)=={'identity','pid'}and inherited['identity']==identity
            and type(inherited['pid'])is int and inherited['pid']!=os.getpid(),'nested or foreign Docker command scope')
    selected=str(command.parent);parts=(before or '').split(os.pathsep)
    os.environ['PATH']=os.pathsep.join([selected,*[x for x in parts if x!=selected]])
    os.environ[MARKER]=json.dumps(dict(identity=identity,pid=os.getpid()),sort_keys=True)
    try:
        require(shutil.which('docker')==str(command),'declared Docker executable was not selected')
        yield dict(command=p['command'],sudo=p['sudo'],docker=p['docker'])
        for item in (p['command'],p['sudo'],p['docker'],source):check(item)
    finally:
        if before is None:os.environ.pop('PATH',None)
        else:os.environ['PATH']=before
        if marker is None:os.environ.pop(MARKER,None)
        else:os.environ[MARKER]=marker
