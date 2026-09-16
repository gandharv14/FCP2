from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import json,pytest
from scripts.fleet import disclosure_revision_cache as M,disclosure_command_continue as D
from scripts.batch import harbor_instruction_worker as W
from scripts import harbor_instruction_campaign as I
from test_instruction_worker import case,instruction_setup
from test_harbor_instruction_producer import setup

def keep(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,sort_keys=True)+'\n');return M.binding(p)

def fixture(tmp_path):
 original=tmp_path/'original';producer=tmp_path/'permit/producer';output=tmp_path/'continuation';output.mkdir()
 for root in (original,producer):
  keep(root/'disclosure/sentences.json',{'original':'review manifest'})
  keep(root/'disclosure/review.json',{'verdict':'rejected'})
  keep(root/'disclosure-build/verify.json',{'pass':True})
  keep(root/'requests/disclosure-review/attempt-01/request.json',{'original':'spent request'})
 p=dict(output=str(output),source_cache_resume={'path':'/proof','sha256':'a'*64})
 a=dict(producer_tree=M.tree(producer));w=SimpleNamespace(run=original.parent)
 # The actual worker's producer is original. Keep fixture path explicit.
 w=SimpleNamespace(run=tmp_path/'worker');(w.run).mkdir();original.rename(w.run/'producer');original=w.run/'producer'
 return p,a,producer,original,w,SimpleNamespace(producer_run=lambda _:producer)


def test_moves_exact_copied_cache_and_preserves_original_budget_bytes(tmp_path):
 p,a,producer,original,w,K=fixture(tmp_path);before=M.tree(original)
 receipt=M.move(p,a,w,K,keep)
 assert not(producer/'disclosure').exists()and not(producer/'requests').exists()
 assert M.tree(original)==before
 assert M.read(receipt)['semantic_budget_reset']is False
 M.cache_state(p,a,producer,original,continued=True)
 # A later real fresh request is allowed; the old build inputs remain exact.
 keep(producer/'disclosure/sentences.json',{'new':'actual candidate'})
 keep(producer/'requests/disclosure-review/request.json',{'new':'request'})
 M.cache_state(p,a,producer,original,continued=True)
 with pytest.raises(ValueError):M.move(p,a,w,K,keep)


@pytest.mark.parametrize('defect',['original','copied','request','archive','receipt','symlink','build'])
def test_no_archive_move_or_cold_reentry_with_foreign_or_missing_evidence(tmp_path,defect):
 p,a,producer,original,w,K=fixture(tmp_path)
 if defect in ('archive','receipt','build'):
  M.move(p,a,w,K,keep)
  if defect=='archive':(Path(p['output'])/'retained-producer-copy/disclosure/sentences.json').unlink()
  if defect=='receipt':keep(Path(p['output'])/'cache-move.json',{'fabricated':True})
  if defect=='build':keep(producer/'disclosure-build/verify.json',{'pass':False})
  with pytest.raises(ValueError):M.cache_state(p,a,producer,original,continued=True)
 else:
  if defect=='original':keep(original/'disclosure/sentences.json',{'foreign':True})
  if defect=='copied':keep(producer/'disclosure/sentences.json',{'foreign':True})
  if defect=='request':keep(producer/'requests/disclosure-review/request.json',{});a['producer_tree']=M.tree(producer)
  if defect=='symlink':(producer/'link').symlink_to(original/'disclosure/sentences.json')
  with pytest.raises(ValueError):M.move(p,a,w,K,keep)
  assert(producer/'disclosure').is_dir()


def test_same_consumed_permit_projects_only_new_review_step(case,tmp_path):
 worker,s,l,*_=case;permit=keep(tmp_path/'permit.json',{'old':'consumed'});worker=replace(worker,disclosure_revision=permit);p={'output':str(tmp_path/'new')};original=W.step_directory
 prior=original(worker,'disclosure-review')
 with M.step_scope(p,W,worker):
  assert W.step_directory(worker,'disclosure-review')==tmp_path/'new/steps/disclosure-review'
  assert W.step_directory(worker,'disclosure-build')==original(worker,'disclosure-build')
  foreign=replace(worker,run=tmp_path/'foreign')
  assert W.step_directory(foreign,'disclosure-review')==original(foreign,'disclosure-review')
 assert W.step_directory is original and W.step_directory(worker,'disclosure-review')==prior


def test_selection_preserves_actual_permit_source_and_consumed_round(tmp_path):
 old=dict(source_revision=True,owner='old',fence=2,output='/old',helper={'old':'helper'},source={'real':'source'},core={'frozen':'core'},review_limit=2)
 oldrow=keep(tmp_path/'old.json',old);proof=dict(schema_version=M.SCHEMA,helper=M.binding(M.__file__),plan=oldrow,checkpoint={},outcome={},worker_outcome={},step_intent={},exception={},producer_tree={},stage_tree={});a=keep(tmp_path/'proof.json',proof)
 p={**old,'owner':'new','fence':3,'output':'/new','helper':{'selected':'shell'},'source_cache_resume':a}
 assert M.selection(p)==(proof,old)
 for k,v in [('source',{'foreign':True}),('core',{'different':True}),('review_limit',3),('fence',4),('owner','old'),('output','/old')]:
  with pytest.raises(ValueError):M.selection({**p,k:v})
