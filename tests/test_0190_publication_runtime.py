from pathlib import Path
import ast
import importlib.util
import sys
import subprocess
from types import SimpleNamespace
import pytest
from scripts.fleet import build_0190_publication_runtime as B
from scripts.fleet import publish as P


@pytest.fixture
def publisher(tmp_path):
    m=P.checked(B.build(tmp_path.resolve()/'successor'))
    spec=importlib.util.spec_from_file_location('test0190runtime',m['publisher']['path']);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def test_0190_actual_ssh_path_and_custody(tmp_path,monkeypatch,publisher):
    p=publisher;root=tmp_path.resolve();runtime=root/'venv/bin';runtime.mkdir(parents=True);system=root/'system';system.mkdir()
    for d,value in [(runtime,'correct'),(system,'wrong')]:
        f=d/'docker';f.write_text('#!/bin/sh\nprintf '+value);f.chmod(0o700)
    python=runtime/'python';python.symlink_to(Path(sys.executable).resolve());monkeypatch.setattr(p.sys,'executable',str(python));monkeypatch.setenv('PATH',str(system));monkeypatch.setenv('HARBOR_FLEET_MODEL_JOURNAL','obsolete')
    monkeypatch.setattr(p,'existing_review_adapter',lambda plan:SimpleNamespace(validate=lambda *a:None))
    monkeypatch.setattr(p,'retained_environment_binding',lambda plan:dict(path='/actual/retained-plan',sha256='b'*64))
    plan=dict(workbook='0190',code_root='/code',existing_review_history=dict(path='/original/history',sha256='a'*64));auth=dict(workbook='0190',owner='publication',token='synthetic',fence=4)
    env=p.publication_environment(plan,auth,action='remote',url='http://127.0.0.1:1',token_file=root/'unused')
    assert subprocess.run(['docker'],env=env,capture_output=True,check=True).stdout==b'correct'
    assert env['HARBOR_FLEET_EXISTING_REVIEW_HISTORY_PATH']=='/original/history'
    assert env['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN']=='/actual/retained-plan'
    assert 'HARBOR_FLEET_MODEL_JOURNAL' not in env


@pytest.mark.parametrize('fault',[None,'helper-changed','cached-foreign'])
def test_preimport_actual_helper(tmp_path,monkeypatch,publisher,fault):
    p=publisher;root=tmp_path.resolve();helper=root/'author_workbook.py';helper.write_text('value=123\n');driver=root/'driver.py';driver.write_text('from author_workbook import value\n')
    monkeypatch.setattr(p,'AUTHOR_HELPER',p.binding(helper));monkeypatch.delitem(sys.modules,'author_workbook',raising=False)
    if fault=='helper-changed':helper.write_text('value=456\n')
    if fault=='cached-foreign':monkeypatch.setitem(sys.modules,'author_workbook',SimpleNamespace(__file__=str(driver)))
    if fault:
        with pytest.raises(ValueError):p.owning_dispatcher(dict(workbook='0190'),dict(driver=p.binding(driver)))
    else:assert p.owning_dispatcher(dict(workbook='0190'),dict(driver=p.binding(driver))).value==123
    sys.modules.pop('author_workbook',None)


def test_only_runtime_and_preimport_changed(publisher):
    def funcs(path):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(Path(path).read_text()).body if isinstance(n,ast.FunctionDef)}
    old=funcs(B.ORIGINAL);new=funcs(publisher.__file__)
    assert set(new)-set(old)=={'owning_dispatcher','retained_environment_binding'}
    assert {k for k in old if old[k]!=new[k]}=={'publication_environment','remote_child'}


@pytest.mark.parametrize('fault',[None,'ready','config','history','dispatcher','closure','version'])
def test_actual_retained_namespace_binding(tmp_path,publisher,fault):
    p=publisher;h=tmp_path.resolve();helper=h/'namespace.py';helper.write_text('value=1\n');bound=p.binding(helper)
    meta=p.retain(h/'meta.json',dict(dispatcher=bound,namespace=bound,files=[bound]));config=dict(path='/exact/config',sha256='f'*64)
    authority={k:dict(path='/original/'+k,sha256='a'*64) for k in ('existing_review_history','existing_review_lineage','history_validator')}
    selection=dict(schema_version='harbor-fleet-retained-environment-continuation/v1',workbook='0190',code_root='/code/c7ad',source_sha256='source',version='c7ad',config=config,history_authority=authority,namespace_adapter=meta)
    if fault=='version':selection['version']='old'
    row=p.retain(h/'selection.json',selection);job=p.retain(h/'job.json',dict(driver={} if fault=='dispatcher' else bound,config=config))
    ready=dict(status='RETRY_READY' if fault=='ready' else 'PUBLICATION_READY',workbook='0190',publication_performed=False,automatic_retry=False,plan=row,config={} if fault=='config' else config,namespace_adapter=meta,job=job)
    plan=dict(workbook='0190',code_root='/code/c7ad',source_sha256='source',ready=p.retain(h/'ready.json',ready),**authority)
    if fault=='history':plan['existing_review_history']={}
    if fault=='closure':helper.write_text('changed')
    if fault:
        with pytest.raises(ValueError):p.retained_environment_binding(plan)
    else:assert p.retained_environment_binding(plan)==row
