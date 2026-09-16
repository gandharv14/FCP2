"""Real owning deterministic step on isolated copies; custody is a test fixture."""
import json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'diagnostics/vm-fleet-20260912/holds-0017/gated-successor-004'

SCRIPT=r'''
import json,sys,shutil,os
from pathlib import Path
from dataclasses import replace
root=Path(sys.argv[1]);out=Path(sys.argv[2]);sys.path.insert(0,str(root))
from scripts.fleet import disclosure_0017_proof as P,disclosure_0017_repair as Q
from scripts.batch import harbor_instruction_worker as W,harbor_model_executor as E
from scripts import harbor_instruction_campaign as I
home=Path(P.profile()['history']['initial']['path']).parent.parent
call_inventory={str(p):P.sha(p.read_bytes()) for p in (home/'model-campaign').rglob('*') if p.is_file()}
d=P.read(P.record(home/'dispatcher-v2/first/008/descriptor.json'));oldw=I.worker_decode(d['worker'])
original=P.read(P.profile()['history']['initial']);s=W.codec.decode(original['selection']);lease=W.Lease(**original['lease'])
stage=out/'stages/0017-outputs';shutil.copytree(s.content.stage,stage)
run=out/'instruction';run.mkdir();shutil.copytree(oldw.run/'producer',run/'producer')
W.keep(run/'initial.json',original)
s=W.refreshed(replace(s,content=replace(s.content,stage=stage)))
permit=W.keep(out/'repair/permit.json',dict(schema_version=Q.SCHEMA,phase='disclosure-build',selection=W.codec.encode(s),lease=original['lease'],predecessor=None,source_proof=P.profile()['source_proof']))
w=replace(oldw,root=root,run=run,code_repair=permit)
tracker=P.record(home/'trackers/workbooks/0017.json');before=P.read(tracker)
W.active=lambda *a:(None,before,tracker)
Q.validate=lambda *a:P.read(permit)
P.require_fleet_custody=lambda *a:dict(test_fixture=True)
E.execute=lambda *a,**k:(_ for _ in ()).throw(AssertionError('test forbids all model calls'))
E._open=E.execute;W.roles.prepare=E.execute;W.roles.consume=E.execute
result=W.step(w,s,lease,checkpoint=permit,models=False,containers=False,commands=True)
assert result.status=='STEP_COMPLETE' and result.next_step=='disclosure-review'
complete=P.read(result.evidence);assert complete['step']=='disclosure-build' and complete['status']=='completed'
intent=P.read(complete['intent']);assert intent['previous']==permit and intent['initial']==P.record(run/'initial.json')
renderer=W.producer.skill_module(root,'task-disclosure','disclose');faith=renderer.faithcheck_task(stage,out/'checks',P.read(P.profile()['source_proof'])['bindings']['source']['path']);assert faith['passed']
extract=W.producer.skill_module(root,'additional-assumptions-dialogue','extract_claims');claims=extract.extract(stage)
assert len(claims['claims'])==47
assert not any((Q.producer_run(w)/'requests/disclosure-review').glob('*'))
assert not (out/'repair/steps/disclosure-build/before-00').exists()
for name in ('after-01','after-02','after-03','after-04'):
 assert P.read(P.record(out/'repair/steps/disclosure-build'/name/'complete.json'))['returncode']==0
try:W.step(w,s,lease,checkpoint=permit,models=False,containers=False,commands=True)
except Exception:pass
else:raise AssertionError('replayed original repaired-build input')
assert call_inventory=={str(p):P.sha(p.read_bytes()) for p in (home/'model-campaign').rglob('*') if p.is_file()}
print(json.dumps(dict(status=result.status,next_step=result.next_step,claims=len(claims['claims']),model_calls=0,role_calls_replayed=0,review_limit=1)))
'''

def test_actual_remaining_commands_commit_owning_checkpoint_without_role_replay(tmp_path):
    root=json.loads((BUILD/'snapshot.json').read_text())['root']
    result=subprocess.run([sys.executable,'-I','-B','-c',SCRIPT,root,str(tmp_path)],capture_output=True,text=True,timeout=180,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout.splitlines()[-1])['claims']==47

def test_original_review_budget_and_all_unrelated_worker_functions_preserved():
    import ast
    snapshot=json.loads((BUILD/'snapshot.json').read_text());root=Path(snapshot['root'])
    profile=json.loads((BUILD/'profile.json').read_text());old=Path(json.loads(Path(profile['previous']['path']).read_text())['root'])
    assert (root/'scripts/batch/harbor_instruction_producer.py').read_bytes()==(old/'scripts/batch/harbor_instruction_producer.py').read_bytes()
    def functions(path):return {n.name:ast.dump(n) for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef)}
    before=functions(old/'scripts/batch/harbor_instruction_worker.py');after=functions(root/'scripts/batch/harbor_instruction_worker.py')
    assert {n for n in before if before[n]!=after[n]}=={'task','_operation','_step'}
    assert '1,1,body,prompt' in (root/'scripts/batch/harbor_instruction_producer.py').read_text()

def test_live_custody_is_required_before_any_repair_reservation(tmp_path):
    root=json.loads((BUILD/'snapshot.json').read_text())['root']
    code='''
import sys,os
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
for key in list(os.environ):
 if key.startswith('HARBOR_FLEET_'):os.environ.pop(key)
from scripts.fleet import disclosure_0017_repair as Q
try:Q.authorize(SimpleNamespace(root=Path(sys.argv[1])),None,None,old_snapshot=None,directory=Path(sys.argv[2]),takeover=None)
except ValueError as exc:assert 'live fleet custody' in str(exc)
else:raise AssertionError('adopted without fleet custody')
assert not Path(sys.argv[2]).exists()
'''
    result=subprocess.run([sys.executable,'-I','-B','-c',code,root,str(tmp_path/'permit')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr

def test_native_dependency_parent_is_created_and_never_replayed(tmp_path):
    import pytest
    from types import SimpleNamespace
    from scripts.fleet.disclosure_0017_continue import native_dependency
    def dependency(recipe,directory):
        assert recipe=='selected' and directory.is_dir()
        (directory/'dependency.json').write_text('{}')
        return {'path':str(directory/'dependency.json')}
    owner=SimpleNamespace(dependency=dependency);directory=tmp_path/'probe/dependency'
    assert native_dependency(owner,'selected',directory)['path']==str(directory/'dependency.json')
    with pytest.raises(ValueError,match='already exists'):native_dependency(owner,'selected',directory)
