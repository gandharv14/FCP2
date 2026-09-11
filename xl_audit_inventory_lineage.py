"""Explicit inventory-reader applicability around immutable issued proof chains.

No old code is executed and no historical artifact is rewritten. Root selects
new wrapper records; normal audit cache and old proof validators stay strict.
"""
from pathlib import Path
from dataclasses import asdict
import copy
import json
import xl_variable_source_audit as A
import xl_audit_inventory_upgrade as I
import xl_audit_renderer_recovery as R
import xl_audit_equivalence as Q
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_tracker_lifecycle import checked_record

AUDIT_SCHEMA='audit-renderer-inventory-applicability/v1'
LOCATOR_SCHEMA='audit-locator-inventory-applicability/v1'
RENDERER_BEFORE='3651babae65be3ab474847305dcdd7f7462af6571391525b264a1446d2d6dbc8'
LOCATOR_BEFORE='06d9989295706eb4cae4ff70b118a80a8f6d498f0ad5aba7fe9e0a89e7d58e40'
REVIEWED_CURRENT={'xl_audit_renderer_recovery.py': '0cbbe7bd49c660089af7f9ecb11e57de887d61be72d5af0d2fa4b3225a5c107f', 'xl_audit_equivalence.py': 'da2c2da1097b7cd46fd70d747172cd588ad887ed4b208b08550187053c584905', 'xl_audit_validator_upgrade.py': '47675b4f857c97539854c98f7d32ddf9010ab73370ef051bf34c445b232ac827'}


def witness(binding,expected):
    checked_record(binding);require(binding['sha256']==expected,'unreviewed historical proof implementation')
    return binding


