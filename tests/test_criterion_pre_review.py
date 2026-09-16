from dataclasses import dataclass, asdict
from pathlib import Path
from types import SimpleNamespace as NS
import copy,json
import pytest
from scripts.fleet import criterion_pre_review as M


def test_budget_forbids_extra_authors_consumed_review_or_bool_ordinal():
    assert M.budget(copy.deepcopy(M.BUDGET))==M.BUDGET
    for key, value in [('added_author_generations',1),('next_review_ordinal',2),
                        ('review_attempts_consumed',1),('review_limit',3),
                        ('next_review_ordinal',True),('semantic_budget_reset',True)]:
        p=dict(M.BUDGET);p[key]=value
        with pytest.raises(ValueError):M.budget(p)


@pytest.mark.parametrize('boundary',['clean','pending-review','review-call','review-result','round2',
                                    'pending-writer2','pending-para2','complete-para','late-runtime'])
def test_exact_unused_boundary_preserves_uncertain_preparations(tmp_path,boundary):
    w=NS(run=tmp_path);W=NS(step_directory=lambda w,k:w.run/'steps'/k)
    for k in ('dialogue-writer','dialogue-paraphraser'):
        (tmp_path/'producer/requests'/k/'attempt-01').mkdir(parents=True)
    paths={'pending-review':'producer/requests/dialogue-review',
           'review-call':'producer/dialogue/review-call-01.json',
           'review-result':'producer/dialogue/round-01/review.json',
           'round2':'producer/dialogue/round-02',
           'pending-writer2':'producer/requests/dialogue-writer/attempt-02',
           'pending-para2':'producer/requests/dialogue-paraphraser/attempt-02',
           'complete-para':'steps/dialogue-paraphraser/complete.json',
           'late-runtime':'steps/final-runtime'}
    if boundary!='clean':
        p=tmp_path/paths[boundary];p.parent.mkdir(parents=True,exist_ok=True);p.touch()
        with pytest.raises(ValueError):M.unspent(w,W)
    else:M.unspent(w,W)


@dataclass
class Call:
    request:dict
    response:dict


@pytest.mark.parametrize('mode',['good','wrong-call','wrong-ordinal','wrong-kind','original-reject'])
def test_original_executor_and_pair_validator_are_required(tmp_path,monkeypatch,mode):
    def put(name,obj):
        p=tmp_path/name;p.write_text(json.dumps(obj));return M.binding(p)
    draft=tmp_path/'old.md';draft.write_text('original model draft')
    old_claims={'claims':[{'record_id':'same'}]}
    reqs=[put('writer.json',{}),put('para.json',{})]
    calls=[Call({'x':1},{'y':1}),Call({'x':2},{'y':2})]
    pair=put('pair.json',dict(author_requests=reqs,author_calls=[asdict(c)for c in calls],
                            original_claims=put('claims.json',old_claims),old_draft=M.binding(draft)))
    requests=[NS(prepared=NS(ordinal=1,limit=2,scope=NS(budget_id=k),policy='policy'))
              for k in ('dialogue-writer','dialogue-paraphraser')]
    if mode=='wrong-ordinal':requests[0].prepared.ordinal=2
    if mode=='wrong-kind':requests[0].prepared.scope.budget_id='dialogue-review'
    seen=[]
    def validate(actual,**kw):
        seen.append(kw)
        assert kw['claims']==old_claims and kw['draft']=='original model draft'
        if mode=='original-reject':raise ValueError('original author provenance rejected')
        return 'verified-original'
    W=NS(request_saved=lambda p:requests[[r['path']for r in reqs].index(str(p))])
    def observed(req,campaign):
        i=requests.index(req)
        return Call({'foreign':1},{}) if mode=='wrong-call' else calls[i]
    args=dict(W=W,P=NS(observed=observed),A=NS(verify_pair=validate),worker=NS(campaign='campaign',root=tmp_path))
    plan=dict(original_pair=pair,workbook='0199',source={'sha256':'original'})
    if mode=='good':
        assert M.original_pair(plan,**args)[1]=='verified-original';assert len(seen)==1
    else:
        with pytest.raises(ValueError):M.original_pair(plan,**args)


def test_owning_authority_refuses_readonly_mode_before_worker_or_reservation(monkeypatch):
    from scripts.fleet.criterion_pre_review_authorize import authorize
    monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY','1')
    with pytest.raises(ValueError,match='read-only'):
        authorize(NS(),live_custody=lambda:pytest.fail('must not reach live custody'))


def test_current_lease_uses_original_full_permit_not_wrong_envelope(tmp_path,monkeypatch):
    from scripts.fleet import criterion_pre_review_reader as Mreader
    from scripts.batch.harbor_workbook_bootstrap import Lease
    class R(Mreader.Reader):
        def owned(self):return {}
        def old_worker(self):return self.old
    tracker=tmp_path/'0199.json';before={'revision':12,'leases':{'task_agent':{'owner':'same','token':'token'}}}
    tracker.write_text(json.dumps(before));b=M.binding(tracker)
    r=object.__new__(R);r.plan={'workbook':'0199'};r.authority={'path':'/authority','sha256':'a'}
    r.old=NS(lease_renewal='old',context=NS(tracker_root=tmp_path));r.R=NS();r.T=NS()
    original={'checkpoint':{'path':'/completed','sha256':'s'},'selection':'retained','lease':'retained'}
    def validate(p,w,actual,lease,**kw):
        assert actual==before and lease==Lease('same','token',12,b['sha256'])
        assert kw['exact_boundary'] is False
        return original
    monkeypatch.setattr(Mreader,'validate_lease',validate)
    worker=NS(dialogue_pre_review_repair=r.authority,lease_renewal='old')
    assert r.historical_lease(worker,before)is original
    changed=copy.deepcopy(before);changed['revision']=13
    with pytest.raises(ValueError):r.historical_lease(worker,changed)
