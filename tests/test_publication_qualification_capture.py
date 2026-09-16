"""A root receiver authenticates the original bounded qualification capture."""
import json
from pathlib import Path
import pytest
from scripts.fleet import publication_launch as P


@pytest.fixture
def case(tmp_path, monkeypatch):
    def put(name, value):
        path=tmp_path/name;path.write_text(json.dumps(value));return P.binding(path)
    helper=tmp_path/'helper.py';helper.write_text('# simulated pinned capture helper\n')
    original=P.binding
    selected=dict(path=str(helper),sha256='51be82e04c1b1a28721b7b8674f8196480a233c9a590ca02e16c124e5e9dbc13')
    monkeypatch.setattr(P,'binding',lambda path:selected if str(path)==str(helper) else original(path))
    probe=tmp_path/'probe.py';probe.write_text('# retained original guarded probe\n')
    plan=put('plan.json',dict(workbook='0001'))
    tracker=dict(path='/original/0001.json',sha256='tracker')
    report=dict(schema_version='harbor-complete-readiness/v1',workbook='0001',stage_sha256='stage',publication_ready=True,
                blockers=[],checks={'required_gate':{'checked':True}},evidence=[{'original':'evidence'}])
    captured=dict(status='ACTUAL_QUEUED_QUALIFICATION_PROOF_PASSED',plan=plan,tracker=tracker,
                  models_dispatched=0,lease_mutations=0,publication_mutations=0)
    captured['request']={'selection':{'value':{'content':{'value':{'stage_sha256':{'type':'scalar','value':'stage'}}}}}}
    proof=dict(tracker=tracker,checks=1,evidence=1)
    p=dict(plan=plan)
    observed=dict(status='PASS',elapsed_seconds=2,models_dispatched=0,publication_mutations=0)
    guard=dict(elapsed_seconds=3,probe=original(probe))
    def build():
        qualification=put('qualification.json',report)
        captured['qualification']=qualification
        capture=put('capture.json',captured)
        proof.update(qualification=qualification,qualification_capture=capture)
        p['proof']=put('proof.json',proof)
        p['qualification_capture']=put('selection.json',dict(schema_version='fleet-guarded-publication-qualification-capture/v1',
            helper=selected,capture=capture,qualification=qualification))
        observed.setdefault('proof',p['proof'])
        guard['log']=json.dumps(observed)
        return p,proof,guard
    return build,report,captured,proof,observed,guard,selected,probe


def test_accepts_original_log_and_raw_report_without_rewriting(case):
    build,*_=case;p,proof,guard=build();before=guard['log']
    P.captured_qualification(p,proof,guard)
    assert guard['log']==before


@pytest.mark.parametrize('fault',[None,'binding','embedded','effects','extra'])
def test_original_queued_capture_envelope_remains_exact(case,fault):
    build,report,captured,*_=case
    if fault=='effects':captured['lease_mutations']=1
    p,proof,guard=build()
    observed=dict(status='FULL_EXPIRED_QUEUED_QUALIFICATION_CAPTURED',publication_proof=p['proof'],proof=dict(proof))
    if fault=='binding':observed['publication_proof']={'path':'different','sha256':'different'}
    elif fault=='embedded':observed['proof']['checks']+=1
    elif fault=='extra':observed['omitted']=True
    guard['log']=json.dumps(observed);raw=guard['log']
    if fault:
        with pytest.raises(ValueError):P.captured_qualification(p,proof,guard)
    else:P.captured_qualification(p,proof,guard)
    assert guard['log']==raw


@pytest.mark.parametrize('defect',['proof','nan','negative','outside_guard','model','publication',
    'lease','foreign_plan','foreign_tracker','failed','blocker','unchecked','empty_checks','no_evidence',
    'count','helper','probe','stage'])
def test_rejects_changed_identity_effects_or_incomplete_original_proof(case,defect):
    build,report,captured,proof,observed,guard,selected,probe=case
    if defect=='proof':observed['proof']={'path':'foreign','sha256':'foreign'}
    elif defect=='nan':observed['elapsed_seconds']=float('nan')
    elif defect=='negative':observed['elapsed_seconds']=-1
    elif defect=='outside_guard':observed['elapsed_seconds']=4
    elif defect=='model':observed['models_dispatched']=1
    elif defect=='publication':captured['publication_mutations']=1
    elif defect=='lease':captured['lease_mutations']=1
    elif defect=='foreign_plan':captured['plan']={'path':'foreign','sha256':'foreign'}
    elif defect=='foreign_tracker':captured['tracker']={'path':'foreign','sha256':'foreign'}
    elif defect=='failed':report['publication_ready']=False
    elif defect=='blocker':report['blockers']=['numerical contradiction']
    elif defect=='unchecked':report['checks']['required_gate']['checked']=False
    elif defect=='empty_checks':report['checks']={}
    elif defect=='no_evidence':report['evidence']=[]
    elif defect=='count':proof['checks']=2
    elif defect=='helper':selected['sha256']='foreign'
    elif defect=='probe':probe.write_text('# changed after execution\n')
    elif defect=='stage':report['stage_sha256']='foreign'
    with pytest.raises(ValueError):P.captured_qualification(*build())
