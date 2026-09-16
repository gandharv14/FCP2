"""Closed prospective fleet successors for three explicitly handed-off size holds.

Uses the reviewed0073 gate pattern, exact original snapshots and completed Calls.
This command only creates isolated code and read-only inspection recipes.
"""
from pathlib import Path
import argparse,shutil,json
from scripts.fleet import build_0190_successor as B
from scripts.fleet.build_0073_successor import READER_SOURCE
ROOT=B.ROOT
CATALOG={
 '0173':('generated-03','a8788ff19357d70bb534dc4d0bac111b5d65fca06b7ba2e49584ae928e66519a',2,'hhu-server-4'),
 '0303':('generated-02','62c1004b4fed08320ab3d2e55fa49e0696e407e491df5534c40711ffa14e1c12',1,'hhu-server-5'),
 '0077':('generated-02','62c1004b4fed08320ab3d2e55fa49e0696e407e491df5534c40711ffa14e1c12',1,'hhu-server-6')}
def build(workbook,output):
 B.require(workbook in CATALOG,'unreviewed workbook');suffix,snapshot_sha,calls,host=CATALOG[workbook];home=B.Q/'authoring'/(workbook+'-'+suffix)
 previous={'path':str(home/'frozen-code.json'),'sha256':snapshot_sha};prior=B.read(previous);old=Path(prior['root']);files={r['relative']:r['sha256'] for r in prior['files']}
 B.require(B.record(old/'.pipeline-code.json')['sha256']==files.pop('.pipeline-code.json'),'old manifest changed')
 actual={str(p.relative_to(old)):B.sha(p.read_bytes()) for p in old.rglob('*') if p.is_file() and p.name!='.pipeline-code.json' and not {'__pycache__','.pytest_cache'}&set(p.parts)}
 B.require(actual==files,'entire old snapshot differs');output=Path(output).absolute();B.require(not output.exists(),'fresh isolated build required');output.mkdir(parents=True);candidate=output/'candidate';candidate.mkdir()
 for name in files:
  p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old/name,p)
 changes={}
 for name,edits in B.fleet_edits().items():
  edits=[(a,b.replace('0190 successor',workbook+' successor').replace('scripts.batch.harbor_normalization_existing_review','scripts.fleet.legacy_'+workbook)) for a,b in edits]
  raw=B.apply((old/name).read_bytes(),edits);(candidate/name).write_bytes(raw);changes[name]=dict(before=B.record(old/name),after=B.keep(output/'witnesses'/name,raw),edits=edits)
 additions={}
 for name in B.ADD:
  raw=(ROOT/name).read_bytes();p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);additions[name]=B.keep(output/'witnesses'/name,raw)
 legacy={}
 for p in sorted((home/'model-campaign').glob('*/attempt-*/plan.json')):
  outcome=B.read(B.record(p.parent/'outcome.json'));B.require(outcome['status']=='completed','unknown historical result remains held')
  for name,digest in outcome['evidence'].items():B.require(B.record(p.parent/name)['sha256']==digest,'old Call evidence differs')
  legacy[str(p.parent)]=dict(plan=B.record(p),outcome=B.record(p.parent/'outcome.json'),evidence=outcome['evidence'])
 B.require(len(legacy)==calls,'original Call inventory differs')
 profile=B.keep(output/'profile.json',dict(schema_version=workbook+'-reviewed-fleet-successor/v1',previous=previous,changes=changes,additions=additions,legacy_calls=legacy,semantic_budget_reset=False))
 reader='scripts/fleet/legacy_'+workbook+'.py';(candidate/reader).write_bytes(READER_SOURCE.replace('0073',workbook).replace('__PROFILE__',repr(profile)).encode())
 files={str(p.relative_to(candidate)):B.sha(p.read_bytes()) for p in sorted(candidate.rglob('*')) if p.is_file()};fingerprint=B.sha(B.canonical(files));B.keep(candidate/'.pipeline-code.json',files);code=output/'code'/fingerprint;code.parent.mkdir();candidate.rename(code)
 snapshot=B.keep(output/'snapshot.json',dict(root=str(code),files=[dict(relative=str(p.relative_to(code)),sha256=B.sha(p.read_bytes())) for p in sorted(code.rglob('*')) if p.is_file()]))
 # Bind original custody/budget before any remote process or lease mutation.
 plan=B.read(B.record(home/'environment/normalization'/(workbook+'-variable-sources')/'production-selection.json'))
 recipe=B.keep(output/'inspection-plan.json',dict(schema_version='harbor-size-hold-inspection/v1',workbook=workbook,host=host,snapshot=snapshot,profile=profile,home=str(home),tracker=B.record(home/'trackers/workbooks'/(workbook+'.json')),original_plan=B.record(home/'environment/normalization'/(workbook+'-variable-sources')/'production-selection.json'),budget=plan['budget'],expected_completed_calls=calls,source=plan['bindings']['original_raw_source']))
 return B.keep(output/'build.json',dict(workbook=workbook,host=host,snapshot=snapshot,profile=profile,inspection_plan=recipe,fingerprint=fingerprint,builder=B.record(__file__),reader_template=B.record(ROOT/'scripts/fleet/build_0073_successor.py'),base_builder=B.record(B.__file__),preserved_calls=calls,models_dispatched=0,semantic_budget_reset=False,original_artifacts_changed=False))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workbook',choices=sorted(CATALOG),required=True);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(a.workbook,a.output)))
