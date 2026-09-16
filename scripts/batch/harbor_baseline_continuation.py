"""Closed failed-baseline migration evidence; no source or model retry.

Both original source lanes remain passed. Only a new bootstrap namespace may
execute the corrected baseline; every failed input and command stays retained.
"""
from pathlib import Path
import json
from scripts.batch.harbor_admission import require,read,record

SCHEMA='harbor-baseline-only-continuation/v1'
BOUNDARY='harbor-failed-baseline-boundary/v1'
PRECOMMAND='harbor-precommand-baseline-boundary/v1'
PASSED={'harbor-source-warden','harbor-segmentation-analyst'}
CHANGES={'xl_input_mask.py','scripts/batch/harbor_source_only_continuation.py',
         'scripts/batch/harbor_baseline_continuation.py'}


def inventory(home):
    home=Path(home);require(home.is_dir() and not any(p.is_symlink()for p in (home,*home.parents)),
                            'canonical retained baseline required')
    rows={}
    for path in home.rglob('*'):
        require(not path.is_symlink(),'symlink in retained baseline attempt')
        if path.is_file():rows[str(path.relative_to(home))]=record(path)
    return rows


def pristine(home,tracker):
    home=Path(home)
    require(PASSED<=set(tracker['lane_state']),'source and segmentation lanes required')
    for lane,value in tracker['lane_state'].items():
        if lane in PASSED:require(value['state']=='passed','both source lanes must remain passed')
        else:require(value['state']=='pending' and value['attempt']==0 and not value['confidence_history']
                     and value['current_confidence']is None,'downstream model lane consumed')
    baseline=home/'bootstrap/baseline'
    require(baseline.is_dir() and not (baseline/'complete.json').exists(),'only a failed incomplete baseline may migrate')
    result=read(record(baseline/'result.json'))
    require(type(result['returncode'])is int and result['returncode']!=0 and
            result['log']==record(baseline/'command.log'),'terminal failed baseline command required')
    names={p.name for p in (home/'environment').iterdir()}
    failures={x for x in names if x.startswith('unresolved-baseline-') and x.endswith('.json')}
    require(len(failures)==1 and names=={'selection.json',*failures},'later or uncertain environment artifact exists')
    failed=read(record(home/'environment'/next(iter(failures))))
    require(failed['status']=='UNRESOLVED' and failed['step']=='baseline' and failed['automatic_retry']is False
            and failed['source_invalidity_claimed']is False,'baseline failure disposition changed')
    require({p.name for p in (home/'bootstrap').iterdir()}<=
            {'admission.json','ledger.snapshot.json','prepared.json','source-normalization.json','baseline'},
            'audit or later bootstrap work already exists')
    require(not (home/'model-campaign').exists() and not (home/'instruction').exists(),
            'model or instruction namespace already exists')
    return record(home/'environment'/next(iter(failures)))


def precommand(p,home,tracker,*,require_pristine=True):
    from scripts.batch import harbor_baseline_precommand as P
    require(p['precommand_validator']==record(P.__file__),'selected precommand validator changed')
    proof=read(p['native_validation']);outcome=read(p['failed_outcome'])
    require(proof['status']=='ACTUAL_PRECOMMAND_BOUNDARY_VERIFIED' and proof['authoring_files_unchanged']is True and proof['models']==proof['claims']==0 and proof['module_sha256']==p['precommand_validator']['sha256'] and proof['tracker']==p['tracker'],'native precommand proof differs')
    expected=dict(status='STOPPED_BEFORE_BASELINE_COMMAND',failure=p['failure'],checkpoint=p['checkpoint'],outcome=p['failed_outcome'],absent_namespaces=['bootstrap/baseline','model-campaign','instruction'],models=0,claims=0)
    require(proof['result']==expected,'native precommand checkpoint differs')
    # The original failed namespace remains absent forever. A later successful
    # continuation may create its original campaign model root; this does not
    # change the authenticated historical precommand failure.
    require(not (Path(home)/'bootstrap/baseline').exists(),'original failed baseline namespace appeared')
    resource=read(outcome['resource']);read(resource['intent']);_bytes(resource['log'])
    require(outcome['status']=='COMPLETED_OPERATION' and outcome['result']==p['checkpoint'] and resource['returncode']==0 and resource['failure']is None,'original stopped precommand guard changed')
    if require_pristine:require(P.validate(home,tracker,p['checkpoint'],p['failed_outcome'])==expected,'original precommand validator differs')
    return p['failure']

def pristine_bound(home,tracker,bound):
    p=read(bound)
    return precommand(p,home,tracker)if p.get('schema_version')==PRECOMMAND else pristine(home,tracker)

