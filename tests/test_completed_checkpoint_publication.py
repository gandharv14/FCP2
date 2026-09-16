from types import SimpleNamespace
import pytest
from scripts.fleet import fresh_publication as F
from scripts.fleet import completed_instruction_continue as S


@pytest.mark.parametrize('fault',[None,'source','reader','schema','mixed'])
def test_completed_checkpoint_history_preserves_exact_source_and_reader(fault):
    row={'path':'plan','sha256':'a'};reader={'path':'reader','sha256':'b'}
    selected=dict(schema_version='fleet-completed-instruction-continuation/v1',helper=reader,
        workbook='0177',source={'sha256':'source'})
    plan=dict(workbook='0177',source_sha256='source',completed_checkpoint=dict(reader=reader,plan=row))
    if fault=='source':plan['source_sha256']='other'
    if fault=='reader':selected['helper']={'foreign':'reader'}
    if fault=='schema':selected['schema_version']='other'
    if fault=='mixed':plan['model_tail']={}
    def require(ok,message):
        if not ok:raise ValueError(message)
    pub=SimpleNamespace(require=require,checked=lambda _:selected,binding=lambda _:reader)
    if fault:
        with pytest.raises(ValueError):F.checkpoint_history_binding(pub,plan)
    else:assert F.checkpoint_history_binding(pub,plan)==row


def test_completed_checkpoint_selects_original_cold_publication_adapter(monkeypatch):
    request=object();producer=SimpleNamespace(run=lambda *a:pytest.fail('unadapted publication'))
    pub=SimpleNamespace(__file__='/publisher.py',binding=lambda p:{'path':p,'sha256':'p'})
    plan={'completed_checkpoint':{'actual':'reader'}};row={'path':'plan','sha256':'r'};seen=[]
    monkeypatch.setattr(F,'install_validation',lambda *a:None)
    monkeypatch.setattr(F,'projected_request',lambda p,P,r,q:r)
    monkeypatch.setattr(F,'reader_evidence',lambda *a:['closed reader'])
    monkeypatch.setattr(F,'publication_arguments',lambda *a:('journal',{}))
    def select(P,**kw):seen.append(kw);return lambda *a,**k:'original'
    assert F.publish(pub,row,plan,(None,None,producer),request,SimpleNamespace(select=select))=='original'
    assert seen==[dict(reader_plan=row,child_wrapper={'path':'/publisher.py','sha256':'p'},reader_evidence=['closed reader'])]


def test_actual_context_has_one_scoped_publication_dependency_site():
    assert F.checkpoint_publication_context(S.context).__name__=='context'


@pytest.mark.parametrize('fault',[None,'publisher_bytes','foreign_worker','foreign_transition','qualification_bytes','foreign_qualification'])
def test_publisher_scope_preserves_original_checks_and_restores_modules(tmp_path,monkeypatch,fault):
    import sys
    old,new=tmp_path/'old',tmp_path/'new'
    for root in(old,new):
        (root/'scripts/batch').mkdir(parents=True)
        (root/'scripts/harbor_publish.py').write_text('original publication checks\n')
        (root/'scripts/batch/harbor_promotion_evidence.py').write_text('original qualification checks\n')
    old_ready=SimpleNamespace(__file__=str(old/'scripts/batch/harbor_ready_transitions.py'))
    old_worker=SimpleNamespace(__file__=str(old/'scripts/batch/harbor_instruction_worker.py'))
    qualification=SimpleNamespace(__file__=str(old/'scripts/batch/harbor_promotion_evidence.py'),transitions=old_ready)
    P=SimpleNamespace(__file__=str(old/'scripts/harbor_publish.py'),ROOT=old,ready=old_ready,instruction=old_worker,qualification=qualification)
    W=SimpleNamespace(__file__=str(new/'scripts/batch/harbor_instruction_worker.py'),ROOT=new)
    current=SimpleNamespace(__file__=str(new/'scripts/batch/harbor_ready_transitions.py'),ROOT=new)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_ready_transitions',current)
    if fault=='publisher_bytes':(new/'scripts/harbor_publish.py').write_text('altered checks')
    if fault=='foreign_worker':W.ROOT=old
    if fault=='foreign_transition':current.ROOT=old
    if fault=='qualification_bytes':(new/'scripts/batch/harbor_promotion_evidence.py').write_text('altered qualification')
    if fault=='foreign_qualification':qualification.__file__='/foreign/promotion.py'
    manager=F.checkpoint_publication_scope(P,W,dict(original=str(old),selected=str(new)))
    if fault:
        with pytest.raises(ValueError):
            with manager:pass
    else:
        with pytest.raises(RuntimeError):
            with manager:
                assert(P.ROOT,P.ready,P.instruction,qualification.transitions)==(new,current,W,current)
                raise RuntimeError('original guard fails')
    assert(P.ROOT,P.ready,P.instruction,qualification.transitions)==(old,old_ready,old_worker,old_ready)
