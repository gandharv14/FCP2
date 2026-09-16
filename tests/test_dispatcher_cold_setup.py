"""Exercise the real setup body in a fresh isolated Python process."""
import ast
import json
from pathlib import Path
import subprocess
import sys


def test_cold_setup_imports_only_after_authenticated_root(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'scripts/fleet/advance_authoring_v8.py'
    tree = ast.parse(source.read_text())
    setup = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'setup'))
    root = tmp_path / 'selected'
    target = root / 'scripts/fleet'; target.mkdir(parents=True)
    (root / 'scripts/__init__.py').write_text('')
    (target / '__init__.py').write_text('')
    (target / 'snapshot_inventory.py').write_text('def equivalent(actual, selected): return actual == selected\n')
    child = tmp_path / 'cold.py'
    # Stop at imports(root), before any workbook operation. This regression
    # proves the actual setup reaches that boundary under -I without PYTHONPATH.
    child.write_text('''import inspect,sys,json
from pathlib import Path
SCHEMA='test'
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):return {'path':'driver','sha256':'test'}
def checked(row):return row
def queue_item(queue,workbook):return {}
def code_snapshot(root):return {'root':str(root),'files':[]}
def imports(root):
 print('AUTHENTICATED_ROOT_IMPORTED');raise SystemExit(0)
''' + setup + '\nsetup(json.loads(sys.argv[1]))\n')
    job = dict(schema_version='test', driver=dict(path='driver', sha256='test'), queue={},
        config=dict(workbook='1234', code=dict(root=str(root), files=[])), outcome={}, operations='unused',
        maximum_steps=100, models=True, commands=True, containers=True, publish=False)
    result = subprocess.run([sys.executable, '-I', '-B', str(child), json.dumps(job)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'AUTHENTICATED_ROOT_IMPORTED'
