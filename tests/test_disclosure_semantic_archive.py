from dataclasses import dataclass,replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import disclosure_semantic_archive as S
from scripts.batch import harbor_disclosure_round_adoption as A,harbor_complete_readiness as C,harbor_completed_lanes as L
from scripts.batch.harbor_admission import record,read
from test_disclosure_adoption_resume import keep

@dataclass(frozen=True)
class Content:
    workbook:str
    bindings:dict

@pytest.fixture
def case(tmp_path):
    instruction=tmp_path/'instruction.md';instruction.write_text('Original reviewed disclosure source')
    old=record(instruction);archive=tmp_path/'budget-034.snapshot';archive.write_bytes(instruction.read_bytes());arch=record(archive)
    source=keep(tmp_path/'source.xlsx',{});plan=keep(tmp_path/'plan.json',dict(workbook='0001',instruction=old))
    aggregate=keep(tmp_path/'aggregate.json',{});run=keep(tmp_path/'run.json',dict(output=str(tmp_path/'run')))
    submitted=keep(tmp_path/'submitted.json',dict(budget_evidence=[arch]));keep(tmp_path/'run/batches/0000/complete.json',dict(index=0,plan=plan,call=dict(submitted=submitted)))
    result=keep(tmp_path/'result.json',dict(aggregate=aggregate,review_plan=plan,actual_review_calls=1))
    value={k:None for k in A.KEYS};value.update(schema_version=A.SCHEMA,implementation=record(A.__file__),round_result=result,round_plan=run,review_plan=plan,aggregate=aggregate,source=source,historical_instruction=old,historical_records=[dict(original=old,archive=old),dict(original=old,archive=arch)])
    receipt=keep(tmp_path/'permit.json',value)
    selection=C.ReadinessSelection(Content('0001',dict(original_raw_source=source)),C.SemanticSelection(None,plan,old,None,None,(),disclosure_partition_aggregate=aggregate),None,None)
    profile=dict(schema_version=S.SCHEMA,complete=record(C.__file__),lanes=record(L.__file__),functions=dict(evidence=A.function_sha(C.evidence_records),disclosure=A.function_sha(C.check_disclosure)))
    instruction.write_text('Actual independently reviewed NAT content')
    return receipt,selection,profile,old,arch

def test_real_evidence_reader_returns_real_archive_and_keeps_live_instruction(case,monkeypatch):
    monkeypatch.setattr(L,"evidence_records",C.evidence_records,raising=False)
    receipt,selection,profile,old,arch=case;original=C.evidence_records;alias=L.evidence_records
    with pytest.raises(Exception):original(selection)
    with S.installed(A,profile,receipt):
        result=C.evidence_records(selection)
        assert arch in result and old not in result
        assert L.evidence_records(selection)==result
        assert 'Actual independently' in Path(old['path']).read_text()
        assert read(receipt)['historical_instruction']==old
        with pytest.raises(Exception):C.evidence_records(old)
    assert C.evidence_records is original and L.evidence_records is alias
    with pytest.raises(Exception):original(selection)

@pytest.mark.parametrize('fault',['archive','missing','foreign_source','foreign_instruction','foreign_workbook','witness'])
def test_closed_scope_and_archive_rechecks(case,fault):
    receipt,s,p,old,arch=case
    if fault=='archive':Path(arch['path']).write_text('corrupt')
    if fault=='missing':Path(arch['path']).unlink()
    if fault=='foreign_source':s=replace(s,content=replace(s.content,bindings=dict(original_raw_source={'path':'/foreign','sha256':'a'*64})))
    if fault=='foreign_instruction':s=replace(s,semantic=replace(s.semantic,disclosure_instruction={'path':'/foreign','sha256':'a'*64}))
    if fault=='foreign_workbook':s=replace(s,content=replace(s.content,workbook='0002'))
    if fault=='witness':p['functions']['evidence']='f'*64
    with pytest.raises(Exception):
        with S.installed(A,p,receipt):C.evidence_records(s)

