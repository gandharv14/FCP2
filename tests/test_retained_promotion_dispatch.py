"""Explicit private-proof dispatch; these mocks do not establish qualification."""
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.batch import harbor_promotion_evidence as promotion
from scripts import harbor_verification_package as package


class NativeBoundary(Exception):
    pass


@pytest.fixture
def dispatch(monkeypatch):
    native, private = [], []
    result = {'state':'PROMOTED', 'verified_promoted':True, 'revalidation_required':False,
              'release_id':'fixture-release', 'private_verification':{'fixture':True}}
    def path(value):
        native.append(value)
        raise NativeBoundary('entered unchanged native tracker validation')
    def retained(*args, **kwargs):
        private.append((args, kwargs))
        return result
    monkeypatch.setattr(promotion.schema, 'safe_path', path)
    monkeypatch.setattr(package, 'qualify_retained_release', retained)
    return native, private, result


def selected():
    return promotion.PrivateVerificationSelection(
        Path('/fixture/private-package'), 'a'*64, 'sha256:'+'b'*64)


@pytest.mark.parametrize('explicit_none', [False, True])
def test_default_still_enters_native_tracker_validation(dispatch, explicit_none):
    native, private, _ = dispatch
    options = {'private_verification':None} if explicit_none else {}
    with pytest.raises(NativeBoundary):
        promotion.validate({'workbook_id':'0001'}, Path('/fixture/tracker.json'),
                           trusted_public_key='/fixture/key.pem', **options)
    assert native == [Path('/fixture/tracker.json')] and private == []


@pytest.mark.parametrize('value', [True, False, {}, {'bundle':'/fixture/private-package'},
                                   SimpleNamespace(bundle=Path('/fixture/private-package'))])
def test_untyped_private_selection_rejected_before_either_verifier(dispatch, value):
    native, private, _ = dispatch
    with pytest.raises(ValueError, match='closed private'):
        promotion.validate({}, Path('/fixture/tracker.json'), trusted_public_key='/fixture/key.pem',
                           private_verification=value)
    assert native == [] and private == []


def test_selection_subclass_is_not_closed_type(dispatch):
    class Derived(promotion.PrivateVerificationSelection):
        pass
    with pytest.raises(ValueError, match='closed private'):
        promotion.validate({}, Path('/fixture/tracker.json'), trusted_public_key='/fixture/key.pem',
                           private_verification=Derived(Path('/fixture/package'), 'a'*64, 'sha256:'+'b'*64))
    assert dispatch[:2] == ([], [])


def test_private_selection_cannot_combine_native_runtime_producer(dispatch):
    with pytest.raises(ValueError, match='cannot combine'):
        promotion.validate({}, Path('/fixture/tracker.json'), trusted_public_key='/fixture/key.pem',
                           private_verification=selected(), runtime_producer=object())
    assert dispatch[:2] == ([], [])


def test_explicit_private_dispatch_preserves_arguments_and_owner_result(dispatch):
    native, private, expected = dispatch
    source = {'workbook_id':'0001','path':'/fixture/raw.xlsx','sha256':'c'*64}
    tracker = Path('/fixture/tracker.json')
    key = '/fixture/key.pem'
    selection = selected()
    result = promotion.validate(source, tracker, trusted_public_key=key,
                                private_verification=selection)
    assert result is expected and native == []
    assert private == [((selection.bundle, selection.manifest_sha256, selection.verifier_image),
                        {'source':source,'tracker_path':tracker,'trusted_public_key':key})]
    assert source == {'workbook_id':'0001','path':'/fixture/raw.xlsx','sha256':'c'*64}


def test_private_rejection_propagates_without_native_fallback(dispatch, monkeypatch):
    error = package.PackageError('fixture private proof rejected')
    def rejected(*args, **kwargs):
        raise error
    monkeypatch.setattr(package, 'qualify_retained_release', rejected)
    with pytest.raises(package.PackageError) as caught:
        promotion.validate({}, Path('/fixture/tracker.json'), trusted_public_key='/fixture/key.pem',
                           private_verification=selected())
    assert caught.value is error and dispatch[0] == []


def test_selection_is_immutable():
    value = selected()
    with pytest.raises(FrozenInstanceError):
        value.manifest_sha256 = 'd'*64
