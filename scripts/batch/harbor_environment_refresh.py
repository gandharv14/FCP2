"""Explicit current applicability of an immutable environment prefix.

Old checkpoints and audit bridge defaults are unchanged. Every current source,
audit, normalization and completed later gate is rechecked before an atomic
binding refresh. No retained code executes and no model budget resets.
"""
from dataclasses import asdict,replace
from pathlib import Path
import copy
from scripts.batch import harbor_environment_worker as W
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_tracker as T
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_transition_codec import encode,decode
SCHEMA='harbor-environment-prefix-refresh/v1'
KINDS=('research','normalization_phase1','source_profile','normalization_phase2','environment')
AUDIT={'audit_equivalence','audit_validator_upgrade','audit_upgrade_original_code','audit_current_inventory'}
NPROOF={'normalization_audit_applicability','normalization_original_locator_code'}
NOUTPUT={'normalization_draft','normalizer','normalization_prepared_manifest','normalized_phase1','normalized_final','normalization_report','normalization_exclusions','normalization_partitioned_review'}


def keep(path,value):return W.keep(path,value)


MUTABLE={'normalized_final':'normalized.json','normalization_report':'normalization_report.json','normalization_exclusions':'exclusions.json'}


def checked(binding,witnesses=()):
    match=[w for w in witnesses if w['original']==binding]
    if match:
        require(len(match)==1 and match[0]['copy']['sha256']==binding['sha256'],'ambiguous mutable witness')
        E.checked_bytes(match[0]['copy'])
    else:E.checked_bytes(binding)


def historical_audit_codes(old,current):
    """Three exact code roles admitted by current independently checked wrappers.

    This does not load historical modules or treat their live paths as current.
    The archived locator/renderer and reconstructed auditor are byte evidence.
    All other predecessor evidence remains checked at its exact original path.
    """
    import xl_audit_equivalence as Q
    import xl_audit_renderer_recovery as R
    import xl_variable_source_audit as A
    import xl_audit_inventory_lineage as L
    import xl_audit_inventory_upgrade as I
    from scripts.qualified_publication import validate_audit_bindings
    require(not unchanged(old,current),'partition delegation requires its own historical proof')
    ob,nb=old.content.bindings,current.content.bindings
    locator=read(nb['audit_equivalence']);renderer=read(nb['audit_validator_upgrade'])
    require(locator.get('schema_version')==L.LOCATOR_SCHEMA and renderer.get('schema_version')==L.AUDIT_SCHEMA,'exact current inventory wrappers required')
    require(locator['previous']==ob['audit_equivalence'] and renderer['previous']==ob['audit_validator_upgrade']
        and locator['audit_applicability']==nb['audit_validator_upgrade'],'historical audit proof pair differs')
    files=validate_audit_bindings(nb,expected_generations={k:nb[k+'_generation']['id'] for k in ('source','segmentation')})
    previous=read(locator['previous']);roles={}
    for module,archive,sha in ((Q,locator['original_locator_code'],L.LOCATOR_BEFORE),(R,renderer['original_renderer_code'],L.RENDERER_BEFORE)):
        L.witness(archive,sha)
        require(files.get(archive['path'])==sha,'historical archive omitted from current owning proof')
        path=str(Path(module.__file__).resolve())
        require(previous['checked_files'].get(path)==sha,'historical code role missing or changed')
        roles[path]=sha
    # The existing reviewed two-block inverse proves the complete original56ab
    # bytes from the entire hash-checked current module, with no execution.
    auditor=record(Path(A.__file__).resolve());raw=I.restore_inventory_lookup(E.checked_bytes(auditor))
    require(E.sha(raw)==I.BEFORE_SHA256 and previous['checked_files'].get(auditor['path'])==I.BEFORE_SHA256,'historical auditor bytes differ')
    require(files.get(auditor['path'])==auditor['sha256'] and files.get(I.evidence()['path'])==I.evidence()['sha256'],'current auditor transition evidence missing')
    roles[auditor['path']]=I.BEFORE_SHA256
    return roles


PRIOR_REFRESH_SHA256='405c7c1dd6ea22474f9b52a867363202adb6cb999bd404be216ae234fc6bb240'
MAX_REFRESH_ANCESTRY=8


