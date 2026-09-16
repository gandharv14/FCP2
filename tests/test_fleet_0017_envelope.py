import ast,copy,json
from dataclasses import dataclass,asdict
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import recover_0017_envelope as X
ROOT=Path(__file__).resolve().parents[1]
HOME=ROOT/'diagnostics/vm-fleet-20260912/returned/0017-preparation-hold-001/offline-snapshot/home/gmahajan_labelbox_com/synthetic-data-fleet/0017-scope-repair-host3-001'
PLAN=json.loads((ROOT/'diagnostics/vm-fleet-20260912/control/0017-envelope-recovery-001/plan.json').read_bytes())
def read(name):return json.loads((HOME/name).read_bytes())
def test_actual_failed_shape_and_only_typed_envelope_fix():
 prior=read('guard/result.json');desc=read('guard/descriptor.json');permit=read('scope-repair/permit.json')
 X.validate_values(PLAN,read('guard/resource.json'),read('result.json'),prior,desc,permit)
 tree=ast.parse(Path(PLAN['campaign_module']['path']).read_text());node=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='_prior')
 @dataclass
 class Lease:owner:str;revision:int;sha256:str;token:str
 lease=Lease(**permit['lease']);mapping={'prior':prior,PLAN['descriptor']['path']:desc,PLAN['permit']['path']:permit}
 def readbound(x):return mapping[x if isinstance(x,str) else x['path']]
 base=SimpleNamespace(executor=SimpleNamespace(checked_bytes=lambda row:None),decode=lambda row:lease,bootstrap=SimpleNamespace(Lease=Lease))
 g=dict(read=readbound,require=X.require,SCHEMA=prior['schema_version'],RESUMABLE={'RETRY_READY'},codec=SimpleNamespace(decode=lambda row:row),base=base,asdict=asdict)
 exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-frozen-I._prior','exec'),g)
 with pytest.raises(KeyError,match='permit'):g['_prior']('prior',PLAN['descriptor'])
 env=X.envelope(PLAN['permit'],asdict(lease),PLAN['descriptor'],PLAN['malformed_checkpoint'],PLAN['tracker'],PLAN['failed_guard'],{'path':'newplan','sha256':'x'})
 mapping['completion']=env;adapted=copy.deepcopy(prior);adapted['checkpoint']={'path':'completion','sha256':'x'};mapping['adapted']=adapted
 assert g['_prior']('adapted',PLAN['descriptor'])[0]==adapted
 env['lease']['revision']+=1
 with pytest.raises(ValueError,match='lease/selection'):g['_prior']('adapted',PLAN['descriptor'])
def test_changed_permit_tracker_or_guard_fails_closed():
 values=[PLAN,read('guard/resource.json'),read('result.json'),read('guard/result.json'),read('guard/descriptor.json'),read('scope-repair/permit.json')]
 for index,mutate in [(0,lambda x:x['tracker'].update(sha256='0'*64)),(1,lambda x:x.update(returncode=0)),(3,lambda x:x['checkpoint'].update(sha256='0'*64)),(4,lambda x:x['worker']['code_repair'].update(sha256='0'*64)),(5,lambda x:x.update(review_limit=2)),(5,lambda x:x.update(models_added=1))]:
  selected=copy.deepcopy(values);mutate(selected[index])
  with pytest.raises(ValueError):X.validate_values(*selected)
def test_readonly_flag_is_mandatory_before_any_load(monkeypatch):
 monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
 monkeypatch.setattr(X,'load',lambda _:pytest.fail('load must not run'))
 with pytest.raises(ValueError,match='nonexecuting'):X.prepare({})
