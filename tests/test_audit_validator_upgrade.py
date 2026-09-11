"""Synthetic retained-journal fixtures: no old code execution or model calls."""
import copy
import json
from pathlib import Path
import pytest
import xl_variable_source_audit as audit
from xl_audit_inventory_upgrade import restore_inventory_lookup
import xl_audit_validator_upgrade as upgrade
import xl_audit_equivalence as eq
from grader.tests.test_variable_source_audit import GOOD_MODEL_TABLE, write_two_variable_fixture


def save(path,value):
    path.write_text(json.dumps(value,sort_keys=True)+'\n');return upgrade.record(path)


@pytest.fixture
def case(tmp_path,monkeypatch):
    artifact,segment=write_two_variable_fixture(tmp_path)
    original=tmp_path/'reviewed-original-generator.py'
    before=restore_inventory_lookup(Path(audit.__file__).read_bytes()).replace(upgrade.LABEL_RENDERING[1],upgrade.LABEL_RENDERING[0],1)
    for guard in upgrade.GUARDS:before=before.replace(guard,b'',1)
    original.write_bytes(before)
    actual_scope=audit._attempt_scope_config
    def old_scope(*args):
        result=actual_scope(*args);result['generator_sha256']=upgrade.ORIGINAL_SHA256;return result
    # Construct a synthetic saved original journal through the current producer.
    # It is fixture evidence, not a historical provider invocation.
    with monkeypatch.context() as patch:
        patch.setattr(audit,'_attempt_scope_config',old_scope)
        patch.setattr(audit,'_call_model',lambda *a,**k:(GOOD_MODEL_TABLE,'stop'))
        output=tmp_path/'audit';audit.generate_audit('0001',artifact,segment,output,'synthetic-unused')
    prefix=output/'0001-inputs-variable-sources'
    meta=Path(str(prefix)+'.metadata.json');value=json.loads(meta.read_text());value['generator_sha256']=upgrade.ORIGINAL_SHA256;save(meta,value)
    bindings={k:upgrade.record(Path(str(prefix)+suffix)) for k,suffix in zip(upgrade.AUDIT_KEYS,('.metadata.json','.inventory.json','.md'))}
    return bindings,upgrade.record(original),output


def propose(case):return upgrade.propose_upgrade(case[0],original_code=case[1])


def test_complete_original_history_current_checks_no_mutation(case,tmp_path):
    before={p:p.read_bytes() for p in case[2].rglob('*') if p.is_file()}
    result=propose(case)
    assert result['coverage']['inventory_rows']==2 and result['retained_consumption']['submitted_counts']=={'1':1}
    assert result['semantic_attempts_added']==0 and result['source_approval'] is False
    receipt=save(tmp_path/'upgrade.json',result)
    checked=upgrade.validate_upgrade(receipt,bindings=case[0],original_code=case[1])
    assert receipt['path'] in checked
    assert all(p.read_bytes()==v for p,v in before.items())
    assert result['identity']['historical_code_executed'] is False


def test_normal_cache_stays_closed(case):
    paths=[case[0][k]['path'] for k in upgrade.AUDIT_KEYS]
    with pytest.raises(audit.AuditRevalidationRequired):audit.validate_saved_audit_coverage(*paths)
    assert propose(case)['coverage']['passed']


@pytest.mark.parametrize('change',['missing_guard','extra_code','changed_prompt','changed_body','duplicate_guard'])
def test_only_exact_reviewed_module_transition_supported(case,tmp_path,monkeypatch,change):
    raw=Path(audit.__file__).read_bytes()
    if change=='missing_guard':raw=raw.replace(upgrade.GUARDS[0],b'',1)
    elif change=='duplicate_guard':raw+=upgrade.GUARDS[0]
    elif change=='changed_prompt':raw=raw.replace(b'You are',b'You were',1)
    elif change=='changed_body':raw=raw.replace(b'limit not in (1, 2)',b'limit not in (1, 3)',1) if b'limit not in (1, 2)' in raw else raw+b'#different\n'
    else:raw+=b'raise RuntimeError("must never execute retained code")\n'
    p=tmp_path/'changed-current.py';p.write_bytes(raw);monkeypatch.setattr(audit,'__file__',str(p))
    with pytest.raises(ValueError,match='reviewed|beyond'):propose(case)


def test_unreviewed_original_code_cannot_be_selected_by_receipt(case,tmp_path):
    p=tmp_path/'not-original.py';p.write_text('raise RuntimeError("must not execute")')
    with pytest.raises(ValueError,match='unreviewed original'):upgrade.propose_upgrade(case[0],original_code=upgrade.record(p))


@pytest.mark.parametrize('field',['model','endpoint','project_id','generator_version','prompt_version','system_prompt_sha256','request_config_sha256'])
def test_changed_original_config_is_not_hidden_by_upgrade(case,field):
    b=case[0];p=Path(b['variable_source_metadata']['path']);m=json.loads(p.read_text());m[field]='changed';b['variable_source_metadata']=save(p,m)
    with pytest.raises((ValueError,audit.AuditRevalidationRequired)):propose(case)


