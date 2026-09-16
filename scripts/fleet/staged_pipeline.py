"""One fenced pool job: verified legacy source, then original bounded authoring.

The pool reserves an author slot for this entire job. Actual phase is reported
separately. Immutable phase intents forbid source, transition or author replay.
"""
from pathlib import Path
import argparse,json,os,sys,time
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,safe,retain
from scripts.fleet import fresh,source_first
from scripts.batch import harbor_legacy_research_history as L,harbor_legacy_staged_history as S
SCHEMA='harbor-fleet-staged-pipeline/v1'
FIELDS={'schema_version','workbook','source_plan','author_template','home','admission_policy','total_seconds','author_memory_bytes','automatic_retry'}
TEMPLATE_FIELDS={'schema_version','workbook','queue','inventory','history_roots','code_root','home','preparer','dispatcher','taxonomy','env_file','batch','owner','publication_root','output_root','reconstruction'}
def require(ok,message):
 if not ok:raise ValueError(message)

def load(selected):
 p=checked(selected);require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['automatic_retry']is False,'closed staged pipeline required')
 source,snapshot,_,_=source_first.load(p['source_plan']);old=L.validate(source['legacy_research']);template=checked(p['author_template'])
 require(source['schema_version']=='fleet-source-first/v3'and source['models']==0 and source['next_attempt']==source['cumulative_reconstruction_limit']==old['new_source_reconstruction_ordinal'],'exact finite source-only phase required')
 require(old['schema_version']in {S.SCHEMA,S.MCP_SCHEMA,L.OPAQUE_SOURCE_SCHEMA}and p['workbook']==source['workbook']==old['workbook'],'pipeline legacy source identity differs')
 require(set(template)==TEMPLATE_FIELDS and template['schema_version']==fresh.SCHEMA and template['workbook']==p['workbook'],'closed original fresh author template required')
 require(template['code_root']==snapshot['root']and Path(__file__).resolve().is_relative_to(snapshot['root']),'one selected code version must own both phases')
 require(template['inventory']==source['inventory']and template['history_roots']==source['history_roots']and template['home']==old['home']+'/authoring'and source['home']==old['home']+'/source'and p['home']==old['home']+'/pipeline','source and author namespaces detached')
 require(template['reconstruction']==dict(mode='existing',handoff=None,generated_curation=None),'author template must consume the exact future source result')
 require(template['dispatcher']==binding(Path(snapshot['root'])/'scripts/fleet/advance_authoring_v8.py'),'original selected dispatcher required')
 for key in ('preparer','taxonomy'):require(binding(template[key]['path'])==template[key],'author dependency changed')
 queue=checked(template['queue']);inventory=checked(source['inventory']);items=[i for i in queue['items']if i['workbook_id']==p['workbook']];sources=[i for i in inventory['sources']if i['workbook_id']==p['workbook']]
 require(len(items)==len(sources)==1 and items[0]['original_source']==sources[0]==source['source'],'original queue source differs')
 require(items[0]['history_class']=='legacy_model_artifacts'and items[0]['preserved_history']==old['histories'][p['workbook']]and items[0]['preserved_curations']==old['curations'],'opaque consumption or curation changed')
 require(type(p['total_seconds'])is int and p['total_seconds']==86400 and type(p['author_memory_bytes'])is int and p['author_memory_bytes']==8*1024**3,'bounded pipeline guards differ')
 require(source['seconds']+450+60<p['total_seconds'],'pipeline has no remaining author guard')
 for path in (p['home'],source['home'],template['home']):safe(path)
 require(binding(p['admission_policy']['path'])==p['admission_policy'],'selected admission policy changed')
 return p,source,old,template

def inspect(selected):
 p,source,old,template=load(selected)
 require(not any(Path(x).exists()for x in (p['home'],source['home'],template['home'])),'pipeline or phase already consumed')
 proof=source_first.inspect(p['source_plan'])
 fresh.load_module('staged_pipeline_preparer',template['preparer']);fresh.load_module('staged_pipeline_dispatcher',template['dispatcher'])
 return dict(status='STAGED_PIPELINE_PREFLIGHT_PASS',plan=selected,source=proof,source_qualified=False,actual_phase='NOT_STARTED',capacity_reservation='author',claims_changed=0,models_dispatched=0)

