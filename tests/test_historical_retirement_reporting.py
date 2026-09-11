"""Explicit historical observations; complete owning retirement fixture retained."""
from pathlib import Path
import copy
import pytest
from test_harbor_admission import setup
from test_retirement_history import transaction,isolated_code_file
from test_slot_retirement import released, R
from scripts.corpus import object_hash

from scripts import harbor_retirement_reporting as A, corpus_qualification as Q


@pytest.fixture
def sealed(released,monkeypatch):
    # The existing retirement fixture mocks namespace policy only. Give that
    # synthetic namespace real path/hash selection records for reporter parsing.
    t,args,root=released
    contract=R.P.put(root/'fixture-contract.json',dict(fixture_namespace=True))
    activation=R.P.put(root/'fixture-activation.json',dict(contract=contract))
    descriptor=R.read(t.tx);descriptor.update(contract=contract,activation=activation)
    Path(t.tx['path']).write_bytes(R.P.canonical(descriptor));t.tx=R.record(t.tx['path'])
    args.update(transaction=t.tx,contract=contract,activation=activation)
    monkeypatch.setattr(A.history,'_policy',R._policy)
    binding=R.seal(**args,output=root/'parallel-policy/retirements/first')
    return released,binding


@pytest.fixture
def reporting(sealed,setup,monkeypatch,isolated_code_file):
    (t,args,root),seal=sealed
    inventory=A.policy.read(setup[1].original_inventory)
    entry=dict(workbook_id='0001',harbor_root=str(root),original=args['original'],tracker=args['tracker'],
               seal=seal,contract=args['contract'],activation=args['activation'])
    selected=A.policy.put(root/'historical-selection.json',dict(schema_version=A.SCHEMA,
        inventory_sha256=inventory['inventory_sha256'],entries=[entry]))
    # Namespace fixture is explicit; all tracker/hash/history proof remains real.
    monkeypatch.setattr(Q,'selected_harbor_roots',lambda run,extra=():(root,))
    isolated_code_file.write_text('# new registered code after root seal\n')
    return root,inventory,selected,entry,t


def test_without_explicit_selection_existing_seal_is_not_discovered(reporting,monkeypatch):
    root,inventory,selection,entry,t=reporting
    monkeypatch.setattr(A,'validate',lambda *a,**k:pytest.fail('historical evidence auto-discovered'))
    result=Q.build_report(inventory,root)
    assert result['states']=={'EVIDENCE_INVALID':1} and result['verified_promoted']==0
    assert 'historical_terminals' not in result


def test_explicit_historical_terminal_is_not_a_promotion_or_source_verdict(reporting):
    root,inventory,selection,entry,t=reporting;before=t.path.read_bytes()
    result=Q.build_report(inventory,root,historical_retirements=selection)
    assert result['states']=={'STOPPED':1} and result['historical_terminals']==1
    assert result['verified_promoted']==0 and result['prior_promotions']==0 and result['harbor_yield']==0
    row=result['workbooks'][0]
    assert row['historical_terminal'] is True and row['current_qualification'] is False
    assert row['source_invalidity'] is False and row['verified_promoted'] is False
    assert row['failure']['subclass']=='pipeline_claim_card_defect_review_budget_exhausted'
    assert row['historical_retirement']['seal']==entry['seal']
    assert t.path.read_bytes()==before


def test_all700_denominator_and_in_progress_state_preserved(reporting):
    root,inventory,selection,entry,t=reporting;manifest=copy.deepcopy(inventory)
    manifest['sources'] += [dict(workbook_id=f'{i:04d}',path=str(root/'raw'/f'{i:04d}.xlsx'),sha256='b'*64,
                                 size_bytes=1,format='.xlsx',corpus='synthetic') for i in range(2,701)]
    manifest['inventory_sha256']=object_hash({k:v for k,v in manifest.items() if k!='inventory_sha256'})
    document=A.policy.read(selection);document['inventory_sha256']=manifest['inventory_sha256']
    selection=A.policy.put(root/'selected700.json',document)
    result=Q.build_report(manifest,root,historical_retirements=selection)
    assert result['raw_denominator']==700 and result['states']=={'STOPPED':1,'NOT_RUN':699}
    assert result['verified_promoted']==0 and result['measurement_state']=='in_progress'


@pytest.mark.parametrize('change',['seal','tracker','noncode','missing_tracker'])
def test_invalid_selected_proof_is_not_fallback_or_success(reporting,change):
    root,inventory,selection,entry,t=reporting
    if change=='seal':Path(entry['seal']['path']).write_bytes(b'{}')
    elif change=='tracker':t.path.write_bytes(t.path.read_bytes()+b' ')
    elif change=='noncode':Path(t.source['path']).write_text('altered actual response')
    else:t.path.unlink()
    result=Q.build_report(inventory,root,historical_retirements=selection)
    assert result['states']=={'EVIDENCE_INVALID':1} and result['verified_promoted']==0
    assert result['historical_terminals']==0


@pytest.mark.parametrize('change',['duplicate','inventory','original','namespace','unknown','extra'])
def test_invalid_root_selection_configuration_rejects(reporting,change):
    root,inventory,selection,entry,t=reporting;value=A.policy.read(selection)
    if change=='duplicate':value['entries'].append(copy.deepcopy(entry))
    elif change=='inventory':value['inventory_sha256']='0'*64
    elif change=='original':value['entries'][0]['original']['sha256']='0'*64
    elif change=='namespace':value['entries'][0]['harbor_root']=str(root/'foreign')
    elif change=='unknown':value['entries'][0]['workbook_id']='0002'
    else:value['approve']=True
    wrong=A.policy.put(root/'wrong.json',value)
    with pytest.raises(ValueError):Q.build_report(inventory,root,historical_retirements=wrong)


def test_historical_option_cannot_mutate_ledgers(reporting):
    root,inventory,selection,entry,t=reporting
    with pytest.raises(ValueError,match='read-only'):
        Q.build_report(inventory,root,bind_ledgers=True,historical_retirements=selection)


def test_reporter_tracker_conflict_precedes_historical_routing(reporting,monkeypatch):
    root,inventory,selection,entry,t=reporting;original=Q.tracker_candidates
    def conflict(source,roots):
        rows=original(source,roots)
        return rows+[dict(rows[0],harbor_root=str(root/'other'))]
    monkeypatch.setattr(Q,'tracker_candidates',conflict)
    result=Q.build_report(inventory,root,historical_retirements=selection)
    row=result['workbooks'][0]
    assert row['state']=='EVIDENCE_INVALID' and row['failure_class']=='ambiguous_original_tracker'
    assert result['verified_promoted']==0
