"""Explicit source-only descriptor migration; no admission or semantic retry.

The existing source-passed tracker remains the owning tracker. Fresh descriptor
paths bind the unchanged source proof and original budget identity. A central
fenced worker and the owning expired-lease takeover must surround commit.
"""
from dataclasses import asdict, replace
import ast
from pathlib import Path
from scripts import harbor_campaign as C
from scripts.batch.harbor_admission import require, read, record

SCHEMA='harbor-source-only-continuation/v1'
SOURCE='harbor-source-warden'


def same_worker_source_boundary(previous,current):
    """Only reviewed prospective normalization routing may differ."""
    allowed=ast.dump(ast.parse('from scripts.batch import harbor_normalization_routing as normalization_routing').body[0],include_attributes=False)
    def project(raw):
        tree=ast.parse(raw)
        tree.body=[n for n in tree.body if not (isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='_normalization') and ast.dump(n,include_attributes=False)!=allowed]
        return ast.dump(tree,include_attributes=False)
    require(project(previous)==project(current),'source/worker boundary changed beyond prospective normalization routing')


def pristine(home, tracker, baseline=None):
    if baseline is not None:
        from scripts.batch import harbor_baseline_continuation as B
        B.pristine_bound(home,tracker,baseline)
        return
    home=Path(home)
    require(tracker['lane_state'][SOURCE]['state']=='passed','source must already have passed')
    for lane,value in tracker['lane_state'].items():
        if lane!=SOURCE:
            require(value['state']=='pending' and value['attempt']==0 and not value['confidence_history'] and value['current_confidence'] is None,'downstream lane consumed; source-only migration forbidden')
    require({p.name for p in (home/'environment').iterdir()}=={'selection.json'},'downstream environment artifact exists')
    require({p.name for p in (home/'bootstrap').iterdir()} <= {'admission.json','ledger.snapshot.json','prepared.json','source-normalization.json'},'baseline/audit attempt already exists')
    require(not (home/'model-campaign').exists() and not (home/'instruction').exists(),'later model/instruction namespace exists')


