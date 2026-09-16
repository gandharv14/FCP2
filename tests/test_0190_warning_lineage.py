"""Actual immutable full-code lineage, without authoring or tracker mutations."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
HOME=ROOT/'diagnostics/vm-fleet-20260912/control/0190-warning-continuation-001/successor-003'


@pytest.fixture
def reader():
    if not HOME.exists():pytest.skip('selected immutable successor unavailable')
    b=json.loads((HOME/'build.json').read_bytes());s=json.loads(Path(b['snapshot']['path']).read_bytes())
    root=Path(s['root']);p=root/'scripts/fleet/dialogue_warning_lineage_0190.py'
    spec=importlib.util.spec_from_file_location('test_0190_lineage',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m,root


def test_full_actual_original_inventory(reader):
    m,root=reader;actual=m.inventory(root);old=m.predecessor(actual)
    assert len(old)==632 and len(actual)>len(old)
    assert '.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py' in actual


@pytest.mark.parametrize('fault',['extra','missing','source_algorithm','gate','reader'])
def test_unselected_code_or_inventory_rejected(reader,fault):
    m,root=reader;actual=m.inventory(root)
    if fault=='extra':actual['unauthorized.py']='0'*64
    elif fault=='missing':actual.pop('xl_ast_graph.py')
    else:
        name={'source_algorithm':'xl_ast_graph.py','gate':'scripts/fleet/model_gate.py','reader':m.SELF}[fault]
        actual[name]='0'*64
    with pytest.raises(ValueError):m.predecessor(actual)


def test_exact_historical_normalization_reader_still_validates(reader):
    _,root=reader
    code='''import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts.batch import harbor_normalization_existing_review as M
from scripts.fleet import dialogue_warning_lineage_0190 as L
actual=L.inventory(Path(sys.argv[1]))
prior=M._fleet_predecessor(actual)
assert prior and len(prior)<len(actual)
from scripts.batch import harbor_dialogue_warning_requalification as Q
profile=L.checked(L.PROFILE)
changes=Q.changes(profile['old_snapshot'],Path(sys.argv[1]))
assert 'scripts/batch/harbor_normalization_existing_review.py' in changes
assert '.pipeline-code.json' in changes
print('PASS')
'''
    p=subprocess.run([sys.executable,'-I','-B','-c',code,str(root)],capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    assert p.stdout.strip()=='PASS'
