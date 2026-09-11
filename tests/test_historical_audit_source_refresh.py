"""Real tiny immutable generations and retained journals; no private corpus.

HTTP/dependency probes are fixture doubles. Source/AST/segmentation, audit
coverage, original transport provenance, source comparison and receipt checks
are production implementations, with comment-only current code witnesses.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import shutil
import pytest
import xl_audit_historical_context as H
import xl_audit_equivalence as Q
import xl_audit_inventory_upgrade as I
import xl_audit_validator_upgrade as U
import xl_variable_source_audit as A
from scripts.batch.harbor_admission import read,record
from scripts.batch import harbor_environment_worker as W
from scripts.batch import harbor_model_executor as E
from test_harbor_admission import setup
from test_harbor_environment_worker import worker,responses
from test_harbor_workbook_bootstrap import selected
from test_audit_renderer_recovery import retained
from test_audit_inventory_lineage import audit_chain


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(E.canonical(value));return record(path)


@pytest.fixture
def direct(worker,monkeypatch,request):
    s,lease=worker;seen=responses(monkeypatch)
    code=s.run.parent/'before-streaming.py';code.write_bytes(I.restore_inventory_lookup(Path(A.__file__).read_bytes()))
    pending=W.advance(s,lease);assert pending['status']=='PENDING_MODEL'
    plan=read(record(s.run/'audit-plan.json'));b=read(plan['baseline'])['bindings']
    inv=Q._context(b,historical=False)[2];output=s.run.parent/'retained56'
    actual=A._attempt_scope_config
    def old_scope(*args):
        value=actual(*args);value['generator_sha256']=I.BEFORE_SHA256;return value
    def complete(*args):
        seen.append(args)
        table='| Variable | Inventory row IDs | Plausible external source(s) |\n|---|---|---|\n'+'\n'.join('| Supplied assumption | '+r['row_id']+' | Supplied workbook |' for r in inv['variables'])
        if getattr(request,'param',False) and len(seen)==1:table=table.replace('R0001','R9999')
        return table,'stop'
    with monkeypatch.context() as m:
        m.setattr(A,'_attempt_scope_config',old_scope);m.setattr(A,'_call_model',complete)
        A.generate_audit('0001',b['baseline_inputs']['path'],b['segmentation_generation']['path'],output,'fixture',segmentation=inv['metadata']['segmentation'])
    for key,suffix in zip(Q.AUDIT_KEYS,('.metadata.json','.inventory.json','.md')):b[key]=record(output/('0001-inputs-variable-sources'+suffix))
    path=Path(b['variable_source_metadata']['path']);value=read(b['variable_source_metadata']);value['generator_sha256']=I.BEFORE_SHA256;b['variable_source_metadata']=save(path,value)
    receipt=save(s.run.parent/'original-upgrade.json',U.propose_upgrade(b,original_code=record(code)))
    return b,receipt,record(code),seen


def fresh(old,upgrade,code,tmp_path,monkeypatch):
    from xl_source_recalc import prepare_source_generation
    import xl_segment,xl_input_mask
    paths=Q.seg_pub.verifier_paths();old_code=[];new_code=[]
    for p in paths:
        retained=tmp_path/'old-code'/p.name;retained.parent.mkdir(exist_ok=True);retained.write_bytes(p.read_bytes());old_code.append(record(retained))
        current=tmp_path/'current-code'/p.name;current.parent.mkdir(exist_ok=True);current.write_bytes(p.read_bytes()+(b'\n# synthetic code-only refresh\n' if p.name in ('xl_ast_graph.py','evaluate.py') else b''));new_code.append(current)
    # Preserve the live builder inventory. A code-only refresh must not quietly
    # remove its new disk-storage dependency from the provenance fixture.
    current_by_name={p.name:p for p in new_code};builder_code=[]
    for original in Q.source_pub._default_builder_paths():
        if original.name not in current_by_name:
            current=tmp_path/'current-code'/original.name
            current.write_bytes(original.read_bytes());current_by_name[original.name]=current
        builder_code.append(current_by_name[original.name])
    monkeypatch.setattr(Q.seg_pub,'verifier_paths',lambda:tuple(new_code))
    monkeypatch.setattr(Q.source_pub,'_default_builder_paths',lambda:tuple(builder_code))
    source=Path(old['original_raw_source']['path']);wb='0001';out=tmp_path/'fresh'
    directory,manifest=prepare_source_generation(source,wb,out/'sources'/wb)
    ast=directory/manifest['layout']['ast_directory']
    cur=out/'segments'/wb/'curation.toml';cur.parent.mkdir(parents=True);cur.write_bytes((Path(old['segmentation_generation']['path'])/'curation.toml').read_bytes())
    xl_segment.segment(wb,SimpleNamespace(source_generation_path=str(directory),source_generation_id=None,ast_dir=str(ast.parent),source=str(source.parent),out=str(out/'segments'),threshold=6,top=40,sample=400,lineage_max=4000,recurate=False,llm=False,no_auto_adjudication=True,no_verify=False,allow_legacy_ast=False))
    segments=list((cur.parent/'generations').iterdir());assert len(segments)==1;sg=segments[0]
    xl_input_mask.main([wb,'--source-generation-root',str(out/'sources'),'--source-generation-id',directory.name,'--segmentation-generation-id',sg.name,'--seg-dir',str(out/'segments'),'--out',str(out/'inputs')])
    def gen(p):return dict(path=str(p),id=p.name,manifest_sha256=Q.digest(p/'generation-manifest.json'))
    masked=out/'inputs'/(wb+'-inputs.xlsx')
    b={**old,'source_generation':gen(directory),'segmentation_generation':gen(sg),'source_file':record(directory/manifest['layout']['source_workbook']),
       'baseline_inputs':record(masked),'baseline_segmentation':record(Q.seg_pub.inputs_sidecar_path(masked)),'curation':record(cur)}
    return H.Selection(old,b,upgrade,code,tuple(old_code))


@pytest.fixture(params=['direct','renderer'])
def context(request,tmp_path,monkeypatch):
    if request.param=='direct':old,upgrade,code,seen=request.getfixturevalue('direct')
    else:
        _,old,_,_,_,seen=request.getfixturevalue('audit_chain');upgrade=old['audit_validator_upgrade'];code=old['audit_upgrade_original_code']
    return fresh(old,upgrade,code,tmp_path,monkeypatch),seen


def test_real_new_pair_reuses_only_complete_historical_evidence(context,tmp_path,monkeypatch):
    s,seen=context;calls=len(seen)
    before={k:Path(s.old_bindings[k]['path']).read_bytes() for k in Q.AUDIT_KEYS}
    # Ordinary current validation must continue to reject the old builder.
    with pytest.raises(ValueError):U.validate_upgrade(s.prior_upgrade,bindings=s.old_bindings,original_code=s.original_auditor)
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('no call permitted'))
    value=H.propose(s);receipt=save(tmp_path/'new-applicability.json',value)
    assert value['source_builder_equivalence']['semantic_ast_bytes_unchanged']
    assert value['retained_consumption']['submitted_counts']=={'1':1}
    assert len(value['differences'])==3 and value['semantic_attempts_added']==0
    assert value['model_reexecuted'] is False and value['historical_code_executed'] is False
    assert len(seen)==calls and all(Path(s.old_bindings[k]['path']).read_bytes()==v for k,v in before.items())
    assert H.validate(receipt,selection=s)[receipt['path']]==receipt['sha256']


@pytest.mark.parametrize('what',['source','ast','curation','baseline','raw_response','budget','old_code','missing_code','code_order','upgrade','target'])
def test_changed_bound_evidence_rejected(context,what):
    s,_=context
    if what=='missing_code':s=replace(s,historical_verifier_code=s.historical_verifier_code[:-1])
    elif what=='code_order':s=replace(s,historical_verifier_code=tuple(reversed(s.historical_verifier_code)))
    else:
        if what=='source':p=Path(s.current_bindings['source_file']['path'])
        elif what=='ast':
            d=Path(s.current_bindings['source_generation']['path']);m=Q.read(d/'generation-manifest.json');p=d/m['layout']['ast_directory']/'nodes.csv'
        elif what=='curation':p=Path(s.current_bindings['segmentation_generation']['path'])/'curation.toml'
        elif what=='baseline':p=Path(s.current_bindings['baseline_inputs']['path'])
        elif what=='old_code':p=Path(s.historical_verifier_code[0]['path'])
        elif what=='upgrade':p=Path(s.prior_upgrade['path'])
        elif what=='target':p=Path(s.current_bindings['segmentation_generation']['path'])/'segments.json'
        else:
            meta=read(s.old_bindings['variable_source_metadata']);coverage=Q.read(meta['coverage_path'])
            p=Path((meta['attempt_policy'] if what=='budget' else coverage['batches'][0]['response'])['path'])
        p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError):H.propose(s)


def test_current_proof_failure_cannot_be_replaced_by_historical_proof(context,monkeypatch):
    s,_=context;actual=Q._context
    def checked(bindings,*,historical):
        if not historical:raise ValueError('current reconstruction failed')
        return actual(bindings,historical=historical)
    monkeypatch.setattr(Q,'_context',checked)
    with pytest.raises(ValueError,match='current reconstruction failed'):H.propose(s)


def test_receipt_cannot_select_another_current_pair(context,tmp_path):
    s,_=context;r=save(tmp_path/'r.json',H.propose(s));changed=copy.deepcopy(s.current_bindings);changed['baseline_inputs']['sha256']='0'*64
    with pytest.raises(ValueError):H.validate(r,selection=replace(s,current_bindings=changed))


def test_extra_original_request_is_not_hidden(context):
    s,_=context;m=read(s.old_bindings['variable_source_metadata']);p=Path(m['attempt_policy']['path']).parent/'batch-extra'
    p.mkdir();(p/'99.request.json').write_text('{}')
    with pytest.raises(ValueError):H.propose(s)


@pytest.mark.parametrize('what',['target_order','curation','input_value','recalc_provenance'])
def test_even_validated_but_different_financial_context_is_rejected(context,monkeypatch,what):
    s,_=context;actual=Q._context
    def projected(bindings,*,historical):
        c,m,i=actual(bindings,historical=historical)
        if not historical:
            m=copy.deepcopy(m);i=copy.deepcopy(i)
            if what=='target_order':m['curated_output_cells']['ordered']=['Sheet!A1']
            elif what=='curation':m['artifacts']['curation.toml']['sha256']='0'*64
            elif what=='input_value':i['variables'][0]['value_summary']={'count':1,'value':9}
            else:i['metadata']['segmentation']['recalc_signals_sha256']='1'*64
        return c,m,i
    monkeypatch.setattr(Q,'_context',projected)
    with pytest.raises(ValueError):H.propose(s)


def test_identity_request_cannot_be_replaced_by_recalculation(context):
    s,_=context;d=Path(s.current_bindings['source_generation']['path']);p=d/'request.json'
    value=Q.read(p);value['schema_version']='source-recalc-request/v2';p.chmod(0o600);p.write_bytes(E.canonical(value))
    with pytest.raises(ValueError,match='native identity'):H._native_pair(s.old_bindings,s.current_bindings)
    with pytest.raises(ValueError):H.propose(s)


def test_missing_completed_response_rejected(context):
    s,_=context;m=read(s.old_bindings['variable_source_metadata']);c=Q.read(m['coverage_path']);Path(c['batches'][0]['response']['path']).unlink()
    with pytest.raises((ValueError,OSError)):H.propose(s)


def test_rehashed_foreign_upgrade_is_not_accepted(context,tmp_path):
    s,_=context;v=read(s.prior_upgrade);v['semantic_attempts_added']=0;v['invented_approval']=True
    changed=replace(s,prior_upgrade=save(tmp_path/'forged.json',v))
    with pytest.raises(ValueError):H.propose(changed)


def test_unreviewed_reader_source_rejected(context,tmp_path,monkeypatch):
    s,_=context;p=tmp_path/'xl_audit_validator_upgrade.py';p.write_bytes(Path(U.__file__).read_bytes()+b'\n')
    monkeypatch.setattr(U,'__file__',str(p))
    with pytest.raises(ValueError,match='reader implementation'):H.propose(s)


def test_historical_proof_cannot_serve_as_current_generation(context):
    s,_=context
    with pytest.raises(ValueError,match='fresh source builder'):H.propose(replace(s,current_bindings=s.old_bindings))


@pytest.mark.parametrize('kind',['direct','renderer'])
def test_both_original_rounds_remain_consumed(worker,tmp_path,monkeypatch,kind):
    if kind=='direct':old,upgrade,code,seen=direct.__wrapped__(worker,monkeypatch,SimpleNamespace(param=True))
    else:
        r=retained.__wrapped__(worker,monkeypatch,SimpleNamespace(param=True))
        _,old,_,_,_,seen=audit_chain.__wrapped__(r,tmp_path)
        upgrade=old['audit_validator_upgrade'];code=old['audit_upgrade_original_code']
    s=fresh(old,upgrade,code,tmp_path,monkeypatch);before=len(seen);value=H.propose(s)
    assert value['retained_consumption']['submitted_counts']=={'1':2}
    assert value['semantic_attempts_added']==0 and len(seen)==before==2


def test_other_scope_cannot_hide_unknown_call_or_reset_budget(context):
    s,_=context;m=read(s.old_bindings['variable_source_metadata']);p=Path(m['attempt_policy']['path']).parent.parent/'unaccounted-scope/batch-0001'
    p.mkdir(parents=True);(p/'01.request.json').write_text('{}')
    with pytest.raises(ValueError):H.propose(s)
