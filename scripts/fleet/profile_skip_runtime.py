"""Declared profile fallback dependencies for retained environment children.

Only a source-bound, verified public-access skip may select this runtime. The
original task snapshot and pipeline commands stay fixed; new producer and
qualification code is explicit in the guarded child dependency receipt.
"""
from pathlib import Path
from contextlib import contextmanager
import hashlib,importlib,importlib.util,json,sys
SCHEMA='fleet-verified-profile-skip-runtime/v1'
MIXED='fleet-observed-public-profile-runtime/v2'
CORRECTED='fleet-observed-public-profile-correction/v3'
DERIVED='fleet-observed-public-profile-derivation/v4'
ROLES={'harbor_profile_readiness','harbor_profile_producer','harbor_transition_codec','harbor_environment_producer','harbor_complete_readiness','harbor_ready_transitions','harbor_tracker'}
FIELDS={'schema_version','helper','runner','runtime','runtime_profile','skip_helper','source','workbook','phase1','inventory','captures','original_snapshot','authorization'}
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'regular profile dependency required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
 require(set(row)=={'path','sha256'}and binding(row['path'])==row,'profile dependency changed');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'profile code changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def validate(row,env,content=None):
 p=checked(row);derived=p.get('schema_version')==DERIVED;corrected=p.get('schema_version')in(CORRECTED,DERIVED);mixed=p.get('schema_version')in(MIXED,CORRECTED,DERIVED)
 require(set(p)==(FIELDS|{'projection_helper','producer_correction','producer_derivation'}if derived else FIELDS|{'projection_helper','producer_correction'}if corrected else FIELDS|{'projection_helper'}if mixed else FIELDS)and p['schema_version']in(SCHEMA,MIXED,CORRECTED,DERIVED)and p['helper']==binding(__file__),'closed selected profile runtime required')
 for key in ('runner','runtime','skip_helper','authorization','source'):require(binding(p[key]['path'])==p[key],'profile policy/source dependency changed')
 snapshot=checked(p['original_snapshot']);root=Path(snapshot['root'])
 require(root==env[6]and p['original_snapshot']==env[5]['code'],'profile runtime differs from original configured snapshot')
 files=snapshot['files']
 if isinstance(files,list):
  require(all(set(x)=={'relative','sha256'}for x in files)and len({x['relative']for x in files})==len(files),'invalid snapshot inventory')
  require(all(not Path(x['relative']).is_absolute()and '..'not in Path(x['relative']).parts for x in files),'snapshot path escapes root')
  files={x['relative']:x['sha256']for x in files}
 require(isinstance(files,dict),'exact original inventory required')
 selected=checked(p['runtime_profile']);require(selected['runtime']==p['runtime']and selected['patches']==selected['symbols']==[]and not selected.get('factories')and not selected.get('historical_imports'),'profile policy requires explicit dependency-only runtime')
 deps=selected['dependencies'];roles=ROLES|{'harbor_profile_correction','harbor_profile_derivation','harbor_profile_presentation'}if derived else ROLES|{'harbor_profile_correction'}if corrected else ROLES;require(len(deps)==len(roles)and {x['module_name']for x in deps}=={'scripts.batch.'+r for r in roles},'unsupported profile dependency roles')
 for item in deps:
  name=item['module_name'].split('.')[-1];relative='scripts/batch/'+name+'.py'
  if name=='harbor_profile_correction':
   require(corrected and item['original']==item['selected'] and checked(p['producer_correction'])['helper']==item['selected'],'correction helper must be explicitly new and bound')
  elif name in ('harbor_profile_derivation','harbor_profile_presentation'):
   declaration=checked(p['producer_derivation']);key='helper'if name=='harbor_profile_derivation'else'presentation_helper'
   require(derived and item['original']==item['selected']==declaration[key],'derived helper must be explicitly new and bound')
  else:require(item['original']==dict(path=str(root/relative),sha256=files[relative]),'profile original module differs from full snapshot')
  for key in ('original','selected'):require(binding(item[key]['path'])==item[key],'profile selected module changed')
 if content is not None:
  require(content.workbook==p['workbook']and content.bindings['original_raw_source']==p['source']and content.bindings['normalized_phase1']==p['phase1']and content.bindings['variable_source_inventory']==p['inventory'],'profile source/phase1 selection differs')
 phase=checked(p['phase1']);inventory=checked(p['inventory']);captures=checked(p['captures'])
 require(phase['environment_id']=='fcp-'+p['workbook']and captures['schema_version']=='harbor-profile-captures/v1','profile capture workbook/schema differs')
 R=importlib.import_module('scripts.batch.harbor_profile_readiness')
 if mixed:
  R=module('_observed_profile_reader',next(x['selected']for x in deps if x['module_name']=='scripts.batch.harbor_profile_readiness'))
  require(binding(p['projection_helper']['path'])==p['projection_helper'],'observed projection helper changed')
 validator,_=R.modules(root)
 forbidden=validator.forbidden_values([Path(p['phase1']['path']),Path(p['inventory']['path'])]);sources=R.worklist(phase,forbidden,root=root)
 S=module('_verified_profile_capture_policy',p['skip_helper'])
 require(set(captures)=={'schema_version','sources'}and isinstance(captures['sources'],list)and all(isinstance(x,dict)and set(x)=={'canonical_url','captures'}for x in captures['sources'])and len({x['canonical_url']for x in captures['sources']})==len(captures['sources']),'duplicate or unbound profile capture sources')
 by={x['canonical_url']:x['captures']for x in captures['sources']}
 require(set(by)=={x['canonical_url']for x in sources}and sources,'exact source capture coverage required')
 skips=[]
 if mixed:
  P=importlib.import_module('scripts.batch.harbor_public_capture');Q=module('_observed_public_projection',p['projection_helper']);observed=0
  for source in sources:
   caps=by[source['canonical_url']]
   if any(checked(r).get('schema_version')==Q.SCHEMA for r in caps):
    require(len(caps)==1,'one exact retained public page required');value=checked(caps[0])
    require(value['helper']==p['projection_helper']and value['source']==p['source']and value['phase1']==p['phase1']and value['inventory']==p['inventory'],'observed page belongs to another source selection')
    Q.derive(caps[0],source['canonical_url'],forbidden,P);observed+=1
   else:
    try:R.captured_pages(caps,source['canonical_url'],forbidden,root=root)
    except (ValueError,OSError,UnicodeError):skips.append(S.validate(source,caps,forbidden,phase1=p['phase1'],inventory=p['inventory'],reader=R))
  require(observed>0,'observed profile runtime requires a retained page')
 else:skips=[S.validate(source,by[source['canonical_url']],forbidden,phase1=p['phase1'],inventory=p['inventory'],reader=R)for source in sources]
 require(all(x['reason'] in {'http_401','http_403'}for x in skips),'only verified public-access skips qualify')
 for skip in skips:
  prior=checked(skip['evidence'][1])['selection']
  require(prior['original_source']==p['source']and prior['workbook_id']==p['workbook'],'capture belongs to another original source')
 return p,skips

