"""Preflight agrees with real durable author capacity, without acquiring it."""
from copy import deepcopy
import pytest
from scripts.fleet import launch_job_v3 as L
from scripts.fleet.coordinator import Coordinator,Conflict
from test_fleet_author_capacity import fleet,activate,claim,auth
from test_author_capacity_extension import extension


def gate(c,workbook='2',**changes):
    status=c.status();current=next(w for w in status['workbooks']if w['workbook']==workbook)
    p=dict(new_owner='hhu-server-2:slot-1:next',expected_owner=current['owner'],
        expected_fence=current['fence'],**changes)
    before=deepcopy(status)
    L.author_capacity_gate(status,p,dict(workbook=workbook),current)
    assert status==before


def test_full_active_and_expired_hosts_refused_without_any_launch(tmp_path):
    c,now=fleet(tmp_path);activate(c);claim(c,0);claim(c,1)
    with pytest.raises(ValueError,match='capacity occupied'):gate(c)
    now[0]+=31
    with pytest.raises(ValueError,match='capacity occupied'):gate(c)
    assert {r['state']for r in c.status()['author_reservations']}=={'hold'}
    c.reconcile('0','exact stopped evidence')
    gate(c);assert claim(c,2)['state']=='active'


def test_only_exact_held_predecessor_may_be_subtracted(tmp_path):
    c,now=fleet(tmp_path);activate(c);claim(c,0);claim(c,1);now[0]+=31
    gate(c,'0')  # This launcher first reconciles this same stopped predecessor.
    with pytest.raises(ValueError,match='capacity occupied'):gate(c,'0',unclaimed_start={'proof':'selected'})
    s=c.status();w=s['workbooks'][0]
    p=dict(new_owner='hhu-server-2:slot-0:next',expected_owner='foreign',expected_fence=w['fence'])
    with pytest.raises(ValueError,match='capacity occupied'):L.author_capacity_gate(s,p,dict(workbook='0'),w)


def test_finished_reservation_and_publication_are_separate(tmp_path):
    c,_=fleet(tmp_path);activate(c);a=claim(c,0);claim(c,1)
    s=c.status();w=s['workbooks'][2]
    L.author_capacity_gate(s,dict(new_owner='hhu-server-2:publication:2:next'),dict(workbook='2'),w)
    c.finish(**auth(a),evidence='original stopped outcome',disposition='hold');gate(c)


def test_preflight_is_not_a_reservation_and_atomic_claim_still_wins(tmp_path):
    c,_=fleet(tmp_path);activate(c);claim(c,0);gate(c)
    other=Coordinator(c.database,clock=c.clock);claim(other,1)
    with pytest.raises(Conflict,match='capacity occupied'):claim(c,2)


def test_finite_extension_and_expiry_match_coordinator(tmp_path):
    c,now=fleet(tmp_path);activate(c)
    c.permit_author_capacity_extension(**extension(c,now))
    claim(c,0,'hhu-server-4:slot-0:first');claim(c,1,'hhu-server-4:slot-1:second')
    def check():
        s=c.status();w=s['workbooks'][2]
        L.author_capacity_gate(s,dict(new_owner='hhu-server-4:slot-2:next'),dict(workbook='2'),w)
    check();now[0]+=121
    with pytest.raises(ValueError,match='capacity occupied'):check()
