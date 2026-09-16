"""First fleet custody for an authenticated completed rejected-review reader.

The historical consumer failure stays a failure. A fresh central claim owns one
explicit additional review; no synthetic checkpoint or historical lease is used.
"""
from pathlib import Path
from scripts.fleet.migration import binding,checked
SCHEMA='fleet-first-rejected-review-authority/v1'
FIELDS={'schema_version','kind','workbook','job','source','plan','inspection','resource','closure','transfers','original_home','offline_bindings','review_runtime'}


def require(ok,message):
    if not ok:raise ValueError(message)


def authority(p,job):
    a=checked(p['authority'])
    require(set(a)==FIELDS and a['schema_version']==SCHEMA and a['kind']=='rejected-reader-review','closed rejected-review authority required')
    require(a['job']==p['job'] and a['workbook']==job['workbook'] and a['source']==job['source'],'review job/source differs')
    from scripts.fleet.offline_bindings import readers
    check,_=readers(a['offline_bindings'])
    plan=checked(a['plan']);reader=checked(plan['reader_selection']);rejection=checked(reader['rejection'])
    require(plan['schema_version']=='fleet-rejected-reader-review/v1' and plan['helper']==job['driver']
            and plan['workbook']==a['workbook'] and plan['source']==reader['source']==a['source']
            and job['inputs'][0]==a['plan'] and plan['owner']==p['new_owner'] and plan['fence']==1,
            'exact initial review selection required')
    require(rejection['schema_version']=='harbor-completed-review-consumption-proof/v1'
            and rejection['round']==2 and rejection['passed']is False and rejection['publication_ready']is False
            and rejection['model_calls_executed']==0 and a['original_home']==str(Path(rejection['call']['request']['path']).parents[3]),
            'actual historical rejected reader required')
    probe=check(a['inspection']);guard=check(a['resource'])
    require(probe['status']=='SOURCE_DERIVATIVE_REVIEW_VALIDATED_NOT_STARTED' and probe['plan']==a['plan']
            and probe['original_consumed']==2 and probe['next_ordinal']==probe['limit']==3
            and probe['changed_claims']==1 and probe['unchanged_claims']==probe['claims']-1
            and probe['mechanics']['passed']is True and probe['models_dispatched']==probe['task_mutations']==0
            and probe['qualified']is False,'complete cold additional-review probe required')
    command=[plan['python'],'-I','-B',job['driver']['path'],'run','--plan',a['plan']['path'],'--sha256',a['plan']['sha256']]
    require(job['command']==command and guard['command']==command[:4]+['probe']+command[5:]
            and guard['returncode']==0 and guard['inspection']==a['inspection']
            and guard['host']==p['host'] and guard['models']==guard['claims']==0
            and guard['memory_limit_bytes']==8*1024**3 and guard['timeout_seconds']==1500,
            'actual bounded native review probe required')
    require(job['operational_allowance']==plan['allowance'] and job['memory_limit_bytes']==8*1024**3
            and job['timeout_seconds']==3300 and job['minimum_disk_bytes']==12*1024**3
            and job['result']==str(Path(plan['home'])/'result.json'),'bounded selected review guards differ')
    runtime=checked(a['review_runtime'])
    require(runtime['schema_version']=='fleet-worker-runtime/v1' and all(binding(row['path'])==row for row in runtime['files'].values()),'review runtime bytes changed')
    require(job['cwd']==runtime['root'] and job['version']==Path(runtime['root']).name
            and job['driver']==runtime['files']['scripts/fleet/dialogue_revision_review.py']
            and plan['context_helper']==runtime['files']['scripts/fleet/retained_rejected_review_context.py']
            and plan['executor']==runtime['files']['scripts/batch/harbor_model_executor.py'],
            'selected review runtime differs')
    closure=checked(a['closure']);require(set(closure)=={'files','historical_bytes_preserved'} and closure['historical_bytes_preserved']is True,'exact custody closure required')
    required=closure['files'];require(isinstance(required,list)and len(required)>0 and len({x['path']for x in required})==len(required),'unique retained closure required')
    transfers={}
    require(isinstance(a['transfers'],list)and 0<len(a['transfers'])<=8,'bounded custody transfer chain required')
    for row in a['transfers']:
        transfer=checked(row);manifest=checked(transfer['manifest']);receipt=checked(transfer['receipt'])
        require(transfer['host']==p['host'] and transfer['claims']==0
                and receipt['archive_sha256']==manifest['archive_sha256'] and receipt['absolute_paths_preserved']is True
                and receipt['historical_bytes_rewritten']is False and receipt['attempts_reset']is False,
                'verified original byte custody required')
        require({x['original_path']:x['sha256']for x in receipt['files']}=={x['path']:x['sha256']for x in manifest['files']},'incomplete custody transfer')
        for x in receipt['files']:
            require(x['original_path']not in transfers or transfers[x['original_path']]==x['sha256'],'historical bytes changed between transfers')
            transfers[x['original_path']]=x['sha256']
    for row in [a['plan'],a['review_runtime'],plan['reader_selection'],reader['rejection'],reader['revision'],reader['snapshot'],*required]:
        require(transfers.get(row['path'])==row['sha256'] and binding(row['path'])==row,'exact authenticated input absent from native custody')
    return a
