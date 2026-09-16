"""The selected physical bridge must affect the actual executor and restore it."""
import os,sys,types
from pathlib import Path
import pytest
from scripts.fleet import disclosure_review_continue as M
from test_fleet_model_gate import case


def fixture(tmp_path,monkeypatch):
    physical=tmp_path/'physical.py';physical.write_text('import os\ndef validate(p): return p, {}\ndef selected(): return ({"path":os.environ["HARBOR_PHYSICAL_MODEL_POLICY"],"sha256":os.environ["HARBOR_PHYSICAL_MODEL_POLICY_SHA256"]},{})\n')
    transport=tmp_path/'transport.py';transport.write_text('sentinel="selected"\n')
    policy=tmp_path/'policy.json';policy.write_text('{}')
    p={'physical_module':M.binding(physical),'transport':M.binding(transport),'physical_policy':M.binding(policy)}
    old=types.SimpleNamespace(sentinel='original');executor=types.SimpleNamespace(xl_transport=old)
    monkeypatch.setenv('HARBOR_PHYSICAL_MODEL_POLICY','prior-path');monkeypatch.setenv('HARBOR_PHYSICAL_MODEL_POLICY_SHA256','prior-sha')
    return p,executor,old


def test_selected_bridge_reaches_executor_and_restores_on_failure(tmp_path,monkeypatch):
    p,e,old=fixture(tmp_path,monkeypatch);prior=sys.modules.get('xl_transport')
    with pytest.raises(RuntimeError):
        with M.transport_runtime(p,e):
            assert e.xl_transport is sys.modules['xl_transport']
            assert e.xl_transport.sentinel=='selected'
            M.transport_checked(p,e)
            raise RuntimeError('consumer failed')
    assert e.xl_transport is old and sys.modules.get('xl_transport')is prior
    assert os.environ['HARBOR_PHYSICAL_MODEL_POLICY']=='prior-path'
    assert os.environ['HARBOR_PHYSICAL_MODEL_POLICY_SHA256']=='prior-sha'


def test_changed_selected_transport_rejected_before_next_dispatch(tmp_path,monkeypatch):
    p,e,old=fixture(tmp_path,monkeypatch)
    with M.transport_runtime(p,e):
        Path(p['transport']['path']).write_text('sentinel="changed"\n')
        with pytest.raises(ValueError,match='transport changed'):M.transport_checked(p,e)
    assert e.xl_transport is old


def test_changed_module_before_context_restores_original_executor(tmp_path,monkeypatch):
    p,e,old=fixture(tmp_path,monkeypatch)
    Path(p['transport']['path']).write_text('sentinel="changed"\n')
    with pytest.raises(ValueError,match='module changed'):
        with M.transport_runtime(p,e):pytest.fail('entered changed transport')
    assert e.xl_transport is old
    assert os.environ['HARBOR_PHYSICAL_MODEL_POLICY']=='prior-path'


def test_fresh_custody_is_checked_after_admission_before_transport():
    from contextlib import contextmanager
    events=[]
    @contextmanager
    def original(**kwargs):
        events.append('admission');yield;events.append('release')
    e=types.SimpleNamespace(xl_transport=types.SimpleNamespace(model_slot=original))
    def fresh():events.append('fresh')
    with M.fresh_admitted_transport(e,fresh):
        with e.xl_transport.model_slot(operation_id='real-operation'):events.append('HTTP')
    assert events==['admission','fresh','HTTP','release']
    assert e.xl_transport.model_slot is original


def test_expired_during_wait_refuses_transport_and_restores_gate():
    from contextlib import contextmanager
    events=[]
    @contextmanager
    def original(**kwargs):
        events.append('admission')
        try:yield
        finally:events.append('retained-release')
    e=types.SimpleNamespace(xl_transport=types.SimpleNamespace(model_slot=original))
    def expired():raise ValueError('expired after waiting')
    with pytest.raises(ValueError,match='expired'):
        with M.fresh_admitted_transport(e,expired):
            with e.xl_transport.model_slot():pytest.fail('HTTP started after expiry')
    assert events==['admission','retained-release']
    assert e.xl_transport.model_slot is original


def test_explicit_allowance_is_loaded_before_round_reader(tmp_path,monkeypatch):
    import inspect
    import scripts.batch as package
    from scripts.batch import harbor_continuation_allowance as A,harbor_disclosure_review_round as Q
    assert list(M.MODULES).index('allowance')<list(M.MODULES).index('rounds')
    selected=tmp_path/'allowance.py';selected.write_bytes(Path(A.__file__).read_bytes())
    name='scripts.batch.harbor_continuation_allowance'
    monkeypatch.setitem(sys.modules,name,sys.modules[name])
    installed=M.module(name,M.binding(selected));monkeypatch.setattr(package,'harbor_continuation_allowance',installed)
    name='selected_round_allowance_regression';monkeypatch.setitem(sys.modules,name,None)
    reader=M.module(name,M.binding(Path(Q.__file__).resolve()))
    assert reader.A is installed
    assert 'cumulative_limit'in inspect.signature(reader.A.reserve_review).parameters


@pytest.mark.parametrize('wrong_identity',[False,True])
def test_real_coordinator_admitted_operation_is_not_treated_as_new_admission(case,monkeypatch,wrong_identity):
    from dataclasses import replace
    from scripts.batch import harbor_model_executor as E
    from scripts.fleet import model_gate,capacity_policy
    from test_harbor_model_executor import Response,response_bytes
    c,campaign,prepared=case
    row=c.status()['workbooks'][0]
    p=dict(capacity_policy=M.binding(Path(capacity_policy.__file__).resolve()),owner=row['owner'],workbook=row['workbook'],fence=row['fence'],source={'sha256':row['source_sha256']})
    sent=[];errors=[]
    def opened(*args):sent.append(1);return Response(response_bytes(prepared.policy))
    monkeypatch.setattr(E,'_open',opened)
    def check():
        selected=replace(prepared,attempt_id='00000000-0000-0000-0000-000000000000')if wrong_identity else prepared
        assert any(v['state']=='active'and v['kind']=='model'for v in c.status()['operations'])
        try:M.admitted_custody(p,Path(row['version']),selected,E,model_gate)
        except BaseException as exc:errors.append(repr(exc));raise
    with M.fresh_admitted_transport(E,check):outcome=E.execute(prepared,campaign,credential='fixture-only')
    assert sent==([]if wrong_identity else[1]),errors
    assert outcome.status==('incomplete'if wrong_identity else'completed')
    assert c.status()['operations'][0]['state']==('hold'if wrong_identity else'confirmed')
