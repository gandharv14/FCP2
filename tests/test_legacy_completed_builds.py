from pathlib import Path
import copy,json,shutil
import pytest
from scripts.batch import harbor_legacy_research_history as L
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def actual(tmp_path):
 old=ROOT.parent/'runs/0043-variable-sources';root=tmp_path/'0043-variable-sources';shutil.copytree(old,root)
 files=L.inventory(root);tracker=json.loads((ROOT.parent/'runs/compatible-rebuild-20260830/workbooks/0043.json').read_text())
 stage=dict(schema_version='harbor-legacy-completed-mcp-builds/v1',maskability=files['maskability_report.json'],leakscan=files['leakscan_report.json'],profiles=files['source_profiles.json'],scanned_source=L.binding(tracker['source_file']))
 return dict(workbook='0043',completed_builds=stage),files,root


def test_retained_completed_builds_and_reports_preserved(actual):
 p,files,root=actual;before=L.inventory(root);extra=L.completed_builds(p,files)
 assert len(extra)==26 and 'mcp/__pycache__/server.cpython-312.pyc'in extra
 assert L.inventory(root)==before

@pytest.mark.parametrize('defect',['report','pair','source','profiles','cardinality'])
def test_completed_builds_refuse_foreign_or_changed_stage(actual,defect):
 p,files,root=actual
 if defect=='report':name='maskability_report.json';change=lambda v:{**v,'valid':False}
 elif defect=='profiles':name='source_profiles.json';change=lambda v:{**v,'profiles':[{}]}
 elif defect=='cardinality':name='mcp/environment.json';change=lambda v:{**v,'tasks':2}
 elif defect=='source':
  p['completed_builds']['scanned_source']['sha256']='0'*64
  with pytest.raises(ValueError):L.completed_builds(p,files)
  return
 else:
  files['mcp-build-b/server.py']['sha256']='0'*64
  with pytest.raises(ValueError):L.completed_builds(p,files)
  return
 target=root/name;target.write_text(json.dumps(change(json.loads(target.read_text()))));files=L.inventory(root)
 if defect=='cardinality':
  other=root/'mcp-build-b/environment.json';other.write_bytes(target.read_bytes());files=L.inventory(root)
 for key in ('maskability','profiles'):
  n='maskability_report.json'if key=='maskability'else'source_profiles.json'
  p['completed_builds'][key]=files[n]
 with pytest.raises(ValueError):L.completed_builds(p,files)
