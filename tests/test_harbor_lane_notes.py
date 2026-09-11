import copy
import json
from pathlib import Path
from types import SimpleNamespace
import time
import zipfile

import pytest
from openpyxl import Workbook
from test_harbor_admission import setup
from scripts.batch import harbor_admission as admission
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.corpus import inventory
from scripts.batch import harbor_lane_notes as n


def case(setup,cache=6,cycle=False):
    _,policy,history,source,tmp=setup
    wb=Workbook();wb.calculation.calcMode='auto';wb.calculation.fullCalcOnLoad=False
    wb.active['A1']=3;wb.active['B1']='=B1' if cycle else '=A1*2'
    if cycle:wb.calculation.iterate=True;wb.calculation.iterateCount=100;wb.calculation.iterateDelta=0.001
    wb.save(source);wb.close()
    with zipfile.ZipFile(source) as z:parts={x:z.read(x) for x in z.namelist()}
    parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'<v></v>',f'<v>{cache}</v>'.encode()).replace(b'<v />',f'<v>{cache}</v>'.encode())
    with zipfile.ZipFile(source,'w') as z:
        for x,y in parts.items():z.writestr(x,y)
    inv=tmp/'new-inventory.json';inventory([source.parent],inv,1)
    object.__setattr__(policy,'original_inventory',n.record(inv))
    admitted=admission.DispatcherAdmission(policy).create('0001')
    path=policy.tracker_root/'0001.json'
    def authorize(op,p,b,request):
        return op=='acquire' and p==str(path) and b['revision']==0 and request['owner']=='test-agent' and request['scope']=='workbook:0001'
    lease=lifecycle.lease_operation(path,authority=lifecycle.RootAuthority('harbor-orchestrator',10**12,authorize),operation='acquire',lease_kind='task_agent',owner='test-agent',scope='workbook:0001',duration_ns=10**12,expected_revision=admitted['revision'],expected_sha256=admitted['sha256'])
    facade=n.LaneNotes(n.RootContext(policy.tracker_root));token=lease['tracker']['leases']['task_agent']['token']
    from xl_source_recalc import prepare_source_generation
    gen,_=prepare_source_generation(source,'0001',tmp/'source-out/0001')
    packet=n.GateEvidence('source',n.record(gen/'generation-manifest.json'))
    ready=facade.complete('0001',packet,**kw(lease,token))
    from xl_seg import bands,model,project
    source_manifest=n.read(packet.manifest);ast=gen/source_manifest['layout']['ast_directory']
    bg=bands.build(project.build(model.load(ast,'0001')))
    target=next(b for b in bg.bands.values() if 'Sheet!B1' in b.cells)
    out=tmp/'seg';(out/'0001').mkdir(parents=True)
    (out/'0001/curation.toml').write_text('[[output]]\nband = '+json.dumps(target.id)+'\ninclude = true\n')
    from xl_segment import segment
    segment('0001',SimpleNamespace(source_generation_path=str(gen),source_generation_id=None,ast_dir=str(ast.parent),source=str(source.parent),out=str(out),threshold=6,top=40,sample=400,lineage_max=4000,recurate=False,llm=False,no_auto_adjudication=True,no_verify=False,allow_legacy_ast=False))
    generations=list((out/'0001/generations').iterdir());assert len(generations)==1
    seg=n.GateEvidence('segmentation',n.record(generations[0]/'generation-manifest.json'))
    return facade,path,ready,token,packet,seg


def kw(result,token):
    return dict(expected_revision=result['revision'],expected_sha256=result['sha256'],owner='test-agent',token=token)


def test_real_pass_generations_derive_owned_bindings(setup):
    f,p,start,token,source,seg=case(setup)
    result=f.complete('0001',seg,**kw(start,token));t=result['tracker']
    assert t['lane_state'][n.SOURCE]['state']==t['lane_state'][n.SEGMENTATION]['state']=='passed'
    assert t['bindings']['curation']==n.record(Path(seg.manifest['path']).parent/'curation.toml')
    assert all(row['attempt']==0 for row in t['lane_state'].values())
    assert t['promotion_ids']==start['tracker']['promotion_ids'] and t['terminal_verdict'] is None


