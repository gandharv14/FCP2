"""Direct completed-audit forwarding uses real tiny current generations/journals."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import xl_output_task as task
import xl_audit_equivalence as Q
import xl_audit_validator_upgrade as U
from scripts.batch import harbor_environment_producer as producer
from scripts.batch.harbor_admission import record,read
from test_historical_audit_source_refresh import direct,save
from test_harbor_admission import setup
from test_harbor_environment_worker import worker,responses
from test_harbor_workbook_bootstrap import selected

@pytest.fixture
def completed(direct,tmp_path,monkeypatch):
    old,_,code,seen=direct
    b=dict(old);run=tmp_path/'canonical-audits/0001-variable-sources';run.mkdir(parents=True)
    import xl_variable_source_audit as audit
    inv=Q._context(b,historical=False)[2];actual=audit._attempt_scope_config
    def old_scope(*args):
        value=actual(*args);value['generator_sha256']=code['sha256'];return value
    table='| Variable | Inventory row IDs | Plausible external source(s) |\n|---|---|---|\n'+'\n'.join('| Supplied assumption | '+r['row_id']+' | Supplied workbook |' for r in inv['variables'])
    with monkeypatch.context() as m:
        m.setattr(audit,'_attempt_scope_config',old_scope)
        m.setattr(audit,'_call_model',lambda *a,**k:(table,'stop'))
        audit.generate_audit('0001',b['baseline_inputs']['path'],b['segmentation_generation']['path'],run,'synthetic',segmentation=inv['metadata']['segmentation'])
    for key,suffix in zip(Q.AUDIT_KEYS,('.metadata.json','.inventory.json','.md')):
        b[key]=record(run/('0001-inputs-variable-sources'+suffix))
    metadata=read(b['variable_source_metadata']);metadata['generator_sha256']=code['sha256']
    b['variable_source_metadata']=save(Path(b['variable_source_metadata']['path']),metadata)
    upgrade=save(tmp_path/'selected-upgrade.json',U.propose_upgrade(b,original_code=code))
    context=Q._context(b,historical=False)[0]
    context['workbook_id']='0001'
    monkeypatch.setattr(task,'generate_audit',lambda *a,**kw:pytest.fail('no audit generation permitted'))
    monkeypatch.setattr(task,'read_env_key',lambda *a,**kw:pytest.fail('no credential read permitted'))
    return b,upgrade,code,context,run,seen

def load(completed,**overrides):
    b,upgrade,code,context,run,_=completed
    args=dict(workbook='0001',artifact=Path(b['baseline_inputs']['path']),audit_root=run.parent,
        receipt_path=upgrade['path'],original_code_path=code['path'],pipeline_context=context,
        model=task.DEFAULT_AUDIT_MODEL,project_id=task.DEFAULT_PROJECT_ID if hasattr(task,'DEFAULT_PROJECT_ID') else 'cms6m4urm006n07z8ecxi1oi2')
    args.update(overrides);return task.load_upgraded_audit(**args)

def test_complete_direct_upgrade_keeps_all_actual_history(completed):
    b,upgrade,code,context,run,seen=completed
    retained={p:p.read_bytes() for p in run.parent.parent.rglob('*') if p.is_file()};count=len(seen)
    result=load(completed)
    assert result['cache_hit'] and result['validator_upgrade_sha256']==upgrade['sha256']
    assert result['current_inventory_sha256']==read(b['variable_source_metadata'])['inventory_sha256']
    assert len(seen)==count and all(p.read_bytes()==raw for p,raw in retained.items())

@pytest.mark.parametrize('what',['receipt','code','baseline','sidecar','metadata','inventory','audit','response','budget','source','segmentation','model','project','foreign','unbound','wrongroot','otherprotocol'])
def test_invalid_direct_proof_never_falls_back(completed,tmp_path,what):
    b,upgrade,code,context,run,_=completed;overrides={}
    if what in {'model','project','foreign','unbound','wrongroot'}:
        key={'model':'model','project':'project_id','foreign':'workbook','unbound':'pipeline_context','wrongroot':'audit_root'}[what]
        overrides[key]=None if what=='unbound' else tmp_path/'missing' if what=='wrongroot' else 'different'
    elif what in {'source','segmentation'}:context['bindings'][what+'_generation_id']='d'*64
    elif what=='otherprotocol':
        v=read(upgrade);v['schema_version']='other/v1';Path(upgrade['path']).write_text(json.dumps(v))
    else:
        path={'receipt':upgrade['path'],'code':code['path'],'baseline':b['baseline_inputs']['path'],
              'sidecar':b['baseline_segmentation']['path'],'metadata':b['variable_source_metadata']['path'],
              'inventory':b['variable_source_inventory']['path'],'audit':b['variable_source_audit']['path']}.get(what)
        if what in {'response','budget'}:
            m=read(b['variable_source_metadata']);coverage=Q.read(m['coverage_path'])
            path=(m['attempt_policy'] if what=='budget' else coverage['batches'][0]['response'])['path']
        Path(path).write_bytes(Path(path).read_bytes()+b'changed')
    with pytest.raises((ValueError,FileNotFoundError,KeyError,json.JSONDecodeError)):load(completed,**overrides)

@pytest.mark.parametrize('what',['receipt','code','parent'])
def test_symlinks_rejected(completed,tmp_path,what):
    _,u,c,_,run,_=completed
    if what=='parent':
        link=tmp_path/'alias';link.symlink_to(run.parent,target_is_directory=True);kw={'audit_root':link}
    else:
        link=tmp_path/'alias.json';link.symlink_to(u['path'] if what=='receipt' else c['path'])
        kw={('receipt_path' if what=='receipt' else 'original_code_path'):link}
    with pytest.raises(ValueError,match='symlink'):load(completed,**kw)

@pytest.mark.parametrize('extra',[
 [],['--audit-upgrade-original-code','original.py'],
 ['--audit-validator-upgrade-receipt','receipt.json'],
 ['--audit-validator-upgrade-receipt','receipt.json','--audit-upgrade-original-code','original.py'],
 ['--audit-validator-upgrade-receipt','receipt.json','--audit-upgrade-original-code','original.py','--require-cached-variable-source-audit','--no-naturalize','--audit-equivalence-receipt','other.json'],
])
def test_incomplete_cli_contract_rejected_before_io(extra):
    if not extra:extra=['--audit-validator-upgrade-receipt','receipt.json','--audit-upgrade-original-code','original.py','--require-cached-variable-source-audit','--no-naturalize']
    with pytest.raises(SystemExit) as e:task.main(['0001',*extra])
    assert e.value.code==2

def content(completed,tmp_path):
    b,up,code,_,_,_=completed
    b={**b,'audit_validator_upgrade':up,'audit_upgrade_original_code':code}
    return SimpleNamespace(workbook='0001',bindings=b,mcp_build_a=None,
        normalization_run=tmp_path/'normalized/0001-variable-sources',stage=tmp_path/'stage/0001-outputs',
        model_policy=SimpleNamespace(model=task.DEFAULT_AUDIT_MODEL,project_id='cms6m4urm006n07z8ecxi1oi2'))

def test_producer_forwards_both_hash_checked_direct_records(completed,tmp_path):
    c=content(completed,tmp_path);_,args=producer.packaging_arguments(c,tmp_path/'work',{'path':'taxonomy.json'})
    assert args[args.index('--audit-validator-upgrade-receipt')+1]==c.bindings['audit_validator_upgrade']['path']
    assert args[args.index('--audit-upgrade-original-code')+1]==c.bindings['audit_upgrade_original_code']['path']
    assert '--require-cached-variable-source-audit' in args and '--no-naturalize' in args
    assert '--audit-equivalence-receipt' not in args

@pytest.mark.parametrize('what',['missing','receipt','code'])
def test_producer_rejects_changed_selected_hash(completed,tmp_path,what):
    c=content(completed,tmp_path)
    if what=='missing':c.bindings.pop('audit_upgrade_original_code')
    else:
        key='audit_validator_upgrade' if what=='receipt' else 'audit_upgrade_original_code'
        c.bindings[key]={**c.bindings[key],'sha256':'0'*64}
    with pytest.raises(ValueError):producer.packaging_arguments(c,tmp_path/'work',{'path':'taxonomy.json'})

def test_existing_equivalence_route_keeps_precedence(completed,tmp_path):
    c=content(completed,tmp_path);c.bindings['audit_equivalence']={'path':'retained-locator.json','sha256':'a'*64}
    _,args=producer.packaging_arguments(c,tmp_path/'work',{'path':'taxonomy.json'})
    assert '--audit-equivalence-receipt' in args and '--audit-validator-upgrade-receipt' not in args


def test_producer_arguments_reach_actual_cli_emission_without_model(completed,tmp_path):
    c=content(completed,tmp_path);taxonomy=tmp_path/'taxonomy.json';taxonomy.write_text('{}')
    _,args=producer.packaging_arguments(c,tmp_path/'work',record(taxonomy))
    result=task.main([str(v) for v in args[1:]])
    assert result in (None,0)
    assert (c.stage/'instruction.md').is_file()
    assert (c.stage/'tests/audit_validator_upgrade.json').read_bytes()==Path(c.bindings['audit_validator_upgrade']['path']).read_bytes()
    assert (c.stage/'tests/pipeline_bindings.json').is_file()
    key=json.loads((c.stage/'tests/answer_key.json').read_text())
    assert key
    assert read(c.bindings['audit_validator_upgrade'])['retained_consumption']['submitted_counts']=={'1':1}
