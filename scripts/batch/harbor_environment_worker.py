"""Root-owned serial workbook composition, ending at validated environment.

Production execution requires a root-provided live lease and outer workbook
resource guard. No lease acquisition, campaign admission, HTTP capture, runtime
container, instruction production or release publication is hidden here.
"""
from dataclasses import dataclass,asdict,replace
from pathlib import Path
import json
from scripts.batch import harbor_workbook_bootstrap as bootstrap
from scripts.batch import harbor_model_executor as executor
from scripts.batch import harbor_audit_bridge as audit_bridge
from scripts.batch import harbor_normalization_producer as normalization
from scripts.batch import harbor_profile_producer as profiles
from scripts.batch import harbor_environment_producer as environment
from scripts.batch import harbor_readiness as static
from scripts.batch import harbor_complete_readiness as complete
from scripts.batch import harbor_completed_lanes as lanes
from scripts.batch.harbor_lane_lifecycle import HarborLaneLifecycle
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_admission import record,read,require
from scripts.batch.harbor_transition_codec import encode,decode

@dataclass(frozen=True)
class Selection:
    bootstrap: bootstrap.Selection
    prepared: dict
    run: Path
    campaign: executor.Campaign
    taxonomy: dict
    normalization_budget: normalization.Budget
    inherited_audit_budget: dict | None = None


def keep(path,value):return bootstrap.keep(path,value)


def _context(s):return RootContext(s.bootstrap.policy.tracker_root,s.bootstrap.policy.trusted_public_key)


def _active(s,lease):
    require(isinstance(s,Selection) and isinstance(lease,bootstrap.Lease),'typed worker and existing root lease required')
    wb=s.bootstrap.workbook;facade=HarborLaneLifecycle(_context(s));facade.require_synchronized(wb)
    path=s.bootstrap.policy.tracker_root/(wb+'.json');raw=path.read_bytes();before=json.loads(raw)
    facade._active(before,raw,lease.revision,lease.sha256,lease.owner,lease.token,'workbook:'+wb)
    require(before['execution_state'] not in {'STOPPED','PROMOTED'},'root resume required for stopped/published tracker')
    return facade,before


def _setup(s,lease):
    facade,before=_active(s,lease);saved=bootstrap._prepared(s.bootstrap,s.prepared)
    run=executor.safe(s.run);b=s.bootstrap
    import xl_variable_source_audit as audit
    require(b.model_policy.project_id==audit.DEFAULT_PROJECT_ID,'current owning publication audit requires configured project identity')
    require(run!=b.run and run not in b.run.parents and b.run not in run.parents,'worker and bootstrap directories must be disjoint')
    for root in (b.policy.tracker_root,Path(b.source_manifest['path']).parent,Path(b.segmentation_manifest['path']).parent,s.campaign.root,s.campaign.slot_root):
        root=executor.safe(root);require(run!=root and run not in root.parents and root not in run.parents,'worker directory overlaps protected evidence')
    require(s.normalization_budget.ordinal==1 and s.normalization_budget.limit==2,'fresh admitted worker cannot reset or invent inherited normalization ordinals')
    require(s.prepared in s.normalization_budget.evidence,'original admitted evidence missing from normalization budget')
    for binding in (s.taxonomy,*s.normalization_budget.evidence):executor.checked_bytes(binding)
    descriptor=dict(schema_version='harbor-environment-worker/v1',selection=bootstrap.serial(asdict(s)),code=record(Path(__file__)))
    if not run.exists():
        require(before['lane_state']['harbor-normalization-engineer']['attempt']==0,'prior normalization budget needs explicit reconciliation')
        run.mkdir(parents=True,mode=0o700)
    keep(run/'selection.json',descriptor)
    return facade,before,saved


def _status(s,status,lease,*,step=None,selection=None,evidence=(),error=None):
    result=dict(status=status,step=step,lease=lease,selection=selection,evidence=list(evidence),publication_ready=False)
    if error is not None:
        diagnostic=dict(status='UNRESOLVED',step=step,error_type=type(error).__name__,error_sha256=executor.sha(str(error).encode()),automatic_retry=False,source_invalidity_claimed=False)
        name='unresolved-'+str(step)+'-'+diagnostic['error_sha256'][:16]+'.json';result['evidence'].append(keep(s.run/name,diagnostic))
    return result


def _lease(lease,result):return replace(lease,revision=result['revision'],sha256=result['sha256'])


def _content(s,bindings,normalizations):
    wb=s.bootstrap.workbook
    return static.Selection(wb,s.run/'stages'/(wb+'-outputs'),'',bindings,s.run/'normalization'/(wb+'-variable-sources'),s.run/'naturalization',{}, {},normalization_records=tuple(normalizations),model_policy=s.bootstrap.model_policy,trusted_public_key=s.bootstrap.policy.trusted_public_key)


def _selection(content,profiles_=None):return complete.ReadinessSelection(content,None,None,None,profiles_)


