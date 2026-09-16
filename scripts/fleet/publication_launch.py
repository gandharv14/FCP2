"""Select one verified publication from a stopped worker; never replay uncertainty."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,shlex,subprocess,sys,time
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,safe
from scripts.fleet.pool import lock,persist
from scripts.fleet.coordinator import Client
from scripts.fleet.prepare_queue import HOSTS
SCHEMA='harbor-fleet-publication-launch/v1'
FIELDS={'schema_version','host','plan','publisher','implementation','proof','guard','worker_outcome','process','process_copy','expected_fence','expected_owner','coordinator_receipt','owner','python','capacity_policy'}
def require(ok,message):
 if not ok:raise ValueError(message)
def module(name,row):
 require(binding(row['path'])==row,'selected module changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def history_import_repair(publisher,fresh,old_row,new_row):
 """One exact optional-history import correction; all owning work stays intact."""
 selector="""def selected_history_survey(plan):
    if plan.get('semantic_continuation')is not None:return None
    if 'continuation_history'in plan:
        from scripts.fleet.continuation_history import projected_survey
    else:
        from scripts.fleet.history import projected_survey
    return projected_survey
"""
 anchor="    if 'continuation_history' in plan:\n        from scripts.fleet.continuation_history import projected_survey\n    else:\n        from scripts.fleet.history import projected_survey"
 child="    from scripts.fleet.history import projected_survey\n    history_survey(pub,plan,history,projected_survey)"
 if publisher.count(anchor)!=1 or fresh.count(child)!=1 or 'def selected_history_survey('in fresh:
  raise ValueError('unrecognized retained-history import failure shape')
 if publisher.count(repr(old_row))!=2:raise ValueError('retained fresh dependency load differs')
 publisher=publisher.replace(anchor,'    projected_survey = fresh_publication.selected_history_survey(plan)').replace(repr(old_row),repr(new_row))
 fresh=fresh.replace(child,'    history_survey(pub,plan,history,selected_history_survey(plan))')+'\n\n'+selector
 return publisher,fresh

def legacy_environment_repair(publisher):
 """Insert only the reviewed direct-warning legacy history environment branch."""
 anchor="        env.update({prefix+'PATH': history['path'], prefix+'SHA256': history['sha256']})"
 addition="""        warning = plan.get('retained_warning', {})
        if 'continuation_plan' in warning:
            prior = checked(warning['continuation_plan'])
            if 'live_renewal' in prior:
                renewal = checked(prior['live_renewal']['plan'])
                fresh = checked(renewal['fresh_plan'])
                legacy = fresh.get('legacy_research_history')
                if legacy is not None:
                    inherited = checked(legacy)
                    require(prior['workbook'] == renewal['workbook'] == fresh['workbook'] == plan['workbook']
                        and inherited['workbook'] == plan['workbook']
                        and inherited['original'] == renewal['source']
                        and inherited['original']['sha256'] == prior['source_sha256'] == plan['source_sha256'],
                        'publication legacy history detached from original source')
                    history = legacy
                    prefix = 'HARBOR_FLEET_LEGACY_RESEARCH_'
"""
 import ast
 tree=ast.parse(publisher)
 functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='publication_environment']
 if len(functions)!=1:raise ValueError('unique original publication environment required')
 node=functions[0];lines=publisher.splitlines(True);original=''.join(lines[node.lineno-1:node.end_lineno])
 if original.count(anchor)!=1 or "warning = plan.get('retained_warning'" in original:
  raise ValueError('unrecognized original publication environment')
 selected=original.replace(anchor,addition+anchor)
 lines[node.lineno-1:node.end_lineno]=[selected]
 return ''.join(lines)

def ready_config_repair(publisher,fresh,old_row,new_row):
 """Change only the exact completed-tail config read and its closed resolver."""
 anchor='job["config"] == ready["config"]'
 resolver='def ready_config(pub,plan,ready,job):\n    """Read the original job\'s config when a completed tail omits duplication."""\n    if \'config\'in ready:return ready[\'config\']\n    a=plan[\'mechanical_tail\'];selected=pub.checked(a[\'plan\']);old=pub.checked(selected[\'original\'])\n    from pathlib import Path\n    pub.require(selected[\'helper\']==a[\'reader\'] and selected[\'workbook\']==ready[\'workbook\']==plan[\'workbook\']\n        and selected[\'source\'][\'sha256\']==plan[\'source_sha256\']\n        and ready[\'continuation\']==a[\'plan\'] and ready[\'status\']==\'PUBLICATION_READY\'\n        and ready[\'job\']==pub.binding(Path(selected[\'output\'])/\'authoring-job.json\')\n        and pub.checked(ready[\'job\'])==job and pub.checked(old[\'original_job\'])[\'config\']==job[\'config\'],\n        \'completed mechanical READY config differs from original job\')\n    return job[\'config\']\n\n\n'
 if publisher.count(anchor)!=1 or publisher.count(repr(old_row))!=2 or 'def ready_config('in fresh or fresh.count('def mechanical_dispatcher(')!=1:
  raise ValueError('unrecognized completed READY config reader')
 publisher=publisher.replace(anchor,'job["config"] == fresh_publication.ready_config(sys.modules[__name__], plan, ready, job)').replace(repr(old_row),repr(new_row))
 return publisher,fresh.replace('def mechanical_dispatcher(',resolver+'def mechanical_dispatcher(',1)

def mechanical_evidence_repair(publisher,fresh,old_row,new_row):
 """Retain historical alias metadata while hashing its actual archived input."""
 anchor="selected['failed_outcome'],*selected['instruction_archive'].values()]"
 selected="selected['failed_outcome'],*[value for key,value in selected['instruction_archive'].items()if key!='original']]"
 if fresh.count(anchor)!=1 or publisher.count(repr(old_row))!=2:
  raise ValueError('unrecognized completed mechanical input closure')
 return publisher.replace(repr(old_row),repr(new_row)),fresh.replace(anchor,selected)

def semantic_evidence_repair(publisher,fresh,old_row,new_row):
 """Select only the completed physical/semantic child evidence closure."""
 anchor="        retained=selected.get('retained_context');takeover=selected['completed_takeover']\n        rows += [*[value for key,value in a.items()if key!='tail'],selected['authorization'],selected['capacity_policy'],\n            *([retained[k]for k in ('helper','plan','config','snapshot')]if retained is not None\n              else [selected['fresh_plan'],selected['original_job']]),*takeover.values()]\n        if a.get('tail')is not None:\n            rows += list(a['tail'].values())\n"
 selected="        if a.get('completed_reader') is not None:\n            M=pub.load('completed_semantic_publication_dependencies',a['completed_reader'])\n            M.history(pub,plan);physical=pub.checked(selected['physical_context'])\n            rows += [*a.values(),selected['physical_context']]\n            rows += [selected[k]for k in ('authorization','capacity_policy','renewal_module',\n                'fresh_plan','original_job','outcome','result','descriptor','blocked')]\n            rows += [physical[k]for k in ('helper','config','snapshot','old_snapshot','fresh_plan',\n                'original_job','outcome','result','preview','renewal','takeover')]\n        else:\n            retained=selected.get('retained_context');takeover=selected['completed_takeover']\n            rows += [*[value for key,value in a.items()if key!='tail'],selected['authorization'],selected['capacity_policy'],\n                *([retained[k]for k in ('helper','plan','config','snapshot')]if retained is not None\n                  else [selected['fresh_plan'],selected['original_job']]),*takeover.values()]\n            if a.get('tail')is not None:\n                rows += list(a['tail'].values())\n"
 if fresh.count(anchor)!=1 or publisher.count(repr(old_row))!=2:
  raise ValueError('unrecognized completed semantic input closure')
 return publisher.replace(repr(old_row),repr(new_row)),fresh.replace(anchor,selected)


