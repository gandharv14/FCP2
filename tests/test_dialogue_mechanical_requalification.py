from dataclasses import asdict
from pathlib import Path
import pytest
from scripts.batch import harbor_dialogue_mechanical_requalification as M
from scripts.batch import harbor_instruction_worker as W
from scripts.batch.harbor_admission import read,record
from test_instruction_worker import case,instruction_setup
from test_source_card_worker import prepared,install


def certificate(worker,s,l,blocked,target):
    old=read(blocked);source=W.keep(worker.run/'mechanical-source.json',{})
    draft=W.keep(worker.run/'mechanical-draft.md',b'unchanged actual generated draft')
    req=W.keep(Path(blocked['path']).parent/'request.json',{'exact':'retained prepared request'})
    initial=record(worker.run/'initial.json');target.mkdir()
    copied=M.keep(target/'request.json',Path(req['path']).read_bytes())
    runtime=W.keep(worker.run/'runtime-profile.json',{'schema_version':'fleet-retained-runtime/v1'})
    p=dict(schema_version=M.SCHEMA,implementation=M.binding(M.__file__),runtime_profile=runtime,worker_run=str(worker.run),initial=initial,source=source,blocked=blocked,original_directory=str(Path(blocked['path']).parent),selection=old['selection'],lease=old['lease'],failed_lease=old['lease'],takeover=None,intent=old['intent'],request=req,request_copy=copied,draft=draft,claims=source,old_check=source,report={'passed':True,'faults':[]},author_calls=[],author_generation=2,evidence=[blocked,initial,draft],tracker=source,review_performed=False,model_calls_added=0,draft_changed=False,automatic_retry=False,directory=str(target))
    cert=M.keep(target/'certificate.json',p)
    complete=M.keep(target/'complete.json',dict(step='dialogue-paraphraser',status='completed',selection=p['selection'],lease=p['lease'],intent=p['intent'],mechanical_requalification=cert))
    return cert,complete,p


def test_real_source_card_worker_continues_to_first_review_without_replaying_authors(case,monkeypatch):
    worker,s,l,_,authority,old_files,reader,_=prepared(case,monkeypatch);seen=[]
    def operation(worker,s,l,phase,directory,**options):
        seen.append(phase)
        return s,l,'DIALOGUE_REPAIR_REQUIRED'if phase=='dialogue-paraphraser'else('MODEL_REQUIRED'if phase=='dialogue-review'else None)
    selected=install(reader,monkeypatch,operation)
    writer=selected.step(worker,s,l,checkpoint=authority)
    held=selected.step(worker,s,l,checkpoint=writer.evidence)
    assert held.status=='DIALOGUE_REPAIR_REQUIRED'
    raw=Path(held.evidence['path']).read_bytes();cert,complete,_=certificate(worker,s,l,held.evidence,worker.run/'mechanical-qualified')
    from scripts import harbor_instruction_campaign as C
    descriptor=W.keep(worker.run/'mechanical-descriptor.json',{'fixture_only':True})
    envelope=W.keep(worker.run/'mechanical-result.json',C._result('STEP_COMPLETE',descriptor,s,l,checkpoint=complete,next_step='dialogue-review',evidence=[held.evidence,complete]))
    prior,selected_s,selected_l=C._prior(envelope,descriptor)
    assert prior['checkpoint']==complete and selected_s==s and selected_l==l
    M.install_projection(selected,cert)
    result=selected.step(worker,s,l,checkpoint=complete,models=False)
    assert result.status=='MODEL_REQUIRED'and result.next_step=='dialogue-review'
    assert seen==['dialogue-writer','dialogue-paraphraser','dialogue-review']
    assert Path(held.evidence['path']).read_bytes()==raw
    assert all(Path(path).read_bytes()==data for path,data in old_files.items())
    assert read(read(result.evidence)['intent'])['previous']==complete

@pytest.mark.parametrize('fault',['draft','blocked','request','completion','initial','profile'])
def test_projected_completion_rechecks_all_bound_evidence(case,monkeypatch,fault):
    worker,s,l,_,authority,_,reader,_=prepared(case,monkeypatch)
    selected=install(reader,monkeypatch,lambda w,s,l,phase,directory,**k:(s,l,'DIALOGUE_REPAIR_REQUIRED'if phase=='dialogue-paraphraser'else None))
    writer=selected.step(worker,s,l,checkpoint=authority);held=selected.step(worker,s,l,checkpoint=writer.evidence)
    cert,complete,p=certificate(worker,s,l,held.evidence,worker.run/'mechanical-qualified')
    M.validate(cert)
    row={'draft':p['draft'],'blocked':p['blocked'],'request':p['request_copy'],'completion':complete,'initial':p['initial'],'profile':p['runtime_profile']}[fault]
    Path(row['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):M.validate(cert)
