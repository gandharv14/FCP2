"""Root-selected new-task bootstrap; no inferred approval or model dispatch.

Preparation validates existing immutable source/segmentation evidence. Admission
and lane commits use fixed installed facades. Baseline execution is an explicit
root-owned workbook operation; audit dispatch remains separate and budget-bound.
"""
from dataclasses import dataclass, asdict, replace
from pathlib import Path
import json
import os
import subprocess
import sys

from scripts.batch import harbor_admission as admission
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_model_executor as journal
from scripts.batch.harbor_lane_notes import GateEvidence, RootContext, LaneNotes
from scripts.batch.harbor_admission import record, read, require
from scripts.batch.harbor_model_review_provenance import Policy
from xl_source_publication import validate_source_generation
from xl_seg.publication import resolve_generation_by_id, validate_inputs_sidecar, inputs_sidecar_path
from xl_release_publication import resolve_build_context
from xl_preflight import classify

ROOT=Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class LedgerRefresh:
    previous: dict  # Preserved bytes matching the frozen manifest's ledger hash.
    current: dict   # Explicit root-selected refreshed ledger, never discovered latest.

@dataclass(frozen=True)
class Selection:
    candidate_manifest: dict
    policy: admission.AdmissionPolicy
    workbook: str
    source_manifest: dict
    segmentation_manifest: dict
    run: Path
    model_policy: Policy
    refresh: LedgerRefresh | None = None

@dataclass(frozen=True)
class Lease:
    owner: str
    token: str
    revision: int
    sha256: str


def serial(value):
    if isinstance(value,Path):return str(value)
    if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [serial(v) for v in value]
    return value


def keep(path,value):
    raw=value if isinstance(value,bytes) else journal.canonical(value)
    if path.exists():require(path.read_bytes()==raw,'bootstrap checkpoint differs')
    else:journal.retain(path,raw)
    return record(path)


def _selection(selection):
    require(isinstance(selection,Selection) and isinstance(selection.policy,admission.AdmissionPolicy) and isinstance(selection.model_policy,Policy),'typed root-selected bootstrap required')
    s=selection;p=s.policy
    manifest=read(s.candidate_manifest)
    derived=admission.policy_from_candidate_manifest(s.candidate_manifest,batch=p.batch,tracker_root=p.tracker_root,original_inventory=p.original_inventory,dependency=p.dependency,dependencies=p.dependencies,history_roots=p.history_roots,expected_original_count=p.expected_original_count)
    require(p.mode=='new' and p.requested_ids==derived.requested_ids,'bootstrap cohort/policy differs from frozen manifest')
    require(all(item in p.campaign_evidence for item in derived.campaign_evidence),'candidate evidence missing from admission policy')
    rows=[row for row in manifest['selected_candidates'] if row['original']['workbook_id']==s.workbook]
    require(len(rows)==1,'workbook outside frozen selected cohort');row=rows[0]
    original=row['original'];require(record(original['path'])['sha256']==original['sha256'],'frozen original changed')
    old={'path':row['ledger']['path'],'sha256':row['ledger']['sha256']}
    if s.refresh:
        require(isinstance(s.refresh,LedgerRefresh),'typed explicit ledger refresh required')
        require(s.refresh.previous['sha256']==old['sha256'],'refresh previous ledger differs from frozen selection')
        previous=read(s.refresh.previous);ledger=read(s.refresh.current);binding=s.refresh.current
        require(previous['source']==original and previous['workbook_id']==s.workbook,'historical ledger original identity changed')
    else:ledger=read(old);binding=old
    require(ledger['workbook_id']==s.workbook and ledger['source']==original,'current ledger original identity changed')
    require(ledger.get('schema_version')=='corpus-workbook/v1','unsupported corpus ledger')
    build=ledger['build'];require(build.get('state')=='VERIFIED','current reconstruction did not finish')
    # This state is only a prerequisite. Actual owning proof validation follows.
    packets=[]
    for kind,bound in [('source',s.source_manifest),('segmentation',s.segmentation_manifest)]:
        value=read(bound);directory=Path(bound['path']).parent
        require(Path(bound['path']).name=='generation-manifest.json' and directory.parent.name=='generations' and directory.parent.parent.name==s.workbook,'generation path differs from canonical workbook')
        require(directory.name==value.get('generation_id')==build[kind+'_generation_id'],'selected generation differs from exact ledger')
        require(bound['sha256']==build[kind+'_manifest_sha256'],'selected generation hash differs from ledger')
        packets.append(GateEvidence(kind,bound))
    cur=read(s.segmentation_manifest)
    require(cur.get('curated_output_cells')==row['retained_targets'] and bool(row['retained_targets']),'ordered curated target identities changed')
    lifecycle.checked_record(row['retained_curation'])
    current=record(Path(s.segmentation_manifest['path']).parent/'curation.toml')
    generated=row.get('generated_curation')
    if generated is not None:
        from scripts.batch import harbor_generated_curation as adoption
        require(s.refresh is None and generated in p.campaign_evidence,'generated adoption must retain original ledger authority')
        proof=adoption.validate(generated,workbook=s.workbook,original=record(original['path']),history_roots=p.history_roots,known_ids={x['workbook_id'] for x in admission.read_inventory(p.original_inventory['path'])['sources']})
        require(proof['ledger']==binding and proof['source_manifest']==s.source_manifest and proof['segmentation_manifest']==s.segmentation_manifest and proof['curation']==current==row['retained_curation'],'generated curation selected bindings differ')
    else:
        from scripts.batch import harbor_legacy_research_history as legacy
        legacy_proof=legacy.selected(p,s.workbook)
        if legacy_proof is not None:
            require(s.refresh is None and current==row['retained_curation'],'legacy source selection differs')
            legacy.source_handoff(legacy_proof,dict(workbook=s.workbook,ledger=binding,source_manifest=s.source_manifest,segmentation_manifest=s.segmentation_manifest))
        else:
            require(current['sha256']==row['retained_curation']['sha256']==build['bindings']['curation_sha256'],'retained curation bytes changed')
    return row,ledger,binding,packets,current


