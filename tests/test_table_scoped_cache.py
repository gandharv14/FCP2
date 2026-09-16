"""Table provenance remains original-bound across a selected manual-mode proof."""
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import time
import zipfile
import pytest
from test_presentation_tables import source_fixture
import xl_source_cache_equivalence as C
import xl_table_scoped_cache as T
from xl_presentation_tables import materialize, verify_materialization


def prepare(tmp_path, monkeypatch):
    source, selected = source_fixture(tmp_path)
    with zipfile.ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace(b'<calcPr ', b'<calcPr calcMode="manual" ')
    parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace(b'<definedNames />',
        b'<definedNames><definedName name="Unused">\'[1]Other\'!A1</definedName></definedNames>')
    parts['xl/worksheets/sheet1.xml'] = parts['xl/worksheets/sheet1.xml'].replace(
        b'</row>', b'<c r="F1"><f>A1*5</f><v>10</v></c></row>', 1).replace(
        b'ref="A1:E3"', b'ref="A1:F3"')
    with zipfile.ZipFile(source, 'w') as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    original_curation = tmp_path/'original.toml'
    original_curation.write_text(selected.read_text()+'[[output]]\nband = "S!F1"\ninclude = true\nname = "Second"\n')
    selected.write_text(original_curation.read_text().replace('band = "S!F1"\ninclude = true', 'band = "S!F1"\ninclude = false'))
    prior = tmp_path/'prior'
    with pytest.raises(ValueError, match='still requires source support'):
        materialize(source, '0001', original_curation, prior, remediate=True)
    projection_trial = tmp_path/'projection-trial'
    with pytest.raises(ValueError, match='still requires source support'):
        materialize(source, '0001', selected, projection_trial, remediate=True)
    generation = next((prior/'inspection-segments/0001/generations').iterdir())
    failure = C.keep(tmp_path/'failure.json', dict(models=0, claims=0, result=dict(
        state='STOPPED', stage='presentation_tables', bindings=dict(source_sha256=C.digest(source)))))
    authorization = tmp_path/'authorization.md'
    authorization.write_text('Explicit test authorization for bounded selected table cache proof.')
    monkeypatch.setattr(C, 'AUTHORIZATION_SHA256', C.digest(authorization))
    now = time.time_ns()
    policy = dict(schema_version=T.POLICY, source=C.binding(source),
        projection_sha256=C.digest(projection_trial/'0001.xlsx'),
        original_curation=C.binding(original_curation), original_inspection=C.binding(generation/'generation-manifest.json'),
        curation=C.binding(selected), ordered_outputs=['S!B1'], excluded_bands=['S!F1'],
        prior_failure=failure, authorization=C.binding(authorization), created_at_ns=now,
        expires_at_ns=now+3600*10**9, max_formula_cells=100, relative_tolerance=1e-6)
    row = C.keep(tmp_path/'policy.json', policy)
    return source, selected, row, policy


def test_original_table_projection_and_exact_selected_cache_proof(tmp_path, monkeypatch):
    source, curation, policy, _ = prepare(tmp_path, monkeypatch)
    original = C.digest(source)
    candidate, receipt = materialize(source, '0001', curation, tmp_path/'qualified',
        remediate=True, cache_policy=policy)
    assert verify_materialization(source, candidate, curation, receipt)['status'] == 'verified'
    edge = receipt['cache_equivalence']
    assert C.digest(source) == original
    assert edge['scope']['original'] == C.binding(source)
    assert edge['projected']['sha256'] != original
    assert edge['proof']['ordered_outputs'] == ['S!B1']
    assert edge['proof']['selected_formulas'] == 1
    assert edge['proof']['whole_workbook_equivalence'] is False
    assert edge['proof']['formula_seeds'] == edge['proof']['oracle_reads'] == 0
    assert C.package(candidate)[1] == C.automatic_members(C.package(edge['projected']['path'])[1])
    changed = copy.deepcopy(receipt)
    changed['cache_equivalence']['proof']['selected_formulas'] += 1
    changed['manifest_sha256'] = C.object_hash({k:v for k,v in changed.items() if k != 'manifest_sha256'})
    with pytest.raises(ValueError):
        verify_materialization(source, candidate, curation, changed)


@pytest.mark.parametrize('fault', ['projection', 'source', 'outputs', 'original-curation', 'expired'])
def test_table_policy_rejects_changed_provenance_or_scope(tmp_path, monkeypatch, fault):
    source, curation, _, policy = prepare(tmp_path, monkeypatch)
    if fault == 'projection':policy['projection_sha256'] = '0'*64
    elif fault == 'source':policy['source']['sha256'] = '0'*64
    elif fault == 'outputs':policy['ordered_outputs'] = ['S!F1']
    elif fault == 'original-curation':policy['original_curation'] = C.binding(curation)
    else:policy['expires_at_ns'] = policy['created_at_ns'] + 1
    row = C.keep(tmp_path/'changed-policy.json', policy)
    with pytest.raises(ValueError):
        materialize(source, '0001', curation, tmp_path/'blocked', remediate=True, cache_policy=row)
    assert not (tmp_path/'blocked/0001.xlsx').exists()


def test_cache_policy_cli_requires_exact_original_pair(tmp_path, monkeypatch):
    from scripts.corpus_build import configured_table_cache_policy
    source, _, row, _ = prepare(tmp_path, monkeypatch)
    args = SimpleNamespace(presentation_cache_policy=row['path'], presentation_cache_policy_sha256=row['sha256'])
    assert configured_table_cache_policy(args, {'path': str(source)}) == row
    args.presentation_cache_policy_sha256 = None
    with pytest.raises(ValueError, match='paired'):
        configured_table_cache_policy(args, {'path': str(source)})