def inspect(config_binding, descriptor_binding, checkpoint_binding, tracker_binding, baseline=None,takeover=None):
    config=read(config_binding);home=Path(config['run_root'])
    require(config_binding==record(home/'config.json'),'noncanonical predecessor config')
    if takeover is None:tracker=read(tracker_binding)
    else:
        require(baseline is not None,'completed takeover inspection requires explicit baseline authority')
        from scripts.batch import harbor_lease_takeover as T
        tracker,after,_=T.validate_completion(takeover)
        stop=read(read(takeover)['stopped_owner_proof'])
        require(stop['selection']['tracker']==tracker_binding and stop['selection']['checkpoint']==checkpoint_binding
                and stop['before']['sha256']==tracker_binding['sha256'],'baseline takeover predecessor differs')
    pristine(home,tracker,baseline)
    descriptor=read(descriptor_binding);prior=read(checkpoint_binding)
    if baseline is None:
        require(prior['descriptor']==descriptor_binding and prior['status']=='STEP_COMPLETE' and prior['step']=='source' and C.content_codec.decode(prior['selection']) is None,'source-only owning checkpoint required')
    else:
        from scripts.batch import harbor_baseline_continuation as B
        proof=read(baseline)
        B.validate(baseline,config=config_binding,descriptor=descriptor_binding,checkpoint=checkpoint_binding,
                   tracker=tracker_binding,new_code=proof['new_code'])
        require(C.content_codec.decode(prior['selection']) is None,'failed baseline cannot import downstream content')
    old=C.decode(descriptor['worker']);lease=C.decode(prior['lease'])
    require(old.run==home/'environment' and old.bootstrap.run==home/'bootstrap' and old.bootstrap.policy.tracker_root==home/'trackers/workbooks','foreign predecessor layout')
    tracker_path=home/'trackers/workbooks'/(old.bootstrap.workbook+'.json')
    require(tracker_binding['path']==str(tracker_path) and tracker_binding['sha256']==lease.sha256 and tracker['revision']==lease.revision
            and (takeover is not None or tracker_binding==record(tracker_path)),
            'checkpoint and tracker must be exact same source boundary')
    require(old.normalization_budget.ordinal==1 and old.normalization_budget.limit==2 and old.normalization_budget.evidence==(old.prepared,) and old.inherited_audit_budget is None,'unexpected inherited semantic budget')
    old_prepared=read(old.prepared);require(old_prepared['selection']==C.bootstrap.serial(asdict(old.bootstrap)),'original bootstrap recipe changed')
    old_code=read(config['code']);root=Path(old_code['root'])
    from scripts import corpus
    inventory=corpus._frozen_code_files(root)
    require(inventory is not None and set(old_code)=={'root','files'} and len(old_code['files'])==len(inventory)+1 and {x['relative']:x['sha256'] for x in old_code['files']}=={**inventory,'.pipeline-code.json':record(root/'.pipeline-code.json')['sha256']},'immutable predecessor snapshot changed')
    require(descriptor['code']==record(root/'scripts/harbor_campaign.py'),'predecessor coordinator identity changed')
    require(record(root/'scripts/batch/harbor_workbook_bootstrap.py')['sha256']==record(C.bootstrap.__file__)['sha256'],'source/bootstrap semantics changed; new review required')
    same_worker_source_boundary((root/'scripts/batch/harbor_environment_worker.py').read_text(),Path(C.worker.__file__).read_text())
    require(old_prepared['code']==record(root/'scripts/batch/harbor_workbook_bootstrap.py'),'predecessor bootstrap producer differs')
    selection=read(record(old.run/'selection.json'))
    require(selection=={'schema_version':'harbor-environment-worker/v1','selection':C.bootstrap.serial(asdict(old)),'code':record(root/'scripts/batch/harbor_environment_worker.py')},'predecessor worker recipe differs')
    validated=C.bootstrap.validate(old.bootstrap)
    require(validated==old_prepared['validated'],'current source/bootstrap semantics differ')
    require(tracker['bindings']['source_generation']==validated['bindings']['source_generation'] and tracker['bindings']['original_raw_source']==validated['original'],'source-passed tracker differs from validated source')
    for evidence in prior['evidence']:C.executor.checked_bytes(evidence)
    return config,old,old_prepared,validated


