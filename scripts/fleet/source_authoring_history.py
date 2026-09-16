"""Explicit first authoring after a retained, monotonic source reconstruction.

The certificate preserves the complete central namespace and any original
pre-audit proof. It grants no reconstruction retry and cannot resume or reset
an authoring attempt. Remote readers verify canonical retained bytes; the
capture is the separately audited full-root observation.
"""
from pathlib import Path
import json,os,time
from scripts.fleet.migration import binding,checked,retain
SCHEMA='fleet-source-to-first-authoring-history/v1'
WORKBOOKS={'0200','0013','0036','0064'}
CERTIFICATES={}
PREPARATION_FAILURES={}

def require(v,m):
    if not v:raise ValueError(m)

def validate_certificate(row,*,workbook,root=None,live=True):
    require(row==CERTIFICATES.get(workbook),'exact root-captured history certificate required')
    p=checked(row)
    keys={'schema_version','workbook','source','inventory','history_roots','scan_roots','known_ids','excluded','histories','central_excluded','central_histories','pre_audit','pause','source_authority','source_result','source_worker_outcome','authoring_home','captured_at_ns','route'}
    if p.get('schema_version')=='fleet-source-to-first-authoring-history/v2':
        require(set(p)==keys|{'preparation_predecessor','preparation_failure'},'closed pre-admission continuation required')
        preparation_failure(p,workbook)
    else:require(set(p)==keys and p['schema_version']==SCHEMA,'closed first-authoring history required')
    require(p['workbook']==workbook and workbook in WORKBOOKS,'closed first-authoring workbook required')
    require(p['route']=='retained_source_attempt2_then_first_authoring_no_reset','unsupported history route')
    require(checked(p['pause'])['new_dispatch_allowed'] is False,'original local dispatcher is not paused')
    inventory=checked(p['inventory']);require(p['known_ids']==sorted(x['workbook_id'] for x in inventory['sources']),'complete inventory differs')
    original=next(x for x in inventory['sources'] if x['workbook_id']==workbook)
    require({k:original[k] for k in ('path','sha256')}==p['source'] and binding(p['source']['path'])==p['source'],'original source changed')
    require(len(p['history_roots'])==len(set(p['history_roots'])) and set(p['history_roots'])<=set(p['scan_roots']),'complete historical roots required')
    require(workbook not in p['excluded'] and workbook not in p['central_excluded'],'published or prior task identity cannot enter first authoring')
    if p['pre_audit'] is None:
        require(workbook in {'0013','0064'} and not p['histories'].get(workbook) and not p['central_histories'].get(workbook),'prior authoring cannot be reset')
    else:
        old=checked(p['pre_audit']);require(workbook in {'0200','0036'} and old['schema_version']=='harbor-pre-audit-history/v1'
            and old['workbook']==workbook and old['original']==p['source'] and old['known_ids']==p['known_ids'] and old['history_roots']==p['history_roots'],'retained pre-audit identity differs')
        require(old['model_history_route']=='pre_audit_not_entered' and old['local_attempt_history']=='retained_without_reset_or_new_retry_allowance','prior model budget cannot be reset')
        require(p['histories'].get(workbook)==[old['tracker']] and p['central_histories'].get(workbook)==[old['tracker']],'additional authoring history requires supported continuation')
        for item in [old['original'],old['tracker'],old['driver'],*old['canonical_curations'],*old['diagnostics'],*old['related_history']]:require(binding(item['path'])==item,'retained historical witness changed')
        require(checked(old['tracker'])==old['historical_tracker_value'],'original tracker changed')
    authority=checked(p['source_authority']);source_plan=checked(authority['plan']);result=checked(p['source_result']);outcome=checked(p['source_worker_outcome'])
    require(authority['workbook']==workbook and authority['current_attempt']==1 and authority['next_attempt']==2,'source attempt transition changed')
    require(result['status']=='SOURCE_RECONSTRUCTION_VERIFIED' and result['plan']==authority['plan'] and result['prior_ledger']==source_plan['ledger']
        and result['models_dispatched']==0 and result['publication_performed'] is False and result['automatic_retry'] is False,'exact completed source-only operation required')
    require(outcome['result']==p['source_result'] and outcome['returncode']==0 and outcome['failure_type'] is None and outcome.get('automatic_retry') is False,'source worker did not complete')
    intent=checked(outcome['intent']);job=checked(intent['job'])
    require(job['inputs'][0]==authority['plan'] and job['driver']==source_plan['helper'] and job['workbook']==workbook
        and job['source']==p['source'] and job['version']==checked(source_plan['snapshot'])['fingerprint'],'completed source worker identity differs')
    previous=checked(source_plan['ledger']);ledger=checked(result['ledger'])
    require(ledger['source']==previous['source'] and ledger['build_history']==previous['build_history']+[previous['build']]
        and ledger['build']['attempts']==2 and ledger['build']['state']=='VERIFIED' and ledger['build']['source_reused'] is True
        and ledger['build']['source_generation_id']==source_plan['generation']==previous['build']['source_generation_id'],'monotonic source history changed')
    if live:
        require(root is not None and os.environ.get('HARBOR_FLEET_WORKBOOK')==workbook,'source-history live workbook missing')
        from scripts.fleet.coordinator import Client
        claim=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=workbook,owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
        require(claim['source_sha256']==p['source']['sha256'] and claim['version']==Path(root).name and claim['state']=='active','first-authoring source/version custody differs')
    return p