def test_real_mismatch_cannot_pass_and_records_exact_notes(setup):
    f,p,start,token,source,seg=case(setup,99)
    with pytest.raises(ValueError,match='did not pass'):f.complete('0001',seg,**kw(start,token))
    result=f.record_failure('0001',seg,**kw(start,token));t=result['tracker']
    assert t['promotion_failure']['subclass']=='cached_mismatch'
    assert t['execution_state']=='running' and t['promotion_failure']['fixability']=='fixable'
    assert n.validate_notes_pair(t,Path(t['agent_notes']['path']).read_bytes())
    assert b'=A1*2' not in Path(t['agent_notes']['path']).read_bytes()
    assert t['leases']['task_agent']['token']==token

class Interrupted(Exception):pass

def pending(f,p,start,token,seg,where):
    def fault(stage):
        if stage==where:raise Interrupted(stage)
    with pytest.raises(Interrupted):f.record_failure('0001',seg,**kw(start,token),_fault=fault)
    descriptors=list((p.parent/'0001-notes-transactions').glob('*/transaction.json'))
    assert len(descriptors)==1
    return n.record(descriptors[0])


@pytest.mark.parametrize('where',['prepared','notes_replaced','tracker_replaced'])
def test_interrupted_pair_exact_recovery_retains_lease(setup,where):
    f,p,start,token,source,seg=case(setup,99)
    journal=pending(f,p,start,token,seg,where)
    with pytest.raises(ValueError,match='pending notes'):f.require_synchronized('0001')
    result=f.recover('0001',journal,owner='test-agent',token=token)
    assert result['revision']==start['revision']+1
    assert result['tracker']['leases']['task_agent']['token']==token
    assert f.require_synchronized('0001')
    assert f.recover('0001',journal,owner='test-agent',token=token)==result
    assert all(v['attempt']==0 for v in result['tracker']['lane_state'].values())


@pytest.mark.parametrize('change',['owner','token','revision','sha','expired'])
def test_failed_lease_or_cas_never_writes_pair(setup,monkeypatch,change):
    f,p,start,token,source,seg=case(setup,99);raw=p.read_bytes();args=kw(start,token)
    if change=='owner':args['owner']='other'
    elif change=='token':args['token']='wrong'
    elif change=='revision':args['expected_revision']-=1
    elif change=='sha':args['expected_sha256']='0'*64
    else:monkeypatch.setattr(n.time,'time_ns',lambda:start['tracker']['leases']['task_agent']['expires_at'])
    with pytest.raises(ValueError):f.record_failure('0001',seg,**args)
    assert p.read_bytes()==raw and not p.with_name('0001-agent-notes.md').exists()


@pytest.mark.parametrize('change',['owner','token','tracker','notes','evidence','prepared_transition','completion'])
def test_recovery_rejects_divergence_and_forged_prepared_evidence(setup,change):
    f,p,start,token,source,seg=case(setup,99);journal=pending(f,p,start,token,seg,'notes_replaced')
    descriptor=n.read(journal);args=dict(owner='test-agent',token=token)
    if change in ('owner','token'):args[change]='other'
    elif change=='tracker':
        t=json.loads(p.read_text());t['revision']+=1;p.write_bytes(lifecycle.canonical(t))
    elif change=='notes':p.with_name('0001-agent-notes.md').write_text('unrelated new notes')
    elif change=='evidence':
        manifest=Path(seg.manifest['path']);manifest.chmod(0o600);manifest.write_text('{}')
    elif change=='prepared_transition':
        after=n.read(descriptor['after']);after['lane_state'][n.SOURCE]['attempt']=123
        Path(descriptor['after']['path']).write_bytes(lifecycle.canonical(after))
        descriptor['after']=n.record(Path(descriptor['after']['path']))
        Path(journal['path']).write_bytes(lifecycle.canonical(descriptor));journal=n.record(Path(journal['path']))
    elif change=='completion':Path(journal['path']).with_name('complete.json').write_text('{}')
    raw=p.read_bytes();notes=p.with_name('0001-agent-notes.md').read_bytes()
    with pytest.raises(ValueError):f.recover('0001',journal,**args)
    assert p.read_bytes()==raw and p.with_name('0001-agent-notes.md').read_bytes()==notes


