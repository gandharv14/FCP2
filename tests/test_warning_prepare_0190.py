from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict
import json
import pytest
from scripts.fleet import warning_prepare_0190 as M
from scripts import harbor_campaign as C,harbor_instruction_campaign as I
from scripts.batch import harbor_dialogue_warning_requalification as Q
from test_dialogue_scope_discovery import fixture,ROOT
from test_dialogue_scope_equivalence import selected
from dataclasses import dataclass,replace

@dataclass
class OuterSelection:
 content: object
 runtime: object
 observation: object

def test_warning_scope_uses_owning_equivalence_and_retains_reviewed_selection(fixture,monkeypatch):
 content,tmp=selected(fixture,monkeypatch)
 original=OuterSelection(content,{'retained':'runtime'},{'retained':'observation'})
 old_bytes=Path(content.dialogue_scope_receipt['path']).read_bytes()
 updated,receipt=M.scope_context(original,tmp/'warning-scope',root=ROOT)
 assert replace(updated.content,dialogue_scope_receipt=content.dialogue_scope_receipt)==content
 assert updated.runtime==original.runtime and updated.observation==original.observation
 assert Path(content.dialogue_scope_receipt['path']).read_bytes()==old_bytes
 assert M.read(receipt)['previous']==content.dialogue_scope_receipt
 assert M.read(receipt)['semantic_attempts_added']==0
 with pytest.raises(ValueError,match='already exists'):
  M.scope_context(original,tmp/'warning-scope',root=ROOT)

def test_warning_scope_rejects_real_claim_change(fixture,monkeypatch):
 content,tmp=selected(fixture,monkeypatch)
 p=Path(content.dialogue_claims['path']);p.write_text('{}')
 with pytest.raises(ValueError):
  M.scope_context(OuterSelection(content,None,None),tmp/'warning-scope',root=ROOT)


def test_actual_coordinator_prior_accepts_real_direct_warning_permit(tmp_path,monkeypatch):
 lease=C.bootstrap.Lease('owner','fixture-token',24,'a'*64)
 permit=M.retain(tmp_path/'permit.json',dict(schema_version=Q.SCHEMA,lease=asdict(lease),selection=I.codec.encode(None)))
 w=SimpleNamespace(warning_requalification=permit)
 monkeypatch.setattr(I,'worker_encode',lambda value:dict(warning_requalification=value.warning_requalification))
 monkeypatch.setattr(I,'worker_decode',lambda value:SimpleNamespace(**value))
 monkeypatch.setattr(I,'selected',lambda row:(w,None,None))
 home=tmp_path/'handoff';home.mkdir()
 result=M.handoff(C,I,w,None,lease,None,None,permit,[permit],home)
 value=M.read(result);actual,selection,chosen=I._prior(result,value['descriptor'])
 assert actual['status']=='RETRY_READY'and actual['next_step']=='dialogue-review'and chosen==lease
 assert actual['checkpoint']==permit and M.read(permit)['schema_version']==Q.SCHEMA


def test_warning_handoff_rejects_permit_not_installed_on_actual_worker(tmp_path,monkeypatch):
 lease=C.bootstrap.Lease('owner','fixture-token',24,'a'*64)
 permit=M.retain(tmp_path/'permit.json',dict(schema_version=Q.SCHEMA,lease=asdict(lease),selection=I.codec.encode(None)))
 monkeypatch.setattr(I,'worker_encode',lambda value:dict(warning_requalification=None))
 monkeypatch.setattr(I,'worker_decode',lambda value:SimpleNamespace(**value));monkeypatch.setattr(I,'selected',lambda row:None)
 home=tmp_path/'handoff';home.mkdir()
 with pytest.raises(ValueError,match='warning checkpoint detached'):
  M.handoff(C,I,SimpleNamespace(),None,lease,None,None,permit,[permit],home)


def test_every_physical_model_entry_is_disabled(monkeypatch):
 import xl_transport
 from scripts.batch import harbor_model_executor as E
 for module,names in [(E,('execute','_open')),(xl_transport,('read_response','_read_response','model_slot'))]:
  for name in names:monkeypatch.setattr(module,name,getattr(module,name))
 M.no_models()
 for fn in (E.execute,E._open,xl_transport.read_response,xl_transport._read_response,xl_transport.model_slot):
  with pytest.raises(ValueError,match='every new model'):fn()


@pytest.mark.parametrize('field,value',[('source_sha256','x'),('version','x'),('fence',4),('owner','other'),('workbook','0029')])
def test_unselected_source_code_custody_rejected_before_import(tmp_path,field,value):
 p={k:None for k in M.FIELDS};p.update(schema_version=M.SCHEMA,workbook='0190',source_sha256=M.SOURCE,version=M.VERSION,
  code_root='/tmp/'+M.VERSION,owner='hhu-server-7:slot-2:0190-warning-continuation-001',fence=3);p[field]=value
 with pytest.raises(ValueError):M.identity(p)
