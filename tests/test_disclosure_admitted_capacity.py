import copy
import pytest
from scripts.fleet import capacity_policy as P,disclosure_review_continue as R
from tests.test_fleet_quarantine import setup,retained
from tests.test_fleet_quarantine_host_capacity import permit,claim
from tests.test_physical_capacity_extension import fixture as extension_fixture,claim as extension_claim


def test_actual_admitted_second_slot_preserves_unknown_and_allows_transport(tmp_path):
    c,now,claims,args=setup(tmp_path);c.permit_quarantine_host_capacity(**permit(c,args));a=claim(c,'new-a')
    assert c.begin('model','new-call',request_sha256='e'*64,**a)['execute']
    s=c.status();op=next(o for o in s['operations']if o['operation_id']=='new-call');before=retained(c)
    assert len(P.model_admission_barriers(s,a['workbook'],a['owner']))==2
    R.admitted_capacity(P,s,a['workbook'],a['owner'],op)
    assert retained(c)==before
    assert next(o for o in c.status()['operations']if o['operation_id']=='uncertain')['state']=='hold'


def test_two_isolated_unknowns_and_one_actual_admitted_call(tmp_path):
    c,now,claims,args=extension_fixture(tmp_path);c.permit_physical_capacity_extension(**args);a=extension_claim(c,'0095')
    assert c.begin('model','new-call',request_sha256='e'*64,**a)['execute'];s=c.status();op=next(o for o in s['operations']if o['operation_id']=='new-call')
    R.admitted_capacity(P,s,a['workbook'],a['owner'],op)
    assert len([o for o in c.status()['operations']if o['state']=='hold'])==2

@pytest.mark.parametrize('fault',['expired','over_limit','unquarantined_other_host','wrong_operation'])
def test_post_admission_keeps_original_guards(tmp_path,fault):
    c,now,claims,args=setup(tmp_path);c.permit_quarantine_host_capacity(**permit(c,args));a=claim(c,'new-a')
    assert c.begin('model','new-call',request_sha256='e'*64,**a)['execute'];s=c.status();op=next(o for o in s['operations']if o['operation_id']=='new-call')
    if fault=='expired':now[0]=2000;s=c.status()
    elif fault=='over_limit':s['operations'].append(dict(op,operation_id='foreign',workbook='foreign',owner='hhu-server:slot-2:foreign'))
    elif fault=='unquarantined_other_host':s['operations'].append(dict(op,operation_id='foreign',workbook='foreign',owner='hhu-server-6:slot-2:foreign',state='hold'))
    else:op=dict(op,operation_id='not-admitted')
    with pytest.raises(ValueError):R.admitted_capacity(P,s,a['workbook'],a['owner'],op)
