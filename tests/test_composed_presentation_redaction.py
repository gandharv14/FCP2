"""An explicit redaction edge removes only out-of-cone table answers."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_table_remediation_composition import fixture, seal
from test_presentation_redaction import policy
from xl_presentation_tables import materialize, verify_materialization
from scripts.corpus_build import configured_redaction_policy


def test_explicit_composition_keeps_original_targets_and_authenticates_redaction(tmp_path):
    source, curation = fixture(tmp_path, answer_in_table=True)
    original = source.read_bytes()
    selected = policy(source, tmp_path)
    with pytest.raises(ValueError, match='discloses'):
        materialize(source, '0001', curation, tmp_path/'without-policy', remediate=True)
    candidate, receipt = materialize(source, '0001', curation, tmp_path/'approved',
                                    remediate=True, redaction_policy=selected)
    assert source.read_bytes() == original
    assert receipt['ordered_outputs'] == ['S!B1']
    assert receipt['redaction_policy'] == selected
    assert verify_materialization(source, candidate, curation, receipt)['status'] == 'verified'
    from xl_source_inventory import transformed_workbook_record
    transformed_workbook_record('0001', source, candidate, curation, receipt)
    missing = copy.deepcopy(receipt)
    del missing['redaction_policy']
    seal(missing)
    with pytest.raises(ValueError, match='unapproved'):
        verify_materialization(source, candidate, curation, missing)
    changed = copy.deepcopy(receipt)
    changed['redaction_policy']['sha256'] = '0'*64
    seal(changed)
    with pytest.raises(ValueError, match='policy changed'):
        verify_materialization(source, candidate, curation, changed)


def test_redaction_never_admits_tables_in_selected_dependencies(tmp_path):
    source, curation = fixture(tmp_path, in_cone=True)
    with pytest.raises(ValueError):
        materialize(source, '0001', curation, tmp_path/'blocked', remediate=True,
                    redaction_policy=policy(source, tmp_path))
    assert not (tmp_path/'blocked/0001.xlsx').exists()


def test_cli_policy_requires_paired_exact_source_bound_bytes(tmp_path):
    source, _ = fixture(tmp_path, answer_in_table=True)
    row = policy(source, tmp_path)
    args = SimpleNamespace(presentation_redaction_policy=row['path'],
                           presentation_redaction_policy_sha256=row['sha256'])
    assert configured_redaction_policy(args, {'path': str(source)}) == row
    args.presentation_redaction_policy_sha256 = None
    with pytest.raises(ValueError, match='paired'):
        configured_redaction_policy(args, {'path': str(source)})
    assert configured_redaction_policy(SimpleNamespace(), {'path': str(source)}) is None


def test_redaction_cannot_hide_an_original_numerical_failure(tmp_path):
    import zipfile
    source, curation = fixture(tmp_path, answer_in_table=True)
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members['xl/worksheets/sheet1.xml'] = members['xl/worksheets/sheet1.xml'].replace(
        b'<f>A1*3</f><v>6</v>', b'<f>A1*3</f><v>999</v>')
    with zipfile.ZipFile(source, 'w') as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError):
        materialize(source, '0001', curation, tmp_path/'numerically-blocked',
                    remediate=True, redaction_policy=policy(source, tmp_path))
    assert not (tmp_path/'numerically-blocked/0001.xlsx').exists()