def validate(selection):
    """Read-only current source/lineage/AST/segmentation validation, no lane PASS."""
    row,ledger,ledger_binding,packets,curation=_selection(selection);s=selection
    directory=Path(s.source_manifest['path']).parent
    manifest=validate_source_generation(directory,expected_generation_id=directory.name)
    require(manifest['layout']['workbook_id']==s.workbook,'source manifest workbook differs')
    source=record(directory/manifest['layout']['source_workbook'])
    from xl_source_health import inspect_workbook
    health=inspect_workbook(source['path']);require(health==read(record(directory/'health.json')) and health['route'] in {'pass','restricted_pass','restricted_recalc_pass'},'current source health differs or is unsupported')
    normalization=ledger['build'].get('normalization')
    if normalization is not None:
        require(isinstance(normalization,dict),'unsupported ledger normalization shape')
        require(normalization.get('original_sha256')==row['original']['sha256'],'normalization original differs')
        from scripts.corpus_lineage import validate_normalization
        validate_normalization(row['original']['path'],normalization['candidate'],normalization)
    # Extracted normalization records retain the hash-bound ledger as provenance;
    # the owning source validator replays all transformations and signed receipts.
    records=[] if normalization is None else [normalization]
    from scripts.batch.harbor_source_evidence import validate_source_lineage
    lineage=validate_source_lineage(row['original']['path'],directory,records,trusted_public_key=s.policy.trusted_public_key)
    from xl_source_publication import validate_ast_provenance,create_ast_provenance
    ast=directory/manifest['layout']['ast_directory'];args=dict(production=True,workbook_id=s.workbook,max_range_expand=100,include_constants=True,include_cached_values=True)
    old=validate_ast_provenance(source['path'],ast,directory/'ast-provenance.json',builder_args=args);current=create_ast_provenance(source['path'],ast,builder_args=args)
    projection=lambda x:[{k:r[k] for k in ('name','sha256','size_bytes')} for r in x['builder']['code']]
    require(projection(old)==projection(current),'current source builder changed')
    seg=Path(s.segmentation_manifest['path']).parent
    _,segmanifest=resolve_generation_by_id(seg.parent.parent,seg.name,source_path=source['path'],ast_dir=ast,source_generation_dir=directory,require_pass=True,validate_live_evidence=True)
    verification=read(record(seg/'segments.json'))['verification']
    require(classify(verification,True)[0]=='healthy','current reconstruction is not healthy')
    pair=resolve_build_context(s.workbook,source_root=directory.parent.parent,source_generation_id=directory.name,segmentation_root=seg.parent.parent,segmentation_generation_id=seg.name)
    original={'path':row['original']['path'],'sha256':row['original']['sha256']}
    bindings=dict(original_raw_source=original,source_file=source,source_generation=dict(path=str(directory),id=directory.name,manifest_sha256=s.source_manifest['sha256']),segmentation_generation=dict(path=str(seg),id=seg.name,manifest_sha256=s.segmentation_manifest['sha256']),ast_workbook_root={'path':str(ast)},curation=curation)
    return dict(workbook=s.workbook,ledger=ledger_binding,original=original,normalization=normalization,lineage=lineage,bindings=bindings,pipeline_bindings=pair['bindings'],targets=segmanifest['curated_output_cells'])


