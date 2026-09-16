"""Build a separately versioned 0006 timeout takeover reader.

The original task snapshot stays byte-identical. The new stopped-owner proof
names this actual derived takeover implementation. Every old lifecycle function
is preserved except the one explicitly bound stopped wall-time predicate.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import sys

SCHEMA='0006-source-preserving-timeout-takeover-reader/v1'
PREDICATE="type(guard['returncode']) is int"
REPLACEMENT="(type(guard['returncode']) is int or _fleet_retained_walltime(s,guard))"


def require(ok,message):
 if not ok:raise ValueError(message)


def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'symlink takeover reader')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
 require(binding(row['path'])==row,'takeover reader evidence changed');return json.loads(Path(row['path']).read_bytes())


def rendered(original,helper,profile):
 require(original.count(PREDICATE)==1,'original stopped-guard predicate changed')
 code=original.replace(PREDICATE,REPLACEMENT,1)
 digest=hashlib.sha256(original.encode()).hexdigest()
 # The prior immutable reader remains readable after this new reader becomes
 # the implementation named in subsequent stopped-owner proofs.
 code+='\n\nHISTORICAL_IMPLEMENTATIONS=HISTORICAL_IMPLEMENTATIONS | {'+repr(digest)+'}\n'
 code+='''\n\ndef _fleet_retained_walltime(s,guard):
    import hashlib,importlib.util,sys
    helper=__HELPER__
    profile=__PROFILE__
    path=Path(helper['path'])
    require(not any(p.is_symlink() for p in (path,*path.parents)) and hashlib.sha256(path.read_bytes()).hexdigest()==helper['sha256'],'exact stopped-timeout helper changed')
    name='selected_0006_timeout_witness'
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module.allowed_timeout(s,guard,profile)
'''.replace('__HELPER__',repr(helper)).replace('__PROFILE__',repr(profile))
 return code


def retain(path,raw):
 p=Path(path);fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return binding(p)


def build(original_copy,original_module,helper,profile,directory):
 require(binding(original_copy['path'])==original_copy and original_copy['sha256']==original_module['sha256'],
  'original task takeover bytes differ')
 for row in (helper,profile):require(binding(row['path'])==row,'root-selected timeout evidence differs')
 d=Path(directory);require(not d.exists(),'reader version already exists');d.mkdir(parents=True,mode=0o700)
 code=rendered(Path(original_copy['path']).read_text(),helper,profile)
 entry=retain(d/'takeover.py',code.encode())
 meta=dict(schema_version=SCHEMA,original_copy=original_copy,original_module=original_module,helper=helper,profile=profile,
  entry=entry,semantic_budget_reset=False,original_snapshot_unchanged=True,expired_replacement_limit=3)
 return retain(d/'manifest.json',(json.dumps(meta,sort_keys=True,indent=2)+'\n').encode())


def install(manifest):
 meta=read(manifest)
 require(set(meta)=={'schema_version','original_copy','original_module','helper','profile','entry','semantic_budget_reset',
  'original_snapshot_unchanged','expired_replacement_limit'} and meta['schema_version']==SCHEMA
  and meta['semantic_budget_reset']is False and meta['original_snapshot_unchanged']is True and meta['expired_replacement_limit']==3,
  'closed timeout-reader lineage required')
 for key in ('original_copy','original_module','helper','profile','entry'):require(binding(meta[key]['path'])==meta[key],'timeout reader dependency changed')
 require(meta['original_copy']['sha256']==meta['original_module']['sha256'],'original reader bytes differ')
 raw=Path(meta['original_copy']['path']).read_text();expected=rendered(raw,meta['helper'],meta['profile'])
 require(Path(meta['entry']['path']).read_bytes()==expected.encode(),'unreviewed takeover implementation change')
 name='scripts.batch.harbor_lease_takeover'
 spec=importlib.util.spec_from_file_location(name,meta['entry']['path']);m=importlib.util.module_from_spec(spec)
 sys.modules[name]=m;spec.loader.exec_module(m)
 import scripts.batch
 setattr(scripts.batch,'harbor_lease_takeover',m)
 require(m.MAX_REPLACEMENTS==3 and m.MAX_DURATION_NS==7200*10**9,'original lifecycle bounds changed')
 return m
