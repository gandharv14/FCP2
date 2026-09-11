"""Complete synthetic issued-renderer → locator → two-attempt review chain.

Only fixture creation projects historical code identities. Actual current
validators independently recheck the wrappers. No historical code executes.
"""
from pathlib import Path
from dataclasses import asdict,replace
from types import SimpleNamespace
import copy
import json
import uuid
import pytest
import xl_variable_source_audit as A
import xl_audit_inventory_upgrade as I
import xl_audit_inventory_lineage as L
import xl_audit_renderer_recovery as R
import xl_audit_equivalence as Q
import xl_audit_validator_upgrade as U
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_recovery as F
from scripts.batch import harbor_normalization_inventory_applicability as P
from scripts.batch import harbor_normalization_audit_applicability as APP
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read,record
from test_audit_renderer_recovery import retained
from test_harbor_admission import setup
from test_harbor_environment_worker import selected,worker,responses
from test_harbor_normalization_producer import mock_review


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(E.canonical(value));return record(path)


@pytest.fixture
def audit_chain(retained,tmp_path):
    r,s,lease,seen=retained
    issued=R.publish(r);old=read(issued)
    # Synthetic historical issued artifacts, preserving the actual fixture Calls.
    old['report']['identity']['current_generator']['sha256']=I.BEFORE_SHA256
    old['report']['identity']['adapter']['sha256']=L.RENDERER_BEFORE
    lookup=old['report']['identity'].pop('inventory_lookup_code')
    old['report']['checked_files'].pop(lookup['path'])
    for item in (old['report']['identity']['current_generator'],old['report']['identity']['adapter']):old['report']['checked_files'][item['path']]=item['sha256']
    metadata=old['audit_bindings']['variable_source_metadata'];path=Path(metadata['path']);value=read(metadata);value['rendering_generator_sha256']=I.BEFORE_SHA256
    path.write_text(json.dumps(value,indent=2)+'\n');binding=record(path);old['audit_bindings']['variable_source_metadata']=binding;old['outputs'][path.name]=binding
    issued=save(Path(issued['path']),old)
    b={**old['report']['original_bindings'],**old['audit_bindings']}
    fixtures=Path(__file__).parent/'fixtures/audit_inventory_before'
    old_r=record(fixtures/'xl_audit_renderer_recovery.py.txt');old_q=record(fixtures/'xl_audit_equivalence.py.txt')
    wrapper=save(tmp_path/'audit-wrapper.json',L.propose_renderer(issued,bindings=b,original_code=r.original_code,original_renderer_code=old_r))
    fresh=Q.propose_audit_equivalence(b,b,validator_upgrade=wrapper,original_auditor=r.original_code)
    old_q_value=copy.deepcopy(fresh);old_q_value['adapter_sha256']=L.LOCATOR_BEFORE;old_q_value['auditor_sha256']=I.BEFORE_SHA256;old_q_value['validator_upgrade']=issued
    old_files=old_q_value['checked_files']
    for path in (wrapper['path'],old_r['path'],I.evidence()['path'],str(Path(L.__file__).resolve()),str(Path(U.__file__).resolve())):old_files.pop(path,None)
    for module,sha in ((A,I.BEFORE_SHA256),(R,L.RENDERER_BEFORE),(Q,L.LOCATOR_BEFORE)):old_files[str(Path(module.__file__).resolve())]=sha
    locator=save(tmp_path/'old-locator.json',old_q_value)
    new_locator=save(tmp_path/'locator-wrapper.json',L.propose_locator(locator,bindings=b,audit_applicability=wrapper,original_code=r.original_code,original_locator_code=old_q))
    old_b={**b,'audit_equivalence':locator,'audit_validator_upgrade':issued,'audit_upgrade_original_code':r.original_code}
    new_b={**b,'audit_equivalence':new_locator,'audit_validator_upgrade':wrapper,'audit_upgrade_original_code':r.original_code}
    return old_b,new_b,old_r,old_q,s,seen


