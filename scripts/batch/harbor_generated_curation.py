"""Explicit adoption of the first immutable, automatically generated curation.

The original successful corpus ledger remains unchanged, including its null input
curation binding. This proof supplies a separately named output-curation binding.
"""
from pathlib import Path
from scripts.batch import harbor_admission as A
SCHEMA='harbor-generated-curation-adoption/v1'
KEYS={'schema_version','workbook','queue','ledger','source_manifest','segmentation_manifest','curation','ordered_targets','history_roots'}

def completed_source(p,ledger):
    """Accept a first generated curation after an explicit pre-source repair.

    This reads the completed source receipt and its original failed ledger. It
    neither grants another reconstruction nor adopts any prior task quality.
    """
    c=p['completed_source'];A.require(set(c)=={'plan','result'},'closed completed source custody required')
    plan=A.read(c['plan']);result=A.read(c['result']);old=A.read(plan['ledger']);build=ledger['build'];prior=old['build']
    A.require(plan['schema_version']=='fleet-pre-source-continuation/v2' and plan['workbook']==p['workbook'] and plan['source']==ledger['source']==old['source'],'completed source identity differs')
    A.require(A.record(plan['helper']['path'])==plan['helper'],'original source helper changed')
    A.require(plan['previous_attempt']==1 and plan['next_attempt']==plan['cumulative_reconstruction_limit']==2 and plan['models']==0 and plan['automatic_retry'] is False,'exact consumed first and second source attempts required')
    A.require(old['state']=='STOPPED' and old['failure']=='source_policy_unsupported' and set(old['reason_codes'])=={'external_links_present'} and not old.get('build_history'),'original pre-source failure differs')
    A.require(prior['state']=='STOPPED' and prior['stage']=='normalization' and prior['attempts']==1 and prior['exit_code']==1 and prior['failure']==plan['expected_failure']=='RemediationError:remediation left unsupported volatile or external references','original failed normalization differs')
    A.require(not any(prior.get(k)for k in ('normalization','source_checkpoint','source_generation_id','segmentation_generation_id')) and plan['generation'] is None and plan['retained_curation'] is None and prior['bindings']['curation_sha256'] is None,'prior source or curation cannot be replaced')
    A.require(plan['restriction_inventory'] is None and plan['inventory_batch_id'] is None,'foreign source repair route')
    A.require(ledger['build_history']==[prior] and build['attempts']==2 and build['curation_path']==prior['curation_path'],'source history or original curation path changed')
    A.require({k:v for k,v in ledger.items()if k not in ('build','build_history')}=={k:v for k,v in old.items()if k not in ('build','build_history')},'original ledger fields changed')
    A.require(Path(c['result']['path'])==Path(plan['home'])/'result.json' and result['plan']==c['plan'] and result['ledger']==p['ledger'] and result['result']==dict(workbook_id=p['workbook'],**build),'completed source receipt detached')
    A.require(result['status']=='RECONSTRUCTION_VERIFIED' and result['models_dispatched']==0 and result['publication_performed'] is False and result['automatic_retry'] is False,'source completion does not qualify')
    A.require(Path(p['ledger']['path'])==Path(plan['home'])/'corpus/workbooks'/p['workbook']/'ledger.json','completed ledger namespace differs')
    snap=A.read(plan['snapshot']);A.require(build['bindings']['code_sha256']==snap['fingerprint'] and build['bindings']['source_sha256']==ledger['source']['sha256'],'completed source fingerprint differs')
    closure=A.read(plan['closure']);A.require(closure['corpus']==plan['corpus'] and closure['files']['workbooks/'+p['workbook']+'/ledger.json']==plan['ledger'],'original source closure detached')
    for bound in closure['files'].values():A.require(A.record(bound['path'])==bound,'original source closure changed')
    from scripts.batch import harbor_continuation_allowance as allowance
    policy=A.read(plan['allowance'])
    allowance.validate(plan['allowance'],expected_scope=allowance.scope(p['workbook'],ledger['source']['sha256']),at=policy['issued_at_ns'])
    A.require(plan['seconds']<=policy['limits']['stage_seconds'] and plan['memory_bytes']<=policy['limits']['memory_bytes'],'original source allowance exceeded')

def validate(proof,*,workbook,original,history_roots,known_ids):
    p=A.read(proof);completed=p.get('schema_version')=='harbor-generated-curation-adoption/v2'
    A.require(set(p)==KEYS|({'completed_source'}if completed else set()) and p['schema_version']in(SCHEMA,'harbor-generated-curation-adoption/v2') and p['workbook']==workbook,'generated curation proof schema/identity differs')
    A.require(p['history_roots']==[str(x) for x in history_roots],'generated curation history scope differs')
    q=A.read(p['queue']);A.require(q['schema_version']=='authoring-request-queue/v1','unknown generated curation queue')
    rows=[r for r in q['items'] if r['workbook_id']==workbook]
    A.require(len(rows)==1,'generated curation queue identity ambiguous');row=rows[0]
    A.require(row['history_class']=='no_history' and row['preserved_curations']==[] and row['preserved_history']==[],'prior curation/history cannot use generated adoption')
    A.require({k:row['original_source'][k] for k in ('path','sha256')}==original,'generated curation original differs')
    A.require(A.record(original['path'])==original,'generated curation source changed')
    excluded,history=A.survey_namespaces(tuple(Path(x) for x in history_roots),set(known_ids))
    A.require(workbook not in excluded and not history.get(workbook),'generated curation has prior task evidence')
    ledger=A.read(p['ledger']);build=ledger['build']
    A.require(ledger['schema_version']=='corpus-workbook/v1' and ledger['workbook_id']==workbook and ledger['source']==row['original_source'],'generated curation ledger identity differs')
    A.require(build['state']=='VERIFIED' and build['bindings']['curation_sha256'] is None,'verified generated curation with no input curation required')
    if completed:completed_source(p,ledger)
    else:A.require(build['attempts']==1 and not ledger.get('build_history'),'only first successful generated curation may be adopted')
    input_path=Path(build['curation_path'])
    A.require(not input_path.exists() and not input_path.is_symlink(),'original curation input now exists')
    for kind in ('source','segmentation'):
        bound=p[kind+'_manifest'];value=A.read(bound);directory=Path(bound['path']).parent
        A.require(Path(bound['path']).name=='generation-manifest.json' and directory.parent.name=='generations' and directory.parent.parent.name==workbook,'generated curation generation path differs')
        A.require(directory.name==value['generation_id']==build[kind+'_generation_id'] and bound['sha256']==build[kind+'_manifest_sha256'],'generated curation generation differs from retained ledger')
    segment=A.read(p['segmentation_manifest']);curation=A.record(Path(p['segmentation_manifest']['path']).parent/'curation.toml')
    A.require(curation==p['curation'] and segment['artifacts']['curation.toml']['sha256']==curation['sha256'],'generated curation differs from immutable output artifact')
    A.require(segment['artifacts']['curation.toml']['size_bytes']==Path(curation['path']).stat().st_size,'generated curation artifact size differs')
    A.require(p['ordered_targets']==segment['curated_output_cells'] and bool(p['ordered_targets']['ordered']),'generated curation ordered targets differ')
    return p
