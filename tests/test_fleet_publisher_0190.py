import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
import pytest


def module():
    path=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/holds-0190/publication-successor-001/publish_0190.py'
    if not path.exists():pytest.skip('optional standalone publisher snapshot')
    spec=importlib.util.spec_from_file_location('test_standalone0190publisher',path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m


def test_unreviewed_adapter_is_rejected_before_loading():
    p=module()
    with pytest.raises(ValueError,match='validator changed'):p.existing_review_adapter(dict(history_validator={'path':'/must-not-load','sha256':'f'*64}))


def test0190_environment_has_only_its_exact_history_and_new_custody(tmp_path,monkeypatch):
    p=module();history={'path':'/retained/0190history','sha256':'a'*64}
    seen=[];monkeypatch.setattr(p,'existing_review_adapter',lambda plan:SimpleNamespace(validate=lambda module,plan:seen.append(plan)))
    plan=dict(workbook='0190',code_root='/reviewed/c7ad-code',existing_review_history=history)
    auth=dict(workbook='0190',owner='current0190',token='synthetic',fence=3)
    monkeypatch.setenv('HARBOR_FLEET_HISTORY_PATH','/fresh/unauthorized');monkeypatch.setenv('HARBOR_FLEET_MODEL_JOURNAL','/old/model-journal')
    env=p.publication_environment(plan,auth,action='remote',url='http://127.0.0.1:8765',token_file=tmp_path/'token')
    assert seen==[plan]
    assert env['HARBOR_FLEET_EXISTING_REVIEW_HISTORY_PATH']==history['path']
    assert env['HARBOR_FLEET_EXISTING_REVIEW_HISTORY_SHA256']==history['sha256']
    assert all(key not in env for key in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_MODEL_JOURNAL'))
    assert env['HARBOR_FLEET_PUBLICATION_ONLY']=='1' and env['HARBOR_FLEET_FENCE']=='3'