def source_handoff(p,source):
 result_binding=binding(Path(source['home'])/'result.json');result=checked(result_binding)
 require(result['status']=='RECONSTRUCTION_VERIFIED'and result['plan']==p['source_plan']and result['legacy_research']==source['legacy_research']and result['models_dispatched']==0 and result['cumulative_reconstruction_ordinal']==source['next_attempt'] and result['automatic_retry']is False,'actual finite source completion required')
 ledger=checked(result['ledger']);require(ledger['build']['state']=='VERIFIED','source ledger did not qualify')
 corpus=Path(source['home'])/'corpus';wb=p['workbook'];generations={}
 for kind,folder in (('source','sources'),('segmentation','segments')):
  generation=ledger['build'][kind+'_generation_id'];require(isinstance(generation,str)and len(generation)==64 and all(c in '0123456789abcdef'for c in generation),'invalid source generation identity')
  generations[kind+'_manifest']=binding(corpus/folder/wb/'generations'/generation/'generation-manifest.json')
 return dict(workbook=wb,state='VERIFIED',ledger=result['ledger'],**generations,source_result=result_binding,consumed_source_attempts=source['next_attempt'],source_reconstruction_automatic_retry=False)

def prepare_author(p,source,old,template):
 """Deterministic local transition; original source and fresh validators own it."""
 directory=Path(p['home'])/'transition';require(not directory.exists(),'author transition consumed; no replay')
 handoff=source_handoff(p,source);directory.mkdir(mode=0o700)
 hand=retain(directory/'handoff.json',handoff)
 opaque=old['schema_version']==L.OPAQUE_SOURCE_SCHEMA
 prior=old['opaque_history']['consumption_upper_bounds']if opaque else dict(naturalization_attempts=old['staged_history']['naturalization_reservations_upper_bound'],dialogue_reviews=old['staged_history']['dialogue_rounds_upper_bound'])
 authority={**old,'schema_version':L.OPAQUE_AUTHOR_SCHEMA if opaque else S.AUTHOR_SCHEMA,'authoring':dict(source_authority=source['legacy_research'],source_result=handoff['source_result'],snapshot=source['snapshot'],new_nat_maximum_attempts=2,cumulative_nat_upper_bound=prior['naturalization_attempts']+2,new_dialogue_reviews=2,cumulative_dialogue_reviews_upper_bound=prior['dialogue_reviews']+2)}
 authority_binding=retain(directory/'author-history.json',authority);L.validate(authority_binding);L.source_handoff(authority_binding,handoff)
 author=retain(directory/'author-plan.json',{**template,'legacy_research_history':authority_binding,'reconstruction':dict(mode='existing',handoff=hand,generated_curation=None)})
 fresh.inspect(author,authority_binding)
 return author

def guarded_phase(p,name,command,seconds,memory_bytes,*,models):
 from scripts.batch.hardened_runner import _run_in_process_group
 require(type(seconds)is int and seconds>0,'no phase runway')
 root=Path(p['home']);intent=retain(root/(name+'-intent.json'),dict(command=command,timeout_seconds=seconds,memory_bytes=memory_bytes,models=models,automatic_retry=False));metrics={};rc=None;failure=None
 env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
 for key in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_HISTORY_SHA256'):env.pop(key,None)
 try:
  with(root/(name+'.log')).open('xb')as log:rc=_run_in_process_group(command,stdout=log,timeout_seconds=seconds,grace_seconds=5,memory_limit_bytes=memory_bytes,metrics=metrics,cwd=Path(checked(p['author_template'])['code_root']),env=env)
 except Exception as exc:failure=type(exc).__name__
 result=retain(root/(name+'-resource.json'),dict(intent=intent,returncode=rc,failure_type=failure,metrics=metrics,log=binding(root/(name+'.log')),automatic_retry=False))
 require(rc==0 and failure is None,name+' requires reconciliation; do not replay')
 return result