def records(value):
    found={}
    def walk(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256'}:
                checked_record(v);found[v['path']]=v['sha256']
            else:
                for item in v.values():walk(item)
        elif isinstance(v,(tuple,list)):
            for item in v:walk(item)
    walk(value);return found


def code_records():
    import xl_audit_validator_upgrade as upgrade
    for module in (R,Q,upgrade):
        require(record(Path(module.__file__).resolve())['sha256']==REVIEWED_CURRENT[Path(module.__file__).name], 'unreviewed current proof adapter change')
    return [record(Path(m.__file__).resolve()) for m in (A,I,R,Q,upgrade)]+[record(Path(__file__).resolve())]


def propose_renderer(previous, *, bindings, original_code, original_renderer_code):
    """Reconstruct the old issued renderer envelope using only current code."""
    old=read(previous);require(old.get('schema_version')==R.SCHEMA and old.get('policy')==R.POLICY,'unsupported original renderer receipt')
    witness(original_renderer_code,RENDERER_BEFORE)
    selection=R.from_data(old['selection']);require(selection.original_code==original_code,'original auditor selection changed')
    current,outputs=R._project(selection)  # Current live source/proof and full inventory equality.
    require({k:bindings[k] for k in R.KEYS}==old['audit_bindings'],'original audit artifacts moved or changed')
    require(Q.selected(bindings)==Q.selected({**current['original_bindings'],**old['audit_bindings']}),'renderer source/baseline pair changed')
    # Exact predecessor identities are admitted only behind this new wrapper.
    expected=copy.deepcopy(current);identity=expected['identity']
    require(identity['current_generator']['sha256']==I.AFTER_SHA256,'unexpected current inventory implementation')
    identity['current_generator']['sha256']=I.BEFORE_SHA256
    identity['adapter']['sha256']=RENDERER_BEFORE
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
    files.update(records([previous,original_renderer_code,original_code,code_records()]))
    return dict(schema_version=AUDIT_SCHEMA,previous=previous,original_code=original_code,original_renderer_code=original_renderer_code,
        audit_bindings={k:bindings[k] for k in R.KEYS},context_bindings=current['original_bindings'],
        current_report=current,checked_files=dict(sorted(files.items())),model_reexecuted=False,semantic_attempts_added=0,source_approval=False)


def validate_renderer(binding, *, bindings, original_code, model=None, endpoint=None, project_id=None):
    value=read(binding);require(value.get('schema_version')==AUDIT_SCHEMA,'wrong renderer applicability schema')
    if model is not None:
        s=R.from_data(read(value['previous'])['selection'])
        require((s.policy.model,s.policy.endpoint.removesuffix('/chat/completions'),s.policy.project_id)==(model,endpoint.rstrip('/'),project_id),'renderer model configuration differs')
    expected=propose_renderer(value['previous'],bindings=bindings,original_code=original_code,original_renderer_code=value['original_renderer_code'])
    require(value==expected,'renderer applicability wrapper stale or altered')
    return {**expected['checked_files'],binding['path']:binding['sha256']}


def propose_locator(previous, *, bindings, audit_applicability, original_code, original_locator_code,
                    model=A.DEFAULT_MODEL,endpoint=A.PROD_ENDPOINT,project_id=A.DEFAULT_PROJECT_ID):
    witness(original_locator_code,LOCATOR_BEFORE);old=read(previous);audit_proof=read(audit_applicability)
    validate_renderer(audit_applicability,bindings=bindings,original_code=original_code,model=model,endpoint=endpoint,project_id=project_id)
    require(old.get('schema_version')==Q.SCHEMA and old.get('adapter_sha256')==LOCATOR_BEFORE
        and old.get('auditor_sha256')==I.BEFORE_SHA256,'unsupported original locator implementation')
    require(old.get('validator_upgrade')==audit_proof['previous'] and old.get('original_auditor')==original_code,'old locator selected another audit proof')
    selected=Q.selected(bindings)
    require(old['old_bindings']==Q.selected(bindings,historical=True) and old['current_bindings']==selected,'inventory wrapper requires the exact same existing pair')
    fresh=Q.propose_audit_equivalence(old['old_bindings'],bindings,model=model,endpoint=endpoint,project_id=project_id,
        validator_upgrade=audit_applicability,original_auditor=original_code)
    omit={'adapter_sha256','auditor_sha256','checked_files','validator_upgrade'}
    require(set(old)==set(fresh) and {k:v for k,v in old.items() if k not in omit}=={k:v for k,v in fresh.items() if k not in omit},
        'locator changed financial context, original calls or budgets')
    updated={str(Path(A.__file__).resolve()):I.BEFORE_SHA256,str(Path(R.__file__).resolve()):RENDERER_BEFORE,str(Path(Q.__file__).resolve()):LOCATOR_BEFORE}
    import xl_audit_validator_upgrade as upgrade
    additions={audit_applicability['path'],audit_proof['original_renderer_code']['path'],
        I.evidence()['path'],str(Path(__file__).resolve()),str(Path(upgrade.__file__).resolve())}
    expected_files=dict(fresh['checked_files'])
    require(additions<=set(expected_files),'current wrapper additions incomplete')
    for path in additions:expected_files.pop(path)
    require(set(updated)<=set(expected_files),'missing original code roles')
    expected_files.update(updated)
    require(old['checked_files']==expected_files,'old locator evidence inventory dropped, added or changed')
    for path,sha in old['checked_files'].items():
        if path in updated:
            require(sha==updated[path],'original locator code role changed')
            require(path in fresh['checked_files'],'current code proof dropped')
        else:
            require(fresh['checked_files'].get(path)==sha,'old locator non-code evidence dropped or changed')
            checked_record(dict(path=path,sha256=sha))
    files={**fresh['checked_files'],**records([previous,original_locator_code,audit_applicability,code_records()])}
    return dict(schema_version=LOCATOR_SCHEMA,previous=previous,audit_applicability=audit_applicability,
        original_code=original_code,original_locator_code=original_locator_code,old_bindings=Q.selected(bindings,historical=True),current_bindings=selected,
        config=dict(model=model,endpoint=endpoint,project_id=project_id),current_projection=fresh,
        checked_files=dict(sorted(files.items())),model_reexecuted=False,semantic_attempts_added=0,source_approval=False)


def validate_locator(binding, *, current_bindings, validator_upgrade, original_auditor,
                     model=A.DEFAULT_MODEL,endpoint=A.PROD_ENDPOINT,project_id=A.DEFAULT_PROJECT_ID):
    value=read(binding);require(value.get('schema_version')==LOCATOR_SCHEMA,'wrong locator applicability schema')
    require(value['audit_applicability']==validator_upgrade and value['original_code']==original_auditor,'root selected another audit applicability')
    expected=propose_locator(value['previous'],bindings=current_bindings,audit_applicability=validator_upgrade,
        original_code=original_auditor,original_locator_code=value['original_locator_code'],model=model,endpoint=endpoint,project_id=project_id)
    require(value==expected,'locator applicability wrapper stale or altered')
    return {**expected['checked_files'],binding['path']:binding['sha256']}