def prepare(selection):
    """Retain immutable bootstrap recipe without creating any tracker."""
    s=selection;result=validate(s);run=journal.safe(s.run)
    require(not run.exists(),'fresh bootstrap directory required; preserve prior attempts')
    protected=[Path(s.source_manifest['path']).parent,Path(s.segmentation_manifest['path']).parent,Path(result['original']['path']).parent,s.policy.tracker_root]
    require(all(run!=p and run not in p.parents and p not in run.parents for p in protected),'bootstrap journal must be disjoint from original/generation/tracker trees')
    dispatcher=admission.DispatcherAdmission(s.policy)
    # Source normalization must be retained as an actual ledger-derived record
    # before admission's independently validated lineage receives it.
    run.mkdir(parents=True,mode=0o700)
    keep(run/'ledger.snapshot.json',Path(result['ledger']['path']).read_bytes())
    normalized=() if result['normalization'] is None else (keep(run/'source-normalization.json',result['normalization']),)
    request,history=dispatcher._check(s.workbook,raw=result['bindings']['source_file'],generation=s.source_manifest,normalization=normalized)
    descriptor=dict(schema_version='harbor-workbook-bootstrap/v1',selection=asdict(s),code=record(Path(__file__)),validated=result,normalization_records=list(normalized),admission_request=request,history=history,model_calls=0,lane_approval=False)
    return keep(run/'prepared.json',serial(descriptor))


def _prepared(selection,bound):
    require(Path(bound['path'])==selection.run/'prepared.json','foreign bootstrap recipe')
    saved=read(bound);require(saved['code']==record(Path(__file__)),'bootstrap producer changed')
    require(saved['selection']==serial(asdict(selection)),'root-selected bootstrap configuration changed')
    fresh=validate(selection);require(saved['validated']==fresh,'bootstrap source/ledger proof changed')
    require(read(record(selection.run/'ledger.snapshot.json'))==read(fresh['ledger']),'preserved ledger differs')
    for binding in saved['normalization_records']:lifecycle.checked_record(binding)
    require([read(r) for r in saved['normalization_records']]==([] if fresh['normalization'] is None else [fresh['normalization']]),'normalization record differs from selected ledger')
    return saved


def admit(selection,prepared):
    saved=_prepared(selection,prepared)
    dispatcher=admission.DispatcherAdmission(selection.policy)
    request,history=dispatcher._check(selection.workbook,raw=saved['validated']['bindings']['source_file'],generation=selection.source_manifest,normalization=tuple(saved['normalization_records']))
    require(request==saved['admission_request'] and history==saved['history'],'admission evidence changed after preparation')
    bound_policy=replace(selection.policy,campaign_evidence=(*selection.policy.campaign_evidence,prepared))
    result=admission.DispatcherAdmission(bound_policy).create(selection.workbook,raw=saved['validated']['bindings']['source_file'],generation=selection.source_manifest,normalization=tuple(saved['normalization_records']))
    keep(selection.run/'admission.json',result)
    return result


def complete_lane(selection,prepared,kind,lease):
    """Root supplies an existing live lease/CAS; fixed owning facade mutates it."""
    require(isinstance(lease,Lease) and kind in {'source','segmentation'},'fixed source/segmentation completion required')
    saved=_prepared(selection,prepared)
    packet=GateEvidence(kind,selection.source_manifest if kind=='source' else selection.segmentation_manifest,tuple(saved['normalization_records']))
    facade=LaneNotes(RootContext(selection.policy.tracker_root,selection.policy.trusted_public_key))
    return facade.complete(selection.workbook,packet,expected_revision=lease.revision,expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)


