"""Closed historical custody for one independently qualified new source attempt.

Historical quality, recorded hashes and model records remain opaque. They grant
no current quality result. The ordinary source/author pipeline owns every new
check; this module only authenticates custody and finite additive allowances.
"""
from pathlib import Path
import json,time
from scripts.batch import harbor_legacy_research_history as L
SCHEMA='harbor-legacy-opaque-source/v1'
AUTHOR_SCHEMA='harbor-legacy-opaque-authoring/v1'
FIELDS={'schema_version','authorization','workbook','original','tracker','curations','audit_directory','audit_files','source_manifest','segmentation_manifest','baseline','baseline_sidecar','history_roots','known_ids','excluded','histories','retained_opaque_research_calls','new_source_reconstruction_ordinal','new_research_max_calls','new_research_max_batches','new_research_attempts_per_batch','home','created_at_ns','expires_at_ns','opaque_history'}
COUNTERS={'research_calls','naturalization_attempts','dialogue_reviews','source_reconstructions'}

def tree(path):
    p=Path(path)
    return L.inventory(p) if p.is_dir() else {'.':L.binding(p)}


def settled_history(namespaces):
    """Reject known provider uncertainty without adopting an old quality pass."""
    files={r['path']:r for index in namespaces.values()for r in index.values()}
    documents={path:L.read(row)for path,row in files.items()if Path(path).suffix=='.json'}
    for name,index in namespaces.items():
        if 'instruction-naturalization'in Path(name).name:
            state=documents.get(str(Path(name)/'state.json'))
            L.require(isinstance(state,dict)and state.get('version')=='naturalize-recovery-v1','missing or unknown original NAT state schema')
    execution_roots=set()
    for path,value in documents.items():
        name=Path(path).name
        if isinstance(value,dict)and value.get('schema_version')=='harbor-model-execution/v1':execution_roots.add(Path(path).parent)
        if name.startswith('transport-')and name.endswith('.started.json'):execution_roots.add(Path(path).parent)
        if name=='submitted.json'and isinstance(value,dict)and 'attempt_id'in value and 'request'in value:execution_roots.add(Path(path).parent)
        if name=='state.json'and isinstance(value,dict)and value.get('version')=='naturalize-recovery-v1':
            attempts=value.get('attempts');status=value.get('status');root=Path(path).parent
            L.require(name=='state.json'and type(value.get('attempt_limit'))is int and value['attempt_limit']==2 and isinstance(attempts,list),'unknown original NAT state')
            L.require(status in {'initialized','validated','applied','failed','exhausted','retry_ready'}and 'staged_attempt'not in value,'pending or unknown original NAT state')
            if status=='initialized':L.require(not attempts,'initialized NAT has consumed unsealed attempts')
            else:L.require(bool(attempts),'terminal NAT state omitted its attempts')
            seen=[]
            for attempt in attempts:
                ordinal=attempt.get('attempt');L.require(type(ordinal)is int and ordinal in (1,2)and type(attempt.get('valid'))is bool,'original NAT attempt is not terminal')
                report=documents.get(str(root/('attempt-%02d'%ordinal)/'validation.json'))
                L.require(isinstance(report,dict)and report.get('attempt')==ordinal and report.get('valid')is attempt['valid'],'original NAT attempt lacks its completed validation')
                seen.append(ordinal)
            L.require(seen==list(range(1,len(seen)+1)),'original NAT attempt history is incomplete')
            if status in {'validated','applied'}:L.require(attempts[-1]['valid']is True,'accepted NAT state has no valid terminal attempt')
    for root in execution_roots:
        outcome=documents.get(str(root/'outcome.json'))
        L.require(isinstance(outcome,dict)and outcome.get('schema_version')=='harbor-model-execution/v1'and outcome.get('semantic_approval')is False and outcome.get('status')in {'completed','http_rejected','response_rejected'},'incomplete or unsealed original executor journal')
        actual={Path(path).name:row['sha256']for path,row in files.items()if Path(path).parent==root and Path(path).name!='outcome.json'}
        L.require(outcome.get('evidence')==actual and {'plan.json','request.json','submitted.json'}<=set(actual),'original executor seal or input inventory differs')
        if outcome['status']=='completed':L.require({'response.raw.json','completed.json'}<=set(actual),'completed executor response is missing')
    return True

