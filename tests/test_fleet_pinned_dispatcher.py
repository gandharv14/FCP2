import sys,types
from pathlib import Path
import pytest
from scripts.fleet import pinned_dispatcher as M,publish as P

@pytest.fixture
def sample(tmp_path,monkeypatch):
 helper=tmp_path/'author_workbook.py';helper.write_text('VALUE=17\n')
 driver=tmp_path/'driver.py';driver.write_text('from author_workbook import VALUE\nRESULT=VALUE+1\n')
 monkeypatch.delitem(sys.modules,'author_workbook',raising=False)
 yield P.binding(driver),P.binding(helper)
 sys.modules.pop('author_workbook',None);sys.modules.pop('fixture_pinned_dispatcher',None)

def test_real_import_uses_exact_pinned_original_helper(sample):
 driver,helper=sample
 assert M.load(P,driver,helper,namespace='fixture_pinned_dispatcher').RESULT==18

@pytest.mark.parametrize('target',['driver','helper'])
def test_tamper_blocks_both_imports(sample,monkeypatch,target):
 driver,helper=sample;selected=driver if target=='driver' else helper;Path(selected['path']).write_text('raise RuntimeError("must not import")\n');calls=[]
 monkeypatch.setattr(P,'load',lambda *args:calls.append(args))
 with pytest.raises(ValueError):M.load(P,driver,helper,namespace='fixture_pinned_dispatcher')
 assert calls==[]

def test_foreign_cached_original_refused(sample,monkeypatch):
 driver,helper=sample;monkeypatch.setitem(sys.modules,'author_workbook',types.SimpleNamespace(__file__='/tmp/foreign.py'))
 with pytest.raises(ValueError,match='foreign cached'):M.load(P,driver,helper,namespace='fixture_pinned_dispatcher')

@pytest.mark.parametrize('namespace',['','author_workbook',None])
def test_bad_module_namespace_refused(sample,namespace):
 with pytest.raises(ValueError):M.load(P,*sample,namespace=namespace)

def test_helper_symlink_rejected_before_execution(sample,tmp_path):
 driver,helper=sample;link=tmp_path/'alias';link.symlink_to(helper['path']);helper=dict(helper,path=str(link))
 with pytest.raises(ValueError):M.load(P,driver,helper,namespace='fixture_pinned_dispatcher')