def configured(roots,known_ids):
    row={'path':os.environ['HARBOR_FLEET_SOURCE_HISTORY_PATH'],'sha256':os.environ['HARBOR_FLEET_SOURCE_HISTORY_SHA256']}
    root=Path(__file__).parents[2];p=validate_certificate(row,workbook=os.environ['HARBOR_FLEET_WORKBOOK'],root=root)
    require(list(map(str,roots))==p['history_roots'] and sorted(known_ids)==p['known_ids'],'history namespace/denominator differs')
    return p

def survey(roots,known_ids):
    p=configured(roots,known_ids);return p['excluded'],p['histories']

def pre_audit(proof,*,workbook,original,history_roots,known_ids):
    p=configured(history_roots,known_ids)
    require(workbook==p['workbook'] and original==p['source'] and proof==p['pre_audit'],'unselected historical proof')
    return checked(proof)


def admission(policy,workbook):
    p=configured(policy.history_roots,{r['workbook_id'] for r in checked(policy.original_inventory)['sources']})
    require(workbook==p['workbook'] and policy.original_inventory==p['inventory'] and tuple(policy.requested_ids)==(workbook,), 'selected first-authoring cohort differs')
    require(Path(policy.tracker_root)==Path(p['authoring_home'])/'authoring/trackers/workbooks','first authoring cannot create another tracker namespace')
    return p


def preparation_failure(p,workbook):
    require(workbook in {'0200','0013'} and p['preparation_failure']==PREPARATION_FAILURES.get(workbook),'exact stopped pre-admission proof required')
    proof=checked(p['preparation_failure']);old=checked(p['preparation_predecessor'])
    require(proof['schema_version']=='fleet-source-first-preparation-stopped/v1' and proof['workbook']==workbook and proof['source_history']==p['preparation_predecessor'],'preparation predecessor differs')
    require(old['schema_version']==SCHEMA and old['workbook']==workbook and old['authoring_home']==proof['old_home'],'original first-authoring namespace differs')
    excluded={'schema_version','authoring_home'}
    require({k:v for k,v in p.items() if k not in excluded|{'preparation_predecessor','preparation_failure'}}=={k:v for k,v in old.items() if k not in excluded},'source/history authority cannot change during preparation continuation')
    home=Path(proof['old_home']);require(Path(p['authoring_home'])==home.parent/('source-'+workbook+'-first-authoring-002'),'one exact remaining preparation namespace required')
    require(proof['cause']=='verifier_inventory_mismatch_before_admission' and proof['admission_started'] is False and proof['model_calls_submitted']==0 and proof['model_journals_absent'] is True and proof['tracker_files']==[] and proof['active_namespace_pids']==[] and proof['budgets_reset'] is False,'pre-admission no-call boundary not proven')
    outcome=checked(proof['worker_outcome']);job=checked(proof['failed_worker_job']);plan=checked(proof['failed_plan']);intent=checked(outcome['intent'])
    require(outcome['returncode']==1 and outcome['result'] is None and outcome['automatic_retry'] is False and intent['job']==proof['failed_worker_job'] and job['inputs'][0]==proof['failed_plan'] and plan['history']==p['preparation_predecessor'] and job['source']==p['source'],'stopped preparation worker differs')
    central=proof['central'];require(central['state']=='hold' and central['fence']==2 and central['owner']==intent['owner'] and central['version']==proof['code_version']==job['version'] and central['source_sha256']==p['source']['sha256'] and json.loads(central['reason'])==proof['worker_outcome'],'stopped preparation custody differs')
    allowed={'intent.json','failure.json','authoring/frozen-code.json','authoring/survey.json','authoring/candidate.json','authoring/dependency.json'}
    require(proof['preparation_files']==sorted(allowed),'unexpected partial preparation inventory')
    expected={str(home/n) for n in allowed};observed=set()
    for path in home.rglob('*'):
        require(not path.is_symlink(),'partial preparation alias')
        if path.is_file():observed.add(str(path))
    require(observed==expected,'partial preparation changed; reconcile without replay')
    require(not list((home/'authoring/trackers/workbooks').iterdir()) and not (home/'authoring/model-campaign').exists() and not (Path(job['run'])/'model-calls').exists(),'authoring admission/model activity cannot be reset')
    require(expected<=set(x['path'] for x in proof['files']),'partial preparation witnesses missing')
    for row in proof['files']:require(binding(row['path'])==row,'stopped preparation evidence changed')
    return proof
