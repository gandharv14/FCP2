"""Typed history reader for the two already admitted source/environment imports.

It grants no fresh admission, rewrites no checkpoint and preserves the original
owning validators. The selected plan binds the completed native import lineage.
"""
from pathlib import Path
import hashlib,importlib.util,json,os,sys
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
D=ROOT/'diagnostics/vm-fleet-20260912'
PROFILES={
 '0006':dict(source='81953973e0bbde9508ba0c7ae53b5b725ffb988cf8cc072367e5d5bd0c4ec960',version='1857e0ce39d62f949149c1ad9d4b7a8e941153f6e7ec8c8b4a2ffe660b54b96a',publisher=dict(path=str(D/'holds-0006/publication-successor-001/publish.py'),sha256='9305a332cb17407f5b247034388a8e0f56614ac68cb3eac5d674b529b3672295')),
 '0190':dict(source='c6e9acb47959d5bcd2c6abac3f3111932a6e305025f59fe1881a03fe9f4292da',version='c7ad14b03f7c0ec3c6364efbb5e388affd9036ac15d745131ea306602700808f',publisher=dict(path=str(D/'holds-0190/publication-successor-001/publish_0190.py'),sha256='f43078222a61ce4465d92770fe2568bdd0789fbe046686ea37af38265cca470b'))}
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)),'regular exact history evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
 require(set(row)=={'path','sha256'} and binding(row['path'])==row,'continued history evidence changed');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'selected continuation reader changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);return m
def context():
 row=dict(path=os.environ['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN'],sha256=os.environ['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN_SHA256']);plan=checked(row);wb=plan['workbook'];require(wb in PROFILES,'closed continued workbook required');profile=PROFILES[wb]
 require(plan['schema_version']=='harbor-fleet-retained-environment-continuation/v1' and plan['source_sha256']==profile['source'] and plan['version']==profile['version'] and plan['publisher']==profile['publisher'],'reviewed source/code/history reader differs')
 P=module('selected_retained_environment_publisher_'+wb,profile['publisher']);authority=plan['history_authority']
 require(authority['workbook']==wb and authority['source_sha256']==profile['source'] and authority['code_root']==plan['code_root'],'continued history scope differs')
 if wb=='0006':
  P.continuation_binding(authority);h=checked(authority['continuation_history']);require(authority['source_continuation']['config']==plan['config'],'completed source config differs')
 else:
  H=P.existing_review_adapter(authority);h,line=H.validate(P,authority);require(line['config']==plan['config'],'completed existing-review config differs')
 return row,plan,profile,P,h

def live(plan,profile):
 require(os.environ.get('HARBOR_FLEET_WORKBOOK')==plan['workbook'],'continued workbook custody differs')
 from scripts.fleet.coordinator import Client
 c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE'])
 claim=c.call('heartbeat',workbook=plan['workbook'],owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
 require(claim['state']=='active' and claim['source_sha256']==profile['source'] and claim['version']==profile['version'],'continued code/source custody differs')
 return claim

def history_authority(item,intake,inventory,root):
 _,plan,profile,P,h=context();require(Path(plan['code_root'])==root and intake.bootstrap.workbook==plan['workbook'],'owning namespace differs')
 from scripts import harbor_campaign as C
 cfg=checked(plan['config']);require(C.decode(checked(cfg['intake']))==intake,'dispatcher intake differs from completed continuation')
 require({k:item['original_source'][k] for k in ('path','sha256')}==h['source'],'queue original source differs')
 require(list(map(str,intake.bootstrap.policy.history_roots))==h['roots'] and sorted(r['workbook_id'] for r in inventory['sources'])==h['known_ids'],'full original namespace/denominator differs')
 require(h['central_histories'].get(plan['workbook']) and h['fresh_admission_allowed'] is False,'retained history omitted')
 if os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1':live(plan,profile)
 def forbidden(*args,**kwargs):raise ValueError('fresh admission prohibited in retained environment continuation')
 from scripts.batch import harbor_admission as A
 A.DispatcherAdmission._check=forbidden
 return plan['history_authority']
