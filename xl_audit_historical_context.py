"""Explicit historical audit applicability after an exact source-builder refresh.

Read-only, standalone owner: it never weakens ordinary current-pair validators.
Historical financial/code/journal integrity and fresh current qualification are
separate required proofs. No service call, old-code execution or budget reset.
"""
from dataclasses import dataclass,asdict
from pathlib import Path
import copy
import datetime as dt
import hashlib
import json
import xl_variable_source_audit as audit
import xl_audit_equivalence as Q
import xl_audit_validator_upgrade as U
import xl_audit_inventory_upgrade as I
import xl_audit_renderer_recovery as R
import xl_audit_inventory_lineage as L
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_tracker_lifecycle import checked_record

SCHEMA='historical-audit-source-builder-applicability/v1'

# Fixed current reader logic copied below has an explicit version boundary.
READER_SHA256={'xl_audit_equivalence.py': 'da2c2da1097b7cd46fd70d747172cd588ad887ed4b208b08550187053c584905', 'xl_audit_validator_upgrade.py': '47675b4f857c97539854c98f7d32ddf9010ab73370ef051bf34c445b232ac827', 'xl_audit_renderer_recovery.py': '0cbbe7bd49c660089af7f9ecb11e57de887d61be72d5af0d2fa4b3225a5c107f', 'xl_audit_inventory_lineage.py': 'b817d7ad461ee3050728aa04e4c142cab4993454d64bcc1097a49ca5c1b8c4d4'}

def _readers():
    files={}
    for module in (Q,U,R,L):
        path=Path(module.__file__).resolve()
        require(Q.digest(path)==READER_SHA256[path.name], 'unreviewed historical reader implementation change')
        files[str(path)]=Q.digest(path)
    return files


def _native_pair(old,new):
    for bindings in (old,new):
        directory=Path(bindings['source_generation']['path'])
        require(Q.read(directory/'request.json').get('schema_version')=='source-identity-request/v2' and
            Q.read(directory/'result.json').get('schema_version')=='source-identity-result/v2',
            'historical applicability supports native identity sources only')

@dataclass(frozen=True)
class Selection:
    old_bindings: dict
    current_bindings: dict
    prior_upgrade: dict
    original_auditor: dict
    historical_verifier_code: tuple
    model: str = audit.DEFAULT_MODEL
    endpoint: str = audit.PROD_ENDPOINT
    project_id: str = audit.DEFAULT_PROJECT_ID


def _code_closure(selection, old_manifest):
    # Exact ordered old verifier fingerprint, including parser and evaluator.
    from xl_seg.diagnostics import fingerprint_values
    names=[p.name for p in Q.seg_pub.verifier_paths()]
    require(len(set(names))==len(names), 'duplicate current verifier module identity')
    # Closed profile migration: the pre-storage/v1 verifier predates exactly
    # these two storage modules. No other removal, insertion or order change
    # is permitted, and its complete original fingerprint remains mandatory.
    profiles={'current':names}
    additions={'xl_graph_store.py','disk_model.py'}
    if all(names.count(name)==1 for name in additions):
        profiles['pre-storage/v1']=[name for name in names if name not in additions]
    records=selection.historical_verifier_code
    require(isinstance(records,tuple), 'complete historical verifier code required')
    matching=[profile for profile in profiles.values() if len(profile)==len(records)]
    require(len(matching)==1, 'complete historical verifier code required')
    names=matching[0]
    logical=[];files={}
    for expected,binding in zip(names,records):
        path=U.checked(binding)
        require(path.name==expected, 'historical verifier order/module identity changed')
        logical.append(dict(path=expected,sha256=binding['sha256'],size_bytes=path.stat().st_size))
        files[str(path)]=binding['sha256']
    require(fingerprint_values(logical)==old_manifest['evidence']['verifier_code'], 'historical verifier fingerprint differs')
    source=Q.source_pub.validate_source_generation(Path(selection.old_bindings['source_generation']['path']),
        expected_generation_id=selection.old_bindings['source_generation']['id'])
    builder=source['ast_provenance']['builder']['code']
    require(isinstance(builder,list) and bool(builder) and len({r['name'] for r in builder})==len(builder),
            'complete historical builder code required')
    by_name={r['path']:r for r in logical}
    require(all(r['name'] in by_name and r['sha256']==by_name[r['name']]['sha256'] and
        r['size_bytes']==by_name[r['name']]['size_bytes'] for r in builder), 'historical builder code differs')
    return files


