"""The sizing boundary must not allocate or change an ordinary review identity."""
from pathlib import Path
from types import SimpleNamespace
import ast
import inspect
import pytest
from scripts.batch import harbor_instruction_producer as P


def test_ordinary_review_uses_identical_material_and_original_request(monkeypatch,tmp_path):
    task=object();golden={'path':'golden','sha256':'a'*64};root=tmp_path
    body={'preserved': ['£', {'formula': '=A1+0.01'}]}
    prompt='Original complete review';evidence=[golden];manifest={'path':'manifest','sha256':'b'*64}
    request=object();events=[]
    def material(t,g,*,root):
        assert (t,g,root)==(task,golden,tmp_path)
        events.append('material');return body,prompt,evidence,manifest
    def allocate(*args):
        assert args==(task,'disclosure-review',P.DISCLOSURE,1,1,body,prompt,evidence)
        events.append('allocate');return request
    monkeypatch.setattr(P,'disclosure_review_material',material)
    monkeypatch.setattr(P,'_request',allocate)
    assert P.prepare_disclosure_review(task,golden,root=root)==(request,manifest)
    assert events==['material','allocate']


def test_material_failure_cannot_allocate_a_request(monkeypatch,tmp_path):
    def invalid(*args,**kwargs):raise ValueError('source differs')
    def forbidden(*args,**kwargs):raise AssertionError('request identity allocated')
    monkeypatch.setattr(P,'disclosure_review_material',invalid)
    monkeypatch.setattr(P,'_request',forbidden)
    with pytest.raises(ValueError,match='source differs'):
        P.prepare_disclosure_review(object(),{},root=tmp_path)


def test_material_contains_no_request_or_transport_entrypoint():
    tree=ast.parse(inspect.getsource(P.disclosure_review_material))
    names={n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
    assert not names.intersection({'_request','dispatch','execute','reserve_review'})
    assert {'task_context','checked_record','disclosure_manifest','project_body'}<=names
