"""Direct publication must be bound to every actual completed worker phase."""
import copy
from pathlib import Path
import pytest
from types import SimpleNamespace
from scripts.fleet import retained_warning_publication as P


def fixture():
    values = {}
    def add(path, value):
        row = {'path': path, 'sha256': 'a' * 64}
        values[path] = value
        return row
    helper = add('/helper.py', {})
    snapshot = add('/snapshot.json', {'root': '/code/version'})
    cfg = add('/config.json', {})
    takeover = add('/takeover.json', {})
    prior = add('/continuation.json', dict(schema_version='harbor-direct-warning-continuation/v1',
        helper=helper, workbook='0303', source_sha256='s', snapshot=snapshot,
        output='/work', config=cfg, takeover_plan=takeover))
    outcomes = [add('/work/step-%02d/outcome.json' % i, {}) for i in range(4)]
    ready = add('/work/result.json', dict(schema_version='harbor-retained-warning-continuation/v1',
        status='PUBLICATION_READY', plan=prior, workbook='0303', config=cfg,
        takeover_profile=takeover, models_dispatched=0, publication_performed=False,
        automatic_retry=False, outcomes=outcomes, outcome=outcomes[-1]))
    job = add('/job.json', dict(inputs=[prior], driver=helper, result=ready['path'],
        source={'sha256': 's'}, version='version'))
    root = add('/launch-plan.json', {'job': job})
    intent = add('/intent.json', {'plan': root})
    launch = add('/launched.json', dict(intent=intent,
        remote={'job': job, 'root_intent': intent}, automatic_retry=False))
    plan = dict(workbook='0303', source_sha256='s', code_root='/code/version', ready=ready,
        retained_warning=dict(continuation_plan=prior, continuation_helper=helper, root_launch=launch, job=job))
    def checked(row):
        assert row == {'path': row['path'], 'sha256': 'a' * 64}
        return values[row['path']]
    def require(ok, message):
        if not ok: raise ValueError(message)
    kw = dict(checked=checked, binding=lambda path: {'path': path, 'sha256': 'a' * 64}, require=require)
    return plan, values, kw


def test_completed_direct_history_binds_real_job_and_all_four_outcomes():
    plan, values, kw = fixture()
    assert P.history_binding(plan, **kw) == plan[P.KEY]['continuation_plan']
    assert P.evidence(plan) == [plan[P.KEY][k] for k in ('continuation_plan', 'continuation_helper')] + [plan['ready'], plan[P.KEY]['job'], plan[P.KEY]['root_launch']]


def test_resumed_publication_reads_exact_launch_archive_not_current_tracker(tmp_path):
    from scripts.fleet.migration import retain,binding,checked
    old=retain(tmp_path/'prior.json',dict(workbook='0064',live_renewal={'completed':'original'}))
    before=retain(tmp_path/'archive.json',dict(workbook_id='0064',bindings={'original_raw_source':{'sha256':'source'}}))
    current=tmp_path/'tracker.json';current.write_text('{"advanced":true}')
    row=dict(path=str(current),sha256=before['sha256'])
    prior=dict(workbook='0064',source_sha256='source',resume={'prior_plan':old},live_renewal={'completed':'original'})
    runway=retain(tmp_path/'runway.json',dict(workbook='0064',source_sha256='source',tracker=row))
    offline=retain(tmp_path/'offline.json',dict(copies=[dict(native=row,copy=before)]))
    root=dict(native_lease_runway=runway,offline_bindings=offline)
    require=lambda ok,msg:None if ok else (_ for _ in ()).throw(ValueError(msg))
    kw=dict(checked=checked,binding=binding,require=require,root=root)
    P.direct_inputs(old,prior,dict(inputs=[old,row]),**kw)
    current.write_text('{"advanced":2}')
    P.direct_inputs(old,prior,dict(inputs=[old,row]),**kw)
    with pytest.raises(ValueError,match='unique'):
        P.direct_inputs(old,prior,dict(inputs=[old,row,row]),**kw)
    Path(before['path']).write_text('{}')
    with pytest.raises(ValueError,match='archive changed'):
        P.direct_inputs(old,prior,dict(inputs=[old,row]),**kw)


def test_resumed_publication_cannot_infer_archive_without_actual_launch():
    plan,values,kw=fixture();prior=values['/continuation.json'];prior['resume']={'prior_plan':{}}
    with pytest.raises(ValueError,match='actual root launch'):
        P.direct_inputs(plan[P.KEY]['continuation_plan'],prior,values['/job.json'],**kw)