def test_mutation_after_install_rejected(case):
    receipt,s,p,old,arch=case
    with S.installed(A,p,receipt):
        assert arch in C.evidence_records(s)
        Path(arch['path']).write_text('changed after install')
        with pytest.raises(Exception):C.evidence_records(s)


def test_explicit_preparation_copy_resolves_to_actual_submitted_snapshot(case,tmp_path):
    receipt,s,p,old,arch=case
    value=read(receipt);intermediate=tmp_path/'prepared-instruction.md';intermediate.write_bytes(Path(arch['path']).read_bytes());middle=record(intermediate)
    value['historical_records']=[dict(original=old,archive=middle),dict(original=middle,archive=arch)]
    chained=keep(tmp_path/'chain-permit.json',value)
    with S.installed(A,p,chained):assert arch in C.evidence_records(s)
    value['historical_records']=[dict(original=old,archive=middle)]
    unsubmitted=keep(tmp_path/'unsubmitted-permit.json',value)
    with pytest.raises(ValueError):
        with S.installed(A,p,unsubmitted):pass


def test_actual_prepared_review_instruction_is_scoped_and_forwarded(case,tmp_path,monkeypatch):
    receipt,s,p,old,arch=case;value=read(receipt)
    middle_path=tmp_path/'prepared-copy.md';middle_path.write_bytes(Path(arch['path']).read_bytes());middle=record(middle_path)
    plan=keep(tmp_path/'prepared-plan.json',dict(workbook='0001',instruction=middle))
    run=keep(tmp_path/'prepared-run.json',dict(output=str(tmp_path/'prepared-run')))
    second=tmp_path/'budget-108.snapshot';second.write_bytes(Path(arch['path']).read_bytes());arch2=record(second)
    submitted=keep(tmp_path/'prepared-submitted.json',dict(budget_evidence=[arch,arch2]))
    keep(tmp_path/'prepared-run/batches/0000/complete.json',dict(index=0,plan=plan,call=dict(submitted=submitted)))
    result=keep(tmp_path/'prepared-result.json',dict(aggregate=value['aggregate'],review_plan=plan,actual_review_calls=1))
    value.update(round_result=result,round_plan=run,review_plan=plan,historical_records=[dict(original=old,archive=arch),dict(original=old,archive=middle),dict(original=middle,archive=arch2)])
    receipt=keep(tmp_path/'prepared-permit.json',value);s=replace(s,semantic=replace(s.semantic,disclosure_instruction=middle,disclosure_manifest=plan))
    calls=[]
    def aggregate(binding,*args,**kw):calls.append(kw['instruction']);return {}
    monkeypatch.setattr(A.review,'validate_aggregate',aggregate)
    with S.installed(A,p,receipt):
        assert arch2 in C.evidence_records(s)
        A.review.validate_aggregate(value['aggregate'],instruction=arch2)
        assert calls==[middle]
        foreign=tmp_path/'same-bytes-foreign.md';foreign.write_bytes(Path(arch['path']).read_bytes())
        with pytest.raises(ValueError):C.evidence_records(replace(s,semantic=replace(s.semantic,disclosure_instruction=record(foreign))))


def scoped_disclosure(selection,*,root=None):
    nested=nested_reader([extra_binding])
    return dict(nested=nested,evidence=evidence_records([extra_binding,selection.semantic.disclosure_instruction]))