def semantic_root_repair(publisher,fresh,old_row,new_row):
 """Change only the completed semantic owning-root gate and closed resolver."""
 resolver='def owning_root(pub,plan):\n    """Authenticate the selected semantic root while retaining original config custody."""\n    from pathlib import Path\n    a=plan.get(\'semantic_continuation\')\n    if a is None or a.get(\'completed_reader\')is None:return Path(plan[\'code_root\'])\n    M=pub.load(\'completed_semantic_owning_root\',a[\'completed_reader\'])\n    M.history(pub,plan)\n    selected=pub.checked(a[\'plan\'])\n    if selected[\'schema_version\']==\'fleet-completed-instruction-continuation/v1\':\n        chosen=selected[\'semantic_context\'];selected=pub.checked(chosen[\'plan\'])\n        pub.require(set(chosen)=={\'helper\',\'plan\'} and selected[\'helper\']==chosen[\'helper\'],\n            \'semantic owning root lost retained context\')\n    context=selected[\'retained_context\'];q=pub.checked(context[\'plan\'])\n    pub.require(selected[\'schema_version\']==\'fleet-instruction-semantic-continuation/v1\'\n        and context.get(\'kind\')==\'completed_disclosure\' and selected[\'fresh_plan\']==context[\'plan\']==plan[\'fresh_authoring\']\n        and q[\'schema_version\']in {\'fleet-retained-completed-disclosure/v1\',\'fleet-retained-completed-aggregate/v1\'}\n        and q[\'snapshot\']==context[\'snapshot\'] and q[\'config\']==context[\'config\']\n        and q[\'helper\']==context[\'helper\'] and q[\'workbook\']==selected[\'workbook\']==plan[\'workbook\']\n        and q[\'source\']==selected[\'source\'] and q[\'source\'][\'sha256\']==plan[\'source_sha256\'],\n        \'semantic owning root detached from selected source\')\n    root=Path(pub.checked(q[\'snapshot\'])[\'root\'])\n    pub.require(root.is_absolute() and root.name==M.version(plan,pub.checked),\'semantic owning root version changed\')\n    return root\n'
 anchor='env[6] == root and job["config"] == fresh_publication.ready_config(sys.modules[__name__], plan, ready, job)'
 if publisher.count(anchor)!=1 or publisher.count(repr(old_row))!=2 or 'def owning_root('in fresh:
  raise ValueError('unrecognized completed semantic owning-root gate')
 publisher=publisher.replace(anchor,'env[6] == fresh_publication.owning_root(sys.modules[__name__], plan) and job["config"] == fresh_publication.ready_config(sys.modules[__name__], plan, ready, job)')
 return publisher.replace(repr(old_row),repr(new_row)),fresh+'\n\n'+resolver


def queued_preowning(p,plan,edge,read,bind,ancestry,*,local=False):
 """Compose settled pre-P custody with one original expired queued lease CAS."""
 import copy
 def need(ok,message):
  if not ok:raise ValueError(message)
 need(set(edge)-{'custody'}=={'schema_version','prior_profile','reader','completion'},'closed queued pre-owning composition required')
 original_read=read;copies={}
 if 'custody'in edge:
  custody=read(edge['custody'])
  need(set(custody)=={'schema_version','files'} and custody['schema_version']=='fleet-queued-publication-custody/v1',
       'closed completed queued custody required')
  for item in custody['files']:
   need(set(item)=={'native','copy'} and item['native']['sha256']==item['copy']['sha256']
        and item['native']['path']not in copies,'queued custody identity differs')
   copies[item['native']['path']]=item
 if local:
  need(bool(copies),'local queued custody required')
  def read(row):
   item=copies.get(row['path'])
   if item is None:return original_read(row)
   need(item['native']==row,'queued custody native hash differs')
   return original_read(item['copy'])
 base=read(edge['prior_profile']);baseplan=read(base['plan'])
 need('preowning_recovery'in base and 'owning_recovery'not in base and 'queued_lease'not in baseplan,
      'one settled original pre-owning chain required')
 result=preowning(base,baseplan,original_read,bind,_ancestry=ancestry,local=local)
 allowed={'plan','proof','guard','preowning_recovery','qualification_capture','input_history','owner'}
 need({k:v for k,v in p.items()if k not in allowed}=={k:v for k,v in base.items()if k not in allowed},
      'queued recovery changed stopped publication authority')
 need({k:v for k,v in plan.items()if k not in {'journal','queued_lease','publication_wait'}}
      =={k:v for k,v in baseplan.items()if k not in {'journal','publication_wait'}} and plan['journal']!=baseplan['journal']
      and plan.get('queued_lease')==dict(reader=edge['reader'],completion=edge['completion']),
      'queued recovery changed original publication work')
 need(bind(edge['reader']['path'])==edge['reader']
      and edge['reader']['sha256']=='7f8530fa6b7e844ef3d5785408a923b676ac78212007d3abd9b13cabdfcf3884',
      'unknown original expired queued reader')
 v=read(edge['completion']);intent=read(v['intent']);profile=intent['profile']
 if copies:
  expected=[edge['completion'],v['intent'],v['before'],v['after'],v['projected_checkpoint'],plan['ready']]
  need({r['path']:r for r in expected}=={k:x['native']for k,x in copies.items()},'queued custody inventory differs')
 before=read(v['before']);after=read(v['after']);old=before['leases']['task_agent'];new=after['leases']['task_agent']
 qualified=read(profile['ready_proof'])
 need(set(v)=={'schema_version','status','intent','before','after','original_checkpoint','projected_checkpoint','models_dispatched','original_checkpoint_rewritten','automatic_retry'}
      and v['schema_version']=='harbor-expired-queued-publication/v1'and v['status']=='EXPIRED_QUEUED_LEASE_REPLACED'
      and v['models_dispatched']==0 and v['original_checkpoint_rewritten']is False and v['automatic_retry']is False,
      'original expired queued completion required')
 need(intent['schema_version']==v['schema_version'] and intent['automatic_retry']is False
      and intent['before']==v['before'] and profile['schema_version']==v['schema_version']
      and profile['tracker']==read(base['proof'])['tracker'] and v['before']['sha256']==profile['tracker']['sha256']
      and profile['workbook']==plan['workbook'] and profile['source_sha256']==plan['source_sha256']
      and qualified['plan']==base['plan'] and qualified['tracker']==profile['tracker']
      and qualified['request']==intent['request']
      and qualified['status']=='ACTUAL_QUEUED_QUALIFICATION_PROOF_PASSED'
      and qualified['models_dispatched']==qualified['lease_mutations']==qualified['publication_mutations']==0
      and intent['request']['checkpoint']==v['original_checkpoint'],
      'queued replacement detached original READY or source')
 proof=read(p['proof'])
 need(proof['tracker']['path']==profile['tracker']['path'] and proof['tracker']['sha256']==v['after']['sha256'],
      'new qualification is not exact replacement tracker')
 need(('publication_wait'in plan)==('publication_wait'in baseplan),'queued recovery added or removed wait authority')
 if 'publication_wait'in plan:
  oldwait=read(baseplan['publication_wait']);newwait=read(plan['publication_wait'])
  need({k:v for k,v in oldwait.items()if k!='lease_tracker'}=={k:v for k,v in newwait.items()if k!='lease_tracker'}
       and oldwait['lease_tracker']==profile['tracker'] and newwait['lease_tracker']==proof['tracker'],
       'queued recovery changed wait limits or detached lease authority')
 event=after['handoffs'][-1]
 need(after['handoffs'][:-1]==before['handoffs'] and after['revision']==before['revision']+1
      and event==dict(schema_version='harbor-lease-event/v1',operation='replace_expired',lease_kind='task_agent',root_actor='harbor-orchestrator',
          before=old,after=new,allowance=profile['allowance'],stopped_owner_proof=profile['stopped_owner_proof'],
          at=event['at'],revision=after['revision'],prior_tracker_sha256=profile['tracker']['sha256'])
      and event['at']>=old['expires_at'],'one original expired queued lease event required')
 need(new==dict(owner=profile['owner'],token=new['token'],scope=old['scope'],acquired_at=event['at'],heartbeat_at=event['at'],
      expires_at=event['at']+14400*10**9,acquisition_revision=after['revision'])and new['token']!=old['token'],
      'queued replacement token or duration differs')
 expected=copy.deepcopy(before);expected.update(revision=after['revision'],updated_at=event['at'],handoffs=after['handoffs']);expected['leases']['task_agent']=new
 need(after==expected,'queued replacement changed source, work or quality')
 return result

