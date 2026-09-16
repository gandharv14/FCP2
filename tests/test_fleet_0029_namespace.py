import json,os,subprocess,sys
from pathlib import Path
import pytest
from scripts.fleet import build_0029_dispatcher as B
from scripts.fleet.legacy_0029_namespace import binding,checked
ROOT=Path(__file__).resolve().parents[1]
CERT=ROOT/'diagnostics/vm-fleet-20260912/holds-0029/namespace-001/certificate.json'
@pytest.fixture(scope='module')
def adapter(tmp_path_factory):return checked(B.build(binding(CERT),tmp_path_factory.mktemp('0029-namespace')/'adapter'))
def child(adapter,code):
    source='''import json,sys,importlib.util,os
from pathlib import Path
manifest='''+repr(adapter)+'''
spec=importlib.util.spec_from_file_location('selected0029',manifest['dispatcher']['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
job=json.loads(Path('''+repr(str(ROOT/'diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/authoring-job-template.json'))+''').read_bytes());job['driver']=manifest['dispatcher']
os.environ['HARBOR_FLEET_CONTINUATION_READ_ONLY']='1'
'''+code
    env={k:v for k,v in os.environ.items() if not k.startswith('HARBOR_')};r=subprocess.run([sys.executable,'-I','-B','-c',source],env=env,capture_output=True,text=True);assert r.returncode==0,r.stdout+r.stderr

def test_actual_history_and_all_later_calls_survive_without_remote_survey(adapter):
    child(adapter,'''
e=m.setup(job)
from scripts.batch import harbor_pre_audit_history as H,harbor_admission as A,harbor_model_executor as E
H.project=lambda *a,**k:(_ for _ in ()).throw(AssertionError('historical roots resurveyed or invented'))
e=m.setup(job);h,old=m.namespace.certificate();assert h['central_histories']['0029'] and len(h['retained_legacy_calls'])==8
assert old['model_history_route']=='pre_audit_not_entered' and h['model_history_route']=='eight_completed_calls_retained_no_reset'
for row in h['retained_legacy_calls'].values():assert m.checked(row['outcome'])['status']=='completed'
for fn in [lambda:A.DispatcherAdmission._check(None),lambda:E.execute(None,None,credential='fixture')]:
 try:fn()
 except ValueError:pass
 else:raise AssertionError('fresh admission or readonly execution accepted')
''')

def test_read_only_and_fresh_outcome_cannot_run(adapter):
    child(adapter,'''
e=m.setup(job)
try:m.namespace.require_instruction_outcome(job,e)
except ValueError as exc:assert 'read-only' in str(exc)
else:raise AssertionError('readonly driver executed')
os.environ.pop('HARBOR_FLEET_CONTINUATION_READ_ONLY')
from scripts.fleet import dialogue_0029_proof as P
P.require_fleet_custody=lambda root:None
try:m.namespace.require_instruction_outcome(job,e)
except ValueError as exc:assert 'only retained instruction' in str(exc)
else:raise AssertionError('original admission outcome used as continuation')
''')

def test_missing_live_custody_rejects_even_valid_history(adapter):
    child(adapter,'''
e=m.setup(job);os.environ.pop('HARBOR_FLEET_CONTINUATION_READ_ONLY')
try:m.setup(job)
except (KeyError,ValueError):pass
else:raise AssertionError('valid historical certificate replaced live ownership')
''')

def test_namespace_substitution_and_source_scope_rejected(adapter):
    child(adapter,'''
e=m.setup(job);h,old=m.namespace.certificate();original=m.namespace.PROFILE
m.namespace.PROFILE=dict(original,sha256='0'*64)
try:m.namespace.certificate()
except ValueError:pass
else:raise AssertionError('changed namespace certificate accepted')
m.namespace.PROFILE=original
cfg,intake,policy,known,proof=m.namespace._context(h['original_config'])
inventory=e[0].admission.read_inventory(policy.original_inventory['path']);item=m.queue_item(m.checked(job['queue']),'0029');item=dict(item,workbook_id='0190')
try:m.namespace.history_authority(item,intake,inventory,e[-2])
except ValueError:pass
else:raise AssertionError('0029 history used for foreign workbook')
''')

def test_publication_requires_its_actual_ready_and_typed_history(adapter):
    child(adapter,'''
e=m.setup(job)
base={'workbook':'0029','source_sha256':m.namespace.SOURCE,'code_root':str(e[-2]),'namespace_history':m.namespace.PROFILE,'namespace_adapter':m.binding(Path(manifest['dispatcher']['path']).parent/'manifest.json')}
for extra in ({'history':m.namespace.PROFILE},{'namespace_history':{'path':'/missing','sha256':'0'*64}},{'ready':{'path':'/missing','sha256':'0'*64}}):
 try:m.namespace.publication_history(dict(base,**extra))
 except (ValueError,KeyError):pass
 else:raise AssertionError('unqualified or mixed history allowed publication')
''')

def test_owning_dispatch_and_publication_algorithms_change_only_reviewed_hooks(adapter):
    import ast
    def functions(path):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(Path(path).read_bytes()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    old=functions(adapter['original_dispatcher']['path']);new=functions(adapter['dispatcher']['path'])
    assert old.keys()==new.keys()
    assert {k for k in old if old[k]!=new[k]}=={'history_authority','run'}
    old=functions(adapter['original_publication_driver']['path']);new=functions(adapter['publication_driver']['path'])
    assert old.keys()==new.keys()
    assert {k for k in old if old[k]!=new[k]}=={'history_binding','publication_environment','remote_child'}
    # In particular: model slot limits, guard timeouts, publication begin/end,
    # artifact transfer, collection idempotency and independent verification stay exact.

def test_publication_child_environment_preserves_fenced_identity_but_no_model_capability(adapter):
    child(adapter,'''
e=m.setup(job)
spec=importlib.util.spec_from_file_location('pub0029',manifest['publication_driver']['path']);pub=importlib.util.module_from_spec(spec);spec.loader.exec_module(pub)
pub.namespace.publication_history=lambda plan:m.namespace.PROFILE
os.environ['HARBOR_FLEET_MODEL_JOURNAL']='/stale/model-journal';os.environ['HARBOR_FLEET_OWNER']='stale';os.environ['OPENAI_API_KEY']='fixture-only'
auth=dict(workbook='0029',owner='fixture-owner',token='fixture-token',fence=9)
env=pub.publication_environment({'workbook':'0029','code_root':str(e[-2])},auth,action='remote',url='http://127.0.0.1:8765',token_file='/fixture/token')
assert env['HARBOR_FLEET_OWNER']=='fixture-owner' and env['HARBOR_FLEET_FENCE']=='9' and env['HARBOR_FLEET_CLAIM_TOKEN']=='fixture-token'
assert env['HARBOR_FLEET_0029_NAMESPACE_PATH']==m.namespace.PROFILE['path'] and env['HARBOR_FLEET_0029_NAMESPACE_SHA256']==m.namespace.PROFILE['sha256']
assert env['HARBOR_FLEET_PUBLICATION_ONLY']=='1' and 'HARBOR_FLEET_MODEL_JOURNAL' not in env and 'OPENAI_API_KEY' not in env
assert 'HARBOR_FLEET_HISTORY_PATH' not in env and 'HARBOR_FLEET_CONTINUATION_HISTORY_PATH' not in env
''')