def precommand_delta(old,new,validator):
    before=read(old);after=read(new);left={r['relative']:r['sha256']for r in before['files']};right={r['relative']:r['sha256']for r in after['files']}
    changed={key for key in set(left)|set(right)if left.get(key)!=right.get(key)}-{'.pipeline-code.json'}
    allowed=(CHANGES-{'xl_input_mask.py'})|{'scripts/batch/harbor_baseline_precommand.py'}
    require(changed<=allowed and validator==record(Path(after['root'])/'scripts/batch/harbor_baseline_precommand.py'),'precommand continuation changed unrelated task semantics')
    return sorted(changed)


def validated_mask(proof,workbook,mask):
    outer=read(proof);require(outer['returncode']==0,'native baseline diagnostic failed')
    guarded=json.loads(outer['stdout'])
    require(guarded['returncode']==0 and guarded['failure']is None and guarded['models']==0
            and guarded['lease_mutations']==0 and guarded['seconds']==900 and guarded['memory_bytes']==8*1024**3,
            'original zero-model baseline guard required')
    values=[]
    for line in guarded['log'].splitlines():
        try:values.append(json.loads(line))
        except json.JSONDecodeError:pass
    require(values and values[-1].get('status')=='PASS' and values[-1]['workbook']==workbook
            and values[-1]['code']['sha256']==mask['sha256'] and values[-1]['models']==values[-1]['claims']==0
            and values[-1]['original_failure_preserved']is True,'native proof selected another baseline')
    read(guarded['probe']) if Path(guarded['probe']['path']).suffix=='.json' else _bytes(guarded['probe'])
    _bytes(values[-1]['code'])


def _bytes(row):
    require(record(Path(row['path']))==row,'baseline evidence changed')


def code_delta(old,new,mask):
    before=read(old);after=read(new)
    left={r['relative']:r['sha256']for r in before['files']}
    right={r['relative']:r['sha256']for r in after['files']}
    # The manifest changes solely because the selected source files change.
    changed={key for key in set(left)|set(right) if left.get(key)!=right.get(key)}-{'.pipeline-code.json'}
    require('xl_input_mask.py'in changed and changed<=CHANGES,'baseline migration changed unrelated task code')
    require(mask==record(Path(after['root'])/'xl_input_mask.py') and right['xl_input_mask.py']==mask['sha256'],
            'corrected numeric mask differs from selected code')
    return sorted(changed)


def selected_code(new_code):
    """Reject a correct plan loaded under a foreign or incomplete code tree."""
    from scripts import harbor_campaign as C,corpus
    value=read(new_code);root=Path(value['root']);inventory=corpus._frozen_code_files(root)
    require(root==C.ROOT and inventory is not None and set(value)=={'root','files'}
            and len(value['files'])==len(inventory)+1 and
            {x['relative']:x['sha256'] for x in value['files']}==
            {**inventory,'.pipeline-code.json':record(root/'.pipeline-code.json')['sha256']},
            'baseline migration must execute its exact complete selected code')


def validate(bound,*,config,descriptor,checkpoint,tracker,new_code,require_pristine=True):
    p=read(bound)
    fields={'schema_version','workbook','config','descriptor','checkpoint','tracker','old_code','new_code','native_validation','failure','source','generation_ids','tracker_archive'}
    early=p.get('schema_version')==PRECOMMAND
    require(set(p)==fields|({'precommand_validator','failed_outcome'}if early else {'mask','failed_files'}) and p['schema_version']in(BOUNDARY,PRECOMMAND),'closed failed-baseline witness required')
    require(p['config']==config and p['descriptor']==descriptor and p['checkpoint']==checkpoint
            and p['tracker']==tracker and p['new_code']==new_code,'baseline predecessor changed')
    require(p['tracker_archive']['sha256']==tracker['sha256'],'baseline tracker archive differs')
    value=read(config);old=read(p['tracker_archive']);home=Path(value['run_root']);prior=read(checkpoint)
    require(prior['descriptor']==descriptor and prior['status']=='UNRESOLVED' and prior['step']=='baseline'
            and prior['publication_ready']is False,'exact unresolved baseline checkpoint required')
    require(p['old_code']==value['code'] and p['workbook']==value['workbook']==old['workbook_id']
            and p['source']==old['bindings']['original_raw_source'] and p['generation_ids']==old['generation_ids'],
            'baseline source or generation pair changed')
    if require_pristine and not early:require(p['failure']==pristine(home,old),'owning unresolved baseline evidence differs')
    failure=read(p['failure'])
    require(Path(p['failure']['path']).parent==home/'environment' and
            failure['status']=='UNRESOLVED' and failure['step']=='baseline' and
            failure['automatic_retry']is False and failure['source_invalidity_claimed']is False
            and p['failure']in prior['evidence'],'owning unresolved baseline evidence differs')
    selected_code(new_code)
    if early:
        precommand(p,home,old,require_pristine=require_pristine);precommand_delta(p['old_code'],new_code,p['precommand_validator'])
    else:
        require(p['failed_files']==inventory(home/'bootstrap/baseline'),'failed baseline bytes changed')
        code_delta(p['old_code'],new_code,p['mask']);validated_mask(p['native_validation'],p['workbook'],p['mask'])
    return p
