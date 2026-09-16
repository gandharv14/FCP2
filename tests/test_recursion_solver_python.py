import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from recursion_solver_python import prepare_solver_python, check_offline_install


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def local_wheel(path):
    files = {'fcp_offline_probe/__init__.py': b'VALUE = 78231\n',
        'fcp_offline_probe-1.0.dist-info/METADATA': b'Metadata-Version: 2.1\nName: fcp-offline-probe\nVersion: 1.0\n',
        'fcp_offline_probe-1.0.dist-info/WHEEL': b'Wheel-Version: 1.0\nGenerator: offline-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    rows = io.StringIO(); writer = csv.writer(rows)
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow([name, 'sha256=' + digest, len(data)])
    writer.writerow(['fcp_offline_probe-1.0.dist-info/RECORD', '', ''])
    files['fcp_offline_probe-1.0.dist-info/RECORD'] = rows.getvalue().encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items(): archive.writestr(name, data)


def test_nested_base_packages_and_offline_pip_write_only_solver_prefix(tmp_path):
    base = tmp_path / 'trusted-base'
    subprocess.run([sys.executable, '-I', '-m', 'venv', str(base)], check=True)
    base_python = str(base / 'bin/python')
    site = Path(subprocess.check_output([base_python, '-I', '-c',
        'import sysconfig;print(sysconfig.get_path("purelib"))'], text=True).strip())
    (site / 'fcp_base_only.py').write_text('VALUE = 1243\n')
    before = snapshot(base)
    home = tmp_path / 'solver'; home.mkdir()
    settings = prepare_solver_python(home, os.getuid(), os.getgid(), base_python)
    prefix = Path(settings['VIRTUAL_ENV'])
    env = dict(os.environ, **{k:v for k,v in settings.items() if k != 'PYTHON_BIN_DIRECTORY'},
        PATH=settings['PYTHON_BIN_DIRECTORY'] + os.pathsep + os.environ['PATH'],
        HOME=str(home), PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
        PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1')
    wheel = tmp_path / 'fcp_offline_probe-1.0-py3-none-any.whl'; local_wheel(wheel)
    subprocess.run(['pip', 'install', '--no-index', '--no-deps', str(wheel)], env=env,
                   check=True, capture_output=True)
    actual = json.loads(subprocess.check_output(['python', '-c',
        'import json,sys,fcp_base_only,fcp_offline_probe;'
        'print(json.dumps([sys.prefix,fcp_base_only.VALUE,fcp_offline_probe.VALUE,fcp_offline_probe.__file__]))'],
        env=env, text=True))
    assert actual[:3] == [str(prefix), 1243, 78231]
    assert Path(actual[3]).is_relative_to(prefix)
    if os.geteuid() != 0:
        smoke = check_offline_install(prefix)
        assert smoke["passed"] and smoke["cleanupComplete"] and smoke["offline"]
    assert snapshot(base) == before
    assert all(p.lstat().st_uid == os.getuid() for p in prefix.rglob('*'))
    assert subprocess.run([base_python, '-I', '-c', 'import fcp_offline_probe'],
                          capture_output=True).returncode != 0
    with pytest.raises(RuntimeError, match='existing solver-writable'):
        prepare_solver_python(home, os.getuid(), os.getgid(), base_python)


def test_rejects_symlink_home(tmp_path):
    target = tmp_path / 'target'; target.mkdir()
    home = tmp_path / 'home'; home.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match='non-symlink'):
        prepare_solver_python(home, os.getuid(), os.getgid(), sys.executable)
