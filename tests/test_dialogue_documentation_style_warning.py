import copy
import pytest
from scripts.batch import harbor_dialogue_first_round_warning as W
from scripts.batch.harbor_readiness import skill_module,ROOT

@pytest.fixture
def case():
 c={'claims':[dict(record_id='source::Cash::001',row_label='Cash',spoken='Use the source; that’s the required plus adjustment, then stop.'),dict(record_id='method::Debt::002',row_label='Debt',spoken='From that result, minus debt. Then minus cash.')]}
 d='\n\n'.join('<!-- claim:'+r['record_id']+' -->\n**Analyst:** Which convention?\n\n**VP:** '+r['spoken'] for r in c['claims'])
 findings=['The file reads as a repetitive formula checklist rather than a natural Slack conversation: nearly every exchange follows the same scoped-cell question and implementation-answer pattern.',"The VP turn for source::Cash::001 reproduces the card's spoken line verbatim with a speaker prefix.",'Several senior turns use stilted, documentation-style wording such as “that’s the required plus adjustment,” “From that result, minus,” and “Then minus.”']
 return c,d,findings

@pytest.mark.parametrize('fault',[None,'wrong_claim','changed_spoken','missing_quote','wrong_speaker','substantive_suffix','missing_turn'])
def test_documentation_only_findings_require_actual_turn_evidence(case,fault):
 c,d,f=copy.deepcopy(case)
 if fault=='wrong_claim':f[1]=f[1].replace('source::Cash::001','foreign::001')
 elif fault=='changed_spoken':c['claims'][0]['spoken']='Different actual source.'
 elif fault=='missing_quote':f[2]=f[2].replace('Then minus','A nonexistent quote')
 elif fault=='wrong_speaker':d=d.replace('**VP:**','**Associate:**',1)
 elif fault=='substantive_suffix':f[0]+=' The result reverses the sign.'
 elif fault=='missing_turn':d=d.replace('**Analyst:** Which convention?','',1)
 V=skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
 review=dict(passed=False,naturalness=dict(verdict='fail',findings=[]),accuracy=dict(verdict='pass',claims=[dict(record_id=x['record_id'],must_say='entailed',findings=[])for x in c['claims']],extras=[],cell_refs_in_senior_turns=[]))
 observed=[]
 for finding in f:
  review['naturalness']['findings']=[finding];observed.append(bool(V.review_warnings(review,c,draft_text=d)))
 assert all(observed) if fault is None else not all(observed)

@pytest.mark.parametrize('fault',['extra','cellref','contradiction','missing_claim'])
def test_style_cannot_override_substantive_accuracy(case,fault):
 c,d,f=case;V=skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
 r=dict(passed=False,naturalness=dict(verdict='fail',findings=f),accuracy=dict(verdict='pass',claims=[dict(record_id=x['record_id'],must_say='entailed',findings=[])for x in c['claims']],extras=[],cell_refs_in_senior_turns=[]))
 if fault=='extra':r['accuracy']['extras']=['Unsupported result']
 elif fault=='cellref':r['accuracy']['cell_refs_in_senior_turns']=['C99']
 elif fault=='contradiction':r['accuracy']['claims'][0]['must_say']='contradicted'
 elif fault=='missing_claim':r['accuracy']['claims'].pop()
 assert V.review_warnings(r,c,draft_text=d)==[]

def test_selected_warning_function_is_independent_of_old_style_helper(case,tmp_path,monkeypatch):
 from scripts.fleet.dialogue_revision_adoption import warning_function,binding
 c,d,f=case;V=skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
 monkeypatch.setattr(W,'style_only',lambda *a:False)
 r=dict(passed=False,naturalness=dict(verdict='fail',findings=f),accuracy=dict(verdict='pass',claims=[dict(record_id=x['record_id'],must_say='entailed',findings=[])for x in c['claims']],extras=[],cell_refs_in_senior_turns=[]))
 selected=warning_function(V,binding(V.__file__))
 assert len(selected(r,c,draft_text=d))==3 and r['passed']is False