def minimum_consumption(namespaces):
    """Conservative bounds, not reconstructed typed call histories.

Every retained JSON record reserves a legacy research maximum (64), a NAT
maximum (2), and two reviews. Explicit larger counters and generation counts
raise these floors. Duplicated records overcount intentionally; absent flags
can never turn a retained namespace into unused work.
"""
    settled_history(namespaces)
    files={r['path']:r for index in namespaces.values() for r in index.values()}
    records=[r for r in files.values() if Path(r['path']).suffix=='.json']
    bounds=dict(research_calls=max(64,64*len(records)),naturalization_attempts=max(2,2*len(records)),dialogue_reviews=max(2,2*len(records)),source_reconstructions=max(1,sum(Path(r['path']).name=='generation-manifest.json' for r in files.values())))
    keys={'api_calls':'research_calls','max_calls':'research_calls','attempt_limit':'naturalization_attempts','maximum_attempts':'naturalization_attempts','review_attempts':'dialogue_reviews','attempts':'source_reconstructions'}
    counts={k:0 for k in COUNTERS}
    def walk(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in keys and type(item)is int and item>=0:counts[keys[key]]+=item
                walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    for row in records:
        value=L.read(row)
        if isinstance(value,dict)and 'api_calls'in value:
            L.require(type(value['api_calls'])is int and value['api_calls']>=0 and value.get('status')=='complete'and value.get('finish_reasons')and all(x=='stop'for x in value['finish_reasons']),'unsettled original research record cannot authorize new work')
        walk(value)
    return {k:max(bounds[k],counts[k]) for k in COUNTERS}

def validate(proof,*,workbook=None,original=None,history_roots=None,known_ids=None,at_ns=None):
    p=L.read(proof);author=p.get('schema_version')==AUTHOR_SCHEMA
    L.require(p.get('schema_version')in {SCHEMA,AUTHOR_SCHEMA} and set(p)==FIELDS|({'authoring'}if author else set()),'closed opaque legacy authority required')
    L.require(p['authorization']['sha256']==L.AUTH_SHA and L.binding(p['authorization']['path'])==p['authorization'],'exact user authorization required')
    now=time.time_ns()if at_ns is None else at_ns
    L.require(type(p['created_at_ns'])is int and type(p['expires_at_ns'])is int and p['created_at_ns']<=now<p['expires_at_ns']<=p['created_at_ns']+86400*10**9,'opaque authority expired')
    wb=p['workbook'];L.require(isinstance(wb,str)and len(wb)==4 and wb.isdigit()and wb in p['known_ids'],'known opaque source required')
    for expected,actual in ((workbook,wb),(original,p['original']),(list(map(str,history_roots))if history_roots is not None else None,p['history_roots']),(sorted(known_ids)if known_ids is not None else None,p['known_ids'])):L.require(expected is None or expected==actual,'opaque source or history scope differs')
    L.require(L.binding(p['original']['path'])==p['original']and Path(p['original']['path']).stem==wb,'original source changed')
    old=L.read(p['tracker']);L.require(old.get('workbook_id')==wb and old.get('source_candidate')==p['original']['path']and old.get('source_health',{}).get('source_sha256')==p['original']['sha256'],'historical raw source identity differs')
    h=p['opaque_history'];L.require(set(h)=={'schema_version','namespaces','generation_roots','consumption_upper_bounds','historical_quality_adopted','scope_policy','prior_selection'}and h['schema_version']=='harbor-opaque-historical-custody/v1'and h['historical_quality_adopted']is False and h['scope_policy']=='retain-original-curation-and-ordered-targets','closed opaque custody required')
    previous=L.read(h['prior_selection'])
    for key in ('workbook','original','tracker','curations','source_manifest','segmentation_manifest','history_roots','known_ids','excluded','histories'):
        L.require(p[key]==previous[key],'opaque selection rewrote historical evidence')
    for key in ('baseline','baseline_sidecar'):
        L.require(p[key]['path']==previous[key]['path'],'current historical artifact selected another path')
    namespaces=h['namespaces'];L.require(isinstance(namespaces,dict)and namespaces,'complete retained namespaces required')
    for path,index in namespaces.items():L.require(tree(path)==index,'opaque namespace changed')
    roots=h['generation_roots'];L.require(set(roots)=={'source','segmentation'},'both complete generation roots required')
    for kind in roots:
        manifest=p[kind+'_manifest'];root=Path(roots[kind]);L.require(root.name=='generations'and root.parent.name==wb and Path(manifest['path']).parent.parent==root and str(root)in namespaces,'generation root detached')
        L.retained_generation(manifest)
    L.require(set(p['excluded'].get(wb,[]))<=set(namespaces)and p['audit_directory']in namespaces and namespaces[p['audit_directory']]==p['audit_files'],'a prior task or research namespace was omitted')
    files={r['path']:r for index in namespaces.values()for r in index.values()}
    for row in [h['prior_selection'],p['tracker'],p['baseline'],p['baseline_sidecar'],*p['curations'],*p['histories'].get(wb,[])]:L.require(files.get(row['path'])==row and L.binding(row['path'])==row,'retained historical file omitted or changed')
    L.require(p['tracker']in p['histories'].get(wb,[])and p['curations'],'original tracker and target choices required')
    segment=L.read(p['segmentation_manifest']);source=L.read(p['source_manifest'])
    L.require(source['bindings']['source_sha256']==p['original']['sha256']and source['generation_id']==old['source_generation_id']and segment['generation_id']==old['segmentation_generation_id'],'selected original source or target generation differs')
    expected=L.binding(Path(p['segmentation_manifest']['path']).parent/'curation.toml')
    L.require(all(row['sha256']==expected['sha256'] for row in p['curations'])and expected['sha256']==old['curation']['sha256_before']==old['curation']['sha256_after'],'historical target choice changed')
    # Current baseline bytes may differ from hashes recorded in the old tracker.
    # Both are retained above; no old baseline/quality result is adopted.
    bounds=h['consumption_upper_bounds'];minimum=minimum_consumption(namespaces)
    L.require(set(bounds)==COUNTERS and all(type(bounds[k])is int and minimum[k]<=bounds[k]<=10**7 for k in COUNTERS),'opaque consumption cannot be reset or unbounded')
    L.require(p['retained_opaque_research_calls']==bounds['research_calls']and p['new_source_reconstruction_ordinal']==bounds['source_reconstructions']+1,'new operations must append to opaque prior bounds')
    L.require(type(p['new_research_max_batches'])is int and 1<=p['new_research_max_batches']<=64 and p['new_research_attempts_per_batch']==2 and p['new_research_max_calls']==2*p['new_research_max_batches'],'finite new research allowance required')
    home=Path(p['home']);L.require(home.is_absolute()and home.name==wb and '..'not in home.parts and not any(home.is_relative_to(Path(x))or Path(x).is_relative_to(home)for x in namespaces),'new namespace overlaps historical custody')
    if author:authoring(p)
    return p

def authoring(p):
    a=p['authoring'];L.require(set(a)=={'source_authority','source_result','snapshot','new_nat_maximum_attempts','cumulative_nat_upper_bound','new_dialogue_reviews','cumulative_dialogue_reviews_upper_bound'},'closed opaque author successor required')
    before=validate(a['source_authority']);L.require(before['schema_version']==SCHEMA and {k:v for k,v in p.items()if k not in {'schema_version','authoring'}}=={k:v for k,v in before.items()if k!='schema_version'},'author successor changed historical custody')
    counts=before['opaque_history']['consumption_upper_bounds']
    L.require(type(a['new_nat_maximum_attempts'])is int and a['new_nat_maximum_attempts']==2 and type(a['new_dialogue_reviews'])is int and a['new_dialogue_reviews']==2 and a['cumulative_nat_upper_bound']==counts['naturalization_attempts']+2 and a['cumulative_dialogue_reviews_upper_bound']==counts['dialogue_reviews']+2,'opaque author consumption cannot reset')
    result=L.read(a['source_result']);plan=L.read(result['plan'])
    L.require(result['status']=='RECONSTRUCTION_VERIFIED'and result['legacy_research']==a['source_authority']and result['models_dispatched']==0 and result['automatic_retry']is False and result['cumulative_reconstruction_ordinal']==p['new_source_reconstruction_ordinal'],'completed independent source qualification required')
    L.require(plan['legacy_research']==a['source_authority']and plan['home']==p['home']+'/source'and plan['source']['sha256']==p['original']['sha256'],'completed source authority differs')
    snapshot=L.read(a['snapshot']);L.require(Path(snapshot['root']).name==snapshot['fingerprint'],'author snapshot namespace differs')
    return a