def test_nested_round_inventory_keeps_original_bindings(case,tmp_path,monkeypatch):
    global nested_reader,extra_binding,evidence_records
    receipt,s,p,old,arch=case;value=read(receipt)
    extra=tmp_path/'extra';extra.write_text('retained prefix input');extra_binding=record(extra)
    copy=tmp_path/'budget-099.snapshot';copy.write_bytes(extra.read_bytes());archive=record(copy)
    run=keep(tmp_path/'scope-run.json',dict(output=str(tmp_path/'scope-run')))
    submitted=keep(tmp_path/'scope-submitted.json',dict(budget_evidence=[arch,archive]))
    keep(tmp_path/'scope-run/batches/0000/complete.json',dict(index=0,plan=value['review_plan'],call=dict(submitted=submitted)))
    value.update(round_plan=run,historical_records=[*value['historical_records'],dict(original=extra_binding,archive=archive)])
    receipt=keep(tmp_path/'scope-permit.json',value)
    nested_reader=lambda rows:C.evidence_records(rows)
    evidence_records=C.evidence_records
    monkeypatch.setattr(C,'check_disclosure',scoped_disclosure)
    p['functions']['disclosure']=A.function_sha(scoped_disclosure)
    with S.installed(A,p,receipt):
        result=C.check_disclosure(s)
        assert extra_binding in result['nested']and archive not in result['nested']
        assert archive in result['evidence']and arch in result['evidence']


def test_only_selected_aggregate_final_evidence_projects_archives(case,monkeypatch):
    receipt,s,p,old,archive=case;value=A.read(receipt);aggregate=value['aggregate']
    calls=[]
    def original(binding,*args,**kwargs):
        calls.append(binding)
        # A nested source inventory must still see the exact original bindings.
        with pytest.raises(Exception):C.evidence_records([old])
        return C.evidence_records([binding,old])
    monkeypatch.setattr(A.review,'validate_aggregate',original)
    with S.installed(A,p,receipt):
        assert archive in A.review.validate_aggregate(aggregate,instruction=archive)
        with pytest.raises(Exception):C.evidence_records([aggregate,old])
        with pytest.raises(Exception):A.review.validate_aggregate({'path':'foreign','sha256':'x'},instruction=archive)
    assert calls[0]==aggregate and A.review.validate_aggregate is original


def test_failed_aggregate_does_not_leak_archive_scope(case,monkeypatch):
    receipt,s,p,old,archive=case;value=A.read(receipt)
    def fail(*args,**kwargs):raise RuntimeError('original independent validation failed')
    monkeypatch.setattr(A.review,'validate_aggregate',fail)
    with S.installed(A,p,receipt):
        with pytest.raises(RuntimeError):A.review.validate_aggregate(value['aggregate'],instruction=archive)
        with pytest.raises(Exception):C.evidence_records([value['aggregate'],old])


def history_case(case,tmp_path):
    receipt,s,p,old,archive=case;value=A.read(receipt)
    value['history_reader']=A.record(A.history.__file__)
    value['initial']=keep(tmp_path/'initial.json',{})
    receipt=keep(tmp_path/'history-permit.json',value)
    return receipt,value,old,archive


def test_validated_adoption_history_returns_real_submitted_archive(case,tmp_path):
    receipt,value,old,archive=history_case(case,tmp_path)
    live=Path(old['path']).read_bytes()
    evidence=S.historical_evidence(A,receipt,value)
    assert evidence[0]==receipt and archive in evidence and old not in evidence
    assert A.read(receipt)==value and Path(old['path']).read_bytes()==live
    assert len(evidence)==8+len(value['historical_records'])


@pytest.mark.parametrize('fault',['changed_archive','missing_archive','foreign_validated_receipt','unsubmitted_archive'])
def test_renewal_history_never_accepts_missing_or_unproven_archive(case,tmp_path,fault):
    receipt,value,old,archive=history_case(case,tmp_path)
    if fault=='changed_archive':Path(archive['path']).write_text('changed')
    elif fault=='missing_archive':Path(archive['path']).unlink()
    elif fault=='foreign_validated_receipt':value={**value,'source':{'path':'/foreign','sha256':'f'*64}}
    else:
        value['historical_records']=[dict(original=old,archive=old)]
        receipt=keep(tmp_path/'unsubmitted-history.json',value)
    with pytest.raises(Exception):S.historical_evidence(A,receipt,value)
