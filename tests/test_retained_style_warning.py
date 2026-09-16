from pathlib import Path
import copy
import pytest
from scripts.batch import harbor_dialogue_mechanical_requalification as M
from scripts.batch import harbor_instruction_worker as W
from scripts.batch.harbor_admission import read,record
from test_instruction_worker import case,instruction_setup
from test_source_card_worker import prepared,install


def warning_checkpoint(worker,s,l,blocked):
    directory=worker.run/'separate-warning';directory.mkdir()
    old=read(blocked)
    review=W.keep(directory/'actual-review.json',{'accuracy':{'verdict':'pass'},'naturalness':{'verdict':'fail'},'passed':False})
    source=W.keep(directory/'source.json',{});initial=record(worker.run/'initial.json')
    p=dict(schema_version=M.WARNING_SCHEMA,implementation=M.binding(M.__file__),runtime_profile=source,step='dialogue-review',worker_run=str(worker.run),initial=initial,original_directory=str(Path(blocked['path']).parent),blocked=blocked,failed_selection=old['selection'],failed_lease=old['lease'],intent=old['intent'],source=source,draft=source,claims=source,review=review,original_receipt=source,notes=source,warnings=[{'code':'dialogue_style_only','finding':'Repetitive.'}],report={'passed':True},semantic_proof={'provenance':{'review':read(review)}},calls={'dialogue-review':{'parsed':review}},evidence=[initial,blocked,source,review],model_calls_added=0,draft_changed=False,review_changed=False,automatic_retry=False,directory=str(directory),selection=old['selection'],lease=old['lease'])
    cert=M.keep(directory/'certificate.json',p)
    complete=M.keep(directory/'complete.json',dict(step='dialogue-review',status='completed',selection=p['selection'],lease=p['lease'],intent=p['intent'],mechanical_requalification=cert))
    return cert,complete,p


def test_original_source_card_worker_moves_from_warning_to_final_runtime_without_review_call(case,monkeypatch):
    worker,s,l,_,authority,_,reader,_=prepared(case,monkeypatch);seen=[]
    def operation(w,s,l,phase,directory,**k):
        seen.append(phase);return s,l,'DIALOGUE_REPAIR_REQUIRED'if phase=='dialogue-review'else('RUNTIME_REQUIRED'if phase=='final-runtime'else None)
    selected=install(reader,monkeypatch,operation)
    writer=selected.step(worker,s,l,checkpoint=authority)
    para=selected.step(worker,s,l,checkpoint=writer.evidence)
    held=selected.step(worker,s,l,checkpoint=para.evidence)
    assert held.status=='DIALOGUE_REPAIR_REQUIRED'
    before=Path(held.evidence['path']).read_bytes();cert,complete,p=warning_checkpoint(worker,s,l,held.evidence)
    from scripts import harbor_instruction_campaign as C
    descriptor=W.keep(worker.run/'warning-descriptor.json',{'fixture_only':True})
    envelope=W.keep(worker.run/'warning-result.json',C._result('STEP_COMPLETE',descriptor,s,l,checkpoint=complete,next_step='final-runtime',evidence=[held.evidence,complete]))
    prior,selected_s,selected_l=C._prior(envelope,descriptor)
    assert prior['checkpoint']==complete and selected_s==s and selected_l==l
    M.install_projection(selected,cert)
    result=selected.step(worker,s,l,checkpoint=complete,models=False,containers=False)
    assert result.status=='RUNTIME_REQUIRED'and result.next_step=='final-runtime'
    assert seen==['dialogue-writer','dialogue-paraphraser','dialogue-review','final-runtime']
    assert Path(held.evidence['path']).read_bytes()==before and read(p['review'])['passed']is False