def _gate(s,lease,kind,selection):
    facade,before=_active(s,lease);packet=lanes.CompletionEvidence(kind,selection)
    checked=lanes.evaluate_completed_gate(before,packet,_context(s))
    args=dict(expected_revision=lease.revision,expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)
    if not checked['passed']:
        result=facade.record_failure(s.bootstrap.workbook,packet,**args)
        return _status(s,'OWNING_GATE_FAILED',_lease(lease,result),step=kind,selection=selection,evidence=checked['evidence'])
    lane=dict(lanes.STEPS)[kind];row=before['lane_state'][lane]
    already=(row.get('phase') in {'phase1_complete','phase2_complete'} if kind=='normalization_phase1' else row['state']=='passed')
    if already:
        require(all(before['bindings'].get(k)==v for k,v in checked['bindings'].items()),'completed lane bindings changed; root invalidation required')
        result_lease=lease
    else:result_lease=_lease(lease,facade.complete(s.bootstrap.workbook,packet,**args))
    return _status(s,'STEP_COMPLETE',result_lease,step=kind,selection=selection,evidence=checked['evidence'])


def _checkpoint(s,kind,selection,evidence):
    return keep(s.run/(kind+'.json'),dict(selection=encode(selection),evidence=list(evidence)))


def _read_checkpoint(s,kind):
    binding=record(s.run/(kind+'.json'));saved=read(binding)
    for item in saved['evidence']:executor.checked_bytes(item)
    return decode(saved['selection'])


def _audit(s,lease,baseline,*,dispatch,credential):
    b=s.bootstrap;path=s.run/'audit-plan.json';root=s.run/'audit-bridge'
    if not path.exists():
        plan=bootstrap.audit_plan(b,s.prepared,baseline,lease,inherited_budget=s.inherited_audit_budget)
        keep(path,plan)
    binding=record(path);plan=read(binding)
    if not root.exists():
        owning_plan={**plan,'batch_consumed':{int(k):v for k,v in plan['batch_consumed'].items()}}
        bootstrap.revalidate_audit_plan(b,s.prepared,baseline,lease,owning_plan,inherited_budget=s.inherited_audit_budget)
    if not dispatch and not root.exists():return None
    # Resume only completed owning audit + complete strict call provenance. The
    # bridge refuses every partial/unknown state before reentering the auditor.
    return audit_bridge.run(binding,root=root,campaign=s.campaign,policy=b.model_policy,credential=credential)


def _normalization(s,content,*,dispatch,credential):
    ns=normalization.Selection(content.workbook,content.bindings,content.normalization_run,content.model_policy,s.normalization_budget)
    prepared=normalization.prepare(ns);call=None
    if read(prepared)['request'] is not None:
        pc=normalization.prepared_call(ns,prepared)
        if not dispatch and not executor.location(pc,s.campaign).exists():return None
        observed=normalization.dispatch(ns,prepared,s.campaign,credential=credential) if dispatch else executor.inspect(pc,s.campaign)
        if observed.status!='completed':raise audit_bridge.AuditTransportStop('normalization transport '+observed.status)
        call=observed.call
    result=normalization.emit_phase1(ns,prepared,call);value=read(result);run=content.normalization_run
    bindings={**content.bindings,'normalization_draft':record(run/'draft.json'),'normalizer':record(run/f'normalize_{content.workbook}.py'),'normalization_prepared_manifest':prepared,'normalized_phase1':value['phase1'],'normalized_final':value['outputs']['normalized.json'],'normalization_report':value['outputs']['normalization_report.json'],'normalization_exclusions':value['outputs']['exclusions.json']}
    if read(prepared)['request'] is not None:bindings['normalization_prepared_request']=read(prepared)['request']
    return replace(content,bindings=bindings,normalization_prepared=prepared,normalization_call=call),result


def _profiles(s,content,captures,*,dispatch,credential):
    root=s.run/'profiles';input_=s.run/'profile-input.json'
    if not input_.exists():
        validator,_=profiles.profiles.modules(profiles.ROOT)
        forbidden=validator.forbidden_values([Path(content.bindings['normalized_phase1']['path']),Path(content.bindings['variable_source_inventory']['path'])])
        worklist=profiles.profiles.worklist(read(content.bindings['normalized_phase1']),forbidden,root=profiles.ROOT)
        keep(s.run/'profile-worklist.json',worklist)
        if captures is None:
            if worklist:return None
            captures=keep(s.run/'empty-captures.json',dict(schema_version='harbor-profile-captures/v1',sources=[]))
        keep(input_,dict(schema_version='harbor-profile-production-input/v1',workbook_id=content.workbook,original_source=content.bindings['original_raw_source'],phase1=content.bindings['normalized_phase1'],inventory=content.bindings['variable_source_inventory'],captures=captures,policy=asdict(content.model_policy),campaign=bootstrap.serial(asdict(s.campaign))))
    elif captures is not None:require(read(record(input_))['captures']==captures,'root capture bindings changed after preparation')
    plan=profiles.prepare(record(input_),root) if not root.exists() else record(root/'plan.json')
    return profiles.run(plan,dispatch=dispatch,credential=credential)


