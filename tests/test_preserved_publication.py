from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import pytest
from scripts.fleet import fresh_publication as F,preserved_gate_continue as M


def test_actual_preserved_context_has_only_closed_publication_projection():
    projected=F.preserved_context(M.context,lambda *a:None,lambda *a:None)
    assert projected.__name__==M.context.__name__


def test_completed_publication_projection_preserves_history_without_author_admission():
    seen=[]
    class Worker:
        def active(self,*a):raise AssertionError('queued publication must use original P custody')
    W=Worker()
    @contextmanager
    def original(row,*,continued=False,invocation=None):
        seen.append(('history',row,continued))
        def current(env,worker):
            if invocation is None:w,controls,s,l=('old','old','old','old')
            else:raise AssertionError('wrong historical invocation')
            W.active(w,s,l)
            return w,controls,s,l
        yield current('original-env','original-worker')
    # Real source projection retains this function's closure globals by binding
    # its explicit test dependencies into the module dictionary.
    import sys
    module=sys.modules[original.__module__];module.seen=seen;module.W=W
    checkpoint=lambda env,worker:('actual-worker','controls','queued-selection','queued-lease')
    selected=F.preserved_context(original,checkpoint,lambda w,s,l:seen.append(('original-P',w,s,l)))
    with selected('sealed-history',continued=True)as result:
        assert result==('actual-worker','controls','queued-selection','queued-lease')
    assert seen==[('history','sealed-history',True),('original-P','actual-worker','queued-selection','queued-lease')]


@pytest.mark.parametrize('fault',[None,'source','helper','schema','fresh'])
def test_preserved_history_never_becomes_fresh_authority(fault):
    row={'path':'plan','sha256':'a'};reader={'path':'reader','sha256':'b'}
    selected=dict(schema_version=M.SCHEMA,helper=reader,workbook='0077',source={'sha256':'source'})
    plan=dict(workbook='0077',source_sha256='source',preserved_gate=dict(reader=reader,plan=row))
    if fault=='source':plan['source_sha256']='foreign'
    if fault=='helper':selected['helper']={'path':'foreign','sha256':'b'}
    if fault=='schema':selected['schema_version']='unrelated'
    if fault=='fresh':plan['fresh_authoring']=row
    def require(ok,message):
        if not ok:raise ValueError(message)
    pub=SimpleNamespace(require=require,checked=lambda _:selected,binding=lambda _:reader)
    if fault:
        with pytest.raises(ValueError):F.preserved_history_binding(pub,plan)
    else:assert F.preserved_history_binding(pub,plan)==row
