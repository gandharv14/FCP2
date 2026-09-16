"""Restore exact historical generation aliases after verified file custody."""
import argparse,hashlib,json,os
from pathlib import Path

SCHEMA='harbor-historical-generation-aliases/v1'
def require(ok,message):
    if not ok:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def target_digest(path):
    p=Path(path);require(p.exists() and not any(x.is_symlink() for x in (p,*p.parents)),'regular resolved target required')
    def allowed(f):return not (f.name.startswith('.env') or f.name.endswith('.env') or f.name in {'credentials.json','application_default_credentials.json'} or f.suffix in {'.pem','.key'})
    require(allowed(p),'credential target forbidden')
    if p.is_file():return {'kind':'file','sha256':sha(p.read_bytes())}
    require(p.is_dir(),'unsupported alias target')
    rows=[]
    for f in sorted(p.rglob('*')):
        require(not f.is_symlink(),'resolved target contains another alias')
        require(allowed(f),'credential target forbidden')
        if f.is_file():rows.append((str(f.relative_to(p)),sha(f.read_bytes())))
    return {'kind':'directory','sha256':sha(canonical(rows)),'files':len(rows)}
def describe(path,root):
    p=Path(path).absolute();root=Path(root).absolute();require(p.is_relative_to(root) and p.is_symlink(),'selected historical alias required')
    require(not any(x.is_symlink() for x in p.parents),'alias parent cannot be symlink')
    link=os.readlink(p);require(not Path(link).is_absolute(),'relative generation alias required')
    target=p.resolve(strict=True);require(target.is_relative_to(root),'alias escaped selected historical root')
    return {'path':str(p),'link_text':link,'resolved_target':str(target),'target':target_digest(target)}
def restore(manifest,expected_sha,receipt,allowed_root):
    manifest=Path(manifest);receipt=Path(receipt);root=Path(allowed_root).absolute()
    require(sha(manifest.read_bytes())==expected_sha,'alias manifest changed');v=json.loads(manifest.read_bytes())
    require(set(v)=={'schema_version','root','aliases'} and v['schema_version']==SCHEMA and Path(v['root'])==root,'closed selected alias root required')
    require(not receipt.exists(),'alias receipt exists; reconcile instead of replay')
    require(not any(p.is_symlink() for p in (root,*root.parents)),'alias root is symlink')
    rows=v['aliases'];require(len({r['path'] for r in rows})==len(rows),'duplicate aliases')
    for row in rows:
        require(set(row)=={'path','link_text','resolved_target','target'},'alias fields changed')
        p=Path(row['path']);target=Path(row['resolved_target']);link=Path(row['link_text'])
        require(p.is_absolute() and target.is_absolute() and '..' not in p.parts and '..' not in target.parts and p.is_relative_to(root) and target.is_relative_to(root),'alias path escapes root')
        require(not link.is_absolute() and '..' not in link.parts,'alias target traversal forbidden')
        require(not any(x.is_symlink() for x in p.parents),'alias parent is symlink')
        require(target_digest(target)==row['target'],'transferred alias target changed')
        if p.exists() or p.is_symlink():require(p.is_symlink() and os.readlink(p)==row['link_text'] and p.resolve(strict=True)==target,'existing alias differs; no overwrite')
    # Directory aliases precede file aliases referring through them. Every
    # final resolved path is checked, with no arbitrary link execution.
    results=[]
    for row in sorted(rows,key=lambda r:(r['target']['kind']!='directory',r['path'])):
        p=Path(row['path']);p.parent.mkdir(parents=True,exist_ok=True)
        require((p.parent/row['link_text']).resolve(strict=True)==Path(row['resolved_target']),'alias resolution differs')
        existed=p.is_symlink()
        if not existed:os.symlink(row['link_text'],p)
        require(describe(p,root)==row,'restored alias differs')
        fd=os.open(p.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        results.append({'path':row['path'],'already_exact':existed})
    receipt.parent.mkdir(parents=True,exist_ok=True)
    with receipt.open('x') as f:json.dump({'schema_version':SCHEMA,'manifest_sha256':expected_sha,'aliases':results,'historical_bytes_rewritten':False},f,indent=2);f.flush();os.fsync(f.fileno())
    return {'aliases':len(rows),'receipt':str(receipt),'sha256':sha(receipt.read_bytes())}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--sha256',required=True);p.add_argument('--receipt',required=True);p.add_argument('--allowed-root',required=True);a=p.parse_args();print(json.dumps(restore(a.manifest,a.sha256,a.receipt,a.allowed_root)))
