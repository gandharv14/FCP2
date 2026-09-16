import json
from types import SimpleNamespace
import pytest
from scripts.fleet import publication_launch as L

@pytest.mark.parametrize('fault',[None,'changed','foreign','duplicate'])
def test_local_version_reads_exact_native_binding_from_custody(tmp_path,monkeypatch,fault):
 f=tmp_path/'copy.json';f.write_text('{"version":"selected"}');local=L.binding(f);native=dict(path='/native/original-job.json',sha256=local['sha256']);files=[dict(original=native,local=local)]
 if fault=='duplicate':files.append(files[0])
 c=tmp_path/'custody.json';c.write_text(json.dumps(dict(schema_version='fleet-publication-version-custody/v1',files=files)));p=dict(version_custody=L.binding(c));plan={}
 if fault=='changed':f.write_text('{}')
 def version(selected,read):
  assert selected is plan
  row=dict(native)
  if fault=='foreign':row['sha256']='foreign'
  return read(row)['version']
 monkeypatch.setattr(L,'publication_version',version)
 if fault:
  with pytest.raises(ValueError):L.local_publication_version(p,plan)
 else:assert L.local_publication_version(p,plan)=='selected'

def test_no_custody_preserves_original_reader(monkeypatch):
 def version(plan,read):
  assert read is L.checked
  return 'original'
 monkeypatch.setattr(L,'publication_version',version)
 assert L.local_publication_version({}, {})=='original'

@pytest.mark.parametrize('changed',[False,True])
def test_serialized_native_version_has_exact_self_contained_reader(tmp_path,changed):
 import inspect
 f=tmp_path/'reader.py';f.write_text("def version(plan,read):\n return read(plan['selected'])['version']\n")
 row=L.binding(f);plan=dict(disclosure_continuation=dict(reader={},plan={},completed_reader=row),selected={'path':'/selected'})
 scope={};exec(inspect.getsource(L.publication_version),scope)
 if changed:f.write_text("def version(plan,read):\n return 'foreign'\n")
 if changed:
  with pytest.raises(ValueError,match='reader changed'):scope['publication_version'](plan,lambda r:{'version':'actual'})
 else:assert scope['publication_version'](plan,lambda r:{'version':'actual'})=='actual'
