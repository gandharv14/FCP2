"""The descriptor codec preserves evidence; owning validators decide validity."""
from dataclasses import replace
from pathlib import Path
import json
import pytest
from scripts import harbor_instruction_campaign as C
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_model_executor import Campaign


def binding(path, digit='a'):
    return {'path':str(path),'sha256':digit*64}


@pytest.fixture
def original(tmp_path):
    return C.worker.Worker(RootContext(tmp_path/'trackers',None),tmp_path/'worker',Campaign(tmp_path/'journal',tmp_path/'slots',1),root=C.ROOT)


@pytest.mark.parametrize('kind',[None,'retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry'])
def test_json_round_trip_preserves_exact_receipts_and_old_nat_history(original,tmp_path,kind):
    w=original
    if kind:
        w=replace(w,retry=binding(tmp_path/'prior-nat.json'))
        if kind!='retry':w=replace(w,**{kind:binding(tmp_path/(kind+'.json'),'b')})
    encoded=C.worker_encode(w)
    assert set(encoded)=={'context','run','campaign','root','retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery'}
    assert C.worker_decode(json.loads(json.dumps(encoded)))==w
    assert C.worker_encode(C.worker_decode(encoded))==encoded
    # Encoding a receipt must not manufacture a file or authorize any action.
    assert not original.run.exists()


@pytest.mark.parametrize('prior',[False,True])
def test_legacy_five_field_descriptor_remains_exact_no_new_permit(original,tmp_path,prior):
    w=replace(original,retry=binding(tmp_path/'prior.json')) if prior else original
    old=C.worker_encode(w);old.pop('dialogue_retry');old.pop('boundary_recovery');old.pop('dialogue_semantic_retry');old.pop('pretransport_recovery')
    decoded=C.worker_decode(old)
    assert decoded==w and decoded.dialogue_retry is None and decoded.boundary_recovery is None and decoded.dialogue_semantic_retry is None


def test_legacy_seven_field_descriptor_preserves_prior_boundary(original,tmp_path):
    w=replace(original,boundary_recovery=binding(tmp_path/'boundary.json'))
    old=C.worker_encode(w);old.pop('dialogue_semantic_retry');old.pop('pretransport_recovery')
    assert C.worker_decode(old)==w


def test_malformed_semantic_receipt_is_not_accepted(original,tmp_path):
    encoded=C.worker_encode(original)
    encoded['dialogue_semantic_retry']={'path':str(tmp_path/'permit.json'),'sha256':'invalid'}
    with pytest.raises(ValueError):C.worker_decode(encoded)


@pytest.mark.parametrize('change',['omit_dialogue','omit_boundary','extra','both','bad_hash','wrong_keys','empty_path','nonrecord'])
def test_partial_ambiguous_or_malformed_new_descriptor_rejected(original,tmp_path,change):
    d=C.worker_encode(replace(original,boundary_recovery=binding(tmp_path/'boundary.json')))
    if change.startswith('omit_'):d.pop(change.removeprefix('omit_')+'_retry' if change=='omit_dialogue' else 'boundary_recovery')
    if change=='extra':d['skip_validation']=True
    if change=='both':d['dialogue_retry']=binding(tmp_path/'dialogue.json')
    if change=='bad_hash':d['boundary_recovery']['sha256']='unverified'
    if change=='wrong_keys':d['boundary_recovery']['passed']=True
    if change=='empty_path':d['boundary_recovery']['path']=''
    if change=='nonrecord':d['boundary_recovery']=True
    with pytest.raises(ValueError):C.worker_decode(d)


def test_encoder_never_discards_conflicting_continuation(original,tmp_path):
    with pytest.raises(ValueError):C.worker_encode(replace(original,dialogue_retry=binding(tmp_path/'a'),boundary_recovery=binding(tmp_path/'b')))


def test_altered_well_formed_receipt_is_preserved_not_approved(original,tmp_path):
    # Hash validation is intentionally performed by the owning Worker, after
    # decoding. The codec cannot silently replace an attacker-changed record.
    w=replace(original,boundary_recovery=binding(tmp_path/'nonexistent'))
    d=C.worker_encode(w);d['boundary_recovery']={**d['boundary_recovery'],'sha256':'0'*64}
    changed=C.worker_decode(d)
    assert changed.boundary_recovery==d['boundary_recovery'] and changed!=w


@pytest.mark.parametrize('kind', ['code_repair', 'warning_requalification'])
def test_policy_repair_receipts_are_never_silently_dropped(original, tmp_path, kind):
    w = replace(original, **{kind: binding(tmp_path/(kind+'.json'))})
    if kind == 'warning_requalification':
        w = replace(w, dialogue_semantic_retry=binding(tmp_path/'semantic.json', 'b'))
    encoded = C.worker_encode(w)
    assert encoded[kind] == getattr(w, kind)
    assert C.worker_decode(json.loads(json.dumps(encoded))) == w
    encoded[kind] = {'path': str(tmp_path/'other'), 'sha256': 'invalid'}
    with pytest.raises(ValueError):
        C.worker_decode(encoded)


def test_warning_cannot_detach_consumed_semantic_history(original, tmp_path):
    with pytest.raises(ValueError):
        C.worker_encode(replace(original, warning_requalification=binding(tmp_path/'warning')))

def test_first_round_warning_keeps_exact_repaired_history(original,tmp_path):
    w=replace(original,warning_requalification=binding(tmp_path/'warning'),code_repair=binding(tmp_path/'repair','b'))
    assert C.worker_decode(C.worker_encode(w))==w
    from scripts.batch.harbor_dialogue_semantic_retry import SCHEMA
    permit=C.base.keep(tmp_path/'semantic-permit.json',dict(schema_version=SCHEMA,prior_continuations={'code_repair':w.code_repair}))
    complete=C.base.keep(tmp_path/'semantic-complete.json',dict(schema_version=SCHEMA,permit=permit))
    combined=replace(w,dialogue_semantic_retry=complete)
    assert C.worker_decode(C.worker_encode(combined))==combined
    with pytest.raises(ValueError,match='detached'):
        C.worker_encode(replace(combined,code_repair=binding(tmp_path/'other-repair','d')))
