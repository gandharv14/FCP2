import copy
import pytest
from scripts.batch.harbor_disclosure_source_revision import source_changes


def test_new_source_revision_carries_build_evidence_without_rejected_review_cache(tmp_path):
    from scripts.batch.harbor_disclosure_source_revision import copy_producer_inputs
    old=tmp_path/'original';new=tmp_path/'revision'
    for name,raw in [('disclosure-build/0177-outputs/bands.json',b'build-evidence'),
                     ('disclosure/sentences.json',b'old-manifest'),
                     ('requests/disclosure-review/attempt-01/response.json',b'rejected-review')]:
        p=old/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    before={str(p.relative_to(old)):p.read_bytes()for p in old.rglob('*')if p.is_file()}
    copy_producer_inputs(old,new)
    assert {str(p.relative_to(new)):p.read_bytes()for p in new.rglob('*')if p.is_file()}=={
        'disclosure-build/0177-outputs/bands.json':b'build-evidence'}
    assert {str(p.relative_to(old)):p.read_bytes()for p in old.rglob('*')if p.is_file()}==before


def case():
    row=dict(entry='npv_timing',cell_keys=['Assumptions!D49'],source='convention_detector',
             disposition='disclosed',label='Periodic NPV',note='First value is one period out.',
             value='excel_default_one_period_out',alternatives=['other'])
    second=dict(row,cell_keys=['Assumptions!D50'],entry='retained')
    old=dict(records=[row,second],agent_records=[row,second],metadata='same')
    new=copy.deepcopy(old)
    for kind in ('records','agent_records'):
        new[kind][0].update(label='Dated XNPV',note='First date is the origin.',value='dated_first_date_origin')
    m=dict(schema_version='harbor-disclosure-sentence-manifest/v1',input_hashes={},agent_record_ids=['a','b'],sentences=[
        dict(order=0,sentence_id='s0',text=row['note'],record_ids=['a']),
        dict(order=1,sentence_id='s1',text='Retained passing sentence.',record_ids=['b'])])
    n=copy.deepcopy(m);n['sentences'][0].update(text=new['records'][0]['note'],sentence_id='corrected',record_ids=['corrected-record']);n['agent_record_ids'][0]='corrected-record'
    review=dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes={},manifest_sha256='m',
        passed=False,blocking_findings=['Wrong convention'],non_blocking_flags=[],sentences=[
        {k:r[k] for k in ('sentence_id','text','record_ids')}|dict(golden_evidence=['actual formula'],delivered_evidence=['actual source'],
        findings=['Wrong convention']if i==0 else[],verdict='blocking'if i==0 else'pass')for i,r in enumerate(m['sentences'])])
    return old,new,m,n,review,'m'


def test_source_convention_changes_only_rejected_sentence_and_preserves_review():
    args=case();before=copy.deepcopy(args[-2]);v=source_changes(*args)
    assert v==dict(record_indices=[0],sentence_indices=[0],records=2,sentences=2)
    assert args[-2]==before and args[-2]['passed']is False


@pytest.mark.parametrize('kind',['locator','source','entry','suppressed','missing','passing','metadata','duplicate','unknown','successful','no_change'])
def test_source_candidate_rejects_changed_scope_or_incomplete_history(kind):
    a,b,m,n,r,s=case()
    if kind=='locator':b['records'][0]['cell_keys']=['Other!A1']
    if kind=='source':b['records'][0]['source']='invented'
    if kind=='entry':b['records'][0]['entry']='other'
    if kind=='suppressed':a['records'][0]['disposition']=b['records'][0]['disposition']='suppressed'
    if kind=='missing':b['records'].pop()
    if kind=='passing':n['sentences'][1]['text']='Changed passing evidence'
    if kind=='metadata':b['metadata']='changed'
    if kind=='duplicate':n['sentences'][1]['record_ids']=['corrected-record']
    if kind=='unknown':r['sentences'][0]['verdict']='unknown'
    if kind=='successful':r['passed']=True
    if kind=='no_change':b=copy.deepcopy(a)
    with pytest.raises(ValueError):source_changes(a,b,m,n,r,s)


def extended_case(kind):
    a,b,m,n,r,s=case()
    if kind=='error':
        row=dict(source='custom_method_detector',disposition='disclosed',coverage_complete=True,
                 cell_keys=['S!A1'],method_profile={'formula':'=IF(A2,1,#REF!)'},
                 fields={'steps':'use the named range "#REF!"','label':'Retained'})
        corrected=copy.deepcopy(row);corrected['fields']['steps']='use the broken reference "#REF!"'
    else:
        row=dict(source='convention_detector',entry='distribution_policy',value='payout_ratio',
                 disposition='disclosed',cell_keys=['S!A1'],label='Dividend',note='ratio')
        corrected=dict(row,value='formula_mechanics',note='amount',
                       fields={'steps':'multiply shares by dividend per share'},mechanics_references=['S!B1','S!C1'])
    for key in ('records','agent_records'):
        a[key][0]=copy.deepcopy(row);b[key][0]=copy.deepcopy(corrected)
    return a,b,m,n,r,s