@pytest.mark.parametrize('change',['review','blocked','source','initial','complete'])
def test_warning_projection_rejects_changed_original_history(case,monkeypatch,change):
    worker,s,l,_,authority,_,reader,_=prepared(case,monkeypatch)
    selected=install(reader,monkeypatch,lambda w,s,l,phase,d,**k:(s,l,'DIALOGUE_REPAIR_REQUIRED'if phase=='dialogue-review'else None))
    a=selected.step(worker,s,l,checkpoint=authority);b=selected.step(worker,s,l,checkpoint=a.evidence);c=selected.step(worker,s,l,checkpoint=b.evidence)
    cert,complete,p=warning_checkpoint(worker,s,l,c.evidence);M.validate_warning(cert)
    row=complete if change=='complete'else p[change];Path(row['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):M.validate_warning(cert)


def test_actual_warning_authorizer_emits_plain_worker_lease_and_typed_result(case,monkeypatch,tmp_path):
    from types import SimpleNamespace
    from dataclasses import asdict
    from scripts import harbor_instruction_campaign as C
    worker,s,l,_,authority,_,reader,_=prepared(case,monkeypatch)
    selected=install(reader,monkeypatch,lambda w,s,l,phase,d,**k:(s,l,'DIALOGUE_REPAIR_REQUIRED'if phase=='dialogue-review'else None))
    a=selected.step(worker,s,l,checkpoint=authority);b=selected.step(worker,s,l,checkpoint=a.evidence);held=selected.step(worker,s,l,checkpoint=b.evidence)
    cert,complete,proof=warning_checkpoint(worker,s,l,held.evidence)
    proof=dict(proof);dummy=proof['source'];proof['calls']={k:{field:dummy for field in ('request','submitted','response','completed','parsed')}for k in ('dialogue-writer','dialogue-paraphraser','dialogue-review')};proof['calls']['dialogue-review']['parsed']=proof['review'];proof['tracker']=dummy
    root=tmp_path/'producer';draft=root/'round-02/draft.md';draft.parent.mkdir(parents=True);draft.write_text('retained draft')
    directory=tmp_path/'actual-authorizer';directory.mkdir();descriptor=W.keep(tmp_path/'old-descriptor.json',dict(origin=[],worker=C.worker_encode(worker)))
    p=dict(descriptor=descriptor,blocked=held.evidence,takeover=dummy,runtime_profile=dummy)
    monkeypatch.setattr(selected,'active',lambda *a:(None,{},dummy));monkeypatch.setattr(selected,'task',lambda *a:None)
    monkeypatch.setattr(selected,'request_saved',lambda *a:SimpleNamespace(prepared=SimpleNamespace(ordinal=2)))
    producer=SimpleNamespace(_dialogue=lambda *a:(root,None,None,None),skill_module=lambda *a:SimpleNamespace(strip_claim_comments=lambda t:t,apply=lambda *a,**k:None))
    def inspect(*a,**k):return dict(proof,notes=k['notes'],evidence=[*proof['evidence'],k['notes']])
    monkeypatch.setattr(M,'inspect_warning',inspect);monkeypatch.setattr(C,'selected',lambda *a:None)
    from dataclasses import replace
    from scripts.batch.harbor_complete_readiness import SemanticSelection
    s=replace(s,semantic=SemanticSelection(None,dummy,dummy,None,dummy,()))
    M.authorize_warning(p,dummy,directory,reader,dict(worker=selected,producer=producer,campaign=C),worker,s,l)
    result=read(record(directory/'result.json'));checkpoint=read(result['checkpoint'])
    assert checkpoint['lease']==asdict(l)and result['lease']==C.base.encode(l)
    assert C._prior(record(directory/'result.json'),result['descriptor'])[2]==l


def legacy_serialization(case,monkeypatch,tmp_path):
    import json,sys
    from dataclasses import asdict
    from scripts import harbor_instruction_campaign as C
    worker,s,l,_,authority,_,reader,_=prepared(case,monkeypatch)
    selected=install(reader,monkeypatch,lambda w,s,l,phase,d,**k:(s,l,'DIALOGUE_REPAIR_REQUIRED'if phase=='dialogue-review'else None))
    a=selected.step(worker,s,l,checkpoint=authority);b=selected.step(worker,s,l,checkpoint=a.evidence);held=selected.step(worker,s,l,checkpoint=b.evidence)
    oldcert,complete,proof=warning_checkpoint(worker,s,l,held.evidence)
    oldimpl=M.binding(Path(__file__).parents[1]/'diagnostics/vm-fleet-20260912/control/0200-retained-style-warning-001/implementation-003/mechanical.py')
    proof=dict(proof,implementation=oldimpl,lease=C.base.encode(l))
    Path(oldcert['path']).write_text(json.dumps(proof));oldcert=M.binding(oldcert['path'])
    completevalue=read(complete);completevalue.update(lease=C.base.encode(l),mechanical_requalification=oldcert);Path(complete['path']).write_text(json.dumps(completevalue));complete=M.binding(complete['path'])
    output=tmp_path/'old-warning';directory=output/'authorization';directory.mkdir(parents=True)
    plan=M.keep(tmp_path/'old-warning-plan.json',dict(schema_version=M.WARNING_RUN_SCHEMA,implementation=oldimpl,output=str(output),blocked=held.evidence,runtime_profile=proof['runtime_profile']))
    descriptor=M.keep(directory/'descriptor.json',dict(initial=dict(selection=C.codec.encode(s),lease=C.base.encode(l)),origin=[]))
    result=M.keep(directory/'result.json',C._result('STEP_COMPLETE',descriptor,s,l,checkpoint=complete,next_step='final-runtime',evidence=[held.evidence,plan,complete]))
    intent=M.keep(directory/'intent.json',dict(schema_version=M.WARNING_RUN_SCHEMA,plan=plan,seconds=900,memory_bytes=8589934592,models=False,automatic_retry=False))
    log=M.keep(directory/'step.log',b'AdmissionError: checkpoint selection or lease changed\n')
    resource=M.keep(directory/'resource.json',dict(returncode=1,failure=None,seconds=900,memory_bytes=8589934592,intent=intent,log=log,command=[sys.executable,'-I','-B',oldimpl['path'],'authorize-child','--plan',plan['path'],'--sha256',plan['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]))
    outcome=M.keep(directory/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,result=None,automatic_retry=False))
    return worker,s,l,selected,C,dict(implementation=oldimpl,plan=plan,outcome=outcome,result=result,certificate=oldcert),held.evidence


def test_recovery_canonicalizes_only_lease_and_preserves_failed_guard(case,monkeypatch,tmp_path):
    worker,s,l,selected,C,authority,blocked=legacy_serialization(case,monkeypatch,tmp_path)
    proof,prior,current,records=M.serialization_source(authority,C);assert current==l
    original={x['path']:Path(x['path']).read_bytes()for x in records}
    directory=tmp_path/'recovered';directory.mkdir();p=dict(retained_warning=authority,runtime_profile=proof['runtime_profile'],blocked=blocked)
    monkeypatch.setattr(C,'selected',lambda *a:None)
    h={k:proof['source']for k in ('original','retained','current','marker','reader','authority')}
    monkeypatch.setattr(M,'instruction_history',lambda *a:h)
    M.authorize_serialization(p,authority['plan'],directory,dict(campaign=C,worker=selected),worker,s,l)
    result=read(M.binding(directory/'result.json'));assert C._prior(M.binding(directory/'result.json'),result['descriptor'])[2]==l
    assert all(Path(path).read_bytes()==raw for path,raw in original.items())
    assert read(authority['outcome'])['status']=='RECONCILIATION_REQUIRED'

@pytest.mark.parametrize('field,value',[('returncode',0),('failure',{'type':'timeout'}),('seconds',1800)])
def test_recovery_rejects_different_or_uncertain_guard(case,monkeypatch,tmp_path,field,value):
    import json
    worker,s,l,selected,C,authority,blocked=legacy_serialization(case,monkeypatch,tmp_path)
    outcome=read(authority['outcome']);resource=read(outcome['resource']);resource[field]=value
    Path(outcome['resource']['path']).write_text(json.dumps(resource));outcome['resource']=M.binding(outcome['resource']['path']);Path(authority['outcome']['path']).write_text(json.dumps(outcome));authority['outcome']=M.binding(authority['outcome']['path'])
    with pytest.raises(ValueError):M.serialization_source(authority,C)


binding=M.binding
class HistoricalReaderFixture:
    def __init__(self,authority,path):self.authority=authority;self.path=path
    def owned(self):return binding(self.path)
    def live(self):return binding(self.path)


def test_instruction_history_reads_exact_witness_only_in_historical_method(tmp_path):
    from types import SimpleNamespace
    stage=tmp_path/'stage';witness=tmp_path/'witness';stage.mkdir();witness.mkdir();(stage/'tests').mkdir()
    old=M.keep(witness/'instruction.md',b'original authored instruction')
    current=M.keep(stage/'instruction.md',b'applied dialogue pointer')
    marker=M.keep(stage/'tests/dialogue-applied.json',dict(applied=True,instruction_sha256=current['sha256']))
    prior=dict(path=current['path'],sha256=old['sha256']);authority=M.keep(tmp_path/'permit.json',dict(history=[prior]))
    reader=HistoricalReaderFixture(authority,current['path'])
    selected=SimpleNamespace(declared_namespace={'_source_cards':reader},codec=SimpleNamespace(decode=lambda _:SimpleNamespace(content=SimpleNamespace(stage=stage,pre_dialogue_witness=SimpleNamespace(stage=witness)))))
    h=M.instruction_history(selected,{'failed_selection':None});assert h['original']==prior and h['retained']==old and h['current']==current
    M.install_instruction_history(selected,{'historical_instruction':h})
    assert reader.owned()==prior and reader.live()==current
    Path(old['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):reader.owned()