def preowning(p,plan,read,bind,*,_ancestry=(),local=False):
 """Authenticate a stopped pre-P attempt while preserving its READY ancestry."""
 import ast,json
 from pathlib import Path
 def need(ok,message):
  if not ok:raise ValueError(message)
 # A later diagnosed pre-P defect can follow a settled earlier defect. Check
 # every retained edge with the same original validator; no flattened history.
 # Four edges is an explicit finite control-repair allowance, not retry logic.
 edge=p['preowning_recovery'];identity=(edge['path'],edge['sha256'])
 need(len(_ancestry)<4,'pre-owning recovery ancestry allowance exhausted')
 need(all(identity[0]!=x[0] and identity[1]!=x[1] for x in _ancestry),'cyclic pre-owning recovery ancestry')
 v=read(edge)
 if v.get('schema_version')=='fleet-publication-preowning-queued-recovery/v1':
  return queued_preowning(p,plan,v,read,bind,(*_ancestry,identity),local=local)
 need(set(v)=={'schema_version','prior_profile','root_intent','failure_proof','reconciliation'} and v['schema_version']in {'fleet-publication-preowning-recovery/v1','fleet-publication-retained-history-import-recovery/v1','fleet-publication-legacy-environment-recovery/v1','fleet-publication-completed-ready-config-recovery/v1','fleet-publication-mechanical-evidence-recovery/v1','fleet-publication-semantic-evidence-recovery/v1','fleet-publication-semantic-root-recovery/v1'},'closed pre-owning recovery required')
 import_repair=v['schema_version']=='fleet-publication-retained-history-import-recovery/v1'
 environment_repair=v['schema_version']=='fleet-publication-legacy-environment-recovery/v1'
 config_repair=v['schema_version']=='fleet-publication-completed-ready-config-recovery/v1'
 evidence_repair=v['schema_version']=='fleet-publication-mechanical-evidence-recovery/v1'
 semantic_repair=v['schema_version']=='fleet-publication-semantic-evidence-recovery/v1'
 semantic_root=v['schema_version']=='fleet-publication-semantic-root-recovery/v1'
 old=read(v['prior_profile']);oldplan=read(old['plan']);root=read(v['root_intent']);proof=read(old['proof'])
 need(read(p['proof'])['tracker']==proof['tracker'],'new publication proof changed stopped tracker')
 need(root['profile']==v['prior_profile'],'exact stopped predecessor required')
 if 'preowning_recovery' in old:
  preowning(old,oldplan,read,bind,_ancestry=(*_ancestry,identity),local=local)
 need({k:x for k,x in oldplan.items()if k!='journal'}=={k:x for k,x in plan.items()if k!='journal'} and oldplan['journal']!=plan['journal'],'recovery changed publication work or reused journal')
 for k in ('host','python','worker_outcome','process','process_copy'):
  need(old[k]==p[k],'recovery detached original READY worker')
 need(p['expected_owner']==old['owner'] and p['expected_fence']==old['expected_fence']+1 and p['owner']!=old['owner'],'recovery custody differs')
 native=read(v['failure_proof'])
 if import_repair or environment_repair or config_repair or evidence_repair or semantic_repair or semantic_root:
  observed=native
  if import_repair:need(plan.get('semantic_continuation')is not None and not any(k in plan for k in ('history','continuation_history','source_continuation')),'only exact retained semantic bootstrap supported')
  need(observed['tracker']==proof['tracker'] and observed['execution_state']=='publication_queued','retained queued tracker changed')
 else:
  need(native['returncode']==0,'failure observation failed');observed=json.loads(native['stdout'])
  need(observed['tracker']==proof['tracker'] | {'size':observed['tracker']['size']},'stopped tracker differs')
 need(not observed['processes'] and observed['owning_intent_exists']is False,'owning process or intent changed')
 files=observed['files'];journal=Path(oldplan['journal'])
 need({x['path']for x in files}=={str(journal/name)for name in ('intent.json','acquired.json','controller.log','reconciliation.json')},'prior attempt progressed beyond controller')
 failure=(json.loads(next(x['text']for x in files if x['path']==str(journal/'reconciliation.json')))if import_repair or environment_repair or config_repair or evidence_repair or semantic_repair or semantic_root else observed['reconciliation']);need(failure['status']=='RECONCILIATION_REQUIRED' and failure['returncode']!=0 and failure['automatic_retry']is False,'failed controller evidence differs')
 receipt=read(v['reconciliation']);settlement=read(receipt['intent']);op=settlement['operation'];wb=settlement['workbook']
 need(settlement['proof']==v['failure_proof'] and receipt['result']==dict(state='abandoned',operation_id=op['operation_id']) and op['kind']=='publication' and op['state']=='hold','exact stopped operation not reconciled')
 need(op['workbook']==plan['workbook'] and op['owner']==p['expected_owner'] and op['fence']==p['expected_fence'] and wb['workbook']==plan['workbook'] and wb['state']=='hold' and wb['owner']==op['owner'] and wb['fence']==op['fence'],'settled custody differs')
 reconciliation=next(x for x in files if x['path']==str(journal/'reconciliation.json'))
 need(json.loads(op['evidence'])=={k:reconciliation[k]for k in ('path','sha256')},'operation failure evidence differs')
 before=read(old['implementation']);after=read(p['implementation']);bd=before['dependencies'];ad=after['dependencies']
 if environment_repair:
  need(before['publisher']==old['publisher'] and after['publisher']==p['publisher'] and bd==ad
       and {k:r for k,r in before.items()if k!='publisher'}=={k:r for k,r in after.items()if k!='publisher'},
       'environment repair changed publication dependency closure')
  for item in [old['publisher'],p['publisher'],*bd.values()]:need(bind(item['path'])==item,'publication environment closure changed')
  prior=read(plan['retained_warning']['continuation_plan']);renewal=read(prior['live_renewal']['plan']);fresh=read(renewal['fresh_plan']);legacy=read(fresh['legacy_research_history'])
  need(prior['workbook']==renewal['workbook']==fresh['workbook']==legacy['workbook']==plan['workbook']
       and legacy['original']==renewal['source']
       and legacy['original']['sha256']==prior['source_sha256']==plan['source_sha256'],
       'environment repair detached original legacy source')
  expected=legacy_environment_repair(Path(old['publisher']['path']).read_text())
  need(ast.dump(ast.parse(expected),include_attributes=False)==ast.dump(ast.parse(Path(p['publisher']['path']).read_text()),include_attributes=False),
       'change exceeds exact legacy environment selection')
  log=next(x['text']for x in files if x['path']==str(journal/'controller.log'))
  need(log.endswith('ValueError: foreign warning history projection\n'),'publication stopped for another reason')
  return old,observed,op,wb
 if import_repair or config_repair or evidence_repair or semantic_repair or semantic_root:
  need(before['publisher']==old['publisher'] and after['publisher']==p['publisher'] and set(bd)==set(ad)
   and {k:r for k,r in bd.items()if k!='fresh'}=={k:r for k,r in ad.items()if k!='fresh'},'retained bootstrap dependency closure changed')
  need({k:r for k,r in before.items()if k not in {'publisher','dependencies'}}=={k:r for k,r in after.items()if k not in {'publisher','dependencies'}},'retained bootstrap implementation work changed')
  for item in [old['publisher'],p['publisher'],*bd.values(),*ad.values()]:need(bind(item['path'])==item,'retained bootstrap bytes changed')
  publisher,fresh=(semantic_root_repair if semantic_root else semantic_evidence_repair if semantic_repair else mechanical_evidence_repair if evidence_repair else ready_config_repair if config_repair else history_import_repair)(Path(old['publisher']['path']).read_text(),Path(bd['fresh']['path']).read_text(),bd['fresh'],ad['fresh'])
  need(ast.dump(ast.parse(publisher),include_attributes=False)==ast.dump(ast.parse(Path(p['publisher']['path']).read_text()),include_attributes=False)
   and ast.dump(ast.parse(fresh),include_attributes=False)==ast.dump(ast.parse(Path(ad['fresh']['path']).read_text()),include_attributes=False),'change exceeds exact missing optional import')
  log=next(x['text']for x in files if x['path']==str(journal/'controller.log'))
  if semantic_root:
   need(plan.get('semantic_continuation',{}).get('completed_reader')is not None
        and log.rstrip().endswith('ValueError: owning code/config changed') and 'in remote_child' in log,
        'semantic owning root stopped for another reason')
  elif semantic_repair:
   need(plan.get('semantic_continuation',{}).get('completed_reader')is not None
        and log.rstrip().endswith("KeyError: 'completed_takeover'") and 'reader_evidence' in log,
        'semantic dependency closure stopped for another reason')
  elif evidence_repair:
   need(plan.get('mechanical_tail')is not None and log.rstrip().endswith('ValueError: selected publication dependency changed') and 'child.py' in log,
        'mechanical dependency closure stopped for another reason')
  elif config_repair:
   need(plan.get('mechanical_tail')is not None and log.rstrip().endswith("KeyError: 'config'") and 'ready["config"]'in log,
        'completed READY stopped for another reason')
  else:need(log.endswith("ModuleNotFoundError: No module named 'scripts.fleet.history'\n"),'retained bootstrap failed for another reason')
  return old,observed,op,wb
 need(before['publisher']==old['publisher'] and after['publisher']==p['publisher'] and {k:r for k,r in bd.items()if k!='status'}=={k:r for k,r in ad.items()if k!='status'} and set(bd)==set(ad),'dependency closure changed')
 need({k:r for k,r in before.items()if k not in {'publisher','dependencies'}}=={k:r for k,r in after.items()if k not in {'publisher','dependencies'}},'implementation work changed')
 for item in [old['publisher'],p['publisher'],*bd.values(),*ad.values()]:need(bind(item['path'])==item,'publication closure bytes changed')
 tree=ast.parse(Path(bd['status']['path']).read_text());count=0;sha=None
 for node in tree.body:
  if isinstance(node,ast.Assign)and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
   if node.targets[0].id=='CAPACITY_POLICY_SHA':sha=ast.literal_eval(node.value)
   if node.targets[0].id=='CAPACITY_POLICY':
    need(ast.literal_eval(node.value)is None,'old dependency already selected');node.value=ast.parse(repr(ad['capacity']),mode='eval').body;count+=1
 need(count==1 and sha==ad['capacity']['sha256'] and ast.dump(tree,include_attributes=False)==ast.dump(ast.parse(Path(ad['status']['path']).read_text()),include_attributes=False),'status change exceeds pinned dependency')
 text=Path(old['publisher']['path']).read_text();need(text.count(repr(bd['status']))==1,'publisher status load differs')
 need(ast.dump(ast.parse(text.replace(repr(bd['status']),repr(ad['status']))),include_attributes=False)==ast.dump(ast.parse(Path(p['publisher']['path']).read_text()),include_attributes=False),'owning publication code changed')
 return old,observed,op,wb

