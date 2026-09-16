"""Cold extractor linkage; no permit, source mutation or owning authority."""
from pathlib import Path
import sys
import pytest
from scripts.fleet import source_card_reader as R
from scripts.batch import harbor_instruction_producer as P

H = Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/control/0200-source-card-contract-001'


def fixture():
    reader = object.__new__(R.Reader)
    manifest = R.binding(H/'manifest-002.json')
    layout = R.checked(manifest)
    reader.plan = dict(renderer_manifest=manifest, dependencies=[manifest, *[r['new'] for r in layout['skill']]],
                       old_snapshot=R.binding(H/'native-prefix-003/ordered-snapshot.json'))
    reader.producer = P; reader._extractor = None
    return reader


def test_cold_extractor_loads_exact_disclosure_without_warm_bare_import(monkeypatch):
    monkeypatch.delitem(sys.modules, 'disclose', raising=False)
    reader = fixture()
    module = reader.extractor()
    original = R.checked(reader.plan['old_snapshot'])
    assert Path(module.disclose.__file__) == Path(original['root'])/'.cursor/skills/task-disclosure/scripts/disclose.py'
    assert 'disclose' not in sys.modules
    assert reader.extractor() is module and callable(module.extract)


def test_foreign_cached_skill_is_rejected_before_extract(monkeypatch):
    from types import SimpleNamespace
    reader = fixture()
    monkeypatch.setattr(P, 'skill_module', lambda *a: SimpleNamespace(__file__=R.__file__))
    with pytest.raises(ValueError, match='foreign cached'):
        reader.extractor()
    assert reader._extractor is None


def test_lazy_renderer_imports_remain_scoped_for_full_extract_and_failure(monkeypatch):
    import importlib.machinery
    from types import SimpleNamespace
    reader = fixture()
    names = ('aa_lib', 'spoken_formula', 'extract_claims', 'claim_scope', 'disclose', 'validate_dialogue')
    old_path = list(sys.path)
    foreign = SimpleNamespace(marker='unrelated preexisting caller module')
    monkeypatch.setitem(sys.modules, 'aa_lib', foreign)
    original = importlib.machinery.SourceFileLoader.exec_module
    fail = [False]

    def load(loader, module):
        original(loader, module)
        if module.__name__ == 'extract_claims':
            def extract(stage):
                # This real renderer performs its aa_lib import at call time.
                with pytest.raises(ValueError, match='source selection'):
                    module.speak_record({'entry': 'source_selection'})
                assert Path(sys.modules['aa_lib'].__file__).parent == H/'skill'
                if fail[0]:
                    raise RuntimeError('retained extraction failure')
                return stage
            module.extract = extract
    monkeypatch.setattr(importlib.machinery.SourceFileLoader, 'exec_module', load)
    before = {name: sys.modules.get(name) for name in names}
    extractor = reader.extractor()
    assert extractor.extract('sentinel') == 'sentinel'
    assert {name: sys.modules.get(name) for name in names} == before
    assert sys.path == old_path
    fail[0] = True
    with pytest.raises(RuntimeError, match='retained extraction failure'):
        extractor.extract('sentinel')
    assert {name: sys.modules.get(name) for name in names} == before
    assert sys.path == old_path