def test_recovery_after_expiry_only_finishes_bookkeeping(setup,monkeypatch):
    f,p,start,token,source,seg=case(setup,99);journal=pending(f,p,start,token,seg,'prepared')
    old=start['tracker']['leases']['task_agent']
    monkeypatch.setattr(n.time,'time_ns',lambda:old['expires_at']+1)
    result=f.recover('0001',journal,owner='test-agent',token=token)
    current=result['tracker']['leases']['task_agent']
    assert current['expires_at']==old['expires_at'] and current['token']==token
    with pytest.raises(ValueError,match='expired'):f.record_failure('0001',seg,**kw(result,token))


def test_repeated_failure_preserves_first_occurrence_and_spent_budgets(setup):
    f,p,start,token,source,seg=case(setup,99)
    first=f.record_failure('0001',seg,**kw(start,token));second=f.record_failure('0001',seg,**kw(first,token))
    old=first['tracker']['promotion_failure'];new=second['tracker']['promotion_failure']
    assert new['first_seen']==old['first_seen'] and new['occurrence_count']==2
    assert new['diagnostic_signature']==old['diagnostic_signature']
    assert second['tracker']['failure_history'][0]==first['tracker']['failure_history'][0]
    assert second['tracker']['repairs']==start['tracker']['repairs']
    assert f.require_synchronized('0001')


@pytest.mark.parametrize('change',['passed_flag','source_bytes','curation','unknown_lane'])
def test_lane_proof_tampering_does_not_pass(setup,change):
    f,p,start,token,source,seg=case(setup);raw=p.read_bytes();packet=seg
    if change=='unknown_lane':packet=n.GateEvidence('naturalization',seg.manifest)
    elif change=='source_bytes':
        file=Path(start['tracker']['raw_source']['path']);file.write_bytes(b'changed original')
    else:
        gen=Path(seg.manifest['path']).parent
        if change=='curation':file=gen/'curation.toml';file.chmod(0o600);file.write_text('')
        else:
            file=gen/'segments.json';value=json.loads(file.read_text());value['verification']={'passed':True};file.chmod(0o600);file.write_text(json.dumps(value))
    with pytest.raises(ValueError):f.complete('0001',packet,**kw(start,token))
    assert p.read_bytes()==raw


def test_expiry_between_note_and_tracker_replacement_is_recoverable(setup,monkeypatch):
    f,p,start,token,source,seg=case(setup,99);raw=p.read_bytes()
    def fault(stage):
        if stage=='notes_replaced':monkeypatch.setattr(n.time,'time_ns',lambda:start['tracker']['leases']['task_agent']['expires_at'])
    with pytest.raises(ValueError,match='expired'):f.record_failure('0001',seg,**kw(start,token),_fault=fault)
    assert p.read_bytes()==raw
    journal=n.record(next((p.parent/'0001-notes-transactions').glob('*/transaction.json')))
    result=f.recover('0001',journal,owner='test-agent',token=token)
    assert result['tracker']['leases']['task_agent']['expires_at']==start['tracker']['leases']['task_agent']['expires_at']


def test_pending_journal_rechecked_inside_lane_cas_lock(setup,monkeypatch):
    f,p,start,token,source,seg=case(setup);raw=p.read_bytes()
    original=n.schema.cas_mutate
    def race(path,**kwargs):
        tx=p.parent/'0001-notes-transactions/pending';tx.mkdir(parents=True);(tx/'transaction.json').write_text('{}')
        return original(path,**kwargs)
    monkeypatch.setattr(n.schema,'cas_mutate',race)
    with pytest.raises(ValueError,match='pending notes'):f.complete('0001',seg,**kw(start,token))
    assert p.read_bytes()==raw


def test_actual_nonunique_recurrence_can_only_stop_with_bound_notes(setup):
    f,p,start,token,source,seg=case(setup,0,cycle=True)
    gate=n.check_gate(start['tracker'],seg,f.context)
    assert not gate['passed'] and gate['terminal']
    result=f.record_failure('0001',seg,**kw(start,token));t=result['tracker']
    assert t['execution_state']==t['final_status']==t['terminal_verdict']=='STOPPED'
    assert t['promotion_failure']['fixability']=='terminal'
    assert f.require_synchronized('0001')
    assert t['leases']['task_agent']['token']==token