@pytest.mark.parametrize('finish',['length','content_filter','',None,True])
def test_complete_looking_truncated_response_rejected_after_rehash(case,finish):
    b=case[0];mp=Path(b['variable_source_metadata']['path']);m=json.loads(mp.read_text());cp=Path(m['coverage_path']);c=json.loads(cp.read_text());batch=c['batches'][0]
    rp=Path(batch['response']['path']);r=json.loads(rp.read_text());r['finish_reason']=finish
    batch['response']=save(rp,r);batch['response_history'][-1]=batch['response']
    m['coverage_sha256']=save(cp,c)['sha256'];b['variable_source_metadata']=save(mp,m)
    with pytest.raises(ValueError,match='completion is incomplete'):propose(case)


@pytest.mark.parametrize('field',['scope','messages','history','response_history','limit','submitted_counts'])
def test_original_journal_and_budget_cannot_be_reset(case,field):
    b=case[0];mp=Path(b['variable_source_metadata']['path']);m=json.loads(mp.read_text());cp=Path(m['coverage_path']);c=json.loads(cp.read_text());batch=c['batches'][0]
    if field in ['limit','submitted_counts']:
        p=Path(m['attempt_policy']['path']);v=json.loads(p.read_text());v[field]=3 if field=='limit' else {'1':0};m['attempt_policy']=save(p,v)
    elif field in ['history','response_history']:batch[field]=[]
    else:
        p=Path(batch['request']['path']);v=json.loads(p.read_text());v[field]={} if field=='scope' else [];batch['request']=save(p,v);batch['history'][-1]=batch['request']
        rp=Path(batch['response']['path']);r=json.loads(rp.read_text());r['request_sha256']=batch['request']['sha256'];batch['response']=save(rp,r);batch['response_history'][-1]=batch['response']
    m['coverage_sha256']=save(cp,c)['sha256'];b['variable_source_metadata']=save(mp,m)
    with pytest.raises(ValueError):propose(case)


def test_receipt_cannot_choose_other_original_or_binding(case,tmp_path):
    receipt=propose(case);p=tmp_path/'receipt.json';binding=save(p,receipt)
    other=copy.deepcopy(case[0]);other['variable_source_audit']['sha256']='0'*64
    with pytest.raises(ValueError):upgrade.validate_upgrade(binding,bindings=other,original_code=case[1])
    receipt['semantic_attempts_added']=1;binding=save(p,receipt)
    with pytest.raises(ValueError,match='stale or altered'):upgrade.validate_upgrade(binding,bindings=case[0],original_code=case[1])


@pytest.fixture
def equivalence_case(tmp_path,monkeypatch):
    from test_audit_equivalence import case as base_fixture
    actual=audit._attempt_scope_config
    def old(*args):
        scope=actual(*args);scope['generator_sha256']=upgrade.ORIGINAL_SHA256;return scope
    monkeypatch.setattr(audit,'_attempt_scope_config',old)
    result=base_fixture.__wrapped__(tmp_path,monkeypatch)
    monkeypatch.setattr(audit,'_attempt_scope_config',actual)
    bindings=result[0];p=Path(bindings['variable_source_metadata']['path']);m=json.loads(p.read_text());m['generator_sha256']=upgrade.ORIGINAL_SHA256;bindings['variable_source_metadata']=save(p,m)
    code=restore_inventory_lookup(Path(audit.__file__).read_bytes()).replace(upgrade.LABEL_RENDERING[1],upgrade.LABEL_RENDERING[0],1)
    for guard in upgrade.GUARDS:code=code.replace(guard,b'',1)
    path=tmp_path/'original.py';path.write_bytes(code);original=upgrade.record(path)
    receipt=save(tmp_path/'upgrade.json',upgrade.propose_upgrade(bindings,original_code=original))
    return result,original,receipt


def test_explicit_equivalence_consumer_revalidates_upgrade_and_current_pair(equivalence_case,tmp_path):
    (old,new,output,calls),original,receipt=equivalence_case
    with pytest.raises(audit.AuditRevalidationRequired):eq.propose_audit_equivalence(old,new)
    result=eq.propose_audit_equivalence(old,new,validator_upgrade=receipt,original_auditor=original)
    assert calls==[True,False] and result['validator_upgrade']==receipt
    path=tmp_path/'new-equivalence.json';bound=save(path,result)
    with pytest.raises(audit.AuditRevalidationRequired):eq.validate_audit_equivalence(path,current_bindings=new)
    checked=eq.validate_audit_equivalence(path,current_bindings=new,validator_upgrade=receipt,original_auditor=original)
    assert receipt['path'] in checked and bound['path'] in checked
    from scripts.qualified_publication import validate_audit_bindings
    current={**new,**{k:old[k] for k in upgrade.AUDIT_KEYS},'audit_equivalence':bound,
             'audit_validator_upgrade':receipt,'audit_upgrade_original_code':original}
    assert bound['path'] in validate_audit_bindings(current)


def test_upgrade_does_not_bypass_current_generation_or_financial_changes(equivalence_case,monkeypatch):
    (old,new,output,calls),original,receipt=equivalence_case
    def unavailable(*a,**k):raise ValueError('current strict reconstruction failed')
    monkeypatch.setattr(eq,'_context',unavailable)
    with pytest.raises(ValueError,match='strict reconstruction'):
        eq.propose_audit_equivalence(old,new,validator_upgrade=receipt,original_auditor=original)


def test_upgrade_requires_root_selected_original_and_complete_pair(equivalence_case):
    (old,new,output,calls),original,receipt=equivalence_case
    with pytest.raises(ValueError,match='selected together'):eq.propose_audit_equivalence(old,new,validator_upgrade=receipt)
