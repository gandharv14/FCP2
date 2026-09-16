"""Source links are exempt only after complete live generation authentication."""
from pathlib import Path
import os
import pytest
from test_presentation_tables import source_fixture
from xl_presentation_tables import materialize
from scripts.batch.harbor_admission import survey_namespaces, MARKERS
from scripts.batch.harbor_source_history_links import verified_inspection_links, LINK_NAMES


def make(tmp_path, directory='tables-attempt-1'):
    source, curation = source_fixture(tmp_path)
    root = tmp_path/'diagnostics/0001'/directory
    materialize(source, '0001', curation, root)
    return root, root/'inspection-segments/0001'


@pytest.mark.parametrize('directory', ['tables-attempt-1', 'table-cache-proof-001/materialization'])
def test_authentic_source_inspection_is_not_prior_authoring(tmp_path, directory):
    root, base = make(tmp_path, directory)
    assert verified_inspection_links(base, {'0001'}, MARKERS) == LINK_NAMES
    assert survey_namespaces((root,), {'0001'}) == ({}, {})


@pytest.mark.parametrize('fault', ['arbitrary-link', 'task-link', 'task-marker', 'escape',
                                  'changed-artifact', 'changed-source', 'changed-pointer', 'ambiguous-id'])
def test_uncertain_or_task_evidence_remains_blocking(tmp_path, fault):
    root, base = make(tmp_path)
    if fault == 'arbitrary-link':
        (base/'unknown').symlink_to('current/segments.json')
    elif fault == 'task-link':
        (root/'0001-outputs').symlink_to(base/'current')
    elif fault == 'task-marker':
        (base/'current/task.toml').write_text('task marker')
    elif fault == 'escape':
        (base/'bands.csv').unlink();(base/'bands.csv').symlink_to(tmp_path/'0001.xlsx')
    elif fault == 'changed-artifact':
        (base/'current/bands.csv').write_text('changed')
    elif fault == 'changed-source':
        with (root/'inspection-source/0001.xlsx').open('ab') as f:f.write(b'changed')
    elif fault == 'changed-pointer':
        (base/'current').unlink();(base/'current').symlink_to('generations/'+'0'*64)
    else:
        base.rename(base.with_name('0002'))
    excluded, histories = survey_namespaces((root,), {'0001', '0002'})
    assert excluded and histories == {}
