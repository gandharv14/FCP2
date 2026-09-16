"""Measured disk reserve for local collection of an already verified archive."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,shutil,tarfile,time
AUTH_SHA='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
SCHEMA='harbor-collection-disk-policy/v1'
SMALL_SCHEMA='harbor-collection-disk-policy/small-archive-v2'
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical collection evidence required')
 with p.open('rb')as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
 return dict(path=str(p),sha256=digest)
def checked(row):
 require(binding(row['path'])==row,'collection policy evidence changed');return json.loads(Path(row['path']).read_bytes())
def existing(path):
 p=Path(path);require(p.is_absolute()and '..'not in p.parts and not any(x.is_symlink()for x in(p,*p.parents)),'canonical collection allocation path required')
 while not p.exists():p=p.parent
 require(p.is_dir(),'allocation parent must be directory');return p

def requirements(plan,*,at_ns=None):
 require(plan['schema_version']=='harbor-fleet-collection-plan/v2','collection-only resource policy required')
 policy=checked(plan['disk_policy']);require(set(policy)=={'schema_version','helper','authorization','workbook','source_sha256','manifest','archive','reserve_bytes','allocation_factor','metadata_bytes','created_at_ns','expires_at_ns'}and policy['schema_version']in {SCHEMA,SMALL_SCHEMA},'closed collection disk policy required')
 small=policy['schema_version']==SMALL_SCHEMA
 require(binding(__file__)==policy['helper'],'selected collection disk helper differs')
 require(policy['authorization']['sha256']==AUTH_SHA and binding(policy['authorization']['path'])==policy['authorization'],'exact operational authorization required')
 now=time.time_ns()if at_ns is None else at_ns
 require(type(policy['created_at_ns'])is int and type(policy['expires_at_ns'])is int and policy['created_at_ns']<=now<policy['expires_at_ns']and policy['expires_at_ns']-policy['created_at_ns']<=(3600 if small else 86400)*10**9,'collection disk policy expired')
 require(policy['workbook']==plan['workbook']and policy['source_sha256']==plan['source_sha256']and policy['manifest']==plan['manifest']and policy['archive']==binding(plan['archive']),'collection policy scope differs')
 require(type(policy['reserve_bytes'])is int and policy['reserve_bytes']==(768*1024**2 if small else 2*1024**3) and type(policy['allocation_factor'])is int and policy['allocation_factor']==3 and type(policy['metadata_bytes'])is int and policy['metadata_bytes']==64*1024**2,'bounded collection reserve changed')
 manifest=checked(plan['manifest']);require(manifest['workbook']==plan['workbook']and manifest['archive_sha256']==policy['archive']['sha256'],'archive manifest differs')
 rows=manifest['files'];require(isinstance(rows,dict)and rows,'nonempty archive allocation required');directories=set();allocation=0
 with tarfile.open(plan['archive'],'r:gz')as archive:
  members=archive.getmembers();require(len(members)==len(rows)and {x.name for x in members}==set(rows),'archive allocation membership differs')
  for m in members:
   name=PurePosixPath(m.name);r=rows[m.name]
   require(m.isfile()and not name.is_absolute()and '..'not in name.parts and name.as_posix()==m.name and type(r['size_bytes'])is int and r['size_bytes']>=0 and m.size==r['size_bytes'],'unsafe archive allocation')
   with archive.extractfile(m)as f:require(hashlib.file_digest(f,'sha256').hexdigest()==r['sha256'],'archive allocation bytes differ')
   allocation+=max(4096,((m.size+4095)//4096)*4096)
   directories.update(str(x)for x in name.parents if str(x)!='.')
 allocation+=4096*len(directories)
 require(not small or allocation<=64*1024**2,'small archive allocation exceeds bounded policy')
 locations={str(existing(plan[k]))for k in ('code_root','output_root','journal')};locations.add(str(existing(Path(plan['archive']).parent)))
 devices={Path(p).stat().st_dev for p in locations};require(len(devices)==1,'collection policy requires one measured filesystem')
 return dict(expires_at_ns=policy['expires_at_ns'],policy=plan['disk_policy'],reserve_bytes=policy['reserve_bytes'],preclaim_bytes=policy['reserve_bytes']+policy['allocation_factor']*allocation+policy['metadata_bytes'],archive_bytes=Path(plan['archive']).stat().st_size,expanded_bytes=sum(x['size_bytes']for x in rows.values()),allocated_bytes=allocation,filesystem_paths=sorted(locations))

def check(measurement,*,during=False):
 require(time.time_ns()<measurement['expires_at_ns'],'collection resource policy expired during execution')
 required=measurement['reserve_bytes']if during else measurement['preclaim_bytes'];free=min(shutil.disk_usage(p).free for p in measurement['filesystem_paths'])
 require(free>=required,'measured collection disk reserve unavailable');return dict(required_bytes=required,free_bytes=free,during=during)