def layout(config,home,worker_kind):
    """Takeover adapter limited to this typed migration's retained tracker."""
    b=config['source_continuation'];v=read(b)
    fields={'schema_version','implementation','predecessor_config','predecessor_descriptor','predecessor_checkpoint','predecessor_tracker','predecessor_prepared','takeover','destination','source','validated','old_budget','new_budget','new_prepared','new_worker','code','semantic_budget_reset','model_calls','new_admission'}
    from scripts.batch import harbor_baseline_continuation as B
    baseline=v.get('schema_version')==B.SCHEMA
    require(set(v)==(fields|{'baseline_failure','retained_lease'} if baseline else fields) and
            v['schema_version'] in (SCHEMA,B.SCHEMA) and v['semantic_budget_reset'] is False and
            v['new_admission'] is False and v['model_calls']==0,'unsupported source-only continuation')
    require(v['implementation']['sha256']==record(__file__)['sha256'],'continuation implementation differs')
    C.executor.checked_bytes(v['implementation'])
    require(v['destination']==str(home) and b==record(home/'source-continuation.json'),'continuation destination differs')
    previous=read(v['predecessor_config']);prior_home=Path(previous['run_root'])
    require(v['predecessor_config']==record(prior_home/'config.json'),'noncanonical prior config')
    tracker=Path(v['predecessor_tracker']['path'])
    require(tracker==prior_home/'trackers/workbooks'/(config['workbook']+'.json'),'foreign retained tracker')
    require(previous['workbook']==config['workbook'],'workbook custody identity changed')
    # Native env-file location is allowed to change, never the workbook slot.
    old_controls=C.decode(previous['controls']);controls=C.decode(config['controls'])
    require(old_controls.slot_lock==controls.slot_lock,'workbook slot changed')
    for key in ('predecessor_descriptor','predecessor_checkpoint','predecessor_prepared','new_prepared','new_worker','code'):C.executor.checked_bytes(v[key])
    if v['takeover'] is not None:C.executor.checked_bytes(v['takeover'])
    else:require(baseline,'original source-only migration requires its takeover')
    require(v['old_budget']=={k:v['new_budget'][k] for k in v['old_budget']} ,'semantic review budget identity changed')
    worker=C.decode(read(v['new_worker'])['worker'])
    require(worker.run==home/'environment' and worker.bootstrap.run==home/'bootstrap' and worker.bootstrap.policy.tracker_root==tracker.parent,'successor worker layout differs')
    require(worker.normalization_budget.ordinal==v['old_budget']['ordinal'] and worker.normalization_budget.limit==v['old_budget']['limit'] and worker.normalization_budget.attempt_id==v['old_budget']['attempt_id'],'successor review budget identity differs')
    from scripts.batch import harbor_lease_takeover as T
    if v['takeover'] is not None:
        before,after,lease=T.validate_completion(v['takeover'])
        stop=read(read(v['takeover'])['stopped_owner_proof'])
        require(stop['selection']['tracker']==v['predecessor_tracker'] and stop['selection']['checkpoint']==v['predecessor_checkpoint'],'continuation takeover predecessor differs')
    else:
        initial=v['retained_lease'];before=read(initial['tracker']);lease=C.bootstrap.Lease(**initial['lease'])
        require(initial['tracker']['sha256']==v['predecessor_tracker']['sha256'] and
                lease.sha256==initial['tracker']['sha256'] and lease.revision==before['revision'] and
                all(getattr(lease,k)==before['leases']['task_agent'][k] for k in ('owner','token')),
                'retained live migration lease differs from original checkpoint')
    if baseline:
        B.validate(v['baseline_failure'],config=v['predecessor_config'],descriptor=v['predecessor_descriptor'],
                   checkpoint=v['predecessor_checkpoint'],tracker=v['predecessor_tracker'],new_code=v['code'],
                   require_pristine=False)
        initial=v['retained_lease'];require(read(initial['tracker'])==(after if v['takeover'] is not None else before)
                and initial['lease']==asdict(lease),'baseline migration initial lease changed')
    require(before['bindings']['original_raw_source']==v['source'] and before['lane_state'][SOURCE]['state']=='passed','continuation original source differs')
    for lane,row in before['lane_state'].items():
        if baseline and lane in B.PASSED:require(row['state']=='passed','baseline migration changed a passed source lane')
        elif lane!=SOURCE:require(row['state']=='pending' and row['attempt']==0 and not row['confidence_history'],'continuation skipped consumed lane')
    old=C.decode(read(v['predecessor_descriptor'])['worker'])
    require(old.prepared==v['predecessor_prepared'],'original bootstrap binding differs')
    expected=replace(old,bootstrap=replace(old.bootstrap,run=home/'bootstrap'),run=home/'environment',prepared=v['new_prepared'],normalization_budget=replace(old.normalization_budget,evidence=(*old.normalization_budget.evidence,v['new_prepared'])))
    require(worker==expected and v['old_budget']=={k:getattr(old.normalization_budget,k) for k in ('ordinal','limit','attempt_id')},'successor changed source/worker/budget semantics')
    prepared=read(v['new_prepared']);prior_prepared=read(v['predecessor_prepared'])
    require(prepared=={**prior_prepared,'selection':C.bootstrap.serial(asdict(expected.bootstrap)),'code':prepared['code']},'new bootstrap changed beyond explicit producer/namespace migration')
    require(prepared['validated']==v['validated'] and v['source']==v['validated']['original'],'continuation source proof differs')
    require(config['code']==v['code'],'successor code snapshot changed')
    return tracker,home,home/worker_kind,controls.slot_lock


def snapshot_initial_tracker(destination,after,lease):
    raw=C.lifecycle.canonical(after)
    require(C.executor.sha(raw)==lease.sha256,'migration tracker serialization differs from exact lease')
    return C.bootstrap.keep(destination/'tracker-at-import.json',raw)


