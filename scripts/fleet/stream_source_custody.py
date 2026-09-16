"""Stream a verified source closure to a fresh remote archive without a local copy."""
from __future__ import annotations
import hashlib,json,os,platform,stat,subprocess,tarfile,time
from pathlib import Path
from scripts.fleet.migration import SCHEMA,binding,retain,safe


def inventory(paths):
    rows=[]
    for path in sorted({safe(p) for p in paths}):
        if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):raise ValueError('regular source files required')
        if path.name in {'.env','credentials.json','application_default_credentials.json'} or path.suffix in {'.pem','.key'}:raise ValueError('credential-like file forbidden')
        rows.append({**binding(path),'size':path.stat().st_size,'mode':stat.S_IMODE(path.stat().st_mode)&0o777,'member':str(path).lstrip('/')})
    if not rows:raise ValueError('empty transfer')
    return rows


class HashWriter:
    def __init__(self,sink):self.sink=sink;self.digest=hashlib.sha256();self.count=0
    def write(self,data):
        view=memoryview(data);offset=0
        while offset<len(view):
            wrote=self.sink.write(view[offset:])
            if wrote is None:wrote=len(view)-offset
            if wrote<=0:raise OSError('short stream write')
            offset+=wrote
        self.digest.update(data);self.count+=len(data);return len(data)
    def flush(self):self.sink.flush()


def stream(rows,sink):
    out=HashWriter(sink)
    with tarfile.open(fileobj=out,mode='w|gz',format=tarfile.PAX_FORMAT) as tar:
        for row in rows:
            path=safe(row['path'])
            if binding(path)!=dict(path=row['path'],sha256=row['sha256']) or path.stat().st_size!=row['size']:raise ValueError('source changed before stream')
            info=tar.gettarinfo(str(path),arcname=row['member']);info.uid=info.gid=info.mtime=0;info.uname=info.gname='';info.mode=row['mode']
            with path.open('rb') as source:tar.addfile(info,source)
    out.flush()
    return dict(sha256=out.digest.hexdigest(),bytes=out.count)


def manifest(rows,archive):
    return dict(schema_version=SCHEMA,archive_sha256=archive['sha256'],source_system=platform.system(),created_at_ns=time.time_ns(),files=rows,path_policy='preserve_absolute_paths',historical_bytes_rewritten=False,qualification_claimed=False)


def receiver(payload):
    """Executed remotely; uncertain streams retain intent and partial archive."""
    import hashlib,json,os,shutil,sys,time
    from pathlib import Path
    root=Path(payload['directory'])
    if not root.is_absolute() or '..' in root.parts or any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('canonical remote directory required')
    root.mkdir(parents=True,exist_ok=False,mode=0o700)
    if shutil.disk_usage(root).free<payload['source_bytes']+12*1024**3:raise ValueError('remote import disk reserve unavailable')
    def keep(path,value):
        with path.open('x') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    keep(root/'stream-intent.json',dict(root_intent=payload['root_intent'],automatic_retry=False,created_at_ns=time.time_ns()))
    sha=hashlib.sha256();count=0
    with (root/'closure.tar.gz').open('xb') as f:
        while data:=sys.stdin.buffer.read(1024*1024):
            f.write(data);sha.update(data);count+=len(data)
            if count>payload['source_bytes']+64*1024**2:raise ValueError('archive stream exceeds bound')
        f.flush();os.fsync(f.fileno())
    result=dict(archive=str(root/'closure.tar.gz'),sha256=sha.hexdigest(),bytes=count,root_intent=payload['root_intent'],automatic_retry=False)
    keep(root/'stream-receipt.json',result)
    print(json.dumps(result))
