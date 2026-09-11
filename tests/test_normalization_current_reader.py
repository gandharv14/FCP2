"""Current reader uses closed approved dependency bytes; history stays data."""
from pathlib import Path
import importlib.util,json,shutil
import pytest
BASE=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910'
READER=BASE/'code-0274-alias-v2/current_normalization_reader'
SELF='scripts/batch/harbor_partition_spent_unknown.py'

def module(root):
 spec=importlib.util.spec_from_file_location('current_reader_adapter_under_test',root/SELF);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def manifest():return json.loads((BASE/'normalization/0274/partitions/prepared.json').read_text())

def test_selected_reader_is_current_and_exact_dependencies():
 m=module(READER);prior,current,old,now=m.code_projection(manifest())
 assert current==READER and prior!=current
 assert now['gen_normalizer.py']==old['gen_normalizer.py']
 assert set(now)-set(old)=={SELF,'original-dependency-inventory.json'}

@pytest.mark.parametrize('mutation',['extra_module','changed_dependency','forged_inventory','symlink'])
def test_reader_dependency_tampering_fails_closed(tmp_path,mutation):
 root=tmp_path/'reader';shutil.copytree(READER,root,ignore=shutil.ignore_patterns('__pycache__'))
 if mutation=='extra_module':(root/'sitecustomize.py').write_text('raise RuntimeError("injected")')
 elif mutation=='changed_dependency':(root/'gen_normalizer.py').write_text('# altered generator')
 elif mutation=='forged_inventory':
  p=root/'original-dependency-inventory.json';v=json.loads(p.read_text());v['gen_normalizer.py']='0'*64;p.write_text(json.dumps(v))
 else:(root/'injected.py').symlink_to(root/'gen_normalizer.py')
 with pytest.raises(ValueError):module(root).code_projection(manifest())
