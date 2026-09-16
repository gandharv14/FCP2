from pathlib import Path
import copy,json
import pytest
from scripts.batch import harbor_legacy_staged_history as S,harbor_legacy_research_history as L
ROOT=Path(__file__).resolve().parents[1]
PROOF=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-002/histories-003/0176.json'

def test_actual_complete_legacy_source_authority_retains_empty_review_and_counts():
 p=S.validate(L.binding(PROOF))
 assert p['staged_history']['dialogue_rounds_upper_bound']==0
 assert p['new_source_reconstruction_ordinal']==2
 assert p['staged_history']['new_authoring_authorized'] is False
 assert len(S.terminal_empty_disclosure_evidence(p['audit_files']))==8

@pytest.fixture
def evidence(tmp_path):
 p=L.read(L.binding(PROOF));names=S.terminal_empty_disclosure_evidence(p['audit_files']);out={}
 for n in names:
  f=tmp_path/n;f.write_bytes(Path(p['audit_files'][n]['path']).read_bytes());out[n]=L.binding(f)
 return out

def change(e,n,fn):
 p=Path(e[n]['path']);value=json.loads(p.read_text());fn(value);p.write_text(json.dumps(value));e[n]=L.binding(p)

def resign(e):
 change(e,'disclosure-faithfulness-validation.json',lambda v:v.update(manifest_sha256=e['disclosure-sentence-manifest.json']['sha256'],review_sha256=e['disclosure-faithfulness.json']['sha256']))

@pytest.mark.parametrize('defect',['partial','unbound','review_fail','validation_fail','nonempty','foreign_input','foreign_manifest','grader_failure','oracle_failure','container_identity'])
def test_terminal_evidence_rejects_changed_or_nonterminal_bytes(evidence,defect):
 e=evidence
 if defect=='partial':e.pop('oracle-process.log')
 elif defect=='unbound':Path(e['oracle-build.log']['path']).write_text('changed')
 elif defect=='review_fail':change(e,'disclosure-faithfulness.json',lambda v:v.update(passed=False));resign(e)
 elif defect=='validation_fail':change(e,'disclosure-faithfulness-validation.json',lambda v:v.update(valid=False))
 elif defect=='nonempty':change(e,'disclosure-sentence-manifest.json',lambda v:v.update(agent_record_ids=['retained-call']));resign(e)
 elif defect=='foreign_input':change(e,'disclosure-faithfulness.json',lambda v:v.update(input_hashes={'foreign':'0'*64}));resign(e)
 elif defect=='foreign_manifest':change(e,'disclosure-faithfulness.json',lambda v:v.update(manifest_sha256='0'*64));resign(e)
 else:
  n={'grader_failure':'grader-process.log','oracle_failure':'oracle-process.log','container_identity':'oracle-container-id.log'}[defect];Path(e[n]['path']).write_text('FAILED');e[n]=L.binding(e[n]['path'])
 with pytest.raises(ValueError):S.terminal_empty_disclosure_evidence(e)

def test_legacy_without_extra_diagnostics_unchanged():
 assert S.terminal_empty_disclosure_evidence({})==set()