@pytest.mark.parametrize('kind',['error','dividend'])
def test_extended_fields_require_complete_source_rederivation(kind):
    args=extended_case(kind);new=args[1];calls=[]
    def derive():calls.append(True);return copy.deepcopy(new),[0]
    assert source_changes(*args,rederive=derive)['record_indices']==[0]
    assert calls==[True]
    with pytest.raises(ValueError,match='require source rederivation'):source_changes(*args)
    with pytest.raises(ValueError,match='differ from source rederivation'):
        source_changes(*args,rederive=lambda:(args[0],[0]))
    with pytest.raises(ValueError,match='differ from source rederivation'):
        source_changes(*args,rederive=lambda:(new,[1]))


@pytest.mark.parametrize('kind',['error','dividend'])
@pytest.mark.parametrize('change',['source','scope','extra','passing','fields'])
def test_rederivation_does_not_allow_changed_identity_or_passing_evidence(kind,change):
    args=list(extended_case(kind));a,b,m,n,r,s=args
    if change=='source':b['records'][0]['source']='other'
    elif change=='scope':b['records'][0]['cell_keys']=['S!B2']
    elif change=='extra':b['records'][0]['unreviewed']='value'
    elif change=='passing':n['sentences'][1]['text']='changed'
    elif kind=='error':b['records'][0]['fields']['label']='new'
    else:b['records'][0]['mechanics_references']=['S!B1','S!B1']
    with pytest.raises(ValueError):source_changes(*args,rederive=lambda:(b,[0]))


def test_original_renewed_predecessor_delegates_complete_historical_validation(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from scripts.batch import harbor_disclosure_source_revision as M,harbor_instruction_lease_renewal as L
    from scripts import harbor_instruction_campaign as I
    root=tmp_path/'old';run=tmp_path/'run';context=SimpleNamespace(tracker_root=tmp_path/'trackers')
    cp={'path':'checkpoint','sha256':'c'};renew={'path':'renewal','sha256':'r'}
    prior=SimpleNamespace(root=root,run=run,context=context,lease_renewal=renew)
    worker=SimpleNamespace(root=tmp_path/'new',run=run,context=context)
    profile=dict(descriptor={'path':'descriptor','sha256':'d'},old_snapshot={'path':'snapshot','sha256':'s'},workbook='0102')
    intent=dict(previous=renew,selection='selection',lease='lease')
    values={'descriptor':{'worker':'encoded'},'renewal':dict(selection='selection',lease='lease'),'tracker':{'revision':3}}
    monkeypatch.setattr(M,'record',lambda p:cp if str(p).endswith('disclosure-build/complete.json')else{'path':'tracker','sha256':'t'})
    monkeypatch.setattr(M,'read',lambda b:values[b['path']]);monkeypatch.setattr(M,'inventory',lambda b:(root,{}))
    monkeypatch.setattr(I,'worker_decode',lambda b:prior);calls=[]
    def validate(w,b,s):calls.append((w,b,s));return dict(checkpoint=cp)
    monkeypatch.setattr(L,'validate_historical_integrity',validate)
    assert M.original_predecessor(profile,worker,intent)=={'lease_renewal':renew}
    assert calls==[(prior,{'revision':3},profile['old_snapshot'])]
    values['renewal']['lease']='other'
    with pytest.raises(ValueError,match='changed the completed boundary'):M.original_predecessor(profile,worker,intent)
    values['renewal']['lease']='lease';prior.lease_renewal={'path':'foreign','sha256':'f'}
    with pytest.raises(ValueError,match='exact ordinary renewed predecessor'):M.original_predecessor(profile,worker,intent)


def test_source_renewal_adapter_only_dispatches_exact_prior_receipt(monkeypatch):
    from types import SimpleNamespace
    from scripts.batch import harbor_instruction_lease_renewal as L,harbor_disclosure_source_revision as S
    keys=L.CONTINUATIONS;w=SimpleNamespace(**{k:None for k in keys});w.disclosure_revision='revision';w.lease_renewal='renewal'
    record=dict(source_revision='source',prior_continuations={'lease_renewal':'renewal'})
    monkeypatch.setattr(L,'read',lambda b:record);calls=[]
    monkeypatch.setattr(S,'historical_lease',lambda worker,before:calls.append((worker,before))or'validated')
    assert L._retained_lease_adapter(w,'tracker')=='validated'and calls==[(w,'tracker')]
    record['prior_continuations']['lease_renewal']='foreign'
    assert L._retained_lease_adapter(w,'tracker')is None
    assert len(calls)==1
