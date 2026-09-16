import pytest
from scripts.fleet import instruction_semantic_continue as U


def test_separate_native_identity_is_exact_completed_custody():
    p=dict(owner='hhu-server-7:slot-1:0187-dialogue003',native_owner='harbor-orchestrator-0187-dialogue003',retained_context=dict(kind='completed_disclosure'),completed_takeover={})
    t=dict(selection=dict(owner=p['native_owner']))
    assert U.native_takeover_owner(p,t)==p['native_owner']
    assert p['owner']=='hhu-server-7:slot-1:0187-dialogue003'


@pytest.mark.parametrize('fault',['different-owner','kind','missing-completion','empty','not-string'])
def test_foreign_native_owner_mapping_rejected(fault):
    p=dict(owner='hhu-server-7:slot-1:0187-dialogue003',native_owner='native',retained_context=dict(kind='completed_disclosure'),completed_takeover={})
    t=dict(selection=dict(owner='native'))
    if fault=='different-owner':t['selection']['owner']='other'
    elif fault=='kind':p['retained_context']['kind']='other'
    elif fault=='missing-completion':del p['completed_takeover']
    elif fault=='empty':p['native_owner']=''
    elif fault=='not-string':p['native_owner']={}
    with pytest.raises(ValueError):U.native_takeover_owner(p,t)


def test_original_same_identity_contract_remains():
    assert U.native_takeover_owner(dict(owner='original'),{})=='original'