def baseline_arguments(selection,output):
    s=selection;b=validate(s)['bindings'];source=Path(b['source_generation']['path']);seg=Path(b['segmentation_generation']['path'])
    return [str(ROOT/'xl_input_mask.py'),s.workbook,'--source',str(Path(b['source_file']['path']).parent),'--segmentation-mode','strict','--source-generation-root',str(source.parents[2]),'--source-generation-id',source.name,'--seg-dir',str(seg.parents[2]),'--segmentation-generation-id',seg.name,'--keep','headers','-o',str(output)]


def _tracker(selection,lease):
    require(isinstance(lease,Lease),'root-selected existing lease/CAS required')
    facade=LaneNotes(RootContext(selection.policy.tracker_root,selection.policy.trusted_public_key));facade.require_synchronized(selection.workbook)
    path=selection.policy.tracker_root/(selection.workbook+'.json');raw=path.read_bytes();before=json.loads(raw)
    facade._active(before,raw,lease.revision,lease.sha256,lease.owner,lease.token,'workbook:'+selection.workbook)
    require(all(before['lane_state'][lane]['state']=='passed' for lane in ('harbor-source-warden','harbor-segmentation-analyst')),'source/segmentation lanes must complete first')
    require(before['generation_ids']['source']==Path(selection.source_manifest['path']).parent.name and before['generation_ids']['segmentation']==Path(selection.segmentation_manifest['path']).parent.name,'tracker generation pair differs')
    return before


