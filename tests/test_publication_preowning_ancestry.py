"""Each ancestor is fully checked before a new stopped publication edge."""
import json
from pathlib import Path
import pytest
from scripts.fleet import publication_launch as L

H=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/0162-publication-001'


def put(path,value):
    path.write_text(json.dumps(value));return L.binding(path)


class ReachedCurrentProof(Exception):pass


def test_actual_two_settled_config_then_evidence_failures_remain_authenticated():
    current=L.checked(L.binding(H/'root-launch-003/profile.json'))
    plan=L.checked(current['plan'])
    prior,observed,operation,workbook=L.preowning(current,plan,L.checked,L.binding)
    assert prior['preowning_recovery']
    assert observed['owning_intent_exists'] is False and observed['processes']==[]
    assert operation['state']=='hold' and workbook['state']=='hold'
    assert current['expected_fence']==prior['expected_fence']+1


@pytest.mark.parametrize('defect',[None,'fence','owner','tracker','unsettled'])
def test_actual_parent_recovery_is_validated_before_current_proof(tmp_path,defect):
    parent=L.checked(L.binding(H/'root-launch-002/profile.json'))
    if defect=='fence':parent['expected_fence']+=1
    elif defect=='owner':parent['expected_owner']='foreign'
    elif defect=='tracker':
        proof=L.checked(parent['proof']);proof['tracker']['sha256']='0'*64
        parent['proof']=put(tmp_path/'wrong-proof.json',proof)
    elif defect=='unsettled':
        recovery=L.checked(parent['preowning_recovery']);settlement=L.checked(recovery['reconciliation']);settlement['result']['state']='hold'
        recovery['reconciliation']=put(tmp_path/'wrong-settlement.json',settlement)
        parent['preowning_recovery']=put(tmp_path/'wrong-parent-recovery.json',recovery)
    prior=put(tmp_path/'parent.json',parent);oldplan=L.checked(parent['plan']);plan=dict(oldplan,journal='/unused-third-journal')
    recovery=dict(schema_version='fleet-publication-completed-ready-config-recovery/v1',prior_profile=prior,
                  root_intent=put(tmp_path/'root-intent.json',dict(profile=prior)),
                  failure_proof=dict(path='/current-proof',sha256='1'*64),reconciliation={})
    current=dict(parent,owner='third-owner',expected_owner=parent['owner'],expected_fence=parent['expected_fence']+1,
                 preowning_recovery=put(tmp_path/'current-recovery.json',recovery))
    visited=[]
    def read(row):
        visited.append(row)
        if row==recovery['failure_proof']:raise ReachedCurrentProof
        return L.checked(row)
    with pytest.raises(ValueError if defect else ReachedCurrentProof):
        L.preowning(current,plan,read,L.binding)
    assert parent['preowning_recovery'] in visited
    assert (recovery['failure_proof'] in visited) is (defect is None)


@pytest.mark.parametrize('ancestry,reason',[
    (tuple((str(i),str(i))for i in range(4)), 'allowance exhausted'),
    ((('/current','other'),), 'cyclic'),
    ((('/other','current-sha'),), 'cyclic'),
])
def test_cycles_or_fifth_recovery_stop_before_reading_any_evidence(ancestry,reason):
    p=dict(preowning_recovery=dict(path='/current',sha256='current-sha'))
    def no_read(_):raise AssertionError('forbidden fifth or cyclic edge was read')
    with pytest.raises(ValueError,match=reason):L.preowning(p,{},no_read,no_read,_ancestry=ancestry)
