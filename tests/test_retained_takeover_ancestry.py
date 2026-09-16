import copy
from types import SimpleNamespace

import pytest

from scripts.fleet import instruction_semantic_continue as M


def chain(tmp_path):
    result={'path':'original-review','sha256':'original'}
    old=dict(campaign_root='/campaign',workbook='0102',worker_kind='instruction',
             guard={'path':'original-guard'},config={'path':'original-config'},
             tracker={'path':'/campaign/tracker','sha256':'before'},
             checkpoint=result,prior_completion=None)
    stop=M.keep(tmp_path/'stop.json',{'selection':old})
    complete=M.keep(tmp_path/'complete.json',{'stopped_owner_proof':stop})
    new=dict(old,tracker={'path':'/campaign/tracker','sha256':'after'},
             checkpoint=complete,prior_completion=complete)
    calls=[]
    return old,new,result,SimpleNamespace(validate_completion=lambda row:calls.append(row)),calls


def test_original_and_second_takeover_keep_original_guard(tmp_path):
    old,new,result,t,calls=chain(tmp_path)
    assert M.retained_takeover_origin(old,result,t)==old
    assert calls==[]
    assert M.retained_takeover_origin(new,result,t)==old
    assert calls==[new['prior_completion']]


@pytest.mark.parametrize('key,value',[
    ('campaign_root','/foreign'),('workbook','0095'),('worker_kind','environment'),
    ('guard',{'path':'foreign'}),('config',{'path':'foreign'}),
    ('tracker',{'path':'/foreign/tracker'}),('prior_completion',None),
    ('checkpoint',{'path':'unrelated','sha256':'unrelated'}),
])
def test_reject_detached_ancestry(tmp_path,key,value):
    old,new,result,t,calls=chain(tmp_path)
    new[key]=value
    with pytest.raises(ValueError):M.retained_takeover_origin(new,result,t)


def test_original_completion_validator_remains_owning(tmp_path):
    old,new,result,t,calls=chain(tmp_path)
    def reject(row):raise ValueError('original takeover validation failed')
    with pytest.raises(ValueError,match='original takeover validation'):
        M.retained_takeover_origin(new,result,SimpleNamespace(validate_completion=reject))


def test_changed_prior_proof_is_rejected(tmp_path):
    old,new,result,t,calls=chain(tmp_path)
    (tmp_path/'stop.json').write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'):
        M.retained_takeover_origin(new,result,t)


def test_repeated_prior_path_is_rejected(tmp_path,monkeypatch):
    old,new,result,t,calls=chain(tmp_path)
    rows={new['prior_completion']['path']:{'stopped_owner_proof':{'path':'cycle'}},
          'cycle':{'selection':copy.deepcopy(new)}}
    monkeypatch.setattr(M,'read',lambda row:rows[row['path']])
    with pytest.raises(ValueError,match='cycle'):
        M.retained_takeover_origin(new,result,t)
