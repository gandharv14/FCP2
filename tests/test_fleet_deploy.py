import importlib.util
import io
import json
from pathlib import Path
import tarfile
import pytest
from scripts.fleet_deploy import bundle
from scripts.fleet.install_release import safe_members, verify_code


def test_dirty_snapshot_is_deterministic_and_excludes_external_state(tmp_path):
    root = tmp_path/'repo'
    root.mkdir()
    (root/'worker.py').write_text('VERSION = 1\n')
    (root/'.env').write_text('SECRET=not_for_release\n')
    (root/'external.py').symlink_to(tmp_path/'external.py')
    (tmp_path/'external.py').write_text('SECRET = "external"\n')
    (root/'scripts').mkdir()
    (root/'scripts'/'job.py').write_text('print("worker")\n')
    (root/'scripts'/'credentials.json').write_text('{"key":"secret"}')
    (root/'scripts'/'outside').symlink_to(tmp_path, target_is_directory=True)
    one = bundle(root, tmp_path/'one')
    two = bundle(root, tmp_path/'two')
    assert one['release'] == two['release']
    assert one['sha256'] == two['sha256']
    with tarfile.open(one['archive']) as stream:
        assert set(stream.getnames()) == {'worker.py', 'scripts/job.py', 'FLEET_CODE_MANIFEST.json', '.pipeline-code.json'}
        code = tmp_path/'code'
        stream.extractall(code, members=safe_members(stream), filter='data')
    verify_code(code, one['release'])
    from scripts import corpus
    assert corpus.object_hash(corpus._frozen_code_files(code)) == one['pipeline_code_hash']
    (code/'worker.py').write_text('VERSION = 2\n')
    with pytest.raises(ValueError, match='code hash mismatch'):
        verify_code(code, one['release'])
    with pytest.raises(ValueError, match='pipeline code snapshot changed'):
        corpus._frozen_code_files(code)
    (root/'worker.py').write_text('VERSION = 2\n')
    assert bundle(root, tmp_path/'three')['release'] != one['release']


@pytest.mark.parametrize('name,kind', [('../escape.py', 'file'), ('/absolute.py', 'file'), ('link.py', 'symlink'), ('twice.py', 'duplicate')])
def test_unsafe_archive_members_rejected(name, kind):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as stream:
        member = tarfile.TarInfo(name)
        if kind == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc/passwd'
        stream.addfile(member)
        if kind == 'duplicate':
            stream.addfile(member)
    data.seek(0)
    with tarfile.open(fileobj=data) as stream, pytest.raises(ValueError, match='unsafe or duplicate'):
        list(safe_members(stream))


def test_real_pinned_source_policy_is_loadable_from_isolated_bundle(tmp_path):
    """Runtime approval data is required code, even though it is not Python."""
    import os
    import subprocess
    import sys
    from xl_inventory_approval import load_pinned_registry
    root = Path(__file__).resolve().parents[1]
    release = bundle(root, tmp_path/'release')
    code = tmp_path/'isolated-code'
    with tarfile.open(release['archive']) as stream:
        stream.extractall(code, members=safe_members(stream), filter='data')
    expected = load_pinned_registry()
    script = 'import json; from xl_inventory_approval import load_pinned_registry; print(json.dumps(load_pinned_registry()))'
    observed = subprocess.run([sys.executable, '-B', '-c', script], cwd=code,
        env={**os.environ, 'PYTHONPATH':str(code), 'PYTHONDONTWRITEBYTECODE':'1'},
        capture_output=True, text=True)
    assert observed.returncode == 0, observed.stderr
    assert json.loads(observed.stdout) == expected
    assert 'verification_manifests/approved_source_inventories.v2.json' in json.loads((code/'.pipeline-code.json').read_text())
    # This optional real retained generation makes the original deployment
    # failure reproducible without copying private workbook data into code.
    generation = os.environ.get('FCP_FLEET_SOURCE_GENERATION')
    if generation:
        script = 'import json,sys; from xl_source_publication import validate_source_generation; r=validate_source_generation(sys.argv[1]); print(json.dumps({"generation_id":r["generation_id"]}))'
        observed = subprocess.run([sys.executable, '-B', '-c', script, generation], cwd=code,
            env={**os.environ, 'PYTHONPATH':str(code), 'PYTHONDONTWRITEBYTECODE':'1'}, capture_output=True, text=True)
        assert observed.returncode == 0, observed.stderr
        assert json.loads(observed.stdout)['generation_id'] == Path(generation).name
    (code/'verification_manifests/approved_source_inventories.v2.json').unlink()
    failed = subprocess.run([sys.executable, '-B', '-c', 'from xl_inventory_approval import load_pinned_registry; load_pinned_registry()'], cwd=code,
        env={**os.environ, 'PYTHONPATH':str(code)}, capture_output=True, text=True)
    assert failed.returncode != 0 and 'approval registry is not a regular file' in failed.stderr