def test_completed_journal_marker_cannot_hide_changed_note_payload(setup):
    f,p,start,token,source,seg=case(setup,99)
    result=f.record_failure('0001',seg,**kw(start,token))
    tx=Path(result['journal']['path']).parent;(tx/'notes.md').write_text('tampered history')
    with pytest.raises(ValueError):f.require_synchronized('0001')


def seed_spent_budget(path,start,token):
    def mutate(t):
        t['lane_state']['harbor-research-auditor']['attempt']=2
        t['repairs']['count']=1;t['repairs']['by_signature']['synthetic-prior-diagnostic']=1
        t['repairs']['history'].append({'signature':'synthetic-prior-diagnostic','consumed':1})
        t['scheduler']['attempts']=3
    return n.schema.cas_mutate(path,**kw(start,token),scope='workbook:0001',mutate=mutate,allowed_fields={'lane_state','repairs','scheduler'},allowed_lanes={'harbor-research-auditor'})


@pytest.mark.parametrize('fails',[False,True])
def test_existing_consumed_budgets_survive_owned_lane_operations(setup,fails):
    f,p,start,token,source,seg=case(setup,99 if fails else 6)
    start=seed_spent_budget(p,start,token)
    method=f.record_failure if fails else f.complete
    result=method('0001',seg,**kw(start,token));before=start['tracker'];after=result['tracker']
    assert after['repairs']==before['repairs'] and after['scheduler']==before['scheduler']
    assert after['lane_state']['harbor-research-auditor']['attempt']==2


def test_changed_upstream_cannot_leave_populated_downstream_current(setup):
    f,p,start,token,source,seg=case(setup)
    def mutate(t):t['lane_state']['harbor-research-auditor']['state']='running'
    result=n.schema.cas_mutate(p,**kw(start,token),scope='workbook:0001',mutate=mutate,allowed_fields={'lane_state'},allowed_lanes={'harbor-research-auditor'})
    raw=p.read_bytes()
    with pytest.raises(ValueError,match='explicit invalidation'):f.complete('0001',seg,**kw(result,token))
    assert p.read_bytes()==raw


def test_recalculation_cannot_use_native_unchanged_bytes_shortcut(setup):
    f,p,start,token,source,seg=case(setup)
    request=Path(source.manifest['path']).parent/'request.json'
    value=json.loads(request.read_text());value['schema_version']='source-recalc-request/v2'
    request.chmod(0o600);request.write_text(json.dumps(value))
    # Relabelling unchanged original bytes is not a signed producer result;
    # current immutable-generation validation must reject the altered request.
    with pytest.raises(ValueError,match='artifact:request.json'):
        n.check_gate(start['tracker'],source,f.context)


def test_revisited_signature_keeps_prior_occurrences_and_repair_evidence(setup):
    f,p,start,token,source,seg=case(setup,99)
    gate=n.check_gate(start['tracker'],seg,f.context);notes=p.with_name('0001-agent-notes.md')
    first,_=n._failure_after(start['tracker'],gate,time.time_ns(),notes)
    first['promotion_failure']['repair_attempts']=[{'attempt':1,'evidence':'synthetic bounded repair'}]
    first['failure_history'][-1]['failure']=copy.deepcopy(first['promotion_failure'])
    distinct={**gate,'diagnostic_sha256':'0'*64}
    other,_=n._failure_after(first,distinct,time.time_ns(),notes)
    resumed,_=n._failure_after(other,gate,time.time_ns(),notes)
    assert resumed['promotion_failure']['first_seen']==first['promotion_failure']['first_seen']
    assert resumed['promotion_failure']['occurrence_count']==2
    assert resumed['promotion_failure']['repair_attempts']==first['promotion_failure']['repair_attempts']
    assert resumed['failure_history'][-1]['event']=='replace'


def test_notes_journal_symlink_rejected_before_writing_payloads(setup):
    f,p,start,token,source,seg=case(setup,99)
    outside=p.parent/'outside-notes-journal';outside.mkdir()
    journal=p.parent/'0001-notes-transactions';journal.symlink_to(outside,target_is_directory=True)
    before=p.read_bytes()
    with pytest.raises(n.schema.Conflict,match='symlink'):
        f.record_failure('0001',seg,**kw(start,token))
    assert p.read_bytes()==before and list(outside.iterdir())==[]
