from pathlib import Path
import pytest
from scripts.fleet import pool_v5 as P
from test_fleet_quarantine import setup,retained
G=1024**3
POLICY=dict(max_total_workers=4,max_author_workers=2,max_source_workers=2,headroom_bytes=4*G)
@pytest.fixture
def fleet(tmp_path,monkeypatch):
 c,now,claims,args=setup(tmp_path);c.quarantine_model(**args)
 monkeypatch.setattr(P,'native_census',lambda host:{})
 monkeypatch.setattr(P,'job_phase',lambda job:'author')
 monkeypatch.setattr(P,'resources',lambda _:dict(memory_available_bytes=100*G,memory_total_bytes=124*G,disk_free_bytes=100*G,load_average=[0,0,0],cpus=16))
 c.enroll('0001','f'*64,'new original');c.reconcile('0001','new source admitted','ready')
 job=dict(driver=P.binding(Path('scripts/fleet/retained_warning_continue.py').absolute()),workbook='0001',source=dict(sha256='f'*64),version='a'*64,run=str(tmp_path/'worker'),cwd=str(tmp_path),memory_limit_bytes=8*G,minimum_disk_bytes=12*G)
 jobs=[(dict(path=str(tmp_path/'job.json'),sha256='e'*64),job)]
 policy=P.binding(Path('scripts/fleet/capacity_policy.py').absolute())
 return c,now,claims,jobs,policy,tmp_path/'pool'

def reserve(f,host='hhu-server-8'):
 c,now,claims,jobs,policy,root=f
 return P.reserve(root,jobs,c.status(),0,host,POLICY,policy)

def test_exact_quarantine_allows_durable_next_dispatch_without_changing_unknown_call(fleet):
 c,_,_,jobs,_,_=fleet;before=retained(c);selected=reserve(fleet)
 assert selected and selected[1]==jobs[0][1] and retained(c)==before
 assert reserve(fleet)is None
 assert P.read(P.binding(selected[2]))['automatic_retry']is False

def test_marker_survives_lost_claim_reply_and_never_replays_even_after_reconciliation(fleet):
 c,_,_,_,_,_=fleet;selected=reserve(fleet);assert selected
 claim=c.claim('0001','hhu-server-8/slot-0/generation','once','a'*64,3600)
 auth={k:claim[k]for k in ('workbook','owner','token','fence')}
 # Reply may be lost; the durable supervisor dispatch marker still owns it.
 assert reserve(fleet)is None
 c.finish(**auth,evidence='stopped exact worker',disposition='hold');c.reconcile('0001','stopped reconciliation','ready')
 assert reserve(fleet)is None

def test_unrelated_held_publication_does_not_block_author(fleet):
 c,_,claims,_,_,_=fleet
 c.begin('publication','publication-held',request_sha256='e'*64,**claims['0141'])
 c.end('publication','publication-held',evidence='publication uncertainty',outcome='uncertain',**claims['0141'])
 before=retained(c);assert reserve(fleet);assert retained(c)==before
 assert next(o for o in c.status()['operations']if o['operation_id']=='publication-held')['state']=='hold'

def test_other_unknown_model_hold_still_blocks_every_new_dispatch(fleet):
 c,_,claims,_,_,root=fleet
 c.begin('model','second-unknown',request_sha256='e'*64,**claims['0140'])
 c.end('model','second-unknown',evidence='no response',outcome='uncertain',**claims['0140'])
 assert reserve(fleet)is None and not(root/'dispatches/0001.json').exists()

def test_quarantined_host_is_not_available_for_new_worker(fleet):
 with pytest.raises(ValueError):reserve(fleet,'hhu-server')

def test_expired_quarantine_restores_barrier(fleet):
 fleet[1][0]=2000
 assert reserve(fleet)is None

def test_target_active_or_held_operation_cannot_be_skipped(fleet):
 c,_,_,jobs,policy,root=fleet
 # In a real ledger an occupied target stays active/held, never centrally ready.
 claim=c.claim('0001','hhu-server-8/slot-0/original','original','a'*64,3600);auth={k:claim[k]for k in ('workbook','owner','token','fence')}
 c.begin('publication','target-publication',request_sha256='d'*64,**auth)
 assert reserve(fleet)is None
 c.end('publication','target-publication',evidence='uncertain target',outcome='uncertain',**auth)
 assert reserve(fleet)is None

def test_changed_selected_policy_refused_without_marker(fleet):
 c,_,_,jobs,policy,root=fleet
 with pytest.raises(ValueError):P.reserve(root,jobs,c.status(),0,'hhu-server-8',POLICY,{**policy,'sha256':'0'*64})
 assert not(root/'dispatches/0001.json').exists()

def test_legacy_pool_module_remains_strict(fleet):
 from scripts.fleet import pool_v4
 c,_,_,jobs,_,root=fleet
 assert pool_v4.reserve(root,jobs,c.status(),0,'hhu-server-8',POLICY)is None
