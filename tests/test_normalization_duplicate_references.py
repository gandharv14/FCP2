"""Duplicate evidence rows keep all source context and one decision per cell."""
from dataclasses import replace
import copy
from pathlib import Path
import pytest
from scripts.batch import harbor_normalization_producer as N,harbor_normalization_partitioned as P
from scripts.batch.harbor_admission import read,record
from test_harbor_normalization_producer import setup


@pytest.mark.parametrize('sibling',[False,True])
def test_real_prepared_unique_references_preserve_duplicate_evidence_rows(setup,monkeypatch,tmp_path,sibling):
    s,campaign,*_=setup();original=N.analyse
    def duplicate(selection):
        draft,rows=original(selection)
        repeated=copy.deepcopy(rows)
        for row in repeated:row['row']['draft_id']+='-second-audit-mention'
        return draft,rows+repeated
    monkeypatch.setattr(N,'analyse',duplicate)
    prepared=N.prepare(s);v=read(prepared)
    assert v['candidate_count']==8 and len(v['candidate_references'])==4
    old={p:p.read_bytes()for p in s.run.iterdir()if p.is_file()};source=Path(s.bindings['source_file']['path']).read_bytes()
    selected=replace(s,run=tmp_path/'sibling'/s.run.name) if sibling else s
    current=P.prepare(selected,campaign,tmp_path/'parts',prior_plan=record(s.run/'production-selection.json') if sibling else None)
    manifest=read(current)
    assert len(P.read(record(selected.run/'analysis.json')))==8
    assert len(P.read(record(selected.run/'review-evidence.json'))['candidates'])==8
    refs=[ref for part in manifest['partitions']for ref in part['references']]
    assert len(refs)==len(set(refs))==4 and set(refs)==set(v['candidate_references'])
    assert all(p.read_bytes()==raw for p,raw in old.items())
    assert Path(s.bindings['source_file']['path']).read_bytes()==source


@pytest.mark.parametrize('sibling',[False,True])
def test_missing_reference_in_prepared_contract_is_rejected(setup,monkeypatch,tmp_path,sibling):
    s,campaign,*_=setup();original=N.analyse
    def duplicate(selection):
        draft,rows=original(selection);repeated=copy.deepcopy(rows)
        for row in repeated:row['row']['draft_id']+='-second-audit-mention'
        return draft,rows+repeated
    monkeypatch.setattr(N,'analyse',duplicate)
    prepared=N.prepare(s);v=read(prepared);v['candidate_references'].pop()
    Path(prepared['path']).write_bytes(P.E.canonical(v))
    selected=replace(s,run=tmp_path/'sibling'/s.run.name) if sibling else s
    with pytest.raises(ValueError):P.prepare(selected,campaign,tmp_path/'parts',prior_plan=record(s.run/'production-selection.json') if sibling else None)
    assert not (P.old_location(s,campaign)/'scope-delegation.json').exists()