def wait_author_admission(p,deadline):
 from scripts.fleet.coordinator import Client
 module=fresh.load_module('staged_pipeline_admission',p['admission_policy'])
 client=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE'])
 while True:
  require(time.monotonic()<deadline,'author admission wait exhausted pipeline runway')
  status=client.call('status');module.validate_occupancy(status)
  rows=[r for r in status['workbooks']if r['workbook']==p['workbook']]
  require(len(rows)==1 and rows[0]['state']=='active'and rows[0]['owner']==os.environ['HARBOR_FLEET_OWNER']and rows[0]['fence']==int(os.environ['HARBOR_FLEET_FENCE'])and rows[0]['expires']>status['at'],'pipeline lost exact live custody')
  source=checked(p['source_plan']);snapshot=checked(source['snapshot'])
  require(rows[0]['source_sha256']==source['source']['sha256']and rows[0]['version']==snapshot['fingerprint'],'pipeline source or code custody changed')
  require(not any(o['workbook']==p['workbook']and o['state']in {'active','hold'}for o in status['operations']),'source phase has an unexpected owning operation')
  if not module.model_admission_barriers(status,p['workbook'],rows[0]['owner']):return
  time.sleep(5)

def run(selected):
 from scripts.fleet.python_entry import command
 inspect(selected);p,source,old,template=load(selected);root=Path(p['home'])
 require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook']and int(os.environ.get('HARBOR_FLEET_FENCE','0'))==1,'one original fenced worker must own both phases')
 root.mkdir(parents=True,mode=0o700);retain(root/'intent.json',dict(plan=selected,driver=binding(__file__),automatic_retry=False,capacity_reservation='author'));started=time.monotonic()
 try:
  source_resource=guarded_phase(p,'source',command(sys.executable,source['helper']['path'],'run','--plan',p['source_plan']['path'],'--sha256',p['source_plan']['sha256']),source['seconds']+450,source['memory_bytes'],models=False)
  handoff=source_handoff(p,source);retain(root/'source-verified.json',dict(source=handoff,resource=source_resource,actual_phase='WAITING_FOR_AUTHOR_ADMISSION'))
  wait_author_admission(p,started+p['total_seconds']-60)
  author=prepare_author(p,source,old,template)
  remaining=int(p['total_seconds']-(time.monotonic()-started)-60)
  author_resource=guarded_phase(p,'author',command(sys.executable,str(Path(template['code_root'])/'scripts/fleet/fresh.py'),'run','--plan',author['path'],'--plan-sha256',author['sha256']),remaining,p['author_memory_bytes'],models=True)
  result=binding(Path(template['home'])/'result.json');ready=checked(result)
  require(ready['status']=='PUBLICATION_READY'and ready['plan']==author and ready['workbook']==p['workbook']and ready['publication_performed']is False,'original author did not reach publication readiness')
  retain(root/'completed.json',dict(plan=selected,source=handoff,author_plan=author,author_resource=author_resource,result=result,automatic_retry=False))
  return result
 except Exception as exc:
  retain(root/'held.json',dict(plan=selected,failure_type=type(exc).__name__,automatic_retry=False));raise

def actual_phase(selected):
 """Observation only; phase is distinct from the whole-job author reservation."""
 p=checked(selected);require(p['schema_version']==SCHEMA,'staged pipeline required');root=Path(p['home'])
 if(root/'completed.json').exists():return 'PUBLICATION_READY'
 if(root/'held.json').exists():return 'HELD'
 if(root/'author-intent.json').exists():return 'AUTHORING'
 if(root/'source-verified.json').exists():return 'WAITING_FOR_AUTHOR_ADMISSION'
 if(root/'source-intent.json').exists():return 'SOURCE_RECONSTRUCTION'
 return 'NOT_STARTED'

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['inspect','run','status']);parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True);a=parser.parse_args();row=dict(path=str(Path(a.plan).absolute()),sha256=a.sha256);print(json.dumps(actual_phase(row)if a.action=='status'else globals()[a.action](row)))
