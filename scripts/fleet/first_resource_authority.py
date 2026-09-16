"""First fleet admission for a retained, resource-only source reconstruction.

This authorizes one monotonic reconstruction, never source/task qualification or
model replay. The original first-custody launcher owns the fenced transition.
"""
from pathlib import Path
import math
from scripts.fleet.migration import binding,checked
from scripts.fleet.capacity_policy import validate_occupancy,model_admission_barriers
SCHEMA='fleet-first-resource-authority/v1'
FIELDS={'schema_version','kind','workbook','job','source','plan','inspection','resource','transfers'}

def require(ok,message):
    if not ok:raise ValueError(message)

def authority(p,job):
    a=checked(p['authority']);require(set(a)==FIELDS and a['schema_version']==SCHEMA and a['kind']=='resource-reconstruction','closed initial resource authority required')
    require(a['job']==p['job'] and a['workbook']==job['workbook'] and a['source']==job['source'],'resource job/source differs')
    plan=checked(a['plan']);ledger=checked(plan['ledger']);snap=checked(plan['snapshot']);probe=checked(a['inspection']);guard=checked(a['resource'])
    require(plan['schema_version']=='fleet-resource-continuation/v1' and plan['helper']==job['driver'] and job['inputs'][0]==a['plan'],'original resource continuation required')
    require(plan['models']==0 and plan['automatic_retry']is False and plan['workbook']==job['workbook'] and {k:plan['source'][k]for k in('path','sha256')}==job['source'],'zero-model source scope changed')
    old=ledger['build'];require(old['state']=='STOPPED' and old['stage']=='segmentation' and old['exit_code']==124 and old['resources']['limit_reason']in('wall_time','process_group_rss'),'actual resource-only failure required')
    require(old['attempts']==plan['previous_attempt'] and plan['next_attempt']==plan['previous_attempt']+1==plan['cumulative_reconstruction_limit'] and len(ledger.get('build_history',[]))==plan['previous_attempt']-1,'preserve every consumed reconstruction')
    require(old['source_checkpoint']['state']=='complete' and old['source_generation_id']==plan['generation'],'completed source generation required')
    expected=dict(status='SOURCE_REUSE_PREFLIGHT_PASS',plan=a['plan'],generation=plan['generation'],previous_attempt=plan['previous_attempt'],next_attempt=plan['next_attempt'],source_reused=True,numerical_segmentation_validated=False,models_dispatched=0,attempts_dispatched=0)
    require(probe==expected and guard['returncode']==0 and guard['failure_type']is None and guard['log']==a['inspection'] and guard['models_dispatched']==guard['attempts_dispatched']==0,'actual guarded source reuse proof required')
    command=[job['command'][0],'-I','-B',job['driver']['path'],'run','--plan',a['plan']['path'],'--sha256',a['plan']['sha256']]
    require(job['command']==command and guard['command']==command[:4]+['inspect']+command[5:],'resource command differs from proven original reader')
    require(job['version']==snap['fingerprint']==Path(snap['root']).name and job['cwd']==snap['root'] and job['result']==str(Path(plan['home'])/'result.json'),'resource code/result differs')
    require(job['memory_limit_bytes']==plan['memory_bytes']==8*1024**3 and 900<plan['seconds']<=7200 and job['timeout_seconds']==plan['seconds']+300 and job['minimum_disk_bytes']==12*1024**3,'bounded resource continuation guards differ')
    require(type(guard['metrics']['peak_rss_bytes'])is int and 0<=guard['metrics']['peak_rss_bytes']<=8*1024**3 and all(type(guard[k])in(int,float) and math.isfinite(guard[k])for k in('started_at','ended_at')) and guard['ended_at']>=guard['started_at'],'invalid native resource measurement')
    require(job.get('operational_allowance')==plan['allowance'],'explicit scoped allowance required')
    for row in (plan['helper'],*snap['bindings'].values()):require(binding(row['path'])==row,'selected resource code changed')
    transferred={}
    require(isinstance(a['transfers'],list) and 0<len(a['transfers'])<=8,'bounded verified transfer chain required')
    for row in a['transfers']:
        transfer=checked(row);manifest=checked(transfer['manifest']);receipt=checked(transfer['receipt'])
        require(transfer['host']==p['host'] and transfer['claims']==transfer['reconstructions']==0 and receipt['archive_sha256']==manifest['archive_sha256'] and receipt['absolute_paths_preserved']is True and receipt['historical_bytes_rewritten']is False and receipt['attempts_reset']is False,'verified original artifact transfer required')
        require({x['original_path']:x['sha256']for x in receipt['files']}=={x['path']:x['sha256']for x in manifest['files']},'incomplete transfer membership')
        for x in receipt['files']:
            require(x['original_path']not in transferred or transferred[x['original_path']]==x['sha256'],'historical transferred bytes rewritten')
            transferred[x['original_path']]=x['sha256']
    closure=checked(plan['closure'])
    for row in [a['plan'],plan['snapshot'],plan['helper'],plan['ledger'],*closure['files'].values(),*snap['bindings'].values()]:
        require(transferred.get(row['path'])==row['sha256'] and binding(row['path'])==row,'source closure absent or changed after verified transfer')
    return a

def central(status,p,job):
    validate_occupancy(status)
    require(status['publication_limit']==1 and not model_admission_barriers(status,job['workbook'],p['new_owner']),'target or host operation requires reconciliation')
    require(not any(o['workbook']==job['workbook']for o in status['operations']),'prior fleet operation requires supported continuation')
    rows=[w for w in status['workbooks']if w['workbook']==job['workbook']];require(len(rows)==1,'unique source claim required');w=rows[0]
    require(w['state']=='hold' and w['fence']==0 and w['owner']is None and w['version']is None and w['claim_id']is None and w['expires']is None and w['source_sha256']==job['source']['sha256'] and w['reason']==p['expected_reason']=='initial ownership reconciliation required','initial resource custody changed; never retry a claim')
    return w

def smoke(p,status):
    from scripts.fleet import fleet_control as F
    v=checked(p['smoke_verification']);c=checked(p['smoke_collection'])
    require(v['schema_version']=='independent-published-task-copy-check/v1' and v['workbook']=='0029' and all(v[k]is True for k in('passed','byte_identical_to_immutable_generation','current_qualification_verified')) and v['model_calls']==v['runtime_operations']==0,'existing independently verified smoke required')
    require(c['schema_version']=='harbor-fleet-collection/v1' and c['status']=='COLLECTED' and c['workbook']=='0029' and c['local_byte_integrity_verified']is True and c['independent_remote_verification']['sha256']==p['smoke_verification']['sha256'] and F.files(Path(c['output']))==c['files'] and c['files']['generation-manifest.json']['sha256']==v['task_manifest']['sha256'],'original smoke collection changed')
    require(any(w['workbook']=='0029'and w['state']=='complete'for w in status['workbooks']),'smoke incomplete')
