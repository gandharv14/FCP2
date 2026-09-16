import copy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).parent))
from test_harbor_promotion_evidence import promoted
from test_harbor_tracker_lifecycle import authority, created, acquire
from scripts.batch import harbor_tracker_lifecycle as L
from scripts.batch import harbor_tracker as S
from scripts.batch import harbor_promotion_evidence as Q
from scripts.batch.harbor_admission import record


@pytest.mark.parametrize('fault', ['none', 'missing-proof', 'missing-validator', 'false-validator', 'foreign-token', 'wrong-cas'])
def test_expired_promoted_publication_release_is_exact_and_audited(tmp_path, monkeypatch, fault):
    path, state, source, selection, proof = promoted(tmp_path, monkeypatch)
    original = copy.deepcopy(state['tracker']);old = original['leases']['publication']
    stopped = tmp_path / 'stopped.json';stopped.write_text('{"stopped":true}')
    args=dict(authority=authority(),operation='release',lease_kind='publication',
        owner=old['owner'],scope=old['scope'],token=old['token'],
        expected_revision=state['revision'],expected_sha256=state['sha256'],clock=lambda:200,
        stopped_owner_proof=record(stopped),prior_owner_stopped_validator=lambda before,lease,bound: before==original and lease==old and bound==record(stopped))
    if fault=='missing-proof':args['stopped_owner_proof']=None
    elif fault=='missing-validator':args['prior_owner_stopped_validator']=None
    elif fault=='false-validator':args['prior_owner_stopped_validator']=lambda *a:False
    elif fault=='foreign-token':args['token']='wrong'
    elif fault=='wrong-cas':args['expected_revision']-=1
    prior=path.read_bytes()
    if fault!='none':
        with pytest.raises(ValueError):L.lease_operation(path,**args)
        assert path.read_bytes()==prior
    else:
        result=L.lease_operation(path,**args)
        Q.after_releases(original,result['tracker'])
        assert result['tracker']['leases']['publication'] is None
        assert result['tracker']['repairs']==original['repairs']
        assert result['tracker']['handoffs'][-1]['stopped_owner_proof']==record(stopped)
        assert L.validate_lifecycle(result['tracker'])==L.validate_lifecycle(original)


def test_expired_running_publication_cannot_be_released(tmp_path):
    path,state=created(tmp_path)
    state=acquire(path,state,lease_kind='publication',scope='publication:0245')
    old=state['tracker']['leases']['publication'];p=tmp_path/'stopped';p.write_text('stopped')
    before=path.read_bytes()
    with pytest.raises(ValueError,match='explicit recovery'):
        L.lease_operation(path,authority=authority(),operation='release',lease_kind='publication',owner=old['owner'],scope=old['scope'],token=old['token'],expected_revision=state['revision'],expected_sha256=state['sha256'],clock=lambda:200,stopped_owner_proof=record(p),prior_owner_stopped_validator=lambda *a:True)
    assert path.read_bytes()==before