@pytest.mark.parametrize('mutate', [
    lambda p,v: v['/work/result.json'].update(status='STEP_COMPLETE'),
    lambda p,v: v['/work/result.json'].update(models_dispatched=1),
    lambda p,v: v['/work/result.json']['outcomes'].pop(0),
    lambda p,v: v['/work/result.json']['outcomes'].reverse(),
    lambda p,v: v['/work/result.json'].update(publication_performed=True),
    lambda p,v: v['/snapshot.json'].update(root='/different'),
    lambda p,v: v['/job.json'].update(result='/foreign/result.json'),
    lambda p,v: v['/job.json'].update(version='other'),
    lambda p,v: v['/continuation.json'].update(source_sha256='other'),
    lambda p,v: v['/launched.json']['remote'].update(root_intent={'path':'foreign','sha256':'x'}),
    lambda p,v: p[P.KEY].update(unreviewed_history={}),
])
def test_direct_history_refuses_partial_reordered_or_detached_completion(mutate):
    plan, values, kw = fixture()
    mutate(plan, values)
    with pytest.raises((ValueError, AssertionError)):
        P.history_binding(plan, **kw)

def test_queued_direct_publication_uses_exact_validated_projection_and_retains_custody():
    plan, values, kw = fixture()
    old = object(); projected = object(); owning = object(); calls = []
    custody = dict(reader={'path':'/queued.py','sha256':'b'*64},
                   completion={'path':'/complete.json','sha256':'c'*64})
    def load(name, row):
        assert row == custody['reader']
        def project(p, request, completion):
            assert p is owning and request is old and completion == custody['completion']
            calls.append(completion)
            return projected
        return SimpleNamespace(projected=project)
    pub = SimpleNamespace(require=kw['require'], load=load)
    assert P.queued_request(pub,plan,owning,old) is old
    assert not calls
    plan['queued_lease'] = custody
    assert P.queued_request(pub,plan,owning,old) is projected
    assert calls == [custody['completion']]
    assert P.evidence(plan)[-2:] == list(custody.values())
    assert P.history_binding(plan, **kw) == plan[P.KEY]['continuation_plan']

@pytest.mark.parametrize('custody',[{}, {'reader':{}}, {'reader':{},'completion':{},'bypass':True}])
def test_queued_direct_rejects_incomplete_or_extra_custody_before_loading(custody):
    def require(ok,message):
        if not ok:raise ValueError(message)
    pub=SimpleNamespace(require=require,load=lambda *a:pytest.fail('invalid reader loaded'))
    with pytest.raises(ValueError,match='closed queued direct'):
        P.queued_request(pub,{'queued_lease':custody},object(),object())

def test_queued_direct_propagates_failed_original_lease_proof():
    def require(ok,message):
        if not ok:raise ValueError(message)
    def project(*a):raise ValueError('queued renewal detached from original request')
    pub=SimpleNamespace(require=require,load=lambda *a:SimpleNamespace(projected=project))
    with pytest.raises(ValueError,match='detached'):
        P.queued_request(pub,{'queued_lease':dict(reader={},completion={})},object(),object())


def test_resume_admission_archive_is_exact_root_selected_input(tmp_path):
    from scripts.fleet import prepare_direct_warning_publication as M
    def put(name,value):
        p=tmp_path/name;p.write_text(__import__('json').dumps(value));return M.binding(p)
    archive=put('actual-before.json',dict(workbook_id='0064',revision=42))
    row=dict(path='/native/tracker.json',sha256=archive['sha256'])
    previous=put('old-plan.json',dict(workbook='0064',live_renewal={'actual':'completed'}))
    prior=dict(workbook='0064',source_sha256='source',live_renewal={'actual':'completed'},resume={'prior_plan':previous})
    launch=dict(native_lease_runway=put('runway.json',dict(workbook='0064',source_sha256='source',tracker=row)),offline_bindings=put('offline.json',dict(copies=[dict(native=row,copy=archive)])))
    job=dict(inputs=[row])
    assert M.resume_admission_archive(prior,launch,job)==dict(input=row,archive=archive)
    with pytest.raises(AssertionError):M.resume_admission_archive(prior,launch,dict(inputs=[]))
    with pytest.raises(AssertionError):M.resume_admission_archive(prior,launch,dict(inputs=[row,row]))
    Path(archive['path']).write_text('changed')
    with pytest.raises(AssertionError):M.resume_admission_archive(prior,launch,job)
    assert M.resume_admission_archive({},launch,job) is None
