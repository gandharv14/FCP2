from copy import deepcopy
from types import SimpleNamespace
import pytest
from scripts.fleet import completed_review_publication_context as M
from scripts.fleet import fresh_publication as F


def fixture():
    a=dict(reader='reader',plan='plan',completed_reader='completed')
    p=dict(schema_version='fleet-completed-instruction-continuation/v1',completed_review=True,helper='reader',previous='previous',bridge_profile='bridge',bridge='B',source={'sha256':'raw'},workbook='0001',outcome='actual-review')
    old=dict(prepared_review=True,original_job='job',source=p['source'],workbook='0001')
    rows=dict(plan=p,previous=old,job=dict(config='config'),config=dict(code='snapshot'),snapshot=dict(root='/original/root'),bridge=dict(implementation='B',source=p['source'],workbook='0001',outcome='actual-review',worker_root='/original/root'))
    return dict(disclosure_continuation=a,workbook='0001',source_sha256='raw',code_root='/original/root'),rows


@pytest.mark.parametrize('fault',[None,'source','root','bridge','kind','helper'])
def test_publication_version_is_bound_to_actual_review_and_original_worker(fault):
    plan,rows=fixture()
    if fault=='source':rows['plan']['source']={'sha256':'other'}
    if fault=='root':rows['bridge']['worker_root']='/different/root'
    if fault=='bridge':rows['bridge']['outcome']='other-review'
    if fault=='kind':rows['previous']['prepared_review']=False
    if fault=='helper':rows['plan']['helper']='different-reader'
    if fault:
        with pytest.raises(ValueError):M.version(plan,rows.__getitem__)
    else:assert M.version(plan,rows.__getitem__)=='root'


def test_omitted_ready_config_delegates_to_exact_completed_reader():
    calls=[];plan=dict(disclosure_continuation={'completed_reader':'bound'})
    def load(name,row):
        assert row=='bound'
        return SimpleNamespace(ready_config=lambda *args:calls.append(args)or 'original-config')
    pub=SimpleNamespace(load=load)
    assert F.ready_config(pub,plan,{},'job')=='original-config'
    assert calls==[(pub,plan,{},'job')]
    assert F.ready_config(pub,plan,{'config':'already-bound'},'job')=='already-bound'
    assert len(calls)==1