def commit(config_binding,descriptor_binding,checkpoint_binding,tracker_binding,lease,takeover,destination,controls,code,baseline=None):
    """Caller has revalidated central custody; old tracker must match takeover."""
    config=read(config_binding);old=C.decode(read(descriptor_binding)['worker']);saved=read(old.prepared)
    from scripts.batch import harbor_lease_takeover as T
    if takeover is not None:
        _,after,observed=T.validate_completion(takeover)
        require(observed==lease,'lease is not the exact owning takeover result')
        stopped=read(read(takeover)['stopped_owner_proof'])
        require(stopped['selection']['tracker']==tracker_binding and stopped['selection']['checkpoint']==checkpoint_binding,'takeover lost exact predecessor')
        predecessor=stopped['before']
    else:
        require(baseline is not None,'source-only migration requires original takeover')
        after=C.bootstrap._tracker(old.bootstrap,lease)
        require(record(old.bootstrap.policy.tracker_root/(old.bootstrap.workbook+'.json'))==tracker_binding,
                'live baseline migration requires the exact retained checkpoint')
        predecessor=tracker_binding
    if baseline is not None:
        from scripts.batch import harbor_baseline_continuation as B
        B.validate(baseline,config=config_binding,descriptor=descriptor_binding,checkpoint=checkpoint_binding,
                   tracker=tracker_binding,new_code=code)
    pristine(Path(config['run_root']),after,baseline)
    require(C.bootstrap.validate(old.bootstrap)==saved['validated'],'source changed before continuation')
    destination=C.executor.safe(destination);require(not destination.exists(),'continuation namespace exists; reconcile without replay')
    require(destination.parent==Path(config['run_root'])/'continuations','dedicated predecessor continuation namespace required')
    destination.mkdir(parents=True)
    bootstrap=replace(old.bootstrap,run=destination/'bootstrap');bootstrap.run.mkdir()
    for name in ('ledger.snapshot.json','source-normalization.json'):
        p=old.bootstrap.run/name
        if p.exists():C.bootstrap.keep(bootstrap.run/name,p.read_bytes())
    prepared=C.bootstrap.keep(bootstrap.run/'prepared.json',{**saved,'selection':C.bootstrap.serial(asdict(bootstrap)),'code':record(C.bootstrap.__file__)})
    budget=replace(old.normalization_budget,evidence=(*old.normalization_budget.evidence,prepared))
    current=replace(old,bootstrap=bootstrap,prepared=prepared,run=destination/'environment',normalization_budget=budget)
    C.worker._setup(current,lease)
    identity={k:getattr(budget,k) for k in ('ordinal','limit','attempt_id')}
    origin=[config_binding,descriptor_binding,checkpoint_binding,predecessor,*([takeover] if takeover is not None else []),prepared]
    base_descriptor=C.keep(destination/'worker-descriptor.json',C._descriptor(current,lease,controls,origin))
    value=dict(schema_version=SCHEMA,implementation=record(__file__),predecessor_config=config_binding,predecessor_descriptor=descriptor_binding,predecessor_checkpoint=checkpoint_binding,predecessor_tracker=tracker_binding,predecessor_prepared=old.prepared,takeover=takeover,destination=str(destination),source=saved['validated']['original'],validated=saved['validated'],old_budget=identity,new_budget=identity,new_prepared=prepared,new_worker=base_descriptor,code=code,semantic_budget_reset=False,model_calls=0,new_admission=False)
    if baseline is not None:
        initial=snapshot_initial_tracker(destination,after,lease)
        value.update(schema_version=B.SCHEMA,baseline_failure=baseline,retained_lease=dict(tracker=initial,lease=asdict(lease)))
    receipt=C.keep(destination/'source-continuation.json',value)
    # The receipt and initial descriptor have no circular hash binding. Later
    # task checkpoints bind this descriptor and retained migration receipt.
    descriptor=C.keep(destination/'descriptor.json',C._descriptor(current,lease,controls,[base_descriptor,receipt]))
    result=C.keep(destination/'result.json',dict(status='IMPORTED',step='baseline' if baseline is not None else 'source',descriptor=descriptor,lease=C.encode(lease),selection=C.content_codec.encode(None),evidence=[receipt,prepared,*([takeover] if takeover is not None else [])],publication_ready=False))
    return current,receipt,descriptor,result