def advance(s,lease,*,dispatch=False,credential=None,captures=None):
    """Execute one fixed stage; caller persists returned lease before next call.

    `dispatch=False` prepares/inspects model work without network. It may perform
    root-authorized local baseline/normalization/packaging workbook work. This
    call never substitutes a fresh run/scope for interrupted work.
    """
    step='bootstrap'
    try:
        _,before,saved=_setup(s,lease);b=s.bootstrap;wb=b.workbook
        for kind,lane in lanes.STEPS[:2]:
            if before['lane_state'][lane]['state']!='passed':
                result=bootstrap.complete_lane(b,s.prepared,kind,lease)
                return _status(s,'STEP_COMPLETE',_lease(lease,result),step=kind)
        step='baseline';baseline_path=b.run/'baseline/complete.json'
        if not baseline_path.exists():
            if (b.run/'baseline').exists():raise RuntimeError('interrupted baseline retained; root reconciliation required')
            _,baseline=bootstrap.run_baseline(b,s.prepared,lease)
        else:baseline=record(baseline_path)
        # audit_plan performs owning baseline sidecar/current code validation.
        content=_content(s,read(baseline)['bindings'],saved['normalization_records'])
        selection=_selection(content)
        for kind in ('research','normalization_phase1','source_profile','normalization_phase2','environment'):
            step=kind;checkpoint=s.run/(kind+'.json')
            if checkpoint.exists():
                selection=_read_checkpoint(s,kind);content=selection.content
                if kind=='research':
                    proof=audit_bridge.validate_completed(s.run/'audit-bridge',b.run/'audit'/(wb+'-variable-sources'),record(s.run/'audit-plan.json'),b.model_policy,s.campaign)
                    require(all(content.bindings[k]==v for k,v in proof['bindings'].items()),'retained research context differs from actual calls')
                # Once downstream emitted mutable normalized outputs, rechecking
                # phase1 as a final phase would be wrong. Each next owning gate
                # revalidates its source, audit and exact phase projection.
                lane=dict(lanes.STEPS)[kind];row=before['lane_state'][lane]
                done=row.get('phase') in {'phase1_complete','phase2_complete'} if kind=='normalization_phase1' else row['state']=='passed'
                if not done:return _gate(s,lease,kind,selection)
                continue
            if kind=='research':
                audited=_audit(s,lease,baseline,dispatch=dispatch,credential=credential)
                if audited is None:return _status(s,'PENDING_MODEL',lease,step=kind,evidence=[record(s.run/'audit-plan.json')])
                content=replace(content,bindings={**content.bindings,**audited['bindings']});selection=_selection(content);evidence=audited['calls']
            elif kind=='normalization_phase1':
                emitted=_normalization(s,content,dispatch=dispatch,credential=credential)
                if emitted is None:return _status(s,'PENDING_MODEL',lease,step=kind)
                content,receipt=emitted;selection=_selection(content);evidence=[receipt]
            elif kind=='source_profile':
                outcome=_profiles(s,content,captures,dispatch=dispatch,credential=credential)
                if outcome is None:return _status(s,'PENDING_CAPTURE',lease,step=kind,evidence=[record(s.run/'profile-worklist.json')])
                if outcome['status']!='COMPLETE':return _status(s,'UNRESOLVED' if outcome['status']=='STOPPED' else 'PENDING_MODEL',lease,step=kind,evidence=outcome.get('evidence',()))
                selection=_selection(content,outcome['selection']);evidence=[outcome['receipt']]
            elif kind=='normalization_phase2':
                content,receipt=environment.finish_normalization(content,selection.public_profiles,s.run/'phase2');selection=replace(selection,content=content);evidence=[receipt]
            else:
                if (s.run/'packaging').exists():raise RuntimeError('interrupted packaging retained; root reconciliation required')
                content,receipt=environment.package(content,selection.public_profiles,s.run/'packaging',s.taxonomy);selection=replace(selection,content=content);evidence=[receipt]
            _checkpoint(s,kind,selection,evidence)
            return _gate(s,lease,kind,selection)
        # A resume handoff is independently revalidated, not a checkpoint flag.
        result=_gate(s,lease,'environment',selection)
        if result['status']=='STEP_COMPLETE':result['status']='ENVIRONMENT_READY'
        return result
    except (Exception,SystemExit) as exc:
        if not s.run.is_dir():raise
        return _status(s,'UNRESOLVED',lease,step=step,error=exc)


def run_to_environment(s,lease,*,dispatch=False,credential=None,captures=None):
    """Root runs this inside its one-workbook resource/parent-death guard."""
    for _ in range(10):
        result=advance(s,lease,dispatch=dispatch,credential=credential,captures=captures)
        lease=result['lease']
        if result['status']!='STEP_COMPLETE':return result
    raise RuntimeError('fixed worker stage bound exceeded')
