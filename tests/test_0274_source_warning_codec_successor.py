"""Pure current-reader codec/projection tests; no task or lease mutations."""
from pathlib import Path
import ast,copy,json
from dataclasses import replace
import pytest

F=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910'
ROOT=F/'code-0274-source-warning-v6'
ORIGINAL=json.loads((F/'0274-source-warning-qualification/receipt/permit.json').read_text())

def function(path,name,namespace):
    tree=ast.parse(path.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
    return namespace[name]

def require(value,message):
    if not value:raise ValueError(message)

@pytest.fixture
def codec_gate():
    return function(ROOT/'scripts/harbor_instruction_campaign.py','_warning_history',
                    dict(require=require,read=lambda _:copy.deepcopy(ORIGINAL)))

def test_actual_first_round_receipt_enters_typed_codec(codec_gate):
    codec_gate(None,None,{'path':'selected-proof','sha256':'a'*64})

@pytest.mark.parametrize('field,value',[('source_first_round',False),('round',2),('model_calls',1),
    ('semantic_budget_reset',True),('semantic',{'path':'detached','sha256':'b'*64}),
    ('code_repair',{'path':'detached','sha256':'b'*64}),('source_warning_proof',None),('calls',{})])
def test_detached_or_budget_changing_first_round_rejected(field,value):
    p=copy.deepcopy(ORIGINAL);p[field]=value
    gate=function(ROOT/'scripts/harbor_instruction_campaign.py','_warning_history',dict(require=require,read=lambda _:p))
    with pytest.raises(ValueError):gate(None,None,{'path':'selected-proof','sha256':'a'*64})

def test_absent_warning_remains_legacy(codec_gate):codec_gate(None,None,None)

@pytest.fixture
def projection():
    # This pure comparison receives real decoded selection data without replacing
    # production namespaces or simulating an owning completion/CAS.
    import sys
    sys.path.insert(0,str(ROOT))
    from scripts.batch import harbor_transition_codec as codec,harbor_tracker_lifecycle as life
    return function(ROOT/'scripts/batch/harbor_dialogue_warning_requalification.py','_source_successor_projection',
                    dict(require=require,copy=copy,codec=codec,replace=replace,life=life))

def current():
    p=copy.deepcopy(ORIGINAL)
    p.update(code={'new-code':'c'*64},code_changes=['reviewed-codec'],lease={**p['lease'],'revision':45},
             tracker={'path':'current-tracker','sha256':'d'*64},source_successor={'retained':'origin'})
    p['source_warning_proof']['root']=str(ROOT)
    return p

def test_exact_source_proof_root_projection(projection):projection(ORIGINAL,current(),ROOT)

@pytest.mark.parametrize('field,value',[('warnings',[]),('round',2),('model_calls',1),
    ('semantic_budget_reset',True),('original_receipt',{'path':'forged','sha256':'f'*64}),
    ('calls',{}),('retained_prefix',[])])
def test_projection_cannot_change_semantic_evidence(projection,field,value):
    p=current();p[field]=value
    with pytest.raises(ValueError):projection(ORIGINAL,p,ROOT)

def test_projected_source_operand_proof_must_be_identical(projection):
    p=current();p['source_warning_proof']['claim_count']-=1
    with pytest.raises(ValueError):projection(ORIGINAL,p,ROOT)
