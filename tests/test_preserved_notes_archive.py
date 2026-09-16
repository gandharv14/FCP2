import copy
from pathlib import Path
import pytest
from scripts.batch import harbor_lane_notes as N
from scripts.fleet.preserved_notes_archive import notes_scope


def test_original_notes_pair_keeps_canonical_old_rows_but_reads_real_archives(tmp_path):
    live=tmp_path/'instruction.md';live.write_text('original instruction');old=N.record(live)
    archive=tmp_path/'submitted-budget.snapshot';archive.write_bytes(live.read_bytes());saved=N.record(archive)
    rows=[old];tracker=dict(workbook_id='0040',batch='test',execution_state='authoring',promotion_failure=None,
        failure_history=[],handoffs=[dict(schema_version='harbor-ready-transition/v1',revision=1,evidence_paths=rows)],
        agent_notes=dict(diagnostic_signature='retained',evidence_set_sha256=N.schema.sha(N.lifecycle.canonical(rows))))
    rendered=N.render_notes(tracker);tracker['agent_notes']['sha256']=N.schema.sha(rendered)
    assert N.validate_notes_pair(tracker,rendered)
    live.write_text('actual later naturalization')
    with pytest.raises(Exception):N.validate_notes_pair(tracker,rendered)
    original=N.validate_notes_pair
    with notes_scope(N,[tracker],{(old['path'],old['sha256']):saved},N.lifecycle.checked_record):
        assert N.validate_notes_pair(tracker,rendered)
        changed=copy.deepcopy(tracker);changed['batch']='foreign';new=N.render_notes(changed);changed['agent_notes']['sha256']=N.schema.sha(new)
        with pytest.raises(Exception):N.validate_notes_pair(changed,new)
        with pytest.raises(Exception):N.validate_notes_pair(tracker,rendered+b'changed')
        archive.write_text('tampered archive')
        with pytest.raises(Exception):N.validate_notes_pair(tracker,rendered)
    assert N.validate_notes_pair is original and live.read_text()=='actual later naturalization'
