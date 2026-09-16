"""Create a solver-owned Python prefix without changing the trusted interpreter.

Run once in trusted startup, before any solver code. The base environment's
packages stay at their original read-only paths; pip installs into the new prefix.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def prepare_solver_python(home: Path, uid: int, gid: int, base_python: str) -> dict[str, str]:
    home = Path(home).absolute()
    if home.is_symlink() or not home.is_dir():
        raise RuntimeError('Solver HOME must be an existing non-symlink directory')
    destination = home / '.venv-solver'
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('Refusing to bootstrap from an existing solver-writable Python environment')
    # -I excludes the working directory, PYTHONPATH and user startup packages.
    metadata = json.loads(subprocess.check_output([base_python, '-I', '-B', '-c',
        'import json,sysconfig; print(json.dumps(list(dict.fromkeys('
        '[sysconfig.get_path("purelib"),sysconfig.get_path("platlib")]))))'], text=True))
    paths = [str(Path(path).resolve(strict=True)) for path in metadata]
    if any('\n' in path or '\r' in path for path in paths):
        raise RuntimeError('Unsupported Python package path')
    subprocess.run([base_python, '-I', '-B', '-m', 'venv', '--without-pip', str(destination)], check=True)
    python = destination / 'bin/python'
    local_site = Path(subprocess.check_output([str(python), '-I', '-B', '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    if not local_site.is_relative_to(destination):
        raise RuntimeError('New Python environment does not own its install prefix')
    (local_site / '_fcp_trusted_base.pth').write_text('\n'.join(paths) + '\n')
    available = subprocess.run([str(python), '-I', '-B', '-c', 'import pip'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if available.returncode:
        # ensurepip uses wheels bundled with Python; it performs no downloads.
        subprocess.run([str(python), '-I', '-B', '-m', 'ensurepip', '--default-pip'], check=True)
    # The entry points use the solver interpreter even when pip itself is read
    # from the trusted base. Its sys.prefix/install scheme is the solver prefix.
    pip_entry = f'#!{python}\nfrom pip._internal.cli.main import main\nraise SystemExit(main())\n'
    version = subprocess.check_output([str(python), '-I', '-B', '-c',
        'import sys; print("%s.%s" % sys.version_info[:2])'], text=True).strip()
    for name in ('pip', 'pip3', 'pip' + version):
        path = destination / 'bin' / name
        path.write_text(pip_entry)
        path.chmod(0o755)
    # Never follow the venv's interpreter symlinks when handing ownership over.
    for directory, directories, files in os.walk(destination, followlinks=False):
        os.chown(directory, uid, gid)
        for name in directories + files:
            os.chown(Path(directory) / name, uid, gid, follow_symlinks=False)
    cache = home / '.cache/pip'
    return {'VIRTUAL_ENV': str(destination), 'PYTHON_BIN_DIRECTORY': str(destination / 'bin'),
            'PIP_CACHE_DIR': str(cache), 'PIP_REQUIRE_VIRTUALENV': 'true'}


def check_offline_install(venvpath: Path) -> dict[str, object]:
    """Exercise ordinary pip/python using a tiny wheel, then remove that package.

    This is a functional package-install check, not a trusted isolation check:
    the solver owns these executables. Call it as the solver, never as root.
    """
    import base64
    import csv
    import hashlib
    import io
    import tempfile
    import uuid
    import zipfile

    prefix = Path(venvpath).absolute()
    if os.geteuid() == 0:
        raise RuntimeError('Offline install smoke check must run as the solver')
    name = 'fcp_offline_probe_' + uuid.uuid4().hex
    dist = name + '-1.0.dist-info'
    files = {name + '/__init__.py': b'VALUE = 78231\n',
        dist + '/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n'.encode(),
        dist + '/WHEEL': b'Wheel-Version: 1.0\nGenerator: fcp-offline-probe\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    records = io.StringIO(); writer = csv.writer(records)
    for filename, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow([filename, 'sha256=' + digest, len(data)])
    writer.writerow([dist + '/RECORD', '', ''])
    files[dist + '/RECORD'] = records.getvalue().encode()
    env = {k:v for k,v in os.environ.items() if not k.startswith('PIP_')}
    env.update(VIRTUAL_ENV=str(prefix), PIP_CONFIG_FILE=os.devnull,
        PIP_REQUIRE_VIRTUALENV='true', PIP_DISABLE_PIP_VERSION_CHECK='1',
        PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    pip = str(prefix / 'bin/pip'); python = str(prefix / 'bin/python')
    installed = False
    with tempfile.TemporaryDirectory(prefix='fcp-offline-wheel-') as temporary:
        wheel = Path(temporary) / (name + '-1.0-py3-none-any.whl')
        with zipfile.ZipFile(wheel, 'w') as archive:
            for filename, data in files.items(): archive.writestr(filename, data)
        try:
            subprocess.run([pip, 'install', '--no-index', '--no-deps', str(wheel)],
                env=env, check=True, capture_output=True, text=True)
            installed = True
            details = json.loads(subprocess.check_output([python, '-I', '-B', '-c',
                'import importlib,json,sys; m=importlib.import_module(sys.argv[1]);'
                'print(json.dumps({"prefix":sys.prefix,"modulePath":m.__file__,"value":m.VALUE}))', name],
                env=env, text=True))
            passed = (details['prefix'] == str(prefix) and details['value'] == 78231
                      and Path(details['modulePath']).is_relative_to(prefix))
            result = {'passed': passed, 'offline': True, **details}
        finally:
            if installed:
                subprocess.run([pip, 'uninstall', '-y', name], env=env,
                               check=True, capture_output=True, text=True)
    result['cleanupComplete'] = not Path(result['modulePath']).exists()
    result['passed'] = result['passed'] and result['cleanupComplete']
    return result
