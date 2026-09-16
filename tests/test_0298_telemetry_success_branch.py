from pathlib import Path
import ast,hashlib,json
import pytest
ROOT=Path(__file__).resolve().parents[1]
DRIVER=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/four-resume-diagnostics/resume_0298_telemetry_v2.py'
def binding(p):return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
def checked(b):
 p=Path(b['path']);assert binding(p)==b;return p
def run_branch(tmp_path,change=None):
 targets=['Sheet!A'+str(i) for i in range(1,16)]
 cur=tmp_path/'curation.toml';cur.write_text('\n'.join('[[output]]\nband = '+json.dumps(t)+'\ninclude = true\n' for t in targets))
 evidence=tmp_path/'segments.json';evidence.write_text(json.dumps({'verification':{'ordered_output_cells':targets,'outputs':{'match':15,'mismatch':0,'unresolved':0,'unverifiable':0}}}))
 ctx={'a':{'curation':binding(cur),'expected_targets':15},'result':{'state':'VERIFIED','outputs':{'match':15,'mismatch':0,'unresolved':0,'unverifiable':0}},'after':{'build':{'verification_evidence':binding(evidence)}},'checked':checked,'json':json}
 if change:change(ctx,evidence,cur)
 tree=ast.parse(DRIVER.read_text());node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="result['state'] == 'VERIFIED'")
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(DRIVER),'exec'),ctx)
 return ctx

def test_real_success_shape_accepts_exact_fifteen(tmp_path):run_branch(tmp_path)
def test_old_integer_tally_rejected(tmp_path):
 with pytest.raises(AssertionError):run_branch(tmp_path,lambda c,e,p:c['result'].update(outputs=15))
def test_mismatch_tally_rejected(tmp_path):
 with pytest.raises(AssertionError):run_branch(tmp_path,lambda c,e,p:c['result']['outputs'].update(match=14,mismatch=1))
def test_evidence_bytes_changed_rejected(tmp_path):
 with pytest.raises(AssertionError):run_branch(tmp_path,lambda c,e,p:e.write_text('{}'))
def test_bound_wrong_order_rejected(tmp_path):
 def change(c,e,p):
  v=json.loads(e.read_text());v['verification']['ordered_output_cells'].reverse();e.write_text(json.dumps(v));c['after']['build']['verification_evidence']=binding(e)
 with pytest.raises(AssertionError):run_branch(tmp_path,change)
def test_duplicate_targets_rejected(tmp_path):
 def change(c,e,p):p.write_text(p.read_text().replace('Sheet!A15','Sheet!A14'));c['a']['curation']=binding(p)
 with pytest.raises(AssertionError):run_branch(tmp_path,change)
def test_stopped_result_does_not_claim_success(tmp_path):
 def change(c,e,p):c['result']={'state':'STOPPED','outputs':{'match':10,'mismatch':5}}
 assert run_branch(tmp_path,change)['result']['state']=='STOPPED'
