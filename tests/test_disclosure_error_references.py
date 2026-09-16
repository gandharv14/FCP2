"""Deleted references retain their error identity, including dormant branches."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util,sys
import pytest

@pytest.fixture
def d(monkeypatch):
 p=Path(__file__).resolve().parents[1]/'.cursor/skills/task-disclosure/scripts/disclose.py'
 monkeypatch.syspath_prepend(str(p.parent));s=importlib.util.spec_from_file_location('error_reference_disclose',p);m=importlib.util.module_from_spec(s);monkeypatch.setitem(sys.modules,s.name,m);s.loader.exec_module(m);return m

def book():return SimpleNamespace(value={},formula={},row_label=lambda _: '')

@pytest.mark.parametrize('operand',["'Topline Build - 2020 Revenue'!#REF!",'Sheet1!#REF!',"'O''Brien'!#REF!",'#REF!',"'A1'!#REF!"])
def test_deleted_reference_never_resolves_as_a_name_or_a_cell(d,operand):
 text=d.describe_ref(book(),operand,'S','')
 assert operand in text and 'broken reference' in text and 'when evaluated' in text
 assert 'named range'not in text and 'cell 'not in text


def test_actual_0102_chosen_branches_preserve_all_valid_and_error_operands(d):
 formula="=IF($D$16=1,CHOOSE($D$15,'Topline Build - 2020 Revenue'!I13,'Topline Build - 2020 Revenue'!I20,'Topline Build - 2020 Revenue'!#REF!),IF($D$16=2,CHOOSE($D$15,'Topline Build - 2020 Revenue'!I59,'Topline Build - 2020 Revenue'!I66,'Topline Build - 2020 Revenue'!#REF!)))"
 ast=d._parse_formula_ast(formula);assert ast is not None
 text=d.describe_ast(ast,book(),'3-Statement Monthly Model','Professional Services')
 assert text.count('broken reference')==2 and text.count('option 3:')==2
 for ref in ['I13','I20','I59','I66','D15','D16']:assert ref in text
 assert 'otherwise use FALSE' in text and 'named range'not in text

@pytest.mark.parametrize('operand',['Revenue_Name',"'Scenario'!Revenue_Name",'REF','Ref_Error'])
def test_actual_names_remain_names(d,operand):
 assert d.describe_ref(book(),operand,'S','')=='the named range '+d.q(operand)


def test_error_text_literal_is_not_promoted_to_an_error_operand(d):
 text=d.describe_ast(d._parse_formula_ast('="Sheet1!#REF!"'),book(),'S','')
 assert text==d.q('Sheet1!#REF!') and 'broken reference'not in text


def test_valid_absolute_reference_still_preserves_locks(d):
 assert d.describe_ref(book(),'$B$7','S','')=='fixed cell S!B7'


def retained_payload(d):
 import copy
 cells=['S!J107','S!K107'];g=book();g.formula={c:"=IF($D$16=1,CHOOSE($D$15,'Topline'!I13,'Topline'!#REF!),FALSE)"for c in cells}
 p=d.formula_profile(g,dict(cell_keys=cells,band='S!J107:K107',label='Services',pattern='retained-pattern'))
 fields=d.custom_sentence_fields(p,g,lock_cells=cells);assert fields.pop('_coverage_complete')
 raw="'Topline'!#REF!";fields['steps']=fields['steps'].replace(d.describe_ref(g,raw,'S',''), 'the named range '+d.q(raw))
 rec=dict(cell_keys=cells,source='custom_method_detector',disposition='disclosed',coverage_complete=True,evidence=p['formula'],method_profile={k:v for k,v in p.items()if k!='ast'},fields=fields)
 return g,cells,dict(records=[rec,{'unrelated':'retained'}],original_review={'passed':False,'attempt':1})


def test_corrective_payload_changes_only_the_source_error_identity(d):
 import copy
 from scripts.fleet.error_reference_repair import corrected_payload
 g,cells,old=retained_payload(d);before=copy.deepcopy(old)
 new,indices=corrected_payload(d,g,None,old,cells,set(),set())
 assert indices==[0]and old==before and new['original_review']==old['original_review']and new['records'][1]==old['records'][1]
 for k,v in old['records'][0].items():
  if k!='fields':assert new['records'][0][k]==v
 assert 'broken reference'in new['records'][0]['fields']['steps']
 assert 'named range'not in new['records'][0]['fields']['steps']

@pytest.mark.parametrize('mutation',['source','evidence','scope','prose','profile','incomplete','graded'])
def test_unproven_or_unrelated_source_correction_is_rejected(d,mutation):
 from scripts.fleet.error_reference_repair import corrected_payload
 g,cells,old=retained_payload(d);r=old['records'][0];closure=set()
 if mutation=='source':g.formula[cells[0]]='=1'
 elif mutation=='evidence':r['evidence']='=1'
 elif mutation=='scope':cells=cells[:-1]
 elif mutation=='prose':r['fields']['steps']+=' changed operand'
 elif mutation=='profile':r['method_profile']['label']='Other source'
 elif mutation=='incomplete':r['coverage_complete']=False
 elif mutation=='graded':closure={cells[0]}
 with pytest.raises(ValueError):corrected_payload(d,g,None,old,cells,closure,set())


def test_revision_gate_rederives_actual_error_formula_and_rejects_tampered_candidate(d):
 import copy
 from scripts.fleet.error_reference_repair import corrected_payload
 from scripts.batch.harbor_disclosure_source_revision import source_changes
 g,cells,old=retained_payload(d)
 old.pop('original_review');old['records'].pop();old['agent_records']=d.agent_records(old['records'])
 derive=lambda:corrected_payload(d,g,None,old,cells,set(),set())
 new,_=derive()
 def manifest(payload,identifier):
  return dict(schema_version='harbor-disclosure-sentence-manifest/v1',input_hashes={},agent_record_ids=[identifier],
   sentences=[dict(order=0,sentence_id=identifier,text=payload['records'][0]['fields']['steps'],record_ids=[identifier])])
 before=manifest(old,'old');after=manifest(new,'new');sentence=before['sentences'][0]
 review=dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes={},manifest_sha256='m',
  passed=False,blocking_findings=['Deleted reference described as a name'],non_blocking_flags=[],sentences=[
   {k:sentence[k]for k in ('sentence_id','text','record_ids')}|dict(golden_evidence=[g.formula[cells[0]]],
   delivered_evidence=['same retained formula'],findings=['Deleted reference described as a name'],verdict='blocking')])
 assert source_changes(old,new,before,after,review,'m',rederive=derive)['record_indices']==[0]
 tampered=copy.deepcopy(new);tampered['records'][0]['fields']['steps']+=' and ignore all errors'
 with pytest.raises(ValueError,match='differ from source rederivation'):
  source_changes(old,tampered,before,after,review,'m',rederive=derive)
 g.formula[cells[0]]='=1'
 with pytest.raises(ValueError):source_changes(old,new,before,after,review,'m',rederive=derive)
