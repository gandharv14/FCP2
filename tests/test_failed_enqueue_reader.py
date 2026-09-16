from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.batch import harbor_failed_enqueue_reader as R

def fixture(tmp_path):
 def save(path,v):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(v));return R.binding(path)
 run=tmp_path/'worker';old=run/'steps/enqueue';new=run/'steps/enqueue-scope-002'
 initial=save(run/'initial.json',{});code=save(tmp_path/'worker.py',{})
 source=save(tmp_path/'source.json',{});authority=save(run/'authority.json',{'source':source})
 selection={'closed':True};lease={'owner':'owner','token':'retained','revision':20,'sha256':'a'*64}
 prior=save(run/'steps/dialogue-commit/complete.json',dict(step='dialogue-commit',status='completed',selection=selection,lease=lease))
 intent=save(old/'intent.json',dict(step='enqueue',previous=prior,selection=selection,lease=lease,initial=initial,code={code['path']:code['sha256']}))
 ex=save(old/'exception.json',dict(automatic_replay=False));proof=save(old/'readiness.json',dict(publication_ready=False,blockers=['context_bindings','context_bindings_final'],mutation_performed=False))
 cert=save(tmp_path/'certificate.json',dict(schema_version=R.SCHEMA,implementation=R.binding(R.__file__),worker_run=str(run),authority=authority,failed_intent=intent,failed_exception=ex,failed_readiness=proof,predecessor=prior,source=source,directory=str(new)))
 worker=SimpleNamespace(run=run);reader=SimpleNamespace(authority=authority,is_card=lambda w:w==worker)
 original=lambda w,phase:run/'steps'/phase
 W=SimpleNamespace(declared_namespace={'_source_cards':reader},step_directory=original,code=lambda w:{code['path']:code['sha256']})
 return cert,W,worker,old,new,save

def test_only_selected_enqueue_namespace_changes_and_failure_is_retained(tmp_path):
 cert,W,w,old,new,save=fixture(tmp_path);before={str(p):p.read_bytes()for p in tmp_path.rglob('*')if p.is_file()}
 R.install_projection(W,cert)
 assert W.step_directory(w,'enqueue')==new
 assert W.step_directory(w,'dialogue-commit')==old.parent/'dialogue-commit'
 assert not new.exists() and before=={str(p):p.read_bytes()for p in tmp_path.rglob('*')if p.is_file()}
 new.mkdir();save(new/'intent.json',{'actual_new_attempt':True})
 assert W.step_directory(w,'enqueue')==new # cold child sees the same selected journal

@pytest.mark.parametrize('change',['completed','uncertain','other_blocker','predecessor','source','code','namespace'])
def test_unrecognized_failure_never_maps_to_fresh_attempt(tmp_path,change):
 cert,W,w,old,new,save=fixture(tmp_path);p=R.read(cert)
 if change=='completed':save(old/'complete.json',{})
 elif change=='uncertain':p['failed_exception']=save(old/'exception.json',{'automatic_replay':True})
 elif change=='other_blocker':p['failed_readiness']=save(old/'readiness.json',dict(publication_ready=False,blockers=['dialogue_review_provenance'],mutation_performed=False))
 elif change=='predecessor':p['predecessor']=save(tmp_path/'foreign.json',{})
 elif change=='source':p['source']={}
 elif change=='code':Path(next(iter(R.read(p['failed_intent'])['code']))).write_text('changed')
 else:p['directory']=str(old.parent.parent/'enqueue-other')
 cert=save(Path(cert['path']),p)
 with pytest.raises((ValueError,KeyError)):R.install_projection(W,cert)
 assert not new.exists()
