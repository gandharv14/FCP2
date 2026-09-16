"""Retained duplicate originals block themselves, not unrelated candidates."""
from pathlib import Path
import pytest
from test_harbor_general_cohort import selected, policy
from test_harbor_admission import setup
from scripts.batch import harbor_admission as A
from scripts import corpus


@pytest.mark.parametrize('alias_of_selected', [False, True])
def test_unselected_duplicate_denominator_is_retained(selected, alias_of_selected):
    s, _, tmp = selected
    inventory=A.read(s.policy.original_inventory)
    rows=inventory['sources']
    selected_ids=set(s.policy.requested_ids)
    unselected=[r for r in rows if r['workbook_id'] not in selected_ids]
    donor=next(r for r in rows if r['workbook_id'] in selected_ids) if alias_of_selected else unselected[-2]
    alias=unselected[-1]
    Path(alias['path']).write_bytes(Path(donor['path']).read_bytes())
    alias.update(sha256=donor['sha256'],size_bytes=Path(alias['path']).stat().st_size)
    inventory['inventory_sha256']=corpus.object_hash({k:v for k,v in inventory.items() if k!='inventory_sha256'})
    def put(name,value):
        path=tmp/name;path.write_text(__import__('json').dumps(value));return A.record(path)
    inv=put('inventory-with-held-alias.json',inventory)
    proposal=A.read(s.candidate_manifest);survey=A.read(proposal['survey']);survey['inventory']=inv
    proposal['survey']=put('alias-survey.json',survey)
    candidate=put('alias-candidate.json',proposal)
    if alias_of_selected:
        with pytest.raises(ValueError,match='ambiguous duplicate selected original bytes'):
            policy(candidate,s.policy,original_inventory=inv)
    else:
        result=policy(candidate,s.policy,original_inventory=inv)
        assert result.expected_original_count==len(rows)==s.policy.expected_original_count
        assert result.original_inventory==inv
        assert result.requested_ids==s.policy.requested_ids
