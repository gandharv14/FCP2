"""Each case starts clean; pytest's previously imported skills cannot hide bugs."""
from pathlib import Path
import subprocess
import sys
import textwrap
import pytest
from scripts.batch import harbor_readiness

ROOT=Path(__file__).resolve().parents[1]
# Root selected by installed module, not a caller-supplied evidence directory.
REPO=next(p for p in Path(__file__).resolve().parents if (p/'xl_segment.py').is_file())


def child(body):
    bootstrap=f'''
import sys,types
from pathlib import Path
import scripts.batch
scripts.batch.__path__=[{str(Path(harbor_readiness.__file__).parent)!r},*scripts.batch.__path__]
from scripts.batch import harbor_readiness as r
ROOT=Path({str(REPO)!r})
'''
    result=subprocess.run([sys.executable,'-B','-c',textwrap.dedent(bootstrap)+textwrap.dedent(body)],cwd=REPO,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('owner',['readiness','producer'])
def test_clean_lazy_naturalization_budget(owner):
    child(f'''
from scripts.batch import harbor_instruction_producer as p
loader={'r' if owner=='readiness' else 'p'}.skill_module
before=list(sys.path)
nat=loader(ROOT,'naturalize-finance-task-instruction','naturalize_recovery')
assert sys.path==before
nat._verified_budget(ROOT,{{'attempts':[]}})
for name in ('span_restoration','frozen_submission','freeze_protected_spans','validate_instruction_rewrite','instruction_spans'):
    assert Path(sys.modules[name].__file__).resolve()==ROOT/'.cursor/skills/naturalize-finance-task-instruction/scripts'/(name+'.py')
assert sys.path==before
''')


@pytest.mark.parametrize('owner',['readiness','producer'])
def test_clean_dialogue_lazy_siblings(owner):
    child(f'''
from scripts.batch import harbor_instruction_producer as p
before=list(sys.path)
composer={'r' if owner=='readiness' else 'p'}.skill_module(ROOT,'additional-assumptions-dialogue','compose_draft')
from validate_dialogue import fill_faults
from claim_scope import prove_groups
assert callable(fill_faults) and callable(prove_groups)
assert composer is sys.modules['compose_draft'] and sys.path==before
assert Path(sys.modules['disclose'].__file__).resolve()==ROOT/'.cursor/skills/task-disclosure/scripts/disclose.py'
''')


@pytest.mark.parametrize('foreign',['span_restoration','instruction_spans','frozen_submission'])
def test_foreign_cached_sibling_rejected_before_any_skill_execution(foreign):
    child(f'''
before=list(sys.path)
bad=types.ModuleType({foreign!r});bad.__file__='/tmp/untrusted-evidence/{foreign}.py';sys.modules[{foreign!r}]=bad
try:r.skill_module(ROOT,'naturalize-finance-task-instruction','naturalize_recovery')
except ValueError as exc:assert 'foreign cached' in str(exc)
else:raise AssertionError('foreign cached module accepted')
assert sys.path==before and 'naturalize_recovery' not in sys.modules
''')


@pytest.mark.parametrize('skill,name',[('../untrusted','payload'),('naturalize-finance-task-instruction','../payload'),('unknown','naturalize_recovery')])
def test_unknown_skill_or_module_rejected(skill,name):
    child(f'''
before=list(sys.path)
try:r.skill_module(ROOT,{skill!r},{name!r})
except ValueError as exc:assert 'unsupported' in str(exc)
else:raise AssertionError('unregistered module accepted')
assert sys.path==before
''')


def test_all_fixed_owners_share_the_exact_module_identity():
    child('''
from scripts.batch import harbor_instruction_producer as p
before=list(sys.path)
first=r.skill_module(ROOT,'naturalize-finance-task-instruction','naturalize_recovery')
second=p.skill_module(ROOT,'naturalize-finance-task-instruction','naturalize_recovery')
assert first is second and p.skill_module is r.skill_module and sys.path==before
''')


def test_symlinked_skill_ancestor_rejected(tmp_path):
    child(f'''
root=Path({str(tmp_path)!r});(root/'.cursor').mkdir();(root/'.cursor/skills').symlink_to(ROOT/'.cursor/skills',target_is_directory=True)
before=list(sys.path)
try:r.skill_module(root,'naturalize-finance-task-instruction','naturalize_recovery')
except ValueError as exc:assert 'unsafe' in str(exc)
else:raise AssertionError('symlinked skill accepted')
assert sys.path==before
''')