def _old_context_records(bindings):
    # Exact record inventory formerly returned by the current context checker;
    # the full old context and its inventory were independently validated first.
    files={}
    for key in Q.GEN_KEYS:
        p=Path(bindings[key]['path'])/'generation-manifest.json'
        files[str(p.resolve())]=Q.digest(p)
    for key in ('source_file','baseline_inputs','baseline_segmentation'):
        p=Q.checked(bindings[key]);files[str(p)]=Q.digest(p)
    return files

def _direct_upgrade(bindings, *, original_code, model, endpoint, project_id):
    """Pure proposal; input identities are selected by the owning root."""
    identity=U.code_identity(original_code)
    require(original_code["sha256"]==I.BEFORE_SHA256, "only exact completed 56ab audit supported")
    selected={k:bindings[k] for k in U.AUDIT_KEYS}
    paths=[U.checked(selected[k]) for k in U.AUDIT_KEYS]
    result=U._validate_original_coverage(*paths,model=model,endpoint=endpoint,project_id=project_id,
        generator_sha256=identity['original_generator']['sha256'])
    inventory_context={}
    from xl_audit_inventory_upgrade import BEFORE_SHA256
    if identity['original_generator']['sha256']==BEFORE_SHA256:
        # Completed current scopes may use this proof only when the full live
        # source/segmentation/baseline inventory independently reconstructs.
        inventory_context=_old_context_records(bindings)
    metadata=json.loads(paths[0].read_text());inventory=json.loads(paths[1].read_text())
    coverage_path=Path(result['coverage_path']);coverage=json.loads(coverage_path.read_text())
    files={str(p):Q.digest(p) for p in paths};files.update(inventory_context)
    files[str(coverage_path.resolve())]=Q.digest(coverage_path)
    def collect(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256'}:
                p=U.checked(value);files[str(p.resolve())]=Q.digest(p)
            for child in value.values():collect(child)
        elif isinstance(value,list):
            for child in value:collect(child)
    collect(metadata);collect(coverage)
    policy=audit._read_bound_json(metadata['attempt_policy']);collect(policy)
    if policy.get('inherited_budget'):collect(audit._read_bound_json(policy['inherited_budget']))
    inherited=audit._inherited_batch_counts(policy.get('inherited_budget'),inventory)
    counts={'limit':policy['limit'],'submitted_counts':policy['submitted_counts'],
            'inherited_batch_counts':{str(k):v for k,v in inherited.items()}}
    for key in ('original_generator','current_validator','adapter','inventory_lookup_code'):
        collect(identity[key])
    return {'schema_version':U.SCHEMA,'policy':U.POLICY,'identity':identity,'audit_bindings':selected,
        'config':{'model':model,'endpoint':endpoint,'project_id':project_id},
        'coverage':result,'retained_consumption':counts,
        'accepted_completions':[{'request':b['request'],'response':b['response'],'finish_reason':'stop'} for b in coverage['batches']],
        'checked_files':dict(sorted(files.items())),'model_reexecuted':False,'semantic_attempts_added':0,
        'source_approval':False}


def _renderer_project(s, old_bindings, historical_inventory):
    from scripts.batch import harbor_audit_bridge as bridge
    from scripts.batch import harbor_model_executor as executor
    from scripts.batch.harbor_model_review_provenance import validate_call
    from xl_source_publication import validate_source_generation
    require(isinstance(s,R.Selection),'typed root-selected renderer recovery required')
    identity=R.code_identity(s.original_code);plan=read(s.root_plan);old_meta=read(s.original_metadata)
    prepared=read(plan['prepared']);wb=prepared['validated']['workbook'];baseline=read(plan['baseline']);b=baseline['bindings']
    require(plan['entry']['sha256']==R.ORIGINAL_SHA256,'root plan belongs to another generator')
    require(old_meta.get('status')=='in_progress' and old_meta.get('workbook')==wb,'only retained unfinished rendering is supported')
    require(bool(s.attempts) and len({r['path'] for r in s.attempts})==len(s.attempts),'complete distinct bridge attempt selection required')
    expected_bridge=Path(s.root_plan['path']).parent/'audit-bridge'
    target=Path(plan['prepared']['path']).parent/'audit'/(wb+'-variable-sources');stem=wb+'-inputs-variable-sources'
    require(Path(s.original_metadata['path'])==target/(stem+'.metadata.json'),'foreign original metadata')
    for key in ('source_file','original_raw_source','baseline_inputs','baseline_segmentation','curation'):checked_record(b[key])
    source=Path(b['source_generation']['path']);seg=Path(b['segmentation_generation']['path'])
    manifest=validate_source_generation(source,expected_generation_id=source.name)
    require(manifest['identity']['policy_decision']['route']=='pass' and b['source_file']['sha256']==b['original_raw_source']['sha256'],'renderer recovery requires unchanged native source-pass identity')
    require(Q.selected(b)==Q.selected(old_bindings), 'renderer selected another historical pair')
    inventory=copy.deepcopy(historical_inventory)
    original_inventory=record(target/(stem+'.inventory.json'));require(read(original_inventory)==inventory,'current baseline/inventory changed')
    require(plan['inventory_sha256']==executor.sha(executor.canonical(inventory)),'root inventory identity differs')
    scope=audit._attempt_scope_config(audit._canonical_hash(inventory),s.policy.endpoint.removesuffix('/chat/completions'),s.policy.model,s.policy.project_id);scope['generator_sha256']=R.ORIGINAL_SHA256
    require(old_meta.get('attempt_scope')==scope,'original audit scope differs')
    scope_root=target/'.audit-attempts'/audit._canonical_hash(scope);own_policy=record(scope_root/'policy.json');policy=read(own_policy)
    require(policy.get('schema_version')=='audit-attempt-policy/v1' and type(policy.get('limit')) is int and policy['limit'] in (1,2),'original policy contract changed')
    require(policy['scope']==scope and policy.get('inherited_budget')==plan['inherited_budget'],'original scope/policy or inherited budget changed')
    observed_paths={str(p) for p in expected_bridge.glob('batch-*/prepared.json')}
    require({r['path'] for r in s.attempts}==observed_paths,'bridge attempt set omitted or expanded')
    all_requests={str(p) for p in scope_root.glob('batch-*/*.request.json')};seen_requests=set();by_batch={};calls=[];completed_at=[]
    require({str(p) for p in (target/'.audit-attempts').rglob('*.request.json')}==all_requests,'another original audit scope has unaccounted attempts')
    files={}
    def collect(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256'}:
                checked_record(value);files[value['path']]=value['sha256']
            else:
                for child in value.values():collect(child)
        elif isinstance(value,(list,tuple)):
            for child in value:collect(child)
    collect([s.root_plan,plan['prepared'],plan['baseline'],s.original_code,s.original_metadata,own_policy,original_inventory,identity,b])
    for directory in (source,seg):
        for item in directory.rglob('*'):
            if item.is_file():collect(record(item))
    for binding in sorted(s.attempts,key=lambda r:r['path']):
        saved=read(binding);call=bridge.prepared_from(saved);ctx=call.audit_context
        require(read(ctx.policy_snapshot)['limit']==policy['limit'],'original final policy limit changed')
        require(call.policy==__import__('dataclasses').replace(s.policy,reasoning_effort=None) and ctx.root_plan==s.root_plan and ctx.inventory==original_inventory,'attempt is outside selected context')
        require(ctx.attempt['path'] in all_requests and ctx.attempt['path'] not in seen_requests,'original request omitted or duplicated');seen_requests.add(ctx.attempt['path'])
        observed=executor.inspect(call,s.campaign,original_renderer_code=s.original_code)
        require(observed.status=='completed' and observed.call is not None,'unknown/rejected transport cannot be reconciled')
        actual=validate_call(observed.call,read(call.request),policy=call.policy,json_response=False,audit_context=ctx,original_renderer_code=s.original_code)
        request=read(ctx.attempt);response_path=Path(ctx.attempt['path']).with_name(Path(ctx.attempt['path']).name.replace('.request.','.response.'));response=record(response_path)
        expected_response=dict(request_sha256=ctx.attempt['sha256'],content=actual['review'].strip(),finish_reason='stop')
        require(response_path.read_bytes()==(json.dumps(expected_response,sort_keys=True)+'\n').encode(),'original auditor response differs from completed strict transport')
        require(Path(binding['path']).parent==expected_bridge/(f'batch-{request["batch"]:04d}-{request["attempt"]:02d}'),'bridge path differs')
        by_batch.setdefault(request['batch'],[]).append((request['attempt'],ctx.attempt,response,audit._clean_table(actual['review'])))
        calls.append(dict(prepared=binding,request=observed.call.request,response=observed.call.response,outcome=observed.outcome,response_id=actual['response_id'],attempt_id=actual['attempt_id']))
        completed_at.append(read(observed.call.completed)['completed_at_ns']);collect([binding,asdict(observed.call),observed.outcome,asdict(ctx),response])
        # Include every immutable transport file named by its sealed outcome.
        transport_root=Path(observed.outcome['path']).parent
        for name,sha in read(observed.outcome)['evidence'].items():collect({'path':str(transport_root/name),'sha256':sha})
    require(seen_requests==all_requests,'an original request lacks complete transport proof')
    chunks=audit._split_chunks(inventory);consumed=audit._inherited_batch_counts(plan['inherited_budget'],inventory)
    require(set(by_batch)==set(range(1,len(chunks)+1)) and set(policy['submitted_counts'])=={str(i) for i in by_batch},'batch coverage incomplete')
    all_ids={r['row_id'] for r in inventory['variables']};batches=[];tables=[]
    for index,(header,rows) in enumerate(chunks,1):
        history=sorted(by_batch[index]);n=len(history)
        require([x[0] for x in history]==list(range(1,n+1)) and n==policy['submitted_counts'][str(index)] and n<=min(policy['limit'],2-consumed[index]),'original budget history incomplete or exceeded')
        number,request,response,table=history[-1];by_id={json.loads(r)['row_id']:json.loads(r) for r in rows}
        faults=audit.validate_row_ids(table,set(by_id),all_ids)
        require(not faults,'retained model table fails current row coverage')
        resolved=audit.resolve_table(table,by_id);require(not audit.validate_table(resolved,rows,inventory['metadata']),'retained model table fails current value/source checks')
        batches.append(dict(row_ids=list(by_id),model_table=table,attempt=number,request=request,response=response,history=[x[1] for x in history],response_history=[x[2] for x in history]));tables.append(resolved)
    parts=[f'# `{Path(b["baseline_inputs"]["path"]).name}` — variable values and plausible external sources','',
        'Re-rendered from the exact retained completed Labelbox responses; no model was called again.','',
        '> **Audit warning:** This audit maps workbook-supplied values to plausible sources. It does not prove that any source supplied the exact value.','']
    for index,table in enumerate(tables,1):
        if len(tables)>1:parts.extend([f'## Inventory batch {index} of {len(tables)}',''])
        parts.extend([table,''])
    markdown='\n'.join(parts).rstrip()+'\n';coverage=dict(schema_version='variable-source-audit-coverage/v1',inventory_sha256=audit._canonical_hash(inventory),batches=batches)
    require(not audit.validate_audit_coverage(markdown,inventory,coverage),'current derived audit coverage failed')
    coverage_raw=(json.dumps(coverage,indent=2)+'\n').encode();md_raw=markdown.encode()
    metadata=dict(schema_version=1,status='complete',completion_kind='derived_from_retained_completed_transport',generator_version=audit.GENERATOR_VERSION,prompt_version=audit.PROMPT_VERSION,row_id_resolution=True,
        generator_sha256=R.ORIGINAL_SHA256,rendering_generator_sha256=identity['current_generator']['sha256'],renderer_policy=R.POLICY,
        system_prompt_sha256=hashlib.sha256(audit.SYSTEM_PROMPT.encode()).hexdigest(),workbook=wb,artifact=b['baseline_inputs']['path'],model=s.policy.model,endpoint=s.policy.endpoint.removesuffix('/chat/completions'),project_id=s.policy.project_id,
        request_config_sha256=scope['request_config_sha256'],inventory_sha256=scope['inventory_sha256'],inventory_rows=len(inventory['variables']),attempt_scope=scope,attempt_policy=own_policy,semantic_attempt_limit=policy['limit'],api_calls=len(calls),model_calls_added=0,
        retained_completed_at_ns=completed_at,original_metadata=s.original_metadata,generated_at=dt.datetime.fromtimestamp(max(completed_at)/1e9,dt.timezone.utc).isoformat(),generated_at_basis='retained_transport_completion',
        markdown_sha256=hashlib.sha256(md_raw).hexdigest(),coverage_path=str(s.output/(stem+'.coverage.json')),coverage_sha256=hashlib.sha256(coverage_raw).hexdigest(),finish_reasons=['stop']*len(chunks),validation={'status':'passed','checks':['complete_current_coverage','exact_retained_transport','unchanged_original_inventory']},markdown=str(s.output/(stem+'.md')),inventory=str(s.output/(stem+'.inventory.json')),cache_hit=False)
    outputs={stem+'.inventory.json':Path(original_inventory['path']).read_bytes(),stem+'.md':md_raw,stem+'.coverage.json':coverage_raw,stem+'.metadata.json':(json.dumps(metadata,indent=2)+'\n').encode()}
    return dict(identity=identity,workbook=wb,original_bindings=b,calls=calls,consumption=dict(submitted_counts=policy['submitted_counts'],inherited_batch_counts={str(k):v for k,v in consumed.items()}),checked_files=files),outputs


def _renderer_wrapper(previous, *, bindings, original_code, original_renderer_code, historical_inventory):
    """Reconstruct the old issued renderer envelope using only current code."""
    old=read(previous);require(old.get('schema_version')==R.SCHEMA and old.get('policy')==R.POLICY,'unsupported original renderer receipt')
    L.witness(original_renderer_code,L.RENDERER_BEFORE)
    selection=R.from_data(old['selection']);require(selection.original_code==original_code,'original auditor selection changed')
    current,outputs=_renderer_project(selection,bindings,historical_inventory)  # Historical context validated by propose; current pair is separately mandatory.
    require({k:bindings[k] for k in R.KEYS}==old['audit_bindings'],'original audit artifacts moved or changed')
    require(Q.selected(bindings)==Q.selected({**current['original_bindings'],**old['audit_bindings']}),'renderer source/baseline pair changed')
    # Exact predecessor identities are admitted only behind this new wrapper.
    expected=copy.deepcopy(current);identity=expected['identity']
    require(identity['current_generator']['sha256']==I.AFTER_SHA256,'unexpected current inventory implementation')
    identity['current_generator']['sha256']=I.BEFORE_SHA256
    identity['adapter']['sha256']=L.RENDERER_BEFORE
    lookup=identity.pop('inventory_lookup_code')
    for item in (identity['current_generator'],identity['adapter']):
        require(item['path'] in expected['checked_files'],'missing original code role')
        expected['checked_files'][item['path']]=item['sha256']
    require(expected['checked_files'].pop(lookup['path'],None)==lookup['sha256'],'lookup proof not independently bound')
    require(old['report']==expected,'renderer changed beyond exact reader proof identity')
    require(set(old)=={'schema_version','policy','selection','report','audit_bindings','outputs','model_reexecuted','semantic_attempts_added','source_approval'}
        and old['model_reexecuted'] is False and old['semantic_attempts_added']==0 and old['source_approval'] is False,'old renderer contract changed')
    require(set(old['outputs'])==set(outputs),'original renderer output set changed')
    files=dict(current['checked_files'])
    for name,data in outputs.items():
        if name.endswith('.metadata.json'):
            meta=json.loads(data);require(meta['rendering_generator_sha256']==I.AFTER_SHA256,'current rendering identity differs')
            meta['rendering_generator_sha256']=I.BEFORE_SHA256
            data=(json.dumps(meta,indent=2)+'\n').encode()
        binding=old['outputs'][name];checked_record(binding)
        require(Path(binding['path'])==selection.output/name and Path(binding['path']).read_bytes()==data,'original renderer financial output changed')
        files[binding['path']]=binding['sha256']
    files.update(L.records([previous,original_renderer_code,original_code,L.code_records()]))
    return dict(schema_version=L.AUDIT_SCHEMA,previous=previous,original_code=original_code,original_renderer_code=original_renderer_code,
        audit_bindings={k:bindings[k] for k in R.KEYS},context_bindings=current['original_bindings'],
        current_report=current,checked_files=dict(sorted(files.items())),model_reexecuted=False,semantic_attempts_added=0,source_approval=False)



def _history(selection, old, inventory):
    """Reconstruct issued evidence with current readers; never trust its flags."""
    issued=read(selection.prior_upgrade)
    config=dict(model=selection.model,endpoint=selection.endpoint,project_id=selection.project_id)
    if issued.get('schema_version')==U.SCHEMA:
        require(selection.original_auditor['sha256']==I.BEFORE_SHA256,'completed scope is not exact 56ab')
        expected=_direct_upgrade(old,original_code=selection.original_auditor,**config)
        require(issued==expected,'historical direct upgrade changed')
        files=dict(expected['checked_files'])
    elif issued.get('schema_version')==L.AUDIT_SCHEMA:
        require(selection.original_auditor['sha256']==R.ORIGINAL_SHA256,'recovered scope is not exact 9a')
        expected=_renderer_wrapper(issued['previous'],bindings=old,original_code=selection.original_auditor,
            original_renderer_code=issued['original_renderer_code'],historical_inventory=inventory)
        require(issued==expected,'historical renderer wrapper changed')
        s=R.from_data(read(issued['previous'])['selection'])
        require((s.policy.model,s.policy.endpoint.removesuffix('/chat/completions'),s.policy.project_id)==
            (selection.model,selection.endpoint.rstrip('/'),selection.project_id),'renderer model configuration differs')
        files=dict(expected['checked_files'])
    else:
        raise ValueError('only issued completed56ab or wrapped recovered0035 evidence is supported')
    files[selection.prior_upgrade['path']]=selection.prior_upgrade['sha256']
    metadata=read(old['variable_source_metadata']);coverage_path=Path(metadata['coverage_path'])
    coverage=Q.read(coverage_path)
    require(metadata['coverage_sha256']==Q.digest(coverage_path),'historical coverage changed')
    require(read(old['variable_source_inventory'])==inventory,'historical saved inventory changed')
    require(metadata['artifact']==old['baseline_inputs']['path'],'historical baseline artifact differs')
    # Preserve actual correction history and all exhausted/inherited rounds.
    U._validate_original_attempts(metadata,inventory,coverage,selection.endpoint,selection.model,
        selection.project_id,selection.original_auditor['sha256'])
    policy=read(metadata['attempt_policy']);expected_requests=set();expected_responses=set()
    for batch in coverage['batches']:
        expected_requests.update(r['path'] for r in batch['history'])
        expected_responses.update(r['path'] for r in batch['response_history'] if r is not None)
    scope=Path(metadata['attempt_policy']['path']).parent
    require({str(p) for p in scope.rglob('*.request.json')}==expected_requests,'unaccounted historical requests')
    require({str(p) for p in scope.rglob('*.response.json')}==expected_responses,'unaccounted historical responses')
    require(scope.parent.name=='.audit-attempts' and scope.name==audit._canonical_hash(policy['scope']) and
        Path(metadata['attempt_policy']['path']).name=='policy.json','historical attempt scope path differs')
    # A different same-workbook scope must not hide a later unknown call or
    # reset budget. Other scopes count only when explicitly inherited and
    # independently accepted by the owning inherited-budget validator below.
    inherited=read(policy['inherited_budget']) if policy.get('inherited_budget') else {}
    all_requests=set(expected_requests);all_responses=set(expected_responses)
    def inherited_records(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in ('request','response') and child is not None and isinstance(child,dict) and set(child)=={'path','sha256'}:
                    U.checked(child)
                    (all_requests if key=='request' else all_responses).add(child['path'])
                inherited_records(child)
        elif isinstance(value,list):
            for child in value:inherited_records(child)
    inherited_records(inherited)
    for pattern,allowed in (('*.request.json',all_requests),('*.response.json',all_responses)):
        observed={str(p) for p in scope.parent.rglob(pattern)}
        retained={p for p in allowed if Path(p).is_relative_to(scope.parent)}
        require(observed==retained,'unaccounted historical audit scope')
    def collect(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256'}:
                path=U.checked(value);files[str(path)]=value['sha256']
            else:
                for v in value.values():collect(v)
        elif isinstance(value,(tuple,list)):
            for v in value:collect(v)
    collect([metadata,coverage,policy])
    if policy.get('inherited_budget'):collect(read(policy['inherited_budget']))
    for key in Q.AUDIT_KEYS:
        p=Q.checked(old[key]);files[str(p)]=Q.digest(p)
    files[str(coverage_path.resolve())]=Q.digest(coverage_path)
    texts=[Path(old['variable_source_audit']['path']).read_text()]
    for batch in coverage['batches']:texts.extend([read(batch['response'])['content'],batch['model_table']])
    for key in Q.SOURCE_LOCATORS:
        locator=inventory['metadata']['segmentation'][key]
        require(not any(locator.casefold() in text.casefold() for text in texts),'audit relies on historical locator')
    consumption=dict(limit=policy['limit'],submitted_counts=policy['submitted_counts'],
        inherited_batch_counts={str(k):v for k,v in audit._inherited_batch_counts(policy.get('inherited_budget'),inventory).items()})
    return coverage,files,consumption


def propose(selection):
    """Standalone read-only proposal; cannot rebind trackers or approve a source."""
    require(isinstance(selection,Selection),'typed root-selected audit contexts required')
    reader_files=_readers()
    old=Q.selected(selection.old_bindings,historical=True);new=Q.selected(selection.current_bindings)
    require(old['source_generation']!=new['source_generation'],'fresh source builder generation required')
    Q.historical_baseline(old)
    old_context,old_seg,inventory=Q._context(old,historical=True)
    new_context,new_seg,fresh=Q._context(new,historical=False)
    source_comparison=Q.source_builder_equivalence(old['source_generation'],new['source_generation'])
    _native_pair(old,new)
    require(old['original_raw_source']==new['original_raw_source'],'original source binding changed')
    require(old_seg['artifacts']['curation.toml']==new_seg['artifacts']['curation.toml'],'curation changed')
    require(old_seg['curated_output_cells']==new_seg['curated_output_cells'],'ordered target identity changed')
    require(bool(new_seg['curated_output_cells']['ordered']),'empty target scope')
    baseline=Q.zip_members(Q.checked(old['baseline_inputs']))
    require(baseline==Q.zip_members(Q.checked(new['baseline_inputs'])),'baseline ZIP member bytes or metadata changed')
    differences=Q.inventory_equivalence(inventory,fresh,source_builder_changed=True)
    files=_code_closure(selection,old_seg);files.update(reader_files)
    coverage,history,consumption=_history(selection,old,inventory);files.update(history)
    refreshed=copy.deepcopy(coverage);refreshed['inventory_sha256']=audit._canonical_hash(fresh)
    require(not audit.validate_audit_coverage(Path(old['variable_source_audit']['path']).read_text(),fresh,refreshed),
        'retained audit fails current inventory coverage')
    for bindings in (old,new):
        for key in Q.FILE_KEYS:
            p=Q.checked(bindings[key]);files[str(p)]=Q.digest(p)
        for key in Q.GEN_KEYS:
            directory=Path(bindings[key]['path'])
            for p in directory.rglob('*'):
                if p.is_file():
                    require(not p.is_symlink(),'generation evidence contains symlink')
                    files[str(p.resolve())]=Q.digest(p)
            if key=='segmentation_generation':
                p=directory.parent.parent/'curation.toml';files[str(p.resolve())]=Q.digest(p)
    if old.get('historical_baseline_witness'):
        for r in old['historical_baseline_witness'].values():p=Q.checked(r);files[str(p)]=Q.digest(p)
    for p in (*Q.seg_pub.verifier_paths(),*Q.source_pub._default_builder_paths(),Path(__file__),
              Path(Q.__file__),Path(U.__file__),Path(R.__file__),Path(L.__file__),Path(audit.__file__)):
        files[str(Path(p).resolve())]=Q.digest(p)
    return dict(schema_version=SCHEMA,selection=asdict(selection),source_builder_equivalence=source_comparison,
        differences=differences,old_inventory_sha256=audit._canonical_hash(inventory),
        current_inventory_sha256=audit._canonical_hash(fresh),baseline_zip_members_sha256=audit._canonical_hash(baseline),
        retained_consumption=consumption,checked_files=dict(sorted(files.items())),model_reexecuted=False,
        historical_code_executed=False,semantic_attempts_added=0,source_approval=False)


def validate(receipt, *, selection):
    """The caller supplies both contexts, upgrade, original code and old code set."""
    value=read(receipt);expected=propose(selection)
    # Canonical JSON normalizes the dataclass tuple while preserving order.
    require(value==json.loads(json.dumps(expected)),'historical audit applicability stale or altered')
    return {**expected['checked_files'],receipt['path']:receipt['sha256']}
