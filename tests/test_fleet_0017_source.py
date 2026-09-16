"""Actual retained 0017 source and unchanged scope gate; no model execution."""
import copy,importlib.util,json,sys,shutil
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import disclosure_0017_proof as P

ROOT=Path(__file__).resolve().parents[1]
HOME=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/authoring/0017-generated-05'

@pytest.fixture
def actual():
    root=Path(json.loads((HOME/'frozen-code.json').read_text())['root'])
    spec=importlib.util.spec_from_file_location('original_0017_disclose',root/'.cursor/skills/task-disclosure/scripts/disclose.py')
    D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)
    stage=HOME/'environment/stages/0017-outputs';old=json.loads((stage/'tests/disclosure.json').read_text())
    inputs=dict(source=P.record(old['golden']),disclosure=P.record(stage/'tests/disclosure.json'),records=P.record(HOME/'instruction/producer/disclosure-build/0017-outputs/records.json'),faithcheck=P.record(HOME/'instruction/producer/disclosure-build/0017-outputs/faithcheck.json'),delivered=P.record(D.find_environment(stage)),answer_key=P.record(stage/'tests/answer_key.json'))
    proof=P.source_proof(D,inputs)
    # Prospective registry addition only, not a faithcheck override or mutation
    # to any historical module/registry bytes.
    D.METHOD_ENTRY_IDS={*D.METHOD_ENTRY_IDS,P.ENTRY};D.FAITHCHECK_SCOPE_ENTRIES={*D.FAITHCHECK_SCOPE_ENTRIES,P.ENTRY}
    D._REGISTRY_CACHE=copy.deepcopy(D.registry())
    D._REGISTRY_CACHE[P.ENTRY]=dict(alternatives=['out_of_catalogue'],ship_when='Section default.',sentences={'out_of_catalogue':D.registry()['method_debt_movement']['sentences']['out_of_catalogue']})
    D.SHIP_WHEN={**D.SHIP_WHEN,P.ENTRY:D.ship_custom_method}
    return D,proof,stage

def test_source_backed_full_family_runs_original_faithcheck_and_verify(actual,tmp_path):
    D,proof,stage=actual;records=P.corrected_records(D,proof)
    assert proof['review_limit']==1 and len(records['records'])==155
    source=HOME/'instruction/producer/disclosure-build/0017-outputs'
    runs=tmp_path/'runs';shutil.copytree(source,runs/'0017-outputs')
    (runs/'0017-outputs/records.json').write_text(json.dumps(records))
    destination=tmp_path/'task/0017-outputs'
    written=D.write_disclosure(SimpleNamespace(task_dir=stage,runs_root=runs,force=False,out=destination,dry_run=False))
    faith=D.faithcheck_task(destination,tmp_path/'checks',proof['bindings']['source']['path'])
    verified=D.verify_task(destination)
    assert written['written_records']==47 and faith['passed'] and faith['faults']==[]
    assert verified['passed'] and verified['faults']==[]
    output=json.loads((destination/'tests/disclosure.json').read_text())
    assert all('S&U!M10' not in r.get('cell_keys',[]) for r in output['agent_records'])
    assert output['historical_scope_correction']['retained_original_m9']==proof['original_m9']
    assert output['historical_scope_correction']['retained_original_assessments']==P.read(proof['bindings']['records'])['method_assessments']
    assert {r['entry'] for r in output['method_assessments'] if r.get('cell_keys') in [['S&U!M8'],['S&U!M9']]}=={P.ENTRY}

def test_missing_scope_member_fails_unchanged_partition(actual):
    D,proof,stage=actual;records=P.corrected_records(D,proof)
    selected=[r for r in records['records'] if r.get('entry')==P.ENTRY]
    gold=D.Book(Path(proof['bindings']['source']['path']))
    assert len(selected)==2
    assert not D._heterogeneous_scope_partitioned(gold,proof['non_graded_family'],selected[1:],P.ENTRY)
    wrong=copy.deepcopy(selected);wrong[0]['entry']='method_debt_movement'
    assert not D._heterogeneous_scope_partitioned(gold,proof['non_graded_family'],wrong,P.ENTRY)

@pytest.mark.parametrize('change',['source','graded','label','record'])
def test_mutated_proof_rejects_before_proposal(actual,change):
    D,proof,stage=actual;bad=copy.deepcopy(proof)
    if change=='source':bad['bindings']['source']['sha256']='0'*64
    elif change=='graded':bad['non_graded_family'].append('S&U!M10')
    elif change=='label':bad['rows'][0]['label']='Debt Draw'
    else:bad['original_m9']['fields']['steps']='assume the same number'
    with pytest.raises(ValueError):P.corrected_records(D,bad)
