import json,subprocess,sys
from pathlib import Path
from scripts.fleet import dialogue_0029_continue as D
ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/plan.json'
def run(action,plan,tmp_path):
    bound=D.retain(tmp_path/'plan.json',plan)
    return subprocess.run([sys.executable,'-I','-B',str(ROOT/'scripts/fleet/dialogue_0029_continue.py'),action,'--plan',bound['path'],'--sha256',bound['sha256']],capture_output=True,text=True)
def test_actual_native_bound_recipe_has_no_model_permission(tmp_path):
    p=json.loads(PLAN.read_bytes());result=run('probe',p,tmp_path)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout);assert value['model_calls']==0 and value['execution_performed'] is False and value['review_ordinal']==1 and value['review_limit']==2

def test_original_source_and_tracker_substitution_rejected(tmp_path):
    p=json.loads(PLAN.read_bytes());p['source']['sha256']='0'*64
    result=run('probe',p,tmp_path);assert result.returncode!=0 and 'source changed' in result.stderr

def test_run_cannot_take_custody_from_mac_interpreter(tmp_path):
    p=json.loads(PLAN.read_bytes());p['home']=str(tmp_path/'never-created')
    result=run('run',p,tmp_path);assert result.returncode!=0 and 'selected native interpreter' in result.stderr
    assert not Path(p['home']).exists()

def test_once_only_namespace_blocks_probe_before_replay(tmp_path):
    p=json.loads(PLAN.read_bytes());home=tmp_path/'consumed';home.mkdir();(home/'intent.json').write_text('uncertain retained intent');p['home']=str(home)
    result=run('probe',p,tmp_path);assert result.returncode!=0 and 'namespace used' in result.stderr
    assert (home/'intent.json').read_text()=='uncertain retained intent'
