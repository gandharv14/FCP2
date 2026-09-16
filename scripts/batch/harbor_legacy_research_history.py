"""Explicit append-only work after terminal legacy research without transport logs.

The old metadata is opaque historical consumption, never typed Call evidence.
Current source, numerical, research and authoring gates must run independently.
"""
from pathlib import Path
import hashlib,json,time

SCHEMA='harbor-legacy-completed-research/v1'
BUILD_SCHEMA='harbor-legacy-completed-research-build/v1'
PACKAGE_SCHEMA='harbor-legacy-completed-package/v1'
PACKAGE_AUTHOR_SCHEMA='harbor-legacy-package-authoring/v1'
STAGED_SOURCE_SCHEMA='harbor-legacy-staged-source/v1'
STAGED_AUTHOR_SCHEMA='harbor-legacy-staged-authoring/v1'
MCP_STAGED_SOURCE_SCHEMA='harbor-legacy-staged-mcp-source/v1'
OPAQUE_SOURCE_SCHEMA='harbor-legacy-opaque-source/v1'
OPAQUE_AUTHOR_SCHEMA='harbor-legacy-opaque-authoring/v1'
SOURCE_ONLY_SCHEMAS={PACKAGE_SCHEMA,STAGED_SOURCE_SCHEMA,MCP_STAGED_SOURCE_SCHEMA,OPAQUE_SOURCE_SCHEMA}
AUTHOR_SCHEMAS={PACKAGE_AUTHOR_SCHEMA,STAGED_AUTHOR_SCHEMA,OPAQUE_AUTHOR_SCHEMA}
SCHEMAS={SCHEMA,BUILD_SCHEMA,PACKAGE_SCHEMA,PACKAGE_AUTHOR_SCHEMA,STAGED_SOURCE_SCHEMA,STAGED_AUTHOR_SCHEMA,MCP_STAGED_SOURCE_SCHEMA,OPAQUE_SOURCE_SCHEMA,OPAQUE_AUTHOR_SCHEMA}
AUTH_SHA='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
GATES={'atomic_normalization','audit_import','baseline_inputs','gpt_5_6_sol_variable_source_audit','immutable_source_ast','plain_eligibility','preflight','source_health','strict_segmentation_validation'}
FILES={'draft.json','first_normalization.json','plain_eligibility.json','exclusions.json','normalized.json','normalization_report.json'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'canonical legacy evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(isinstance(row,dict)and set(row)=={'path','sha256'}and binding(row['path'])==row,'legacy evidence bytes changed')
    return json.loads(Path(row['path']).read_bytes())

def inventory(directory):
    p=Path(directory);require(p.is_dir()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical legacy directory required')
    require(not any(x.is_symlink()for x in p.rglob('*')),'legacy directory aliases prohibited')
    return {str(x.relative_to(p)):binding(x)for x in sorted(p.rglob('*'))if x.is_file()}

def retained_generation(row):
    """Authenticate historical bytes without claiming current builder validity."""
    value=read(row);root=Path(row['path']).parent;files=inventory(root)
    require(root.name==value['generation_id']and Path(row['path']).name=='generation-manifest.json','historical generation namespace differs')
    require(set(files)==set(value['artifacts'])|{'generation-manifest.json'},'historical generation artifact set changed')
    for name,item in value['artifacts'].items():
        require(item['path']==name and files[name]['sha256']==item['sha256']and (root/name).stat().st_size==item['size_bytes'],'historical generation artifact changed')
    return value

def completed_builds(p,actual):
    """Authenticate old deterministic MCP artifacts; never adopt their quality."""
    selected=p['completed_builds']
    require(set(selected)=={'schema_version','maskability','leakscan','profiles','scanned_source'}
        and selected['schema_version']in {'harbor-legacy-completed-mcp-builds/v1','harbor-legacy-completed-mcp-builds/v2-baseline-scan'},
        'closed completed MCP build evidence required')
    wb=p['workbook'];names={'Dockerfile','environment.json','eval/tasks.jsonl','mask_cells.json',
        'masked_inputs.json','runtime/datasets.json','runtime/documents.jsonl','runtime/records.jsonl',
        'runtime/server.json','runtime/sources.json','server.py'}
    extras={'maskability_report.json','leakscan_report.json','source_profiles.json'}
    for folder in ('mcp','mcp-build-b'):extras.update(folder+'/'+n for n in names)
    # This cache is retained as opaque historical bytes, never loaded/executed.
    cache='mcp/__pycache__/server.cpython-312.pyc'
    if cache in actual:extras.add(cache)
    for n in names:require(actual['mcp/'+n]['sha256']==actual['mcp-build-b/'+n]['sha256'],
        'retained deterministic MCP builds differ')
    for key,name in [('maskability','maskability_report.json'),('leakscan','leakscan_report.json'),('profiles','source_profiles.json')]:
        require(selected[key]==actual[name],'completed build report detached')
    mask=read(selected['maskability']);leak=read(selected['leakscan']);profiles=read(selected['profiles'])
    baseline_scan=selected['schema_version']=='harbor-legacy-completed-mcp-builds/v2-baseline-scan'
    if baseline_scan:
        report_fields={'allowlist_errors','approved_value_leaks','manifest_errors','mask_cells','masked_cells_not_blank','unapproved_value_leaks','unused_allowlist_entries','valid','workbook_scanned'}
        require(set(mask)==set(leak)==report_fields and mask==leak and mask['valid']is True,'closed original baseline scan reports required')
        require(all(mask[k]==[]for k in report_fields-{'mask_cells','valid','workbook_scanned'}),'baseline scan contains findings')
        require(type(mask['mask_cells'])is int and mask['mask_cells']>0 and selected['scanned_source']==p['baseline']and binding(p['baseline']['path'])==p['baseline']and Path(p['baseline']['path']).name==wb+'-inputs.xlsx'and mask['workbook_scanned']=='inputs_out/'+wb+'-inputs.xlsx','original baseline scan identity differs')
    else:
        require(mask.get('valid')is True and leak.get('valid')is True and mask.get('workbook')==wb,
            'retained maskability/leak reports did not pass')
        for report in (mask,leak):
            require(all(report.get(k)==[]for k in ('allowlist_errors','manifest_errors','masked_cells_not_blank',
                'unapproved_value_leaks','unused_allowlist_entries')),'retained build has unresolved leak findings')
        source=selected['scanned_source'];require(binding(source['path'])==source
            and Path(source['path']).name==wb+'.xlsx'
            and (Path(source['path']).as_posix().endswith('/'+mask['workbook_scanned'])
                 or Path(source['path'])==Path(mask['workbook_scanned'])),
            'maskability source identity is missing')
    require(profiles.get('schema_version')=='1.0' and profiles.get('profiles')==[]
        and profiles['capture']['profiles_sha256']==hashlib.sha256(b'[]').hexdigest(),
        'nonempty or unresolved source profile capture needs a separate continuation')
    tasks=read(actual['mcp/environment.json']);masked=read(actual['mcp/masked_inputs.json']);cells=read(actual['mcp/mask_cells.json'])
    if baseline_scan:
        refs=[ref for item in masked for ref in item['refs']]
        require(tasks['environment_id']=='fcp-'+wb and type(tasks['tasks'])is int and tasks['tasks']==len(masked)==len({item['variable_id']for item in masked})and mask['mask_cells']==len(cells)==len(set(cells))==len(refs)==len(set(refs))and set(cells)==set(refs),'baseline MCP task/mask cardinality differs')
    else:
        require(tasks['environment_id']=='fcp-'+wb and type(tasks['tasks'])is int
            and tasks['tasks']==len(masked)==mask['variables_reviewed']
            and mask['mask_cells']==leak['mask_cells']==mask['total_masked_cells']==len(cells),
            'completed MCP task/mask cardinality differs')
    return extras


def completed_package(p):
    """Retain later local namespaces before another zero-model source attempt."""
    v=p['completed_package'];wb=p['workbook']
    require(set(v)=={'schema_version','package','namespaces','naturalization_reservations_upper_bound','new_authoring_authorized'}
        and v['schema_version']=='harbor-legacy-package-custody/v1'
        and v['new_authoring_authorized']is False,'closed source-only package custody required')
    require(set(v['namespaces'])==set(p['excluded'].get(wb,[])) and v['namespaces'],
        'not all prior excluded namespaces retained')
    for root,files in v['namespaces'].items():require(inventory(root)==files,'old package or downstream history changed')
    package=Path(v['package']);require(package.name==wb+'-outputs'and str(package)in v['namespaces'],
        'original packaged workbook required')
    files=v['namespaces'][str(package)];selected=read(files['tests/pipeline_bindings.json'])
    source=read(p['source_manifest']);segment=read(p['segmentation_manifest'])
    require(selected['source_generation_id']==source['generation_id']
        and selected['source_manifest_sha256']==p['source_manifest']['sha256']
        and selected['segmentation_generation_id']==segment['generation_id']
        and selected['segmentation_manifest_sha256']==p['segmentation_manifest']['sha256'],
        'old package uses another generation; preserve and reconcile both')
    reservations=0
    for name,index in v['namespaces'].items():
        root=Path(name)
        if root==package:continue
        if root.name==wb+'-instruction-naturalization':
            state=read(index['state.json']);require(state.get('version')=='naturalize-recovery-v1'
                and state.get('status')in {'initialized','validated'} and state['instruction_path']==str(package/'instruction.md')
                and state['attempt_limit']==2,'unknown legacy NAT stage')
            attempts=state.get('attempts',[]);require(all(x.get('valid')is True for x in attempts),
                'rejected legacy NAT requires explicit separate continuation')
            directories={int(Path(n).parts[0].split('-')[1])for n in index if Path(n).parts[0].startswith('attempt-')}
            require(directories<=set(range(1,state['attempt_limit']+1)),'unexpected legacy NAT attempt namespace')
            reservations=max([len(attempts),*directories],default=0)
            require(files['instruction.md']['sha256']in {state['source_sha256'],state.get('final_instruction_sha256')},
                'old package instruction detached from NAT state')
        else:
            require(root.name==wb+'-outputs' and root.parent.name=='disclosure','unknown downstream namespace')
            for record in ('faithcheck.json','verify.json'):
                proof=read(index[record]);require(proof.get('passed')is True and proof.get('faults')==[],
                    'retained deterministic disclosure did not pass')
    require(type(v['naturalization_reservations_upper_bound'])is int
        and v['naturalization_reservations_upper_bound']==reservations,'opaque NAT reservations cannot reset')
    return v


def package_authoring(p):
    """A distinct bounded author allowance after completed source-only work."""
    a=p['authoring'];require(set(a)=={'source_authority','source_result','snapshot','new_nat_maximum_attempts','cumulative_nat_upper_bound','new_dialogue_reviews'},'closed package author allowance required')
    before=validate(a['source_authority'])
    require(before['schema_version']==PACKAGE_SCHEMA,'author successor must follow source-only custody')
    require({k:v for k,v in p.items()if k not in {'schema_version','authoring'}}=={k:v for k,v in before.items()if k!='schema_version'},'package successor changed prior evidence or budget')
    require(type(a['new_nat_maximum_attempts'])is int and a['new_nat_maximum_attempts']==2
        and type(a['new_dialogue_reviews'])is int and a['new_dialogue_reviews']==2
        and type(a['cumulative_nat_upper_bound'])is int and a['cumulative_nat_upper_bound']==before['completed_package']['naturalization_reservations_upper_bound']+2,
        'new author allowance cannot reset opaque prior NAT reservations')
    result=read(a['source_result']);require(result['status']=='RECONSTRUCTION_VERIFIED'
        and result['legacy_research']==a['source_authority']and result['models_dispatched']==0
        and result['cumulative_reconstruction_ordinal']==2 and result['automatic_retry']is False,
        'completed source-only result required before new authoring')
    plan=read(result['plan']);require(plan['legacy_research']==a['source_authority']
        and plan['home']==p['home']+'/source'and plan['source']['sha256']==p['original']['sha256'],
        'completed source result belongs to another source authority')
    snapshot=read(a['snapshot']);require(Path(snapshot['root']).name==snapshot['fingerprint'],
        'selected author snapshot namespace differs')
    return a

def author_survey(p):
    """Project only the selected, fully retained package into a new attempt."""
    require(p['schema_version']not in SOURCE_ONLY_SCHEMAS,'source-only package cannot admit authoring')
    excluded=p['excluded']
    if p['schema_version']in AUTHOR_SCHEMAS:
        if p['schema_version']==PACKAGE_AUTHOR_SCHEMA:package_authoring(p)
        elif p['schema_version']==OPAQUE_AUTHOR_SCHEMA:
            from scripts.batch.harbor_legacy_opaque_history import authoring
            authoring(p)
        else:
            from scripts.batch.harbor_legacy_staged_history import authoring
            authoring(p)
        excluded={k:v for k,v in excluded.items()if k!=p['workbook']}
    return excluded,p['histories']


def validate(proof,*,workbook=None,original=None,history_roots=None,known_ids=None,at_ns=None):
    p=read(proof)
    if p.get('schema_version')in {OPAQUE_SOURCE_SCHEMA,OPAQUE_AUTHOR_SCHEMA}:
        from scripts.batch.harbor_legacy_opaque_history import validate as opaque_validate
        return opaque_validate(proof,workbook=workbook,original=original,history_roots=history_roots,known_ids=known_ids,at_ns=at_ns)
    if p.get('schema_version')in {STAGED_SOURCE_SCHEMA,STAGED_AUTHOR_SCHEMA,MCP_STAGED_SOURCE_SCHEMA}:
        from scripts.batch.harbor_legacy_staged_history import validate as staged_validate
        return staged_validate(proof,workbook=workbook,original=original,history_roots=history_roots,known_ids=known_ids,at_ns=at_ns)
    keys={'schema_version','authorization','workbook','original','tracker','curations','audit_directory','audit_files','source_manifest','segmentation_manifest','baseline','baseline_sidecar','history_roots','known_ids','excluded','histories','retained_opaque_research_calls','new_source_reconstruction_ordinal','new_research_max_calls','new_research_max_batches','new_research_attempts_per_batch','home','created_at_ns','expires_at_ns'}
    author=p.get('schema_version')==PACKAGE_AUTHOR_SCHEMA
    package=p.get('schema_version')in {PACKAGE_SCHEMA,PACKAGE_AUTHOR_SCHEMA}
    build=p.get('schema_version')in {BUILD_SCHEMA,PACKAGE_SCHEMA,PACKAGE_AUTHOR_SCHEMA}
    require(set(p)==keys|({'completed_builds'}if build else set())|({'completed_package'}if package else set())|({'authoring'}if author else set()) and p['schema_version']in SCHEMAS,'closed legacy research authority required')
    require(p['authorization']['sha256']==AUTH_SHA and binding(p['authorization']['path'])==p['authorization'],'exact user authorization required')
    now=time.time_ns()if at_ns is None else at_ns
    require(type(p['created_at_ns'])is int and type(p['expires_at_ns'])is int and p['created_at_ns']<=now<p['expires_at_ns']and p['expires_at_ns']-p['created_at_ns']<=24*3600*10**9,'legacy authority expired or invalid')
    wb=p['workbook'];require(isinstance(wb,str)and len(wb)==4 and wb.isdigit()and wb in p['known_ids'],'known legacy workbook required')
    for expected,actual in ((workbook,wb),(original,p['original']),(list(map(str,history_roots))if history_roots is not None else None,p['history_roots']),(sorted(known_ids)if known_ids is not None else None,p['known_ids'])):
        require(expected is None or expected==actual,'selected legacy source/namespace differs')
    require(binding(p['original']['path'])==p['original']and Path(p['original']['path']).stem==wb,'legacy original changed')
    old=read(p['tracker']);require(old.get('workbook_id')==wb and old.get('source_candidate')==p['original']['path']and old.get('source_health',{}).get('source_sha256')==p['original']['sha256'],'legacy source identity differs')
    require(old.get('status')=='normalized_pending_downstream'and old.get('mode')in('plain','mcp')and old.get('first_blocker')is None and old.get('staged_path')is None and old.get('promotion_attempted')is False,'only terminal research before downstream authoring is supported')
    require(set(old.get('gates',{}))==GATES and all(v=='pass'for v in old['gates'].values()),'legacy stage boundary differs')
    actual=inventory(p['audit_directory']);expected=FILES|{f'{wb}-inputs-variable-sources'+suffix for suffix in('.md','.inventory.json','.metadata.json')}|{f'normalize_{wb}.py'}
    if build:expected|=completed_builds(p,actual)
    require(set(actual)==expected and actual==p['audit_files'],'complete legacy research namespace changed')
    meta=read(actual[f'{wb}-inputs-variable-sources.metadata.json'])
    require(meta.get('workbook')==wb and meta.get('status')=='complete'and meta.get('model')=='openai/gpt-5.6-sol'and meta.get('prompt_version')=='variable-source-audit-v2','terminal selected legacy research metadata required')
    require(type(meta.get('api_calls'))is int and 0<meta['api_calls']==p['retained_opaque_research_calls']<=64,'opaque legacy call consumption differs')
    require(meta.get('finish_reasons')and all(x=='stop'for x in meta['finish_reasons']),'uncertain legacy model response cannot restart')
    require(meta.get('artifact')in(p['baseline']['path'],f'inputs_out/{wb}-inputs.xlsx')and old.get('baseline_inputs')==p['baseline'],'retained baseline differs')
    for row in(p['baseline'],p['baseline_sidecar'],*p['curations']):require(binding(row['path'])==row,'legacy financial evidence changed')
    source=retained_generation(p['source_manifest']);segment=retained_generation(p['segmentation_manifest'])
    require(source['generation_id']==old['source_generation_id']and source['bindings']['source_sha256']==p['original']['sha256']and segment['generation_id']==old['segmentation_generation_id'],'retained generation identities differ')
    require(p['curations']and all(r['sha256']==old['curation']['sha256_before']==old['curation']['sha256_after']for r in p['curations'])and old['curation']['preserved']is True and old['curation']['recurate_used']is False,'retained curation cannot reset')
    if package:completed_package(p)
    require((package or wb not in p['excluded'])and p['histories'].get(wb),'complete source-scoped legacy history required')
    allowed={r['path']:r for r in [p['tracker'],*actual.values()]}
    for row in p['histories'][wb]:require(allowed.get(row['path'])==row and binding(row['path'])==row,'unaccounted prior task or model history')
    require(p['tracker']in p['histories'][wb],'original research tracker omitted')
    require(p['new_source_reconstruction_ordinal']==2 and p['new_research_attempts_per_batch']==2 and type(p['new_research_max_batches'])is int and 1<=p['new_research_max_batches']<=64 and p['new_research_max_calls']==2*p['new_research_max_batches'],'bounded additional operations required')
    home=Path(p['home']);require(home.is_absolute()and home.name==wb and '..'not in home.parts,'closed continuation root required')
    if author:package_authoring(p)
    return p

def selected(policy,workbook):
    matches=[r for r in policy.campaign_evidence if (v:=read(r)).get('schema_version')in SCHEMAS and v.get('workbook')==workbook]
    require(len(matches)<=1,'ambiguous legacy research authority')
    return matches[0]if matches else None

def source_handoff(proof,handoff):
    """Bind a completed new source pair to unchanged historical target choices."""
    p=validate(proof,workbook=handoff['workbook']);root=Path(p['home'])/'source'
    result=read(binding(root/'result.json'));authority=p['authoring']['source_authority']if p['schema_version']in AUTHOR_SCHEMAS else proof
    if p['schema_version']in AUTHOR_SCHEMAS:require(binding(root/'result.json')['sha256']==p['authoring']['source_result']['sha256'],'actual completed source differs from author allowance')
    source_plan=read(result['plan']);ledger=read(handoff['ledger']);build=ledger['build']
    require(result['status']=='RECONSTRUCTION_VERIFIED' and result['legacy_research']==authority and result['cumulative_reconstruction_ordinal']==p['new_source_reconstruction_ordinal'] and result['models_dispatched']==0 and result['ledger']==handoff['ledger'],'completed source continuation differs')
    require(source_plan['schema_version']=='fleet-source-first/v3' and source_plan['legacy_research']==authority and source_plan['home']==str(root) and source_plan['source']['sha256']==p['original']['sha256'],'source authority differs')
    require(ledger['workbook_id']==p['workbook'] and build['state']=='VERIFIED' and build['attempts']==1 and not ledger.get('build_history') and build['bindings']['source_sha256']==p['original']['sha256'],'new source attempt differs')
    for kind,folder in (('source','sources'),('segmentation','segments')):
        path=root/'corpus'/folder/p['workbook']/'generations'/build[kind+'_generation_id']/'generation-manifest.json'
        require(handoff[kind+'_manifest']==binding(path) and handoff[kind+'_manifest']['sha256']==build[kind+'_manifest_sha256'],'new generation detached from completed ledger')
    segment=retained_generation(handoff['segmentation_manifest']);old=retained_generation(p['segmentation_manifest'])
    current=binding(Path(handoff['segmentation_manifest']['path']).parent/'curation.toml')
    require(current['sha256']==p['curations'][0]['sha256'] and segment['curated_output_cells']==old['curated_output_cells'],'historical curation or target scope changed')
    require(build['bindings']['curation_sha256'] in (None,current['sha256']),'source input curation conflicts with retained scope')
    if build['bindings']['curation_sha256'] is not None:
        require(binding(build['curation_path'])['sha256']==current['sha256'],'source input curation changed')
    return dict(legacy=p['tracker'],source_result=binding(root/'result.json'),curation=current,reconstruction_replayed=False)

def budget(proof,history,inventory,*,workbook,inherited_budget):
    """New bounded operations; never report legacy opaque calls as typed zero."""
    p=validate(proof,workbook=workbook)
    require(p['schema_version']not in SOURCE_ONLY_SCHEMAS,'package custody authorizes source reconstruction only')
    require(inherited_budget is None,'opaque legacy and typed inheritance cannot be combined')
    require(history==p['histories'][workbook],'additional or omitted legacy research history')
    import xl_variable_source_audit as audit
    count=len(audit._split_chunks(inventory))
    require(0<count<=p['new_research_max_batches'],'new research batch count exceeds explicit allowance')
    extra={}
    if p['schema_version']==OPAQUE_AUTHOR_SCHEMA:
        records={row['path']:row for index in p['opaque_history']['namespaces'].values()for row in index.values()}
        recorded=sum(value['api_calls']for row in records.values()if Path(row['path']).suffix=='.json'and isinstance(value:=read(row),dict)and type(value.get('api_calls'))is int)
        extra=dict(legacy_calls_opaque_is_upper_bound=True,legacy_recorded_api_calls=recorded)
    return dict(authority=proof,legacy_calls_opaque=p['retained_opaque_research_calls'],legacy_calls_replayed=False,
        new_batches=count,new_attempts_per_batch=2,new_maximum_calls=count*2,cumulative_maximum_calls=p['retained_opaque_research_calls']+count*2,**extra)

def projected_survey(roots,known_ids):
    """Preserve full root-observed history under the live exclusive fleet claim."""
    import os
    row=dict(path=os.environ['HARBOR_FLEET_LEGACY_RESEARCH_PATH'],sha256=os.environ['HARBOR_FLEET_LEGACY_RESEARCH_SHA256'])
    p=validate(row,workbook=os.environ['HARBOR_FLEET_WORKBOOK'],history_roots=roots,known_ids=known_ids)
    require(p['schema_version']not in SOURCE_ONLY_SCHEMAS,'package custody cannot project authoring history')
    from scripts.fleet.coordinator import Client
    claim=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
    require(claim['state']=='active'and claim['source_sha256']==p['original']['sha256'],'live legacy source custody differs')
    return author_survey(p)