def owning_recovery(p,plan,read,bind,*,local=False):
 """A reconciled controller may resume its exact unspawned owning intent once."""
 from pathlib import Path
 import importlib.util,types
 def need(ok,message):
  if not ok:raise ValueError(message)
 edge=read(p['owning_recovery'])
 need(set(edge)=={'schema_version','prior_profile','root_intent','failure_proof','reconciliation','resume','guard_implementation','copies'}
  and edge['schema_version']in {'fleet-publication-owning-recovery/v1','fleet-publication-preexecute-recovery/v1'},'closed owning recovery required')
 old=read(edge['prior_profile']);oldplan=read(old['plan']);root=read(edge['root_intent'])
 need('owning_recovery'not in old and root['profile']==edge['prior_profile'],'one exact owning recovery required')
 need(plan==dict(oldplan,journal=plan['journal'],publication_resume=edge['resume']) and plan['journal']!=oldplan['journal'],
  'owning recovery changed work or reused controller')
 for k in ('host','python','worker_outcome','process','process_copy'):
  need(p[k]==old[k],'owning recovery detached completed author')
 need(p['expected_owner']==old['owner'] and p['expected_fence']==old['expected_fence']+1
  and p['owner']!=old['owner'],'owning recovery custody differs')
 resume=read(edge['resume']);observed=read(edge['failure_proof'])
 need(resume['prior_plan']==old['plan'] and resume['failure_proof']==edge['failure_proof']
  and resume['reconciliation']==edge['reconciliation'] and observed['profile']==edge['prior_profile'],
  'owning stop proof detached')
 copies={}
 for row in edge['copies']:
  need(set(row)=={'original','local'} and row['original']['path']not in copies
   and row['original']['sha256']==row['local']['sha256'],'owning evidence custody differs')
  copies[row['original']['path']]=row
 need(sorted((v['original']for v in copies.values()),key=lambda r:r['path'])==sorted(observed['files'],key=lambda r:r['path']),
  'complete original owning evidence custody required')
 def evidence(row):
  if not local or row['path']not in copies:return read(row)
  copy=copies[row['path']];need(row==copy['original'],'owning evidence reference differs');return read(copy['local'])
 def evidence_binding(path):
  if not local or path not in copies:return bind(path)
  copy=copies[path];need(bind(copy['local']['path'])==copy['local'],'owning evidence copy changed');return copy['original']
 manifest=read(p['implementation']);fresh=manifest['dependencies']['fresh']
 need(bind(fresh['path'])==fresh,'resume reader changed')
 spec=importlib.util.spec_from_file_location('selected_owning_resume_reader',fresh['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 m.resume_selection(types.SimpleNamespace(checked=evidence,binding=evidence_binding,require=need),plan)
 receipt=read(edge['reconciliation']);settlement=read(receipt['intent']);op=settlement['operation'];wb=settlement['workbook']
 need(op['owner']==p['expected_owner'] and op['fence']==p['expected_fence']
  and wb['workbook']==plan['workbook'] and wb['state']=='hold' and wb['owner']==op['owner'] and wb['fence']==op['fence'],
  'settled owning custody differs')
 need(observed['tracker']==read(old['proof'])['tracker']==read(p['proof'])['tracker'],
  'owning recovery changed queued tracker')
 return old,observed,op,wb


def validate_owning_resume_code(p):
 """Local reviewed transforms plus native rehashes bind the complete successor."""
 from scripts.fleet.build_fresh_publication import mechanical_guard_repair,owning_resume_repair,preexecute_resume_repair
 edge=checked(p['owning_recovery']);old=checked(edge['prior_profile'])
 before=checked(old['implementation']);middle=checked(edge['guard_implementation']);after=checked(p['implementation'])
 if edge['schema_version']=='fleet-publication-preexecute-recovery/v1':
  require(middle==before,'preexecute recovery must retain original guard implementation')
  transitions=((before,after,preexecute_resume_repair),)
 else:transitions=((before,middle,mechanical_guard_repair),(middle,after,owning_resume_repair))
 for a,b,transform in transitions:
  ad,bd=a['dependencies'],b['dependencies'];require({k:v for k,v in ad.items()if k!='fresh'}=={k:v for k,v in bd.items()if k!='fresh'},'unrelated publication dependencies changed')
  require({k:v for k,v in a.items()if k not in ('publisher','dependencies')}=={k:v for k,v in b.items()if k not in ('publisher','dependencies')},'publication implementation metadata changed')
  for row in(a['publisher'],b['publisher'],*ad.values(),*bd.values()):require(binding(row['path'])==row,'publication transition bytes changed')
  publisher,fresh=transform(Path(a['publisher']['path']).read_text(),Path(ad['fresh']['path']).read_text(),ad['fresh'],bd['fresh'])
  require(publisher==Path(b['publisher']['path']).read_text() and fresh==Path(bd['fresh']['path']).read_text(),'publication change exceeds reviewed guard and resume transforms')


def pool_completed(p,plan,read,bind,*,local=False):
 """Authenticate an immutable pool completion without inventing a process ID."""
 from pathlib import Path
 def need(ok,message):
  if not ok:raise ValueError(message)
 c=read(p['pool_completion'])
 need(set(c)=={'schema_version','completion','generation','dispatch','copies'} and
      c['schema_version']=='fleet-publication-pool-completion/v1', 'closed pool completion custody required')
 copies=c['copies'];need(isinstance(copies,list),'pool custody copies required')
 mapping={}
 for row in copies:
  need(set(row)=={'original','local'} and row['original']['sha256']==row['local']['sha256'] and
       row['original']['path'] not in mapping,'duplicate or detached pool custody copy')
  mapping[row['original']['path']]=row
 def evidence(row):
  if not local:return read(row)
  need(row['path'] in mapping and mapping[row['path']]['original']==row,'missing exact pool custody copy')
  return read(mapping[row['path']]['local'])
 completion=evidence(c['completion']);generation=evidence(c['generation']);dispatch=evidence(c['dispatch'])
 outcome=evidence(p['worker_outcome']);intent=evidence(outcome['intent']);job=evidence(intent['job'])
 required=[c['completion'],c['generation'],c['dispatch'],p['worker_outcome'],outcome['intent'],intent['job']]
 need(set(mapping)=={row['path']for row in required},'pool custody inventory differs')
 need(completion.get('job')==intent['job'] and completion.get('outcome')==p['worker_outcome'] and
      completion.get('dispatch')==c['dispatch'] and completion.get('returncode')==0 and
      completion.get('failure_type')is None and completion.get('forced')is False and
      completion.get('automatic_retry')is False and completion.get('publication_performed')is False,
      'pool completion did not finish exactly')
 need(dispatch.get('job')==intent['job'] and dispatch.get('host')==p['host'] and
      type(dispatch.get('slot'))is int and dispatch['slot']in(0,1), 'pool dispatch differs')
 need(generation.get('host')==p['host'] and generation.get('schema_version')in
      {'harbor-fleet-pool/v3','harbor-fleet-pool/v4','harbor-fleet-pool/v5'} and
      isinstance(generation.get('generation'),str) and generation['generation'], 'pool generation differs')
 slot=dispatch['slot'];gid=generation['generation'];directory=Path(c['generation']['path']).parent
 need(directory.name==gid and directory.parent.name=='generations' and
      c['generation']['path']==str(directory/'generation.json') and
      c['completion']['path']==str(directory/('slot-'+str(slot)+'.workbook-'+plan['workbook']+'.result.json')) and
      c['dispatch']['path']==str(directory.parent.parent/'dispatches'/(plan['workbook']+'.json')),
      'pool generation path differs')
 need(intent.get('owner')==p['expected_owner']==p['host']+'/slot-'+str(slot)+'/'+gid and
      job['workbook']==plan['workbook'] and job['source']['sha256']==plan['source_sha256'] and
      job['version']==Path(plan['code_root']).name and
      outcome.get('returncode')==0 and outcome.get('failure_type')is None and
      outcome.get('result')==plan['ready'] and outcome.get('publication_performed')is False and
      outcome.get('automatic_retry')is False and
      p['worker_outcome']['path']==str(Path(job['run'])/'outcome.json') and
      outcome['intent']['path']==str(Path(job['run'])/'intent.json'),
      'pool worker source or READY chain differs')
 need(p['process']is None and p['process_copy']is None and 'completed_ready'not in p and
      'preowning_recovery'not in p, 'pool custody cannot replace another process recovery')
 return c


def captured_qualification(p,proof,guard):
 """Read the original guarded capture without rewriting its log or rerunning it."""
 import math
 c=checked(p['qualification_capture'])
 require(set(c)=={'schema_version','helper','capture','qualification'} and
  c['schema_version']=='fleet-guarded-publication-qualification-capture/v1', 'closed qualification capture required')
 require(c['helper']['sha256']=='51be82e04c1b1a28721b7b8674f8196480a233c9a590ca02e16c124e5e9dbc13'
  and binding(c['helper']['path'])==c['helper'],'original full capture helper changed')
 observed=json.loads(guard['log'])
 if set(observed)=={'status','publication_proof','proof'}:
  require(observed['status']=='FULL_EXPIRED_QUEUED_QUALIFICATION_CAPTURED'
   and observed['publication_proof']==p['proof'] and observed['proof']==proof,
   'guarded queued capture differs from exact retained publication proof')
 else:require(set(observed)=={'status','proof','elapsed_seconds','models_dispatched','publication_mutations'}
  and observed['status']=='PASS' and observed['proof']==p['proof']
  and observed['models_dispatched']==observed['publication_mutations']==0
  and type(observed['elapsed_seconds'])in(int,float) and math.isfinite(observed['elapsed_seconds'])
  and 0<=observed['elapsed_seconds']<=guard['elapsed_seconds'], 'guard did not produce this capture')
 require(binding(guard['probe']['path'])==guard['probe'], 'original guarded probe changed')
 captured=checked(c['capture']);report=checked(c['qualification'])
 require(proof['qualification_capture']==c['capture'] and proof['qualification']==c['qualification']
  and captured['status']=='ACTUAL_QUEUED_QUALIFICATION_PROOF_PASSED'
  and captured['plan']==p['plan'] and captured['tracker']==proof['tracker']
  and captured['qualification']==c['qualification']
  and captured['models_dispatched']==captured['lease_mutations']==captured['publication_mutations']==0,
  'original capture identity or effects differ')
 plan=checked(p['plan']);checks=report.get('checks')
 stage=captured['request']['selection']['value']['content']['value']['stage_sha256']
 require(stage=={'type':'scalar','value':report.get('stage_sha256')} and
  isinstance(stage['value'],str) and bool(stage['value']), 'captured request stage differs')
 require(report['schema_version']=='harbor-complete-readiness/v1' and report['workbook']==plan['workbook']
  and report['publication_ready']is True and report['blockers']==[]
  and isinstance(checks,dict) and bool(checks) and all(isinstance(v,dict) and v.get('checked')is True for v in checks.values())
  and isinstance(report['evidence'],list) and bool(report['evidence'])
  and proof['checks']==len(checks) and proof['evidence']==len(report['evidence']),
  'original full qualification did not pass')

def load(row):
 p=checked(row);require(set(p)-{'input_history','preowning_recovery','owning_recovery','completed_ready','pool_completion','qualification_capture','admission_lock','version_custody','coordinator_transport'}==FIELDS and p['schema_version']==SCHEMA and p['host']in HOSTS,'closed authorized publication selection required')
 selected_coordinator_url(p)
 plan=checked(p['plan']);manifest=checked(p['implementation']);proof=checked(p['proof'])
 require(plan['schema_version']=='harbor-fleet-remote-publication/v1' and manifest['publisher']==p['publisher'] and binding(p['publisher']['path'])==p['publisher'],'exact remote publisher required')
 for r in manifest['dependencies'].values():require(binding(r['path'])==r,'publication dependency changed')
 require(proof['status']=='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED' and proof['plan']==p['plan'] and proof['publisher']==p['publisher'] and proof['ready']==plan['ready'] and proof['models_dispatched']==proof['publication_mutations']==0,'actual full native publication proof required')
 guard=checked(p['guard']);require(guard['returncode']==0 and guard['failure']is None and guard['seconds']==3300 and guard['memory_bytes']==8589934592,'actual original-guard proof required')
 if 'qualification_capture'in p:captured_qualification(p,proof,guard)
 else:require(json.loads(guard['log'])==proof,'actual original-guard proof required')
 if 'input_history'in p:
  h=checked(p['input_history'])
  require(set(h)=={'schema_version','input','archive','proof','ready'} and h['schema_version']=='fleet-completed-tracker-input/v1'
   and h['proof']==p['proof'] and h['ready']==plan['ready'] and h['input']['path']==proof['tracker']['path']
   and h['archive']['sha256']==h['input']['sha256'] and binding(h['archive']['path'])==h['archive'],
   'exact archived admission tracker and completed proof required')
 require(type(p['expected_fence'])is int and p['expected_fence']>0 and isinstance(p['owner'],str) and p['owner'] and p['owner']!=p['expected_owner'],'new explicit publication owner required')
 if 'pool_completion'in p:pool_completed(p,plan,checked,binding,local=True)
 else:require(binding(p['process_copy']['path'])==p['process_copy'] and p['process_copy']['sha256']==p['process']['sha256'],'native stopped process custody differs')
 require(Path(p['python']).is_absolute() and Path(p['publisher']['path']).is_absolute(),'absolute selected runtime required')
 if 'preowning_recovery'in p:preowning(p,plan,checked,binding,local=True)
 if 'owning_recovery'in p:
  require('preowning_recovery'not in p,'mixed publication recovery modes')
  owning_recovery(p,plan,checked,binding,local=True);validate_owning_resume_code(p)
 if 'completed_ready'in p:
  c=checked(p['completed_ready'])
  require(c['schema_version']=='fleet-stopped-wrapper-completed-ready/v1' and c['worker_outcome']==p['worker_outcome'] and c['ready']==plan['ready'],'declared completed READY differs')
 return p,plan,proof

def publication_version(plan,read):
 """Keep original source imports distinct from a proved completed worker root."""
 from pathlib import Path
 try:reader_module=module
 except NameError:
  def reader_module(name,row):
   import hashlib,importlib.util,sys
   path=Path(row['path'])
   if (set(row)!={'path','sha256'} or not path.is_absolute()
       or any(x.is_symlink() for x in (path,*path.parents))
       or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']):
    raise ValueError('selected publication version reader changed')
   spec=importlib.util.spec_from_file_location(name,path);reader=importlib.util.module_from_spec(spec);sys.modules[name]=reader;spec.loader.exec_module(reader);return reader
 if 'disclosure_continuation'in plan:
  a=plan['disclosure_continuation']
  if set(a)!={'reader','plan','completed_reader'}:raise ValueError('closed completed disclosure history required')
  reader=reader_module('completed_disclosure_version',a['completed_reader'])
  return reader.version(plan,read)
 if plan.get('semantic_continuation',{}).get('completed_reader')is not None:
  a=plan['semantic_continuation'];selected=read(a['plan'])
  if (selected.get('retained_context',{}).get('kind') in ('completed_disclosure','completed_pre_dispatch') or
                (selected.get('schema_version')=='fleet-completed-instruction-continuation/v1' and ('checkpoint_lease'in selected or 'completed_enqueue'in selected or ('checkpoint_takeover'in selected and 'interrupted_commit'in selected))) or
                (selected.get('schema_version')=='fleet-ordinary-mechanical-dialogue/v1' and 'semantic_context'in selected)): 
   optional={'stopped_ready'}
   if selected.get('retained_context',{}).get('kind')=='completed_pre_dispatch':optional.add('renewal_before')
   if set(a)-optional!={'reader','plan','completed_reader'}:raise ValueError('closed completed semantic history required')
   return reader_module('completed_semantic_version',a['completed_reader']).version(plan,read)
 if 'completed_checkpoint'not in plan:return Path(plan['code_root']).name
 a=plan['completed_checkpoint']
 if set(a)!={'reader','plan'}:raise ValueError('closed completed checkpoint history required')
 selected=read(a['plan']);bridge=read(selected['bridge_profile']);facade=selected['facade']
 if (selected['schema_version']!='fleet-completed-instruction-continuation/v1'
  or selected['helper']!=a['reader']or selected['workbook']!=plan['workbook']
  or selected['source']['sha256']!=plan['source_sha256']
  or facade['original']!=plan['code_root']or facade['selected']!=bridge['worker_root']
  or bridge['schema_version']!='fleet-completed-checkpoint-takeover/v1'
  or bridge['source']!=selected['source']or bridge['workbook']!=selected['workbook']
  or not Path(facade['selected']).is_absolute()):
  raise ValueError('publication version detached from completed worker authority')
 return Path(facade['selected']).name


def local_publication_version(p,plan):
 """Resolve the same native version using exact retained local input bytes."""
 copies={}
 if 'version_custody'in p:
  c=checked(p['version_custody'])
  require(set(c)=={'schema_version','files'} and c['schema_version']=='fleet-publication-version-custody/v1','closed publication version custody required')
  for item in c['files']:
   require(set(item)=={'original','local'} and item['original']['sha256']==item['local']['sha256'] and item['original']['path']not in copies,'publication version custody differs')
   require(binding(item['local']['path'])==item['local'],'publication version custody bytes changed');copies[item['original']['path']]=item
 def read(row):
  if row['path']not in copies:return checked(row)
  item=copies[row['path']];require(item['original']==row,'publication version native binding changed');return checked(item['local'])
 return publication_version(plan,read if copies else checked)


def central(status,p,plan,capacity,now=None,*,held_reason=None):
 capacity.validate_occupancy(status);now=time.time()if now is None else now
 require(type(status.get('at'))in(int,float) and abs(now-status['at'])<=60,'fresh central observation required')
 rows=[w for w in status['workbooks']if w['workbook']==plan['workbook']];require(len(rows)==1,'unique publication workbook required');w=rows[0]
 require(w['state']=='hold' and w['fence']==p['expected_fence'] and w['owner']==p['expected_owner'] and w['source_sha256']==plan['source_sha256'] and w['version']==local_publication_version(p,plan),'exact stopped publication custody required')
 for op in status['operations']:
  if op['state']=='hold':
   owners=[x for x in status['workbooks'] if x['workbook']==op['workbook']]
   require(op['kind']=='model' and op['workbook']!=plan['workbook'] and len(owners)==1
    and owners[0]['state']=='hold' and owners[0]['owner']==op.get('owner')
    and owners[0]['fence']==op.get('fence'),'uncertain publication, target or unknown owner requires reconciliation')
   # Publication has no model effects. Preserve unrelated uncertainty verbatim.
   continue
  if op['state']=='active':
   require(op['kind']=='model' and op['workbook']!=plan['workbook'],'publication or target operation still active')
   active=[x for x in status['workbooks']if x['workbook']==op['workbook']]
   require(len(active)==1 and active[0]['state']=='active' and active[0]['owner']==op['owner'] and active[0]['fence']==op['fence'] and active[0]['expires']>now,'unrecognized active model owner')
 if 'preowning_recovery'in p or 'owning_recovery'in p:
  if 'owning_recovery'in p:_,_,settled,prior=owning_recovery(p,plan,checked,binding,local=True)
  else:_,_,settled,prior=preowning(p,plan,checked,binding,local=True)
  require(w==prior,'pre-owning held workbook changed')
  matches=[x for x in status['operations']if x['operation_id']==settled['operation_id']]
  require(len(matches)==1 and matches[0]['state']=='abandoned' and matches[0]['owner']==settled['owner'] and matches[0]['fence']==settled['fence'],'prior publication not terminal')
 else:require(w['reason']==(json.dumps(p['worker_outcome'],sort_keys=True)if held_reason is None else held_reason),'held outcome differs')
 return w

def completed_ready(p,plan,read,bind):
 """Read an original READY guard when its outer wrapper stopped afterwards."""
 from pathlib import Path
 def need(ok,message):
  if not ok:raise ValueError(message)
 c=read(p['completed_ready']);outcome=read(p['worker_outcome']);intent=read(outcome['intent']);job=read(intent['job']);ready=read(plan['ready'])
 need(set(c)-{'enqueue_permission'}=={'schema_version','worker_outcome','job','guard','checkpoint','ready','failure_log'}
  and c['schema_version']=='fleet-stopped-wrapper-completed-ready/v1'
  and c['worker_outcome']==p['worker_outcome'] and c['job']==intent['job'] and c['ready']==plan['ready']
  and c['guard']==ready['outcome'] and c['checkpoint']==ready['checkpoint']
  and outcome['returncode']==1 and outcome['failure_type']is None and outcome['result']is None
  and outcome['log']==c['failure_log'] and bind(c['failure_log']['path'])==c['failure_log'],
  'stopped post-READY wrapper certificate differs')
 guard=read(c['guard']);resource=read(guard['resource']);command=read(resource['intent']);checkpoint=read(c['checkpoint'])
 permission=False
 if 'enqueue_permission'in c:
  # This is an execution permission, not evidence that a model was called.
  # Authenticate the exact completed enqueue selected by the original command.
  e=c['enqueue_permission']
  need(set(e)=={'schema_version','intent','prior','descriptor','completion','step_intent'}
   and e['schema_version']=='fleet-completed-enqueue-permission/v1'
   and e['intent']==resource['intent'] and command['models']is True
   and command['arguments']=={'descriptor':e['descriptor'],'checkpoint':e['prior']},
   'enqueue permission is not bound to the original command')
  prior=read(e['prior']);descriptor=read(e['descriptor']);complete=read(e['completion']);step=read(e['step_intent'])
  need(prior['status']=='STEP_COMPLETE' and prior['next_step']=='enqueue'
   and prior['publication_performed']is False and prior['descriptor']==e['descriptor']
   and descriptor['schema_version']==prior['schema_version']==checkpoint['schema_version']==command['schema_version']
   and checkpoint['descriptor']==e['descriptor'] and checkpoint['checkpoint']==e['completion']
   and checkpoint['next_step']is None and checkpoint['publication_performed']is False
   and e['prior']in checkpoint['evidence'] and e['completion']in checkpoint['evidence']
   and complete['step']=='enqueue' and complete['status']=='completed'
   and complete['intent']==e['step_intent'] and complete['selection']==checkpoint['selection']
   and step['step']=='enqueue' and step['previous']==prior['checkpoint']
   and step['selection']==prior['selection']
   and Path(e['completion']['path']).name=='complete.json'
   and Path(e['step_intent']['path'])==Path(e['completion']['path']).with_name('intent.json'),
   'original command did not select and complete only enqueue')
  permission=True
 need(guard['status']=='COMPLETED_OPERATION' and guard['result']==c['checkpoint']
  and resource['returncode']==0 and resource['failure']is None and command['action']=='step'
  and (command['models']is False or permission) and command['commands']is True and command['containers']is True
  and Path(c['guard']['path']).parent==Path(c['checkpoint']['path']).parent
  and checkpoint['status']=='PUBLICATION_READY' and ready['status']=='PUBLICATION_READY'
  and ready['publication_performed']is False and ready['workbook']==plan['workbook'],
  
  'original guarded instruction did not finish READY')
 return c


def native(payload):
 from pathlib import Path
 import hashlib,json,os,time
 def bind(path):
  q=Path(path)
  if not q.is_absolute() or any(x.is_symlink()for x in(q,*q.parents)):raise ValueError('unsafe native binding')
  return dict(path=str(q),sha256=hashlib.sha256(q.read_bytes()).hexdigest())
 def read(row):
  if bind(row['path'])!=row:raise ValueError('native evidence changed')
  return json.loads(Path(row['path']).read_bytes())
 p=payload['profile'];plan=read(p['plan']);proof=read(p['proof']);manifest=read(p['implementation'])
 if manifest['publisher']!=p['publisher']or bind(p['publisher']['path'])!=p['publisher']:raise ValueError('publisher changed')
 for row in manifest['dependencies'].values():
  if bind(row['path'])!=row:raise ValueError('publisher dependency changed')
 original_owner=p['expected_owner']
 prior_journals=[];prior_publishers=[];ancestor=p;ancestor_plan=plan
 if 'owning_recovery'in p:
  old,observed,settled,_=owning_recovery(p,plan,read,bind)
  oldplan=read(old['plan']);prior=Path(oldplan['journal'])
  expected=sorted(observed['files'],key=lambda x:x['path'])
  actual=sorted([bind(x)for x in prior.rglob('*')if x.is_file()],key=lambda x:x['path'])
  if actual!=expected:raise ValueError('original owning journal changed before resume')
  original_owner=old['expected_owner'];ancestor=old;ancestor_plan=oldplan
  prior_journals.append(prior);prior_publishers.append(old['publisher']['path'])
 while 'preowning_recovery'in ancestor:
  # preowning authenticates the complete bounded ancestry. Native checks also
  # rehash every stopped journal and reject any surviving ancestor process.
  old,observed,settled,_=preowning(ancestor,ancestor_plan,read,bind)
  original_owner=old['expected_owner'];prior_journal=Path(read(old['plan'])['journal'])
  if len(prior_journals)>=4 or prior_journal in prior_journals:raise ValueError('native publication ancestry exceeds bounded distinct journals')
  prior_journals.append(prior_journal);prior_publishers.append(old['publisher']['path'])
  actual=[bind(x)for x in prior_journal.rglob('*')if x.is_file()]
  expected=[{k:x[k]for k in ('path','sha256')}for x in observed['files']]
  if sorted(actual,key=lambda x:x['path'])!=sorted(expected,key=lambda x:x['path'])or (prior_journal/'owning-publication').exists():raise ValueError('prior publication directory changed')
  prior_intent=read(bind(prior_journal/'intent.json'))
  if prior_intent['plan']!=old['plan']or prior_intent['driver']!=old['publisher']or prior_intent['operation_id']!=settled['operation_id']or prior_intent['owner']!=old['owner']:raise ValueError('prior native controller intent differs')
  ancestor=old;ancestor_plan=read(old['plan'])
 ready=read(plan['ready']);outcome=read(p['worker_outcome']);process=None if 'pool_completion'in p else read(p['process']);intent=read(outcome['intent']);job=read(intent['job'])
 if 'pool_completion'in p:pool_completed(p,plan,read,bind)
 if 'completed_ready'in p:completed_ready(p,plan,read,bind)
 elif outcome['returncode']!=0 or outcome['failure_type']is not None or outcome['result']!=plan['ready']or ready['status']!='PUBLICATION_READY' or ready['publication_performed']is not False:raise ValueError('worker not exactly stopped READY')
 if job['workbook']!=plan['workbook']or job['version']!=publication_version(plan,read) or job['source']['sha256']!=plan['source_sha256']or intent['owner']!=original_owner:raise ValueError('stopped job differs')
 historical=None
 if 'input_history'in p:
  h=read(p['input_history'])
  if set(h)!={'schema_version','input','archive','proof','ready'}or h['schema_version']!='fleet-completed-tracker-input/v1'or h['proof']!=p['proof']or h['ready']!=plan['ready']or h['input']['path']!=proof['tracker']['path']or h['archive']['sha256']!=h['input']['sha256']:raise ValueError('detached admission tracker history')
  before=read(h['archive']);current=read(proof['tracker'])
  if before['workbook_id']!=plan['workbook']or current['workbook_id']!=plan['workbook']or before['raw_source']!=current['raw_source']or before['batch']!=current['batch']or before['revision']>=current['revision']or current['handoffs'][:len(before['handoffs'])]!=before['handoffs']:raise ValueError('completed tracker ancestry differs')
  if job['inputs'].count(h['input'])!=1:raise ValueError('archived tracker was not an exact admission input')
  historical=h['input']
 for row in job['inputs']:
  if row!=historical and bind(row['path'])!=row:raise ValueError('task dependency changed')
 if bind(proof['tracker']['path'])!=proof['tracker']:raise ValueError('queued tracker changed after full proof')
 required_runway=7000
 if plan.get('publication_wait') is not None:
  import importlib.util
  row=manifest['dependencies']['publication_wait'];spec=importlib.util.spec_from_file_location('selected_publication_wait',row['path']);wait=importlib.util.module_from_spec(spec);spec.loader.exec_module(wait)
  def demand(ok,message):
   if not ok:raise ValueError(message)
  selected=wait.configuration(plan,checked=read,binding=bind,require=demand)
  if selected['value']['lease_tracker']!=proof['tracker']:raise ValueError('wait policy detached from qualified tracker')
  required_runway=selected['maximum_wait_seconds']+3500+200
 if 'owning_recovery'in p:
  selected=read(plan['publication_resume']);policy=read(selected['policy'])
  required_runway=policy['minimum_lease_runway_seconds']
 tracker=read(proof['tracker']);lease=tracker['leases']['task_agent']
 if not isinstance(lease,dict)or type(lease.get('expires_at'))is not int or lease['expires_at']-time.time_ns()<=required_runway*10**9:raise ValueError('native publication lease runway insufficient')
 paths=proof['paths']
 for name in('journal','output','publication_root'):
  q=Path(paths[name])
  if q.exists()or not q.parent.is_dir():raise ValueError('publication path consumed or parent missing')
 if paths['journal']!=plan['journal']:raise ValueError('publication journal differs')
 needles=[job['run'],job['result'],intent['job']['path'],plan['journal'],p['publisher']['path']]
 needles.extend(str(x)for x in prior_journals);needles.extend(prior_publishers)
 for entry in Path('/proc').iterdir():
  if not entry.name.isdigit()or int(entry.name)==os.getpid():continue
  try:
   st=(entry/'stat').read_text().rsplit(')',1)[1].split()
   if st[0]=='Z':continue
   argv=[a.decode(errors='replace')for a in(entry/'cmdline').read_bytes().split(b'\0')if a]
   if process is not None and int(entry.name)==process['pid']and str(st[19])==str(process['start_ticks']):raise ValueError('prior worker identity still alive')
   if any(any(a==n or a.startswith(n.rstrip('/')+'/')for n in needles)for a in argv):raise ValueError('task/publication process remains')
  except(FileNotFoundError,ProcessLookupError):continue
 return dict(status='PUBLICATION_VALIDATED_NOT_STARTED',at=time.time(),plan=p['plan'],ready=plan['ready'],tracker=proof['tracker'],models_dispatched=0)

def selected_coordinator_url(p):
 if 'coordinator_transport'not in p:return 'http://127.0.0.1:8765'
 selected=checked(p['coordinator_transport'])
 reader=module('publication_coordinator_transport',selected['helper'])
 return reader.validate(p['coordinator_transport'],host=p['host'],coordinator=p['coordinator_receipt'])

def native_coordinator_status(url='http://127.0.0.1:8765'):
 from pathlib import Path
 import json,time,urllib.request
 token=Path('/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token').read_text().strip()
 if url not in {'http://127.0.0.1:8765','http://127.0.0.1:8766'}:raise ValueError('closed loopback coordinator URL required')
 request=urllib.request.Request(url+'/v1',data=b'{"action":"status","arguments":{}}',headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
 with urllib.request.urlopen(request,timeout=10) as response:status=json.load(response)
 if status.get('schema_version')!='fleet-coordinator/v1' or abs(time.time()-status['at'])>30:
  raise ValueError('remote coordinator status is stale or foreign')
 return status

def connected_status(p):
 import inspect
 return ssh(p,inspect.getsource(native_coordinator_status)+'\nprint(__import__("json").dumps(native_coordinator_status('+repr(selected_coordinator_url(p))+')))')

def ssh(p,code):
 command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([p['python'],'-I','-B','-c',code])]
 r=subprocess.run(command,capture_output=True);require(r.returncode==0,'native publication preflight failed: '+r.stderr.decode(errors='replace'));return json.loads(r.stdout)

def admission_lock_path(p,receipt):
 if 'admission_lock'not in p:return Path(p['coordinator_receipt']['path']).parent/'job-launch.lock'
 return module('publication_admission_lock',p['admission_lock']).path(receipt)

def launch(row,journal,execute=False):
 p,plan,proof=load(row);capacity=module('publication_launch_capacity',p['capacity_policy']);receipt=checked(p['coordinator_receipt']);client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
 import inspect
 intent=None;out=None
 try:
  with lock(admission_lock_path(p,receipt),blocking=False):
   before=central(client.call('status'),p,plan,capacity)
   remote=ssh(p,inspect.getsource(history_import_repair)+'\n'+inspect.getsource(legacy_environment_repair)+'\n'+inspect.getsource(ready_config_repair)+'\n'+inspect.getsource(mechanical_evidence_repair)+'\n'+inspect.getsource(semantic_evidence_repair)+'\n'+inspect.getsource(semantic_root_repair)+'\n'+inspect.getsource(queued_preowning)+'\n'+inspect.getsource(preowning)+'\n'+inspect.getsource(owning_recovery)+'\n'+inspect.getsource(completed_ready)+'\n'+inspect.getsource(pool_completed)+'\n'+inspect.getsource(publication_version)+'\n'+inspect.getsource(native)+'\nprint(__import__("json").dumps(native('+repr(dict(profile=p))+')))')
   central(connected_status(p),p,plan,capacity)
   if not execute:return dict(status='PUBLICATION_VALIDATED_NOT_STARTED',profile=row,native=remote,claims_changed=0)
   out=safe(Path(journal).absolute());require(not out.exists(),'root publication journal consumed; reconcile without replay');out.mkdir(parents=True,mode=0o700)
   intent=persist(out/'intent.json',dict(profile=row,driver=binding(Path(__file__).resolve()),before=before,native=remote,automatic_retry=False),immutable=True)
   central(client.call('status'),p,plan,capacity)
   ready=client.call('reconcile',workbook=plan['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'publication':p['plan']})
   require(ready['state']=='ready'and ready['fence']==p['expected_fence'],'unexpected ready response');persist(out/'ready.json',ready,immutable=True)
   command=[p['python'],'-I','-B',p['publisher']['path'],'remote','--plan',p['plan']['path'],'--plan-sha256',p['plan']['sha256'],'--url',selected_coordinator_url(p),'--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['owner'],'--version',local_publication_version(p,plan)]
   persist(out/'command.json',command,immutable=True)
  # Only admission holds the dispatch lock. The coordinator's publication slot
  # serializes owning work while unrelated authoring dispatch remains available.
  ssh_command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join(command)]
  with(out/'response.log').open('xb')as log:r=subprocess.run(ssh_command,stdout=log,stderr=subprocess.STDOUT)
  require(r.returncode==0,'publication owning response requires reconciliation')
  return persist(out/'completed.json',dict(intent=intent,returncode=0,status='RESPONSE_RECEIVED_REQUIRES_VERIFIED_RESULT_FETCH',automatic_retry=False),immutable=True)
 except BaseException as exc:
  if intent is not None:persist(out/'uncertain.json',dict(intent=intent,status='RECONCILIATION_REQUIRED',failure_type=type(exc).__name__,automatic_retry=False),immutable=True)
  raise

def main():
 a=argparse.ArgumentParser();a.add_argument('--profile',required=True);a.add_argument('--sha256',required=True);a.add_argument('--execute',action='store_true');a.add_argument('--journal');x=a.parse_args()
 if x.execute and not x.journal:a.error('--journal required for once execution')
 print(json.dumps(launch(dict(path=str(Path(x.profile).absolute()),sha256=x.sha256),x.journal,x.execute)))
if __name__=='__main__':main()