def run_baseline(selection,prepared,lease):
    """Explicit root-coordinated workbook operation; no model/container calls."""
    _tracker(selection,lease);saved=_prepared(selection,prepared);s=selection;work=s.run/'baseline'
    require(not work.exists(),'baseline already attempted; preserve partial evidence and choose fresh bootstrap')
    work.mkdir();output=work/'inputs';argv=baseline_arguments(s,output)
    require(sys.version_info[:2]==(3,12),'pipeline requires Python 3.12')
    keep(work/'intent.json',dict(argv=[sys.executable,*argv],entry=record(ROOT/'xl_input_mask.py'),prepared=prepared))
    with (work/'command.log').open('xb') as log:
        result=subprocess.run([sys.executable,*argv],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=900,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        log.flush();os.fsync(log.fileno())
    keep(work/'result.json',dict(returncode=result.returncode,log=record(work/'command.log')))
    require(result.returncode==0,'baseline command failed; partial directory retained')
    _prepared(s,prepared);_tracker(s,lease)
    source=Path(saved['validated']['bindings']['source_file']['path']);artifact=output/(s.workbook+'-inputs'+source.suffix)
    validate_inputs_sidecar(artifact,expected_generation_id=Path(s.segmentation_manifest['path']).parent.name,generation_dir=Path(s.segmentation_manifest['path']).parent,expected_pipeline_bindings=saved['validated']['pipeline_bindings'])
    bindings={**saved['validated']['bindings'],'baseline_inputs':record(artifact),'baseline_segmentation':record(inputs_sidecar_path(artifact))}
    receipt=keep(work/'complete.json',dict(prepared=prepared,bindings=bindings,command_intent=record(work/'intent.json'),command_result=record(work/'result.json'),model_calls=0,lane_approval=False))
    return bindings,receipt


def audit_plan(selection,prepared,baseline_receipt,lease,*,inherited_budget=None,env_file=Path('.env')):
    """Recheck exact prior audit consumption and current batches before dispatch."""
    s=selection;_tracker(s,lease);saved=_prepared(s,prepared);receipt=read(baseline_receipt)
    require(Path(baseline_receipt['path'])==s.run/'baseline/complete.json' and receipt['prepared']==prepared,'foreign baseline result')
    intent=read(receipt['command_intent']);result=read(receipt['command_result'])
    require(Path(receipt['command_intent']['path'])==s.run/'baseline/intent.json' and Path(receipt['command_result']['path'])==s.run/'baseline/result.json','baseline command evidence escaped attempt')
    require(intent==dict(argv=[sys.executable,*baseline_arguments(s,s.run/'baseline/inputs')],entry=record(ROOT/'xl_input_mask.py'),prepared=prepared),'baseline command or current mask producer changed')
    require(result['returncode']==0 and Path(result['log']['path'])==s.run/'baseline/command.log','baseline command did not finish')
    lifecycle.checked_record(result['log'])
    bindings=receipt['bindings'];require({k:bindings[k] for k in saved['validated']['bindings']}==saved['validated']['bindings'],'baseline source context changed')
    for key in ('baseline_inputs','baseline_segmentation'):lifecycle.checked_record(bindings[key])
    source=Path(s.source_manifest['path']).parent;seg=Path(s.segmentation_manifest['path']).parent
    validate_inputs_sidecar(bindings['baseline_inputs']['path'],expected_generation_id=seg.name,generation_dir=seg,expected_pipeline_bindings=saved['validated']['pipeline_bindings'])
    dispatcher=admission.DispatcherAdmission(s.policy);excluded,histories=admission.survey_namespaces(s.policy.history_roots,set(dispatcher.sources))
    require(s.workbook not in excluded,'new prior task evidence appeared before audit dispatch')
    own=str(s.policy.tracker_root/(s.workbook+'.json'))
    history=[r for r in histories.get(s.workbook,[]) if r['path']!=own]
    require(history==saved['history'],'audit/history evidence changed; reconcile before model dispatch')
    from scripts.batch import harbor_legacy_research_history as legacy
    legacy_proof=legacy.selected(s.policy,s.workbook)
    if legacy_proof is None:admission.validate_prior_audit_budget(history,inherited_budget,s.workbook)
    import xl_variable_source_audit as audit
    require(s.model_policy.endpoint==audit.PROD_ENDPOINT.rstrip('/')+'/chat/completions','auditor endpoint differs from configured model policy')
    segment,meta=audit.resolve_segmentation_directory(seg.parents[2],s.workbook,segmentation_generation_id=seg.name,source_generation_root=source.parents[2],source_generation_id=source.name)
    inventory=audit.build_inventory(s.workbook,bindings['baseline_inputs']['path'],segment,segmentation=meta)
    legacy_budget=legacy.budget(legacy_proof,history,inventory,workbook=s.workbook,inherited_budget=inherited_budget)if legacy_proof else None
    counts=audit._inherited_batch_counts(inherited_budget,inventory)
    require(all(n<2 for n in counts.values()),'prior audit batch budget exhausted; completed-audit applicability or explicit reconciliation required')
    audit_root=s.run/'audit';target=audit_root/(s.workbook+'-variable-sources')
    require(not target.exists(),'audit output already attempted; use owning retained checkpoint, never fresh scope')
    argv=[str(ROOT/'xl_variable_source_audit.py'),s.workbook,'--inputs-root',str(Path(bindings['baseline_inputs']['path']).parent),'--seg-root',str(seg.parents[2]),'--segmentation-generation-id',seg.name,'--source-generation-root',str(source.parents[2]),'--source-generation-id',source.name,'--audit-root',str(audit_root),'--env-file',str(env_file),'--model',s.model_policy.model,'--endpoint',audit.PROD_ENDPOINT,'--project-id',s.model_policy.project_id,'--semantic-attempt-limit','2']
    if inherited_budget:argv+=['--inherited-budget',inherited_budget['path'],'--inherited-budget-sha256',inherited_budget['sha256']]
    return dict(schema_version='harbor-bootstrap-audit-plan/v1',argv=[sys.executable,*argv],entry=record(ROOT/'xl_variable_source_audit.py'),prepared=prepared,baseline=baseline_receipt,history=history,inherited_budget=inherited_budget,batch_consumed=counts,inventory_sha256=journal.sha(journal.canonical(inventory)),actual_model_calls=0,requires_root_model_slot=True,lane_approval=False,**({'legacy_research':legacy_budget}if legacy_budget else {}))


def revalidate_audit_plan(selection,prepared,baseline_receipt,lease,plan,*,inherited_budget=None,env_file=Path('.env')):
    """Root calls immediately before its separately guarded model dispatch."""
    current=audit_plan(selection,prepared,baseline_receipt,lease,inherited_budget=inherited_budget,env_file=env_file)
    require(plan==current,'audit plan changed or is stale; do not dispatch')
    return current
