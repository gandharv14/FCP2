import hashlib,importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('lease_directory_control',ROOT/'diagnostics/bulk-production/replace_selected_lease.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
def b(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
@pytest.fixture
def selected(tmp_path):
 tracker=tmp_path/'tracker.json';tracker.write_text('{}');row=b(tracker);campaign=tmp_path/'campaign';campaign.mkdir()
 runtime=tmp_path/'runtime.py';runtime.write_text(f'''from pathlib import Path
from types import SimpleNamespace as N
import hashlib
def load(profile):
 s=N(workbook='0173',tracker={row!r})
 def record(path):return dict(path=path,sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
 T=N(schema=N(safe_path=lambda x:x),_layout=lambda s:(None,Path({str(campaign)!r})),record=record,replace_expired=lambda *a:(_ for _ in ()).throw(AssertionError('must not replace')))
 return None,T,s,None
''')
 return dict(runtime=b(runtime),profile={'selected':True},workbook='0173',native_directory=str(campaign/'lease-takeovers'/'next'))
def test_real_preflight_accepts_only_original_takeover_namespace(selected):
 v=M.native_preflight(selected)
 assert v['status']=='SELECTED_TAKEOVER_DIRECTORY_VALIDATED' and v['lease_mutations']==0
 assert not Path(selected['native_directory']).exists()
@pytest.mark.parametrize('kind',['wrong-parent','occupied','symlink','tracker'])
def test_preflight_rejects_before_any_root_or_task_intent(selected,kind,tmp_path):
 if kind=='wrong-parent':selected['native_directory']=str(tmp_path/'outside')
 elif kind=='occupied':Path(selected['native_directory']).mkdir(parents=True)
 elif kind=='symlink':
  p=Path(selected['native_directory']).parent;p.symlink_to(tmp_path/'campaign',target_is_directory=True)
 else:(tmp_path/'tracker.json').write_text('{"changed":true}')
 with pytest.raises(AssertionError):M.native_preflight(selected)
 assert not (tmp_path/'intent.json').exists()