def test_explicit_renderer_locator_wrappers_preserve_all_issued_bytes(audit_chain):
    old_b,new_b,old_r,old_q,s,seen=audit_chain;calls=len(seen)
    originals={r['path']:Path(r['path']).read_bytes() for r in old_b.values() if isinstance(r,dict) and set(r)=={'path','sha256'}}
    from scripts.qualified_publication import validate_audit_bindings
    checked=validate_audit_bindings(new_b)
    assert new_b['audit_equivalence']['path'] in checked
    assert len(seen)==calls and all(Path(p).read_bytes()==v for p,v in originals.items())
    with pytest.raises(ValueError):validate_audit_bindings(old_b)


@pytest.mark.parametrize('target',['original_renderer_code','original_locator_code','old_metadata','old_renderer_receipt','old_locator','wrapper','original_response'])
def test_each_issued_layer_and_original_call_is_currently_revalidated(audit_chain,target):
    old_b,new_b,old_r,old_q,s,seen=audit_chain
    bindings={'original_renderer_code':old_r,'original_locator_code':old_q,'old_metadata':old_b['variable_source_metadata'],'old_renderer_receipt':old_b['audit_validator_upgrade'],'old_locator':old_b['audit_equivalence'],'wrapper':new_b['audit_validator_upgrade']}
    if target=='original_response':
        old=read(old_b['audit_validator_upgrade']);binding=old['report']['calls'][0]['response']
    else:binding=bindings[target]
    path=Path(binding['path']);original=path.read_bytes()
    try:
        path.write_bytes(original+b' ')
        from scripts.qualified_publication import validate_audit_bindings
        with pytest.raises(ValueError):validate_audit_bindings(new_b)
    finally:path.write_bytes(original)


@pytest.fixture
def normalized(audit_chain,tmp_path,monkeypatch):
    old_b,new_b,old_r,old_q,worker,seen=audit_chain
    wb=worker.bootstrap.workbook;run=tmp_path/'normalization-first'/(wb+'-variable-sources')
    budget=N.Budget(1,2,str(uuid.uuid4()),(worker.prepared,))
    selection=N.Selection(wb,old_b,run,worker.bootstrap.model_policy,budget)
    old_q_value=read(old_b['audit_equivalence']);old_files={old_b[k]['path']:old_b[k]['sha256'] for k in U.AUDIT_KEYS};old_files.update(old_q_value['checked_files']);old_files[old_b['audit_equivalence']['path']]=old_b['audit_equivalence']['sha256']
    actual_codes=N.code_bindings;current=actual_codes();upgrades={};witnesses={}
    for name in F.UPGRADES:
        path=tmp_path/('synthetic-before-'+name+'.txt');path.write_text('Fixture historical bytes '+name)
        witnesses[name]=record(path);upgrades[name]=[record(path)['sha256'],next(v for k,v in current.items() if Path(k).name==name)]
    monkeypatch.setattr(F,'UPGRADES',upgrades)
    previous={k:upgrades[Path(k).name][0] if Path(k).name in upgrades else v for k,v in current.items()}
    # Fixture construction emulates the earlier validator's evidence list. The
    # wrapper under test subsequently runs every current audit/source gate.
    with monkeypatch.context() as m:
        m.setattr(N,'validate_audit_bindings',lambda *a,**k:old_files)
        with m.context() as first:
            first.setattr(N,'PROMPT',F.old_prompt());first.setattr(N,'code_bindings',lambda:previous)
            prepared=N.prepare(selection);mock_review(first,selection,change=lambda v:v.update(schema=v.pop('schema_version')))
            original=N.dispatch(selection,prepared,worker.campaign,credential='synthetic').call
        N.keep(run/'normalization-review.json',Path(original.parsed['path']).read_bytes())
        failure=save(tmp_path/'old-worker-failure.json',dict(automatic_retry=False,error_sha256='abf6742f443f82708972fdcd79d40a24e52b83ef3310fe734e7757c04038597b',error_type='SystemExit',source_invalidity_claimed=False,status='UNRESOLVED',step='normalization_phase1'))
        second,prepared2,pc,recovery=F.prepare_recovery(selection,prepared,original,worker.campaign,code_witnesses=witnesses,prior_worker_failure=failure,root=tmp_path/'format-recovery',new_run=tmp_path/'second'/run.name,attempt_id=str(uuid.uuid4()))
        mock_review(m,second);call=N.dispatch(second,prepared2,worker.campaign,credential='synthetic').call
        emitted=N.emit_phase1(second,prepared2,call)
    _,rows=N.analyse(second);args=dict(source=old_b['source_file'],baseline=old_b['baseline_inputs'],draft=record(second.run/'draft.json'),rows=rows,bindings=new_b,policy=second.policy,original_locator_code=old_q)
    result=P.propose(prepared2,call,**args,original_recovery_code=record(Path(F.__file__)))
    receipt=save(tmp_path/'normalization-wrapper.json',result)
    return second,prepared2,call,args,receipt,original,recovery,worker.campaign,emitted


