"""Actual-shaped regression: mutate a real code evidence file after sealing."""
from pathlib import Path
import importlib.util
import pytest
import test_dialogue_terminal as terminal_fixtures
from test_harbor_admission import setup
from test_slot_retirement import released, create, R

from scripts import harbor_retirement_history as H
FILE=Path(H.__file__)


@pytest.fixture(autouse=True)
def isolated_code_file(tmp_path,monkeypatch):
    code=tmp_path/'registered_claim_generator.py';code.write_text('# actual synthetic old code bytes\n')
    monkeypatch.setattr(terminal_fixtures,'P',code)
    return code


@pytest.fixture
def transaction(setup,monkeypatch,isolated_code_file):
    unknown=isolated_code_file.with_name('unregistered_payload.py')
    unknown.write_text('# synthetic evidence, not registered implementation code\n')
    # Enrich the existing synthetic owning-review fixture with an actual code
    # evidence row in its failure proof, so canonical notes bind that file too.
    class Context:
        def setattr(self,target,name,value):
            if target is terminal_fixtures.T and name == 'inspect':
                original=value
                def inspect(*args):
                    result=original(*args)
                    result['evidence']=H.N.evidence_list(result['evidence']+[H.record(isolated_code_file),H.record(unknown)])
                    return result
                value=inspect
            monkeypatch.setattr(target,name,value)
    return terminal_fixtures.transaction.__wrapped__(setup,Context())


@pytest.fixture
def sealed(released,monkeypatch):
    monkeypatch.setattr(H,'_policy',R._policy)
    binding=create(released)
    return released,binding


def validate(sealed):
    (t,args,root),binding=sealed
    return H.validate(binding,**{k:v for k,v in args.items() if k!='transaction'})


def test_real_live_code_change_reproduces_v1_gap_and_history_succeeds(sealed,isolated_code_file):
    (t,args,root),b=sealed
    isolated_code_file.write_text('# different registered generator bytes\n')
    with pytest.raises(ValueError,match='changed evidence'):
        R.validate(b,**{k:v for k,v in args.items() if k!='transaction'})
    result=validate(sealed)
    assert result['historical_observation'] and not result['current_qualification']
    assert [r['path'] for r in result['code_changes']]==[str(isolated_code_file)]


def test_history_never_calls_live_note_quality_or_original_validator(sealed,monkeypatch):
    monkeypatch.setattr(R,'validate',lambda *a,**k:pytest.fail('v1 quality path called'))
    monkeypatch.setattr(H.N,'validate_notes_pair',lambda *a,**k:pytest.fail('live evidence notes path called'))
    monkeypatch.setattr(H.T,'_material',lambda *a,**k:pytest.fail('historical code executed'))
    assert validate(sealed)['terminal']=='STOPPED'


@pytest.mark.parametrize('which',['noncode','archive_code','tracker','notes','original','complete','missing_archive','implementation','unknown_field'])
def test_changed_immutable_evidence_rejects(sealed,isolated_code_file,which):
    (t,args,root),b=sealed;s=H.read(b)
    if which=='noncode':Path(t.source['path']).write_text('changed actual retained response')
    elif which=='archive_code':
        item=next(x for x in s['copies'] if x['original']['path']==str(isolated_code_file))
        Path(item['retained']['path']).write_text('changed archived code')
    elif which=='tracker':t.path.write_bytes(t.path.read_bytes()+b' ')
    elif which=='notes':Path(H.read(H.record(t.path))['agent_notes']['path']).write_bytes(b'changed notes')
    elif which=='original':Path(args['original']['path']).write_bytes(b'changed raw workbook')
    elif which=='complete':(Path(t.tx['path']).parent/'complete.json').write_bytes(b'{}')
    elif which=='missing_archive':Path(s['copies'][0]['retained']['path']).unlink()
    else:
        if which=='implementation':s['implementation']['sha256']='0'*64
        else:s['approved']=True
        Path(b['path']).write_bytes(H.P.canonical(s));sealed=(sealed[0],H.record(b['path']))
    with pytest.raises((ValueError,KeyError,FileNotFoundError)):validate(sealed)


def test_pending_notes_transaction_rejects_even_after_code_change(sealed,isolated_code_file):
    (t,args,root),b=sealed
    isolated_code_file.write_text('# new registered code\n')
    pending=t.path.parent/'0001-notes-transactions/pending';pending.mkdir()
    (pending/'transaction.json').write_bytes(Path(t.tx['path']).read_bytes())
    with pytest.raises((ValueError,FileNotFoundError)):validate(sealed)


def test_code_suffix_does_not_make_an_unregistered_artifact_exempt(sealed,isolated_code_file):
    isolated_code_file.write_text('# registered changed code\n')
    isolated_code_file.with_name('unregistered_payload.py').write_text('# altered unregistered evidence\n')
    with pytest.raises(ValueError,match='non-code original changed'):validate(sealed)


def test_missing_registered_live_code_still_rejects(sealed,isolated_code_file):
    isolated_code_file.unlink()
    with pytest.raises(ValueError,match='regular evidence'):validate(sealed)


def test_archive_mutation_between_inventory_hash_and_consumption_rejects(sealed,isolated_code_file,monkeypatch):
    _,binding=sealed;seal=H.read(binding)
    item=next(x for x in seal['copies'] if x['original']['path']==str(isolated_code_file))
    retained=Path(item['retained']['path']);real=H.record;changed=False
    def racing_record(path):
        nonlocal changed
        result=real(path)
        if Path(path)==retained and not changed:
            changed=True
            retained.write_bytes(b'changed archive after initial hash observation')
        return result
    monkeypatch.setattr(H,'record',racing_record)
    with pytest.raises(ValueError,match='hash|archive|changed'):validate(sealed)
    assert changed
