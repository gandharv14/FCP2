from pathlib import Path
import importlib.util,json
import pytest
from scripts.fleet import response_ready_v2 as M,response_review as R


def test_publisher_preserves_all_native_gates(tmp_path):
    root=Path(__file__).resolve().parents[1]
    plan=dict(schema_version='harbor-current-response-publisher/v1',helper=R.binding(M.__file__),response_helper=R.binding(R.__file__),native=R.binding(root/'scripts/fleet/publish.py'),intermediate=str(tmp_path/'strict.py'),output=str(tmp_path/'current.py'))
    b=R.retain(tmp_path/'plan.json',plan);result=M.build_publisher(b);v=R.checked(result)
    source=Path(plan['output']).read_text();compile(source,plan['output'],'exec')
    for required in ('_response_gate("remote", plan_binding)','_response_gate("collect",','publication_only()','env[2].run(request','driver.verify(job','package = pack(','atomic collection','_ready_adapter().publication_binding','projected_survey = _ready_adapter().projected_survey'):
        assert required.lower() in source.lower()
    assert v['native_publication_and_collection_gates_preserved']
    with pytest.raises(ValueError,match='fresh publisher'):M.build_publisher(b)


def test_review_checks_custody_before_dispatch(tmp_path,monkeypatch):
    selected=R.retain(tmp_path/'review.json',{'ready':{'path':'x','sha256':'y'}})
    p=R.retain(tmp_path/'plan.json',dict(schema_version='harbor-current-response-review/v1',helper=R.binding(M.__file__),response_helper=R.binding(R.__file__),review_plan=selected,continuation_history={'path':'a','sha256':'b'}))
    def fail(*args,**kw):raise ValueError('changed tracker')
    monkeypatch.setattr(M,'validate',fail)
    with pytest.raises(ValueError,match='changed tracker'):M.review(p)


def test_budget_selection_retains_attempts_and_budget_files(tmp_path):
    for f in ('instruction/budget.json','normalization/attempts/001/request.json','instruction/normal.txt'):
        p=tmp_path/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
    rows=M.budget_files(R,tmp_path)
    assert len(rows)==2
    assert {Path(x['path']).name for x in rows}=={'budget.json','request.json'}


def test_reviewer_budget_cannot_change_author_budget_closure(tmp_path):
    author=tmp_path/'author/budget.json';author.parent.mkdir();author.write_text('{}')
    before=M.budget_files(R,tmp_path)
    review=tmp_path/'response-review-final/model-campaign/attempts/001/budget.json';review.parent.mkdir(parents=True);review.write_text('{}')
    assert M.budget_files(R,tmp_path)==before
    other=tmp_path/'author/attempts/002/result.json';other.parent.mkdir(parents=True);other.write_text('{}')
    assert M.budget_files(R,tmp_path)!=before


def custody_fixture(tmp_path,monkeypatch):
    from types import SimpleNamespace
    home=tmp_path/'author';home.mkdir()
    def retain(name,v):return R.retain(tmp_path/name,v)
    source=retain('source.json',{})
    config=retain('config.json',{'run_root':str(home)})
    descriptor=retain('descriptor.json',{})
    checkpoint=retain('checkpoint.json',{})
    ready=retain('ready.json',{'workbook':'4036','config':config,'checkpoint':checkpoint})
    tracker=R.retain(home/'trackers/workbooks/4036.json',{'consumed_attempts':2})
    central=retain('central.json',dict(schema_version='fleet-central-history/v1',roots=[str(home)],scan_roots=[str(home)],known_ids=['4036']))
    outcome=retain('outcome.json',dict(status='PUBLICATION_READY',returncode=0,result=ready,failure_type=None,automatic_retry=False))
    value=dict(schema_version='harbor-ready-response-custody/v1',helper=R.binding(M.__file__),response_helper=R.binding(R.__file__),code_root=str(tmp_path),workbook='4036',source=source,
      ready=ready,config=config,tracker=tracker,descriptor=descriptor,checkpoint=checkpoint,budgets=[],central_predecessor=central,roots=[str(home)],known_ids=['4036'],excluded={'4036':[str(home)]},histories={'4036':[tracker]},scan_roots=[str(home)],central_excluded={'4036':[str(home)]},central_histories={'4036':[tracker]},author_outcome=outcome,captured_at_ns=1,fresh_admission_allowed=False)
    def owning(*args):return R.checked(ready),{},None,(None,None,None,None,None,{'run_root':str(home)}),{'descriptor':descriptor},SimpleNamespace(selection=SimpleNamespace(content=SimpleNamespace(bindings={'original_raw_source':source})))
    monkeypatch.setattr(M,'helper',lambda _:R);monkeypatch.setattr(R,'owning_ready',owning)
    return value,home


def test_current_custody_accepts_actual_tracker_and_rejects_mutation(tmp_path,monkeypatch):
    value,home=custody_fixture(tmp_path,monkeypatch);b=R.retain(tmp_path/'custody.json',value)
    assert M.validate(b)['fresh_admission_allowed'] is False
    Path(value['tracker']['path']).write_text('{"consumed_attempts":0}')
    with pytest.raises(ValueError,match='tracker changed'):M.validate(b)


@pytest.mark.parametrize('key',('histories','central_histories'))
def test_current_custody_cannot_hide_own_history(tmp_path,monkeypatch,key):
    value,_=custody_fixture(tmp_path,monkeypatch);value[key]={'4036':[{'path':'unrelated','sha256':'0'*64}]}
    b=R.retain(tmp_path/'custody.json',value)
    with pytest.raises(ValueError,match='tracker omitted'):M.validate(b)


def test_current_custody_cannot_use_false_original_scope(tmp_path,monkeypatch):
    value,_=custody_fixture(tmp_path,monkeypatch);value['known_ids'].append('4444');b=R.retain(tmp_path/'custody.json',value)
    with pytest.raises(ValueError,match='original complete history scope'):M.validate(b)


def test_actual_model_campaign_and_attempt_identities_are_retained(tmp_path):
    paths=['model-campaign/scope/attempt-01/identity.json','model-campaign/attempt-identities/uuid.json','model-campaign/scope/attempt-01/transport-00.started.json','environment/audit-bridge/attempt-01/result.json']
    for name in paths:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
    assert len(M.budget_files(R,tmp_path))==4
