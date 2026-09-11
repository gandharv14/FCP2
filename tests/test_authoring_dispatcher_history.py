import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.batch import harbor_pre_audit_history as H
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def driver(monkeypatch):
 p=ROOT/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v3.py';monkeypatch.syspath_prepend(str(p.parent));spec=importlib.util.spec_from_file_location('dispatcher3',p);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d);return d
@pytest.mark.parametrize('fault',[None,'no_proof','policy_omits','source','curation','validator_rejects','fresh_with_proof'])
def test_history_requires_owning_typed_proof_and_exact_queue(driver,monkeypatch,fault):
 source={'path':'/original','sha256':'a','workbook_id':'0150'};curation={'path':'/curation','sha256':'b'};proof={'path':'/proof','sha256':'c'};candidate={'path':'/candidate','sha256':'d'}
 item={'history_class':'no_history' if fault=='fresh_with_proof' else 'legacy_model_artifacts','original_source':source,'preserved_curations':[curation]}
 row={'original':source,**({} if fault=='no_proof' else {'pre_audit_history':proof})};policy=NS(campaign_evidence=[candidate]+([] if fault=='policy_omits' else [proof]),history_roots=('/history',));intake=NS(bootstrap=NS(policy=policy,workbook='0150',candidate_manifest=candidate))
 monkeypatch.setattr(driver,'checked',lambda b:{'selected_candidates':[row]})
 seen=[]
 def validate(b,**kw):
  seen.append((b,kw))
  if fault=='validator_rejects':raise ValueError('uncertain model history')
  return {'original':{'path':'/changed','sha256':'a'} if fault=='source' else {k:source[k] for k in ('path','sha256')},'curations':[] if fault=='curation' else [curation]}
 monkeypatch.setattr(H,'validate',validate)
 if fault is None:
  assert driver.history_authority(item,intake,{'sources':[source]},ROOT)==proof
  assert seen==[(proof,{'workbook':'0150','original':{'path':'/original','sha256':'a'},'history_roots':('/history',),'known_ids':{'0150'}})]
 else:
  with pytest.raises(ValueError):driver.history_authority(item,intake,{'sources':[source]},ROOT)
