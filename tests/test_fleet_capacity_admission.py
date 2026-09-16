import copy
import pytest
from scripts.fleet.capacity_admission import operation_admission
from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256,CHANGE_ID


def fixture():
    claims=[];operations=[];known={}
    for i in range(16):
        w=f'{i:04}';known[w]={'version':'b'*64,'source':{'sha256':'a'*64}}
        claims.append(dict(workbook=w,owner=f'host-{i}',fence=2,state='active',claim_id=f'claim{i}',version='b'*64,source_sha256='a'*64,expires=1180))
        operations.append(dict(kind='model',operation_id=f'op{i}',workbook=w,owner=f'host-{i}',fence=2,state='active',started=990,expires=1180,ended=None,evidence=None,identity='c'*64))
    s=dict(schema_version='fleet-coordinator/v1',at=1000,model_limit=None,publication_limit=1,workbooks=claims,operations=operations,
           model_policy=dict(schema_version='fleet-concurrency-policy/v1',revision=1,model_limit=None,authorization=dict(path='/approval.json',sha256=AUTHORIZATION_SHA256),change_id=CHANGE_ID,per_workbook_model_limit=1,unknown_model_hold_barrier='global',publication_limit=1))
    return s,known


def test_sixteen_known_models_do_not_grant_dispatch_or_change_status():
    s,k=fixture();before=copy.deepcopy(s)
    v=operation_admission(s,{'workbook':'0999'},{'0999'},k)
    assert len(v['active_other_models'])==16 and v['model_limit'] is None
    assert v['model_permission_granted'] is False and s==before


@pytest.mark.parametrize('target,value',[('state','hold'),('kind','publication'),('fence',3),('owner','other'),('expires',999),('identity','x'),('evidence','terminal'),('started',float('nan'))])
def test_any_bad_concurrent_operation_blocks_admission(target,value):
    s,k=fixture();s['operations'][7][target]=value
    with pytest.raises(ValueError):operation_admission(s,{'workbook':'0999'},{'0999'},k)


def test_unknown_source_version_duplicate_or_own_operation_blocks():
    for change in ['source','version','duplicate','own']:
        s,k=fixture()
        if change=='source':s['workbooks'][7]['source_sha256']='d'*64
        if change=='version':s['workbooks'][7]['version']='d'*64
        if change=='duplicate':s['operations'].append(s['operations'][7].copy())
        target='0007' if change=='own' else '0999'
        with pytest.raises(ValueError):operation_admission(s,{'workbook':target},{target},k)


def test_boolean_claim_fence_cannot_equal_integer_operation_fence():
    s,k=fixture();s['workbooks'][0]['fence']=True;s['operations'][0]['fence']=1
    with pytest.raises(ValueError):operation_admission(s,{'workbook':'0999'},{'0999'},k)
