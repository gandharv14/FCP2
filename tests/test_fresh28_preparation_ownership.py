from pathlib import Path
import copy,importlib.util,json,sys
import pytest
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/fresh28-current-repair-v2/reconstruct_fresh28_v2.py'
@pytest.fixture
def driver(tmp_path,monkeypatch):
 monkeypatch.setattr(sys,'argv',[str(P),'check','0073']);spec=importlib.util.spec_from_file_location('fresh28_owned_driver',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 m.PREP=tmp_path/'canonical-preparation.json';m.HOME=tmp_path/'operation';m.HOME.mkdir()
 old=json.loads(m.OLD_PREP.read_text());item=old['selected_item'];proof={'history_roots':old['history_roots']};inventory=Path(old['inventory']['path']);value=m.preparation_value(item,proof,inventory);m.keep(m.PREP,value)
 return m,item,proof,inventory,value
def seal(m,value):return m.keep(m.HOME/'intent.json',value)['sha256']
def test_full_actual_ancestry_value_roundtrip_without_pipeline_effects(driver):
 m,item,proof,inventory,value=driver;assert m.checked_preparation(item,proof,inventory)==m.binding(m.PREP)
 intent=m.intent_value();sha=seal(m,intent);assert m.checked_child(sha,item,proof,inventory)==intent
@pytest.mark.parametrize('field,value',[('schema_version','detached/v1'),('previous_preparation',{}),('snapshot',{}),('queue',{}),('restriction_inventory',{}),('selected_item',{'workbook_id':'0064'}),('new_local_attempt_limit',2),('inner_memory_bytes',16*1024**3),('inner_timeout_seconds',1800),('outer_timeout_seconds',1800),('models',True),('automatic_retry',True)])
def test_rebound_but_wrong_preparation_rejected_by_child(driver,field,value):
 m,item,proof,inventory,prep=driver;prep[field]=value;m.PREP.write_text(json.dumps(prep));sha=seal(m,m.intent_value())
 with pytest.raises(AssertionError,match='whole preparation'):m.checked_child(sha,item,proof,inventory)
@pytest.mark.parametrize('field,value',[('preparation',{}),('driver',{}),('snapshot',{}),('fleet',{}),('timeout_seconds',2700),('memory_limit_bytes',16*1024**3),('models',True),('automatic_retry',True)])
def test_wrong_intent_with_valid_own_hash_rejected(driver,field,value):
 m,item,proof,inventory,_=driver;intent=m.intent_value();intent[field]=value;sha=seal(m,intent)
 with pytest.raises(AssertionError,match='whole owning intent'):m.checked_child(sha,item,proof,inventory)
def test_byte_identical_detached_preparation_path_rejected(driver,tmp_path):
 m,item,proof,inventory,_=driver;detached=tmp_path/'other-workbook.json';detached.write_bytes(m.PREP.read_bytes());intent=m.intent_value();intent['preparation']=m.binding(detached);sha=seal(m,intent)
 with pytest.raises(AssertionError,match='whole owning intent'):m.checked_child(sha,item,proof,inventory)
def test_child_validates_before_corpus_creation():
 text=P.read_text();child=text[text.index('def child(sha):'):];assert child.index('checked_child(')<child.index('CORPUS.mkdir()')
