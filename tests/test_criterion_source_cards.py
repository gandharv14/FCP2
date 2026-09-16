import copy
import importlib
import importlib.util
import json
from pathlib import Path

import pytest
from scripts.fleet.criterion_source_cards import corrected_claims

ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-next-001'


@pytest.fixture
def actual(monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(ROOT/'.cursor/skills/additional-assumptions-dialogue/scripts'))
    for name in ('spoken_formula','extract_claims'):
        monkeypatch.delitem(sys.modules,name,raising=False)
    new=importlib.import_module('spoken_formula');extractor=importlib.import_module('extract_claims')
    snapshot=json.loads((H/'implementation/snapshot.json').read_bytes())
    path=Path(snapshot['root'])/'.cursor/skills/additional-assumptions-dialogue/scripts/spoken_formula.py'
    spec=importlib.util.spec_from_file_location('retained_criterion_renderer',path)
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    claims=json.loads((H/'0199-mechanical-hold/draft-evidence/4.json').read_bytes())
    formulas={c['reviewer_only']['cells'][0]:c['reviewer_only']['evidence']for c in claims['claims']}
    return claims,old,new,extractor,formulas


def test_full_actual_inventory_changes_only_four_false_row_roles(actual):
    original,*_=actual
    fixed,changes=corrected_claims(*actual)
    assert len(fixed['claims'])==18 and len(changes)==4
    assert [c['record_id'].rsplit('::',1)[1]for c in changes]==['012','013','014','015']
    for before,after in zip(original['claims'],fixed['claims']):
        assert before['reviewer_only']==after['reviewer_only']
        assert set(before['must_say'])-set(after['must_say'])<= {'the row labelled "<>0"'}
    restored=copy.deepcopy(fixed)
    for i,c in enumerate(original['claims']):
        for key in ('spoken','must_say'):
            restored['claims'][i][key]=c[key]
            restored['writer_pack']['claims'][i][key]=c[key]
    restored['disclosure_body']=original['disclosure_body']
    restored['writer_pack']['disclosure_body']=original['writer_pack']['disclosure_body']
    assert restored==original


@pytest.mark.parametrize('fault',['formula','pack','card','duplicate','literal_requirement','extra_requirement','renderer'])
def test_source_projection_rejects_changed_or_broader_authority(actual,fault):
    original,old,new,extractor,formulas=actual
    if fault=='formula':formulas["'P&L Projection'!G120"]='=0'
    elif fault=='pack':original['writer_pack']['claims'][11]['spoken']+=' changed'
    elif fault=='card':
        original['claims'][11]['spoken']+=' changed'
        original['writer_pack']['claims'][11]['spoken']+=' changed'
    elif fault=='duplicate':original['claims'][0]['record_id']=original['claims'][1]['record_id']
    elif fault in ('literal_requirement','extra_requirement'):
        for claims in (original['claims'],original['writer_pack']['claims']):
            if fault=='literal_requirement':claims[11]['must_say'].remove('the text literal "<>0"')
            else:claims[11]['must_say'].append('a distinct required instruction')
    else:new=old
    with pytest.raises(ValueError):corrected_claims(original,old,new,extractor,formulas)