def _history_checkpoint(s,descriptor,kind,witnesses,seen):
    """Read and revalidate committed refresh ancestry without executing old code."""
    from scripts import harbor_campaign as coordinator
    d=read(descriptor)
    require(set(d)=={'schema_version','worker','initial_lease','controls','origin','code','refresh'}
            and d['schema_version']=='harbor-environment-coordinator-refresh/v1','invalid refreshed predecessor descriptor')
    require(coordinator.decode(d['worker'])==s,'refreshed predecessor belongs to another worker')
    require(E.checked_bytes(d['code'])==E.checked_bytes(record(coordinator.__file__)),'unreviewed predecessor coordinator')
    for item in d['origin']:checked(item,witnesses)
    binding=d['refresh'];key=(binding['path'],binding['sha256'])
    require(key not in seen and len(seen)<MAX_REFRESH_ANCESTRY,'cyclic or overlong refresh ancestry')
    seen=(*seen,key);v=read(binding);root=Path(binding['path']).parent
    require(set(v)=={'schema_version','code','worker','prior','checkpoint','original_checkpoints','before','selection','last','results','evidence','semantic_budget_reset','mutable_witnesses'}
            and v['schema_version']==SCHEMA and v['semantic_budget_reset'] is False,'invalid historical refresh schema')
    require(v['worker']==record(s.run/'selection.json'),'historical refresh worker changed')
    E.checked_bytes(v['code']);require(v['code']['sha256'] in {PRIOR_REFRESH_SHA256,record(__file__)['sha256']},'unreviewed historical refresh implementation')
    selected=decode(v['selection']);before=read(v['before']);local=v['mutable_witnesses']
    require(len(local)==len({w['key'] for w in local}) and all(w['key'] in MUTABLE for w in local),'invalid historical mutable witnesses')
    for w in local:
        require(Path(w['original']['path'])==selected.content.normalization_run/MUTABLE[w['key']]
                and Path(w['copy']['path'])==root/'generated-before'/MUTABLE[w['key']],'foreign historical output witness')
        checked(w['original'],local)
    combined=tuple({(w['original']['path'],w['original']['sha256']):w for w in (*witnesses,*local)}.values())
    for item in [v['prior'],v['checkpoint'],*v['original_checkpoints'],*v['evidence']]:checked(item,combined)
    earlier,prior_kind,checkpoint=predecessor(s,v['prior'],combined,current=selected,_seen=seen)
    partition=unchanged(earlier,selected)
    require(checkpoint==v['checkpoint'] and v['last']==('normalization_phase1' if partition else prior_kind),'historical refresh predecessor changed')
    committed=read(record(root/'committed.json'));lease=coordinator.decode(d['initial_lease'])
    require(set(committed)=={'prepared','tracker_sha256','revision'} and committed['prepared']==binding
            and committed['revision']==before['revision']+1 and (lease.revision,lease.sha256)==(committed['revision'],committed['tracker_sha256']),
            'historical refresh commit differs from descriptor')
    prior_lease=before['leases']['task_agent']
    require((lease.owner,lease.token)==(prior_lease['owner'],prior_lease['token']),'historical refresh changed lease owner/token')
    # A later phase2 may have replaced only these managed outputs. Recompute
    # their current owning projection, then compare the historical bindings via
    # exact retained witnesses; no mixed or arbitrary output is accepted.
    current_bindings=dict(selected.content.bindings);replacement={}
    for w in local:
        actual=record(w['original']['path']);current_bindings[w['key']]=actual
        replacement[(actual['path'],actual['sha256'])]=w['original']
    inspection=replace(selected,content=replace(selected.content,bindings=current_bindings))
    results=checks(s,before,inspection,v['last'])
    current_root=Path(__file__).resolve().parents[2];prior_root=Path(v['code']['path']).parents[2]
    def historical(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256'}:
                pair=(value['path'],value['sha256'])
                if pair in replacement:return replacement[pair]
                path=Path(value['path'])
                if path.is_relative_to(current_root):
                    old=prior_root/path.relative_to(current_root)
                    require(record(old)['sha256']==value['sha256'],'historical evidence code differs')
                    return dict(path=str(old),sha256=value['sha256'])
            return {k:historical(x) for k,x in value.items()}
        if isinstance(value,list):return [historical(x) for x in value]
        return value
    require(historical(results)==v['results'] and v['evidence']==[x for r in v['results'] for x in r['evidence']],
            'historical refresh owning evidence changed')
    continuation=root/'continuation';checkpoint=continuation/(kind+'.json')
    require(not (s.run/(kind+'.json')).exists(),'competing original and refreshed checkpoint')
    require(not any((continuation/(k+'.json')).exists() for k in KINDS[KINDS.index(kind)+1:]),'later refreshed checkpoint requires full-prefix selection')
    return record(checkpoint)


def predecessor(s,binding,witnesses=(),*,current=None,_seen=()):
    value=read(binding)
    require(value.get('step') in KINDS and value.get('status') in {'STEP_COMPLETE','ENVIRONMENT_READY'},'exact completed predecessor result required')
    selected=decode(value['selection']);kind=value['step']
    descriptor=value.get('descriptor')
    refreshed=descriptor is not None and read(descriptor).get('schema_version')=='harbor-environment-coordinator-refresh/v1'
    checkpoint=_history_checkpoint(s,descriptor,kind,witnesses,_seen) if refreshed else record(s.run/(kind+'.json'))
    saved=read(checkpoint)
    require(saved['selection']==value['selection'],'predecessor differs from original checkpoint')
    roles=None
    for item in value['evidence']+saved['evidence']:
        try:checked(item,witnesses)
        except T.Conflict:
            if current is None:raise
            if roles is None:roles=historical_audit_codes(selected,current)
            require(roles.get(item['path'])==item['sha256'],'changed non-code or unreviewed code in predecessor')
    require(selected.content.workbook==s.bootstrap.workbook,'foreign predecessor workbook')
    return selected,kind,checkpoint


def unchanged(old,new):
    """Only owning proof locators, or explicitly delegated partition output, vary."""
    a,b=old.content,new.content;ob,nb=a.bindings,b.bindings
    partition=nb.get('normalization_partitioned_review') is not None and a.normalization_prepared is None
    allowed=AUDIT|NPROOF|(NOUTPUT if partition else set())
    require({k:v for k,v in ob.items() if k not in allowed}=={k:v for k,v in nb.items() if k not in allowed},'non-proof financial binding changed')
    candidate=replace(b,bindings=ob)
    if partition:
        require(b.normalization_call is None,'partition output cannot invent a single Call')
        from scripts.batch import harbor_normalization_partitioned as P
        manifest=read(b.normalization_prepared)
        require(manifest['previous']['prior_plan']==record(a.normalization_run/'production-selection.json'),'partition predecessor differs')
        from scripts.batch.harbor_normalization_validator_bridge import validate_content
        validate_content(b)
        candidate=replace(candidate,normalization_run=a.normalization_run,normalization_prepared=a.normalization_prepared,normalization_call=a.normalization_call)
    require(replace(new,content=candidate)==old,'task, model Call or non-proof selection changed')
    for key in ('audit_equivalence','audit_validator_upgrade'):
        if ob.get(key) is not None and nb.get(key)!=ob[key]:
            require(read(nb[key]).get('previous')==ob[key],'audit proof does not retain exact predecessor')
    return partition


def checks(s,before,selection,last):
    require(last in KINDS and KINDS.index(last)>=1,'refresh requires completed normalization evidence')
    results=[]
    for kind in KINDS[:KINDS.index(last)+1]:
        frame=copy.deepcopy(before)
        # This is a validator frame for the already independently proven phase1,
        # not a tracker mutation or substituted model evidence.
        if kind=='normalization_phase2':
            frame['lane_state']['harbor-normalization-engineer']['phase']='phase1_complete'
        if kind in {'source_profile','normalization_phase2','environment'}:
            require(before['lane_state'][dict(W.lanes.STEPS)[kind]]['state'] in {'passed','running'},'cannot refresh an uncompleted downstream gate')
        result=W.lanes.evaluate_completed_gate(frame,W.lanes.CompletionEvidence(kind,selection),W._context(s))
        require(result['passed'],'current owning prefix rejected: '+kind)
        results.append(result)
    return results


def prepare(s,lease,prior,current,last,root):
    require(read(record(s.run/'selection.json'))['selection']==W.bootstrap.serial(asdict(s)),'retained worker recipe changed')
    _,before=W._active(s,lease);old,kind,checkpoint=predecessor(s,prior,current=current)
    partition=unchanged(old,current)
    require(last==('normalization_phase1' if partition else kind),'prefix cannot skip unfinished stages')
    if partition:
        from scripts.batch import harbor_normalization_partitioned as P
        selected=P.selection(current.content.normalization_prepared)
        require(selected.budget==s.normalization_budget,'worker original budget differs from partition delegation')
    else:require(KINDS.index(kind)>=1,'normalization proof required before retained checkpoint refresh')
    # Future checkpoints cannot be silently abandoned or reused under old proof.
    require(not any((s.run/(k+'.json')).exists() for k in KINDS[KINDS.index(last)+1:]),'later checkpoint requires full-prefix selection')
    for later in KINDS[KINDS.index(last)+1:]:
        row=before['lane_state'][dict(W.lanes.STEPS)[later]]
        if later=='normalization_phase2' and last in {'normalization_phase1','source_profile'}:
            require(row.get('phase')!= 'phase2_complete','completed phase2 needs full-prefix selection')
        else:require(row['state']=='pending','completed downstream lane needs full-prefix selection')
    results=checks(s,before,current,last)
    root=E.safe(root);require(not root.exists(),'refresh preparation exists; retain and select it explicitly')
    root.mkdir(parents=True,mode=0o700)
    E.retain(root/'before.json',(s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json')).read_bytes())
    before_record=record(root/'before.json')
    evidence=[item for result in results for item in result['evidence']]
    originals=[record(s.run/(k+'.json')) for k in KINDS if (s.run/(k+'.json')).exists()]
    witnesses=[]
    for key,name in MUTABLE.items():
        item=current.content.bindings.get(key)
        if item:
            require(Path(item['path'])==current.content.normalization_run/name,'managed output path differs')
            raw=E.checked_bytes(item);copy_path=root/'generated-before'/name
            copy_path.parent.mkdir(exist_ok=True,mode=0o700);E.retain(copy_path,raw)
            witnesses.append(dict(key=key,original=item,copy=record(copy_path)))
    descriptor=dict(mutable_witnesses=witnesses,schema_version=SCHEMA,code=record(__file__),worker=record(s.run/'selection.json'),prior=prior,checkpoint=checkpoint,original_checkpoints=originals,before=before_record,selection=encode(current),last=last,results=results,evidence=evidence,semantic_budget_reset=False)
    return keep(root/'prepared.json',descriptor)


def validate(s,binding,before=None):
    v=read(binding)
    require(set(v)=={'schema_version','code','worker','prior','checkpoint','original_checkpoints','before','selection','last','results','evidence','semantic_budget_reset','mutable_witnesses'} and v['semantic_budget_reset'] is False,'invalid refresh evidence schema/budget')
    require(read(record(s.run/'selection.json'))['selection']==W.bootstrap.serial(asdict(s)),'retained worker recipe changed')
    require(v['schema_version']==SCHEMA and v['code']==record(__file__) and v['worker']==record(s.run/'selection.json'),'refresh owner/worker changed')
    witnesses=v['mutable_witnesses']
    require(len({w['key'] for w in witnesses})==len(witnesses) and all(w['key'] in MUTABLE for w in witnesses),'invalid managed witnesses')
    for w in witnesses:
        require(Path(w['original']['path'])==decode(v['selection']).content.normalization_run/MUTABLE[w['key']] and Path(w['copy']['path'])==Path(binding['path']).parent/'generated-before'/MUTABLE[w['key']],'foreign generated witness')
        checked(w['original'],witnesses)
    for item in [v['before'],v['prior'],v['checkpoint'],*v['original_checkpoints'],*v['evidence']]:checked(item,witnesses)
    current=decode(v['selection']);old,kind,checkpoint=predecessor(s,v['prior'],witnesses,current=current);partition=unchanged(old,current)
    require(checkpoint==v['checkpoint'] and v['last']==('normalization_phase1' if partition else kind),'prefix identity changed')
    old_before=read(v['before'])
    later=Path(binding['path']).parent/'continuation/normalization_phase2.json'
    selected=current;expected=v['results']
    if later.exists():
        require(v['last'] in {'normalization_phase1','source_profile'},'unexpected later phase2 checkpoint')
        saved=read(record(later));latest=decode(saved['selection'])
        if v['last']=='source_profile':
            require(current.public_profiles==latest.public_profiles,'phase2 lost exact retained profile proof')
        else:
            profile=read(record(later.parent/'source_profile.json'));profile_selection=decode(profile['selection'])
            require(profile_selection.public_profiles==latest.public_profiles,'phase2 lost exact prior profile proof')
            for item in profile['evidence']:checked(item,witnesses)
        for item in saved['evidence']:E.checked_bytes(item)
        live=read(record(s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json')))
        checks(s,live,latest,'normalization_phase2')
        require(latest.content.normalization_run==current.content.normalization_run and latest.content.normalization_call==current.content.normalization_call and latest.content.normalization_prepared==current.content.normalization_prepared,'phase2 changed independent normalization identity')
        for key,val in current.content.bindings.items():
            if key not in MUTABLE:require(latest.content.bindings.get(key)==val,'phase2 changed prior financial/evidence binding')
        replacements={w['original']['path']:latest.content.bindings[w['key']] for w in witnesses}
        def replace_records(value):
            if isinstance(value,dict):
                if set(value)=={'path','sha256'} and value['path'] in replacements:return replacements[value['path']]
                return {k:replace_records(x) for k,x in value.items()}
            if isinstance(value,list):return [replace_records(x) for x in value]
            return value
        expected=replace_records(expected)
        selected=replace(current,content=replace(current.content,bindings={**current.content.bindings,**{k:latest.content.bindings[k] for k in MUTABLE}}))
    else:
        for w in witnesses:E.checked_bytes(w['original'])
    results=checks(s,old_before,selected,v['last'])
    require(results==expected,'current owning evidence changed')
    require(v['evidence']==[item for result in v['results'] for item in result['evidence']],'retained evidence inventory changed')
    if before is not None:
        for key in ('raw_source','generation_ids'):
            require(before[key]==old_before[key],'original or generation changed since refresh')
    return v,current


def _mutation(before,v):
    after=copy.deepcopy(before)
    for result in v['results']:
        after['bindings'].update(result['bindings']);row=after['lane_state'][result['lane']]
        row['evidence_paths']=W.lanes.notes.evidence_list(result['evidence'])
        if result.get('attempt') is not None:
            require(result['attempt']>=row['attempt'],'semantic budget decreased')
            row['attempt']=result['attempt']
        # Phase2 overwrites phase1 later in the same complete-prefix projection.
        if result.get('phase'):row['phase']=result['phase']
        row['state']='running' if result.get('phase')=='phase1_complete' else 'passed'
        row['current_confidence']=result.get('confidence','high')
        if result.get('disposition') is not None:row['disposition']=result['disposition']
        row['confidence_history'].append(dict(level=row['current_confidence'],reason='current_independent_prefix_refresh',revision=before['revision']+1))
    levels=[(k,v['current_confidence']) for k,v in after['lane_state'].items() if v.get('current_confidence')]
    after['current_confidence']=dict(level=min((v for _,v in levels),key={'low':0,'medium':1,'high':2}.get),reason_codes=['current_independent_prefix_refresh'],contributors=[k for k,_ in levels],computed_at_revision=before['revision']+1)
    after['confidence_history'].append(copy.deepcopy(after['current_confidence']))
    return after


def commit(s,lease,binding,*,fault=lambda _:None):
    facade,before=W._active(s,lease);v,current=validate(s,binding,before)
    require(record(s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json'))['sha256']==v['before']['sha256'],'exact prepared tracker CAS changed')
    desired=_mutation(before,v);keys={k for k in desired['bindings'] if desired['bindings'].get(k)!=before['bindings'].get(k)}
    def mutate(after):
        facade.require_synchronized(s.bootstrap.workbook)
        validate(s,binding,before)
        for key in ('bindings','lane_state','current_confidence','confidence_history'):after[key]=copy.deepcopy(desired[key])
    def evidence(old,new,changed):
        require(old==before and new['bindings']==desired['bindings'] and new['lane_state']==desired['lane_state'],'unexpected refresh mutation')
        validate(s,binding,old);return True
    result=T.cas_mutate(s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json'),expected_revision=lease.revision,expected_sha256=lease.sha256,owner=lease.owner,token=lease.token,scope='workbook:'+s.bootstrap.workbook,mutate=mutate,allowed_fields={'bindings','lane_state','current_confidence','confidence_history'},allowed_lanes={r['lane'] for r in v['results']},binding_replacements={k:before['bindings'].get(k) for k in keys},evidence_validator=evidence,fault_hook=fault)
    receipt=keep(Path(binding['path']).parent/'committed.json',dict(prepared=binding,tracker_sha256=result['sha256'],revision=result['revision']))
    return replace(lease,revision=result['revision'],sha256=result['sha256']),current,receipt


def reconcile(s,binding):
    """After uncertain CAS: prove its exact single revision, never replay it."""
    v,current=validate(s,binding);before=read(v['before']);path=s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json');actual=read(record(path));desired=_mutation(before,v)
    desired['revision']=before['revision']+1;desired['updated_at']=actual['updated_at'];desired['leases']['task_agent']['heartbeat_at']=actual['leases']['task_agent']['heartbeat_at']
    require(actual==desired and actual['leases']['task_agent']['heartbeat_at']==actual['updated_at'] and actual['updated_at']>=before['updated_at'],'CAS not exactly committed; root reconciliation required')
    return keep(Path(binding['path']).parent/'committed.json',dict(prepared=binding,tracker_sha256=record(path)['sha256'],revision=actual['revision']))


def advance(s,lease,binding,directory,*,dispatch=False,credential=None,captures=None):
    _,before=W._active(s,lease);v,selection=validate(s,binding,before)
    committed=read(record(Path(binding['path']).parent/'committed.json'))
    require(committed['prepared']==binding,'wrong committed refresh')
    for result in v['results']:
        require(all(before['bindings'].get(k)==val for k,val in result['bindings'].items() if k not in {'normalized_final','normalization_report','normalization_exclusions'}),'tracker differs from refreshed prefix')
    # New checkpoints live only in this explicit continuation namespace.
    continuation=Path(binding['path']).parent/'continuation'
    continuation.mkdir(exist_ok=True,mode=0o700)
    start=v['last']
    phase2=continuation/'normalization_phase2.json'
    if phase2.exists():
        selection=decode(read(record(phase2))['selection']);start='normalization_phase2'
    for kind in KINDS[KINDS.index(start)+1:]:
        checkpoint=continuation/(kind+'.json')
        if checkpoint.exists():
            saved=read(record(checkpoint));selection=decode(saved['selection'])
            for item in saved['evidence']:E.checked_bytes(item)
            result=W._gate(s,lease,kind,selection)
            if result['lease']!=lease:return result
            continue
        if kind=='source_profile':
            outcome=W._profiles(s,selection.content,captures,dispatch=dispatch,credential=credential)
            if outcome is None:return W._status(s,'PENDING_CAPTURE',lease,step=kind,selection=selection,evidence=[record(s.run/'profile-worklist.json')])
            if outcome['status']!='COMPLETE':return W._status(s,'UNRESOLVED' if outcome['status']=='STOPPED' else 'PENDING_MODEL',lease,step=kind,selection=selection,evidence=outcome.get('evidence',()))
            selection=replace(selection,public_profiles=outcome['selection']);evidence=[outcome['receipt']]
        elif kind=='normalization_phase2':
            content,receipt=W.environment.finish_normalization(selection.content,selection.public_profiles,s.run/'phase2');selection=replace(selection,content=content);evidence=[receipt]
        else:
            require(not (s.run/'packaging').exists(),'interrupted packaging retained')
            content,receipt=W.environment.package(selection.content,selection.public_profiles,s.run/'packaging',s.taxonomy);selection=replace(selection,content=content);evidence=[receipt]
        keep(checkpoint,dict(selection=encode(selection),evidence=evidence))
        return W._gate(s,lease,kind,selection)
    result=W._gate(s,lease,'environment',selection)
    if result['status']=='STEP_COMPLETE':result['status']='ENVIRONMENT_READY'
    return result
