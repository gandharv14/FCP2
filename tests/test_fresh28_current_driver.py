from pathlib import Path
import ast,copy,json
import pytest
ROOT=Path(__file__).resolve().parents[1];Q=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z';P=Q/'fresh28-current-repair-v2/reconstruct_fresh28_v2_pending.py'
def checked_prior(payload=None):
 tree=ast.parse(P.read_text());nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.Import,ast.ImportFrom)) and (not isinstance(n,ast.FunctionDef) or n.name in {'binding','check','read','prior_preparation'})];ns={};exec(compile(ast.Module(body=nodes,type_ignores=[]),str(P),'exec'),ns)
 ns.update(OLD_PREP=Q/'fresh28-reconstruction-preparations/0073.json',OLD_DRIVER=Q/'admission-analysis/reconstruct_fresh28.py',OLD_DRIVER_SHA='ada254f1df98ed45f5c589b7abf06d50712f193f9675a60a782696fd74208396',OLD_SNAPSHOT=Q/'fresh28-restricted-snapshot-v1.json',OLD_SNAPSHOT_SHA='364e5dd49df62ec2e507f7e4576d55d9b85489096478ce83913fd50b92cabaaf',QUEUE=Q/'queue.json',QUEUE_SHA='96da9bbbd6ae180a02d0c78243fd5e9963e0b7d3dab7ccc48c2b1d4b2fb26aab',SOURCE_INVENTORY=Q/'restricted-cohort28-preparation/inventory.json',SOURCE_INVENTORY_SHA='312ffbb8bf466426dc637c40b9dd2a3589678e9ed9bffe26c88bdc6f3c26a2d1',BATCH='queue-extension-fresh28-restricted-20260911-v1',MINIMUM_FREE_DISK=12*1024**3)
 old=json.loads(ns['OLD_PREP'].read_text());item=old['selected_item'];proof={'history_roots':old['history_roots']};inventory=Path(old['inventory']['path'])
 if payload is not None:ns['read']=lambda b:payload
 return ns,item,proof,inventory,old
def test_exact_actual_unused_preparation_retained():
 ns,item,proof,inventory,old=checked_prior();assert ns['prior_preparation'](item,proof,inventory)==ns['binding'](ns['OLD_PREP'])
@pytest.mark.parametrize('key,value',[('models',True),('automatic_retry',True),('new_local_attempt_limit',2),('inner_timeout_seconds',1800),('inner_memory_bytes',16*1024**3),('outer_timeout_seconds',3000),('minimum_free_disk_bytes',0),('snapshot',{}),('driver',{}),('restriction_inventory',{}),('history_roots',[]),('selected_item',{})])
def test_changed_history_scope_or_guard_rejected(key,value):
 *_,old=checked_prior();bad=copy.deepcopy(old);bad[key]=value;ns,item,proof,inventory,_=checked_prior(bad)
 with pytest.raises(AssertionError):ns['prior_preparation'](item,proof,inventory)
def test_disabled_until_bound_and_same_attempt_namespace():
 text=P.read_text();assert "PENDING_REVIEWED_FROZEN_SNAPSHOT" in text
 assert "HOME=QROOT/('reconstruction-'+WB+'-attempt-001')" in text
 tree=ast.parse(text);run=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='run');chunk=ast.get_source_segment(text,run)
 assert chunk.index('assert not HOME.exists()')<chunk.index('fleet.fleet_slot')<chunk.index('HOME.mkdir')
 assert 'guardian_lock_fds=fds' in chunk