def test_complete_retained_second_attempt_chain_and_current_phase1(normalized):
    from scripts.batch.harbor_completed_lanes import normalization_projection
    second,prepared,call,args,receipt,original,recovery,campaign,emitted=normalized
    assert APP.validate_applicability(receipt,prepared,call,**args)['review']==read(call.parsed)
    value=read(receipt);assert value['retained_ordinals']==[1,2] and value['limit']==2 and value['semantic_attempts_added']==0
    b={**args['bindings'],'normalization_audit_applicability':receipt,'normalization_original_locator_code':args['original_locator_code']}
    for key,name in [('normalization_draft','draft.json'),('normalized_final','normalized.json'),('normalization_report','normalization_report.json')]:b[key]=record(second.run/name)
    b['normalized_phase1']=read(emitted)['phase1']
    selected=SimpleNamespace(workbook=second.workbook,bindings=b,normalization_run=second.run,normalization_prepared=prepared,normalization_call=call,model_policy=second.policy)
    assert normalization_projection(selected,phase1=True)['semantic_attempt']==2
    assert read(original.parsed).get('schema_version') is None


@pytest.mark.parametrize('target',['first_response','second_response','recovery','first_failure','old_draft','second_draft','second_prepared','budget'])
def test_no_chain_layer_can_disappear_or_change(normalized,target):
    second,prepared,call,args,receipt,original,recovery,campaign,emitted=normalized
    proof=read(recovery)
    mapping=dict(first_response=original.response,second_response=call.response,recovery=recovery,first_failure=proof['prior_worker_failure'],old_draft=read(proof['prior_prepared'])['draft'],second_draft=args['draft'],second_prepared=prepared,budget=second.budget.evidence[0])
    binding=mapping[target];path=Path(binding['path']);path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError):APP.validate_applicability(receipt,prepared,call,**args)


@pytest.mark.parametrize('change',['drop_old_file','changed_original_source','target','consumption','current_code'])
def test_locator_continuity_is_not_general_code_or_evidence_exemption(audit_chain,change,tmp_path,monkeypatch):
    old_b,new_b,old_r,old_q,s,seen=audit_chain
    if change=='current_code':
        path=tmp_path/'xl_audit_equivalence.py';path.write_bytes(Path(Q.__file__).read_bytes()+b'\n')
        monkeypatch.setattr(Q,'__file__',str(path))
    else:
        previous=old_b['audit_equivalence'];value=read(previous)
        if change=='drop_old_file':
            key=next(k for k in value['checked_files'] if not k.endswith('.py'))
            value['checked_files'].pop(key)
        elif change=='consumption':value['retained_consumption']['submitted_counts']['1']=0
        elif change=='target':value['differences']={'changed':'target'}
        else:value['current_bindings']['original_raw_source']['sha256']='0'*64
        save(Path(previous['path']),value)
    from scripts.qualified_publication import validate_audit_bindings
    with pytest.raises(ValueError):validate_audit_bindings(new_b)


def test_rehashed_locator_cannot_omit_original_evidence(audit_chain,tmp_path):
    old_b,new_b,old_r,old_q,s,seen=audit_chain
    value=read(old_b['audit_equivalence'])
    value['checked_files'].pop(next(k for k in value['checked_files'] if not k.endswith('.py')))
    forged=save(tmp_path/'rehashed-incomplete-locator.json',value)
    with pytest.raises(ValueError,match='evidence inventory dropped'):
        L.propose_locator(forged,bindings=old_b,
            audit_applicability=new_b['audit_validator_upgrade'],
            original_code=old_b['audit_upgrade_original_code'],original_locator_code=old_q)
