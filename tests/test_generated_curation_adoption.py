from pathlib import Path
import json,hashlib
import pytest
from scripts.batch import harbor_generated_curation as G

def save(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return G.A.record(path)
@pytest.fixture
def case(tmp_path):
 source=tmp_path/'0197.xlsx';source.write_bytes(b'synthetic source');original=G.A.record(source);full={**original,'workbook_id':'0197'};history=tmp_path/'history';history.mkdir();queue=save(tmp_path/'queue.json',{'schema_version':'authoring-request-queue/v1','items':[{'workbook_id':'0197','original_source':full,'history_class':'no_history','preserved_curations':[],'preserved_history':[]}]})
 target={'ordered':['S!B1'],'fingerprint':{'sha256':'targets'}};sd=tmp_path/'sources/0197/generations/source';gd=tmp_path/'segments/0197/generations/seg';gd.mkdir(parents=True);cur=gd/'curation.toml';cur.write_text('targets=[]');cb=G.A.record(cur)
 sm=save(sd/'generation-manifest.json',{'generation_id':'source'});gm=save(gd/'generation-manifest.json',{'generation_id':'seg','curated_output_cells':target,'artifacts':{'curation.toml':{'sha256':cb['sha256'],'size_bytes':cur.stat().st_size}}})
 ledger={'schema_version':'corpus-workbook/v1','workbook_id':'0197','source':full,'build_history':[],'build':{'state':'VERIFIED','attempts':1,'bindings':{'curation_sha256':None},'curation_path':str(tmp_path/'absent/curation.toml'),'source_generation_id':'source','source_manifest_sha256':sm['sha256'],'segmentation_generation_id':'seg','segmentation_manifest_sha256':gm['sha256']}}
 lb=save(tmp_path/'ledger.json',ledger);proof={'schema_version':G.SCHEMA,'workbook':'0197','queue':queue,'ledger':lb,'source_manifest':sm,'segmentation_manifest':gm,'curation':cb,'ordered_targets':target,'history_roots':[str(history)]}
 return tmp_path,proof,original,ledger

def validate(case,proof=None):
 root,p,original,_=case;b=save(root/'proof.json',p if proof is None else proof);return G.validate(b,workbook='0197',original=original,history_roots=[root/'history'],known_ids={'0197'})

def test_first_generated_adoption_keeps_original_null_ledger(case):
 root,p,_,_=case;before=Path(p['ledger']['path']).read_bytes();assert validate(case)==p;assert Path(p['ledger']['path']).read_bytes()==before

@pytest.mark.parametrize('fault',['existing_input','second_attempt','non_null_input','unverified','history','targets','artifact','source','manifest','extra'])
def test_adoption_never_overrides_existing_or_changed_evidence(case,fault):
 root,p,original,ledger=case
 if fault=='existing_input':Path(ledger['build']['curation_path']).parent.mkdir();Path(ledger['build']['curation_path']).write_text('old')
 elif fault in {'second_attempt','non_null_input','unverified'}:
  if fault=='second_attempt':ledger['build']['attempts']=2
  if fault=='non_null_input':ledger['build']['bindings']['curation_sha256']=p['curation']['sha256']
  if fault=='unverified':ledger['build']['state']='STOPPED'
  p['ledger']=save(root/'ledger.json',ledger)
 elif fault=='history':
  q=G.A.read(p['queue']);q['items'][0]['preserved_curations']=[p['curation']];p['queue']=save(root/'queue.json',q)
 elif fault=='targets':p['ordered_targets']={'ordered':['S!C1']}
 elif fault=='artifact':Path(p['curation']['path']).write_text('changed');p['curation']=G.A.record(p['curation']['path'])
 elif fault=='source':Path(original['path']).write_bytes(b'changed')
 elif fault=='manifest':p['segmentation_manifest']=p['source_manifest']
 elif fault=='extra':p['ignore_mismatch']=True
 with pytest.raises(ValueError):validate(case)


def test_broken_original_input_symlink_is_not_absence(case):
 root,p,_,ledger=case
 target=Path(ledger['build']['curation_path']);target.parent.mkdir(parents=True);target.symlink_to(root/'missing')
 with pytest.raises(G.A.AdmissionError):validate(case)
