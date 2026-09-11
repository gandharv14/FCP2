"""Exact pre-streaming completed journal applicability, never model replay."""
from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import pytest
import xl_variable_source_audit as A
import xl_audit_inventory_upgrade as I
import xl_audit_validator_upgrade as U
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_completed_lanes import normalization_projection
from scripts.batch.harbor_admission import record
from test_harbor_normalization_producer import setup,mock_review


@pytest.fixture
def prior(setup,monkeypatch,tmp_path):
    original=tmp_path/'pre-streaming-generator.py';original.write_bytes(I.restore_inventory_lookup(Path(A.__file__).read_bytes()))
    actual=A._attempt_scope_config
    def old_scope(*a):
        scope=actual(*a);scope['generator_sha256']=I.BEFORE_SHA256;return scope
    with monkeypatch.context() as m:
        m.setattr(A,'_attempt_scope_config',old_scope)
        s,c,resolver,live=setup()
    path=Path(s.bindings['variable_source_metadata']['path']);meta=N.read(s.bindings['variable_source_metadata']);meta['generator_sha256']=I.BEFORE_SHA256
    path.write_bytes(E.canonical(meta));s.bindings['variable_source_metadata']=record(path)
    return s,c,record(original),resolver


def upgraded(prior,tmp_path):
    s,c,code,resolver=prior;value=U.propose_upgrade(s.bindings,original_code=code)
    path=tmp_path/'upgrade.json';path.write_bytes(E.canonical(value));receipt=record(path)
    bindings={**s.bindings,'audit_validator_upgrade':receipt,'audit_upgrade_original_code':code}
    return value,receipt,bindings


def test_completed_current_history_requires_explicit_exact_inventory_upgrade(prior,tmp_path,monkeypatch):
    s,c,code,_=prior;before={k:Path(s.bindings[k]['path']).read_bytes() for k in U.AUDIT_KEYS}
    with pytest.raises(A.AuditRevalidationRequired):A.validate_saved_audit_coverage(*[s.bindings[k]['path'] for k in U.AUDIT_KEYS])
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('no model replay'))
    value,receipt,bindings=upgraded(prior,tmp_path)
    assert value['coverage']['inventory_rows']==2 and value['retained_consumption']['submitted_counts']=={'1':1}
    assert value['semantic_attempts_added']==0 and value['model_reexecuted'] is False
    from scripts.qualified_publication import validate_audit_bindings
    records=validate_audit_bindings(bindings)
    assert receipt['path'] in records and I.evidence()['path'] in records
    assert all(Path(s.bindings[k]['path']).read_bytes()==raw for k,raw in before.items())


@pytest.mark.parametrize('which',['source','baseline','segmentation','inventory','rows','missing_context'])
def test_rebuilt_current_inventory_and_bound_inputs_required(prior,tmp_path,monkeypatch,which):
    s,c,code,resolver=prior
    if which=='missing_context':
        with pytest.raises(ValueError):U.propose_upgrade({k:s.bindings[k] for k in U.AUDIT_KEYS},original_code=code)
        return
    if which in ('source','baseline'):
        path=Path(s.bindings['source_file' if which=='source' else 'baseline_inputs']['path']);path.write_bytes(path.read_bytes()+b'changed')
    elif which=='segmentation':resolver.side_effect=ValueError('current pair rejected')
    elif which=='inventory':
        real=A.build_inventory
        def changed(*a,**k):
            inventory=real(*a,**k);inventory['variables'][0]['value_summary']={'count':0};return inventory
        monkeypatch.setattr(A,'build_inventory',changed)
    else:
        path=Path(s.bindings['segmentation_generation']['path'])/'segments.json';v=json.loads(path.read_text());v['inputs']=[];path.write_text(json.dumps(v))
    with pytest.raises(ValueError):upgraded(prior,tmp_path)


@pytest.mark.parametrize('change',['extra_code','old_code','request','response','budget'])
def test_exact_code_and_all_original_journals_remain_bound(prior,tmp_path,monkeypatch,change):
    s,c,code,_=prior
    if change=='extra_code':
        p=tmp_path/'fake-current.py';p.write_bytes(Path(A.__file__).read_bytes()+b'\n');monkeypatch.setattr(A,'__file__',str(p))
    elif change=='old_code':Path(code['path']).write_bytes(Path(code['path']).read_bytes()+b'\n')
    else:
        meta=N.read(s.bindings['variable_source_metadata']);coverage=json.loads(Path(meta['coverage_path']).read_text())
        item=meta['attempt_policy'] if change=='budget' else coverage['batches'][0][change]
        path=Path(item['path']);path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError):upgraded(prior,tmp_path)


def test_existing_normalization_call_and_current_projection_survive_exact_inventory_upgrade(prior,tmp_path,monkeypatch):
    s,c,code,_=prior
    # Construct the synthetic original call before the implementation transition;
    # the current functions run unchanged and no historical code is executed.
    with monkeypatch.context() as m:
        m.setattr(A,'__file__',code['path'])
        prepared=N.prepare(s);mock_review(m,s)
        call=N.dispatch(s,prepared,c,credential='fixture-unused').call
        emitted=N.read(N.emit_phase1(s,prepared,call))
    before={k:Path(v['path']).read_bytes() for k,v in asdict(call).items() if v}
    value,receipt,b=upgraded(prior,tmp_path)
    for key,name in [('normalization_draft','draft.json'),('normalized_final','normalized.json'),('normalization_report','normalization_report.json')]:b[key]=record(s.run/name)
    b['normalized_phase1']=emitted['phase1']
    content=SimpleNamespace(workbook=s.workbook,normalization_call=call,normalization_prepared=prepared,
        normalization_run=s.run,bindings=b,model_policy=s.policy)
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('no replay'))
    assert normalization_projection(content,phase1=True)['semantic_attempt']==1
    assert all(Path(asdict(call)[k]['path']).read_bytes()==raw for k,raw in before.items())
    assert N.read(call.submitted)['semantic_ordinal']==1