@contextmanager
def installed(row,env,content=None):
 p,skips=validate(row,env,content);runtime=module('_verified_profile_dependency_runtime',p['runtime']);profile=runtime.profile(p['runtime_profile'])
 import scripts.batch as package
 skip_name='scripts.batch.harbor_profile_skips';prior=sys.modules.get(skip_name);attribute=getattr(package,'harbor_profile_skips',None)
 if prior is not None:require(binding(prior.__file__)==p['skip_helper'],'foreign cached skip policy')
 selected=module(skip_name,p['skip_helper']);setattr(package,'harbor_profile_skips',selected)
 projection_name='scripts.batch.harbor_public_projection_recovery';projection_prior=sys.modules.get(projection_name);projection_attribute=getattr(package,'harbor_public_projection_recovery',None)
 if 'projection_helper'in p:
  if projection_prior is not None:require(binding(projection_prior.__file__)==p['projection_helper'],'foreign cached projection policy')
  projection=module(projection_name,p['projection_helper']);setattr(package,'harbor_public_projection_recovery',projection)
 correction_name='scripts.batch.harbor_profile_correction';correction_prior=sys.modules.get(correction_name);correction_attribute=getattr(package,'harbor_profile_correction',None)
 if 'producer_correction'in p:
  correction_binding=checked(p['producer_correction'])['helper']
  if correction_prior is not None:require(binding(correction_prior.__file__)==correction_binding,'foreign cached correction helper')
  correction=module(correction_name,correction_binding);setattr(package,'harbor_profile_correction',correction)
 extra_restores=[]
 if 'producer_derivation'in p:
  declaration=checked(p['producer_derivation'])
  for name,key in [('harbor_profile_derivation','helper'),('harbor_profile_presentation','presentation_helper')]:
   full='scripts.batch.'+name;prior_module=sys.modules.get(full);prior_attribute=getattr(package,name,None)
   if prior_module is not None:require(binding(prior_module.__file__)==declaration[key],'foreign cached profile derivation helper')
   selected_extra=module(full,declaration[key]);setattr(package,name,selected_extra)
   extra_restores.append((full,name,prior_module,prior_attribute))
 roots=[];type_restores=[]
 originals={x['module_name']:importlib.import_module(x['module_name'])for x in profile['dependencies']}
 try:
  with runtime.installed(p['runtime_profile']):
   for item in profile['dependencies']:
    m=importlib.import_module(item['module_name']);require(binding(m.__file__)==item['selected'],'selected profile dependency not installed')
    if item['module_name'] in {'scripts.batch.harbor_profile_producer','scripts.batch.harbor_environment_producer','scripts.batch.harbor_ready_transitions'}:
     roots.append((m,m.ROOT));m.ROOT=env[6]
   from dataclasses import is_dataclass,fields,MISSING
   for module_name,names in {'scripts.batch.harbor_profile_readiness':('ProfileSelection','ProfileCall'),'scripts.batch.harbor_complete_readiness':('ReadinessSelection','SemanticSelection')}.items():
    old=originals[module_name];new=importlib.import_module(module_name)
    for symbol in names:
     a=getattr(old,symbol);z=getattr(new,symbol)
     require(is_dataclass(a)and is_dataclass(z),'profile evidence class changed kind')
     prior_fields={f.name for f in fields(a)};new_fields={f.name for f in fields(z)}
     require(prior_fields<=new_fields and all(f.default is not MISSING or f.default_factory is not MISSING for f in fields(z)if f.name not in prior_fields),'profile class removed required history')
     for consumer in tuple(sys.modules.values()):
      ns=getattr(consumer,'__dict__',None)
      if ns is None:continue
      for key,value in tuple(ns.items()):
       if value is a:type_restores.append((ns,key,a));ns[key]=z
   producer_restore=None
   if 'producer_correction'in p:
    Q=importlib.import_module('scripts.batch.harbor_profile_correction');P=importlib.import_module('scripts.batch.harbor_profile_producer')
    correction,original_input,*_=Q.validate(p['producer_correction'],root=env[6])
    require(all(p[k]==correction[k]for k in ('source','phase1','inventory','captures','authorization')),'profile correction policy detached from original source')
    original_run=P.run;producer_restore=(P,original_run)
    def corrected_run(plan,*args,**kwargs):
     if plan!=correction['original_plan']:return original_run(plan,*args,**kwargs)
     Q.validate(p['producer_correction'],root=env[6])
     parent=Path(plan['path']).parent.parent
     if 'producer_derivation'in p:
      F=importlib.import_module('scripts.batch.harbor_profile_derivation');F.validate(p['producer_derivation'],root=env[6])
      folder=parent/'profiles-derived-003'
      inp=P.retained(parent/'profile-derived-input-003.json',dict(original_input,schema_version=P.DERIVATION_INPUT,producer_recovery=p['producer_correction'],producer_derivation=p['producer_derivation']))
     else:
      folder=parent/'profiles-correction-002'
      inp=P.retained(parent/'profile-correction-input-002.json',dict(original_input,schema_version=P.CORRECTION_INPUT,producer_recovery=p['producer_correction']))
     selected_plan=P.prepare(inp,folder)if not folder.exists()else P.record(folder/'plan.json')
     P.checked_plan(selected_plan)
     return original_run(selected_plan,*args,**kwargs)
    P.run=corrected_run
   try:yield p,skips
   finally:
    if producer_restore is not None:producer_restore[0].run=producer_restore[1]
 finally:
  for ns,key,value in reversed(type_restores):ns[key]=value
  for full,name,old_module,old_attribute in reversed(extra_restores):
   if old_module is None:sys.modules.pop(full,None)
   else:sys.modules[full]=old_module
   if old_attribute is None:delattr(package,name)
   else:setattr(package,name,old_attribute)
  for m,root in reversed(roots):m.ROOT=root
  if prior is None:sys.modules.pop(skip_name,None)
  else:sys.modules[skip_name]=prior
  if attribute is None:delattr(package,'harbor_profile_skips')
  else:setattr(package,'harbor_profile_skips',attribute)
  if 'producer_correction'in p:
   if correction_prior is None:sys.modules.pop(correction_name,None)
   else:sys.modules[correction_name]=correction_prior
   if correction_attribute is None:delattr(package,'harbor_profile_correction')
   else:setattr(package,'harbor_profile_correction',correction_attribute)
  if 'projection_helper'in p:
   if projection_prior is None:sys.modules.pop(projection_name,None)
   else:sys.modules[projection_name]=projection_prior
   if projection_attribute is None:delattr(package,'harbor_public_projection_recovery')
   else:setattr(package,'harbor_public_projection_recovery',projection_attribute)
