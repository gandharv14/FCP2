"""Closed 0073 prospective fleet gate, with pinned historical read-only Calls."""
from pathlib import Path
import argparse,json,shutil
from scripts.fleet import build_0190_successor as B
ROOT=B.ROOT
HOME=B.Q/'authoring/0073-generated-01'
PREVIOUS={'path':str(HOME/'frozen-code.json'),'sha256':'0b7abc02e80a724ec0a167a71c5e694746b12bc63fa39b53ceb6f856bfff4644'}
READER='scripts/fleet/legacy_0073.py'
READER_SOURCE='''"""Exact immutable 0073 historical inspection; never execution compatibility."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[2]
PROFILE=__PROFILE__
def require(ok,message):
 if not ok:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def record(path):return dict(path=str(Path(path).absolute()),sha256=sha(Path(path).read_bytes()))
def read(bound):
 require(record(bound['path'])==bound,'bound successor evidence changed')
 return json.loads(Path(bound['path']).read_bytes())
def inventory(root):
 return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file() and p.name!='.pipeline-code.json' and not {'__pycache__','.pytest_cache'}&set(p.parts)}
def fleet_profile():
 value=read(PROFILE)
 require(set(value)=={'schema_version','previous','changes','additions','legacy_calls','semantic_budget_reset'} and value['schema_version']=='0073-reviewed-fleet-successor/v1' and value['semantic_budget_reset'] is False,'closed reviewed profile required')
 previous=read(value['previous']);before={r['relative']:r['sha256'] for r in previous['files']};old=Path(previous['root'])
 require(record(old/'.pipeline-code.json')['sha256']==before.pop('.pipeline-code.json'),'old complete manifest changed')
 require(inventory(old)==before,'entire original snapshot changed')
 require(set(value['changes'])=={'scripts/batch/harbor_model_executor.py','xl_transport.py'} and set(value['additions'])=={'scripts/fleet/model_gate.py','scripts/fleet/coordinator.py'},'unreviewed gate roles')
 expected=dict(before)
 for name,change in value['changes'].items():
  require(change['before']==record(old/name),'unreviewed predecessor patch')
  raw=Path(change['before']['path']).read_text()
  for before_text,after_text in change['edits']:
   require(raw.count(before_text)==1,'patch witness must be exact');raw=raw.replace(before_text,after_text,1)
  require(record(change['after']['path'])==change['after'] and raw.encode()==Path(change['after']['path']).read_bytes(),'deterministic patch differs')
  expected[name]=change['after']['sha256']
 for name,b in value['additions'].items():require(record(b['path'])==b,'gate witness differs');expected[name]=b['sha256']
 now=inventory(ROOT);expected['scripts/fleet/legacy_0073.py']=now['scripts/fleet/legacy_0073.py']
 require(now==expected,'unreviewed full code inventory change')
 return value
def fleet_legacy_plan(root,current):
 value=fleet_profile();key=str(Path(root))
 require(key in value['legacy_calls'],'legacy call is not explicitly pinned')
 row=value['legacy_calls'][key]
 require(set(row)=={'plan','outcome','evidence'},'legacy call fields changed')
 old=read(row['plan']);out=read(row['outcome'])
 require(row['plan']==record(Path(root)/'plan.json') and row['outcome']==record(Path(root)/'outcome.json'),'legacy location changed')
 require(out['status']=='completed' and 'fleet_transport_policy' not in old,'uncertain or prospective call forbidden')
 require(row['evidence']==out['evidence'],'historical evidence inventory changed')
 for name,digest in row['evidence'].items():require(record(Path(root)/name)['sha256']==digest,'historical evidence changed')
 expected=dict(old);expected.update(fleet_transport_policy='single-dispatch-durable-global-gate/v1',max_transport_retries=0)
 require(canonical(current)==canonical(expected),'historical identity, budget or configuration changed')
 return old
'''
def build(output):
 output=Path(output).absolute();B.require(not output.exists(),'new immutable build required')
 prior=B.read(PREVIOUS);old=Path(prior['root']);files={r['relative']:r['sha256'] for r in prior['files']}
 B.require(B.record(old/'.pipeline-code.json')['sha256']==files.pop('.pipeline-code.json'),'historical manifest changed')
 actual={str(p.relative_to(old)):B.sha(p.read_bytes()) for p in old.rglob('*') if p.is_file() and p.name!='.pipeline-code.json' and not {'__pycache__','.pytest_cache'}&set(p.parts)}
 B.require(actual==files,'full historical snapshot differs');output.mkdir(parents=True);candidate=output/'candidate';candidate.mkdir()
 for name in files:
  p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old/name,p)
 changes={}
 for name,edits in B.fleet_edits().items():
  edits=[(a,b.replace('0190 successor','0073 successor').replace('scripts.batch.harbor_normalization_existing_review','scripts.fleet.legacy_0073')) for a,b in edits]
  raw=B.apply((old/name).read_bytes(),edits);(candidate/name).write_bytes(raw)
  changes[name]=dict(before=B.record(old/name),after=B.keep(output/'witnesses'/name,raw),edits=edits)
 additions={}
 for name in B.ADD:
  raw=(ROOT/name).read_bytes();p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);additions[name]=B.keep(output/'witnesses'/name,raw)
 legacy={}
 for p in sorted((HOME/'model-campaign').glob('*/attempt-*/plan.json')):
  out=B.read(B.record(p.parent/'outcome.json'));B.require(out['status']=='completed','historical uncertainty must remain held')
  for name,digest in out['evidence'].items():B.require(B.record(p.parent/name)['sha256']==digest,'old call evidence differs')
  legacy[str(p.parent)]=dict(plan=B.record(p),outcome=B.record(p.parent/'outcome.json'),evidence=out['evidence'])
 B.require(len(legacy)==3,'exact original completed call count changed')
 profile=B.keep(output/'profile.json',dict(schema_version='0073-reviewed-fleet-successor/v1',previous=PREVIOUS,changes=changes,additions=additions,legacy_calls=legacy,semantic_budget_reset=False))
 raw=READER_SOURCE.replace('__PROFILE__',repr(profile)).encode();(candidate/READER).write_bytes(raw)
 files={str(p.relative_to(candidate)):B.sha(p.read_bytes()) for p in sorted(candidate.rglob('*')) if p.is_file()};fingerprint=B.sha(B.canonical(files));B.keep(candidate/'.pipeline-code.json',files)
 code=output/'code'/fingerprint;code.parent.mkdir();candidate.rename(code)
 snapshot=B.keep(output/'snapshot.json',dict(root=str(code),files=[dict(relative=str(p.relative_to(code)),sha256=B.sha(p.read_bytes())) for p in sorted(code.rglob('*')) if p.is_file()]))
 return B.keep(output/'build.json',dict(snapshot=snapshot,profile=profile,builder=B.record(__file__),base_builder=B.record(B.__file__),fingerprint=fingerprint,preserved_calls=3,models_dispatched=0,semantic_budget_reset=False,original_artifacts_changed=False))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(a.output)))
