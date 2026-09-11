"""External-volume paths bind only private materialized bytes, read-only."""
from pathlib import Path
import pytest
from scripts import harbor_verification_package as P


def test_external_volume_root_uses_only_materialized_namespace(tmp_path):
    namespace = tmp_path.resolve()/'namespace'
    source = namespace/'Volumes'/'Evidence Drive'/'custom-artifacts'
    source.mkdir(parents=True)
    (source/'graph.csv').write_bytes(b'fixture graph')
    assert P._mounts(namespace) == ['--mount', f'type=bind,src={namespace}/Volumes,dst=/Volumes,readonly']


def test_external_volume_symlink_root_is_rejected(tmp_path):
    namespace = tmp_path.resolve()/'namespace'; namespace.mkdir()
    foreign = tmp_path.resolve()/'foreign'; foreign.mkdir()
    (namespace/'Volumes').symlink_to(foreign, target_is_directory=True)
    with pytest.raises(P.PackageError, match='symlink'): P._mounts(namespace)


def test_external_volume_root_cannot_be_regular_file(tmp_path):
    (tmp_path/'Volumes').write_bytes(b'not directory')
    with pytest.raises(P.PackageError, match='directory'): P._mounts(tmp_path)


@pytest.mark.parametrize('root', ['usr', 'bin', 'sbin', 'proc', 'dev', 'System', 'Library'])
def test_system_roots_remain_forbidden(tmp_path, root):
    (tmp_path/root).mkdir()
    with pytest.raises(P.PackageError, match='unsupported'): P._mounts(tmp_path)


def test_external_mount_cannot_inject_mount_options(tmp_path):
    namespace = tmp_path/'namespace,readonly=false'; (namespace/'Volumes').mkdir(parents=True)
    with pytest.raises(P.PackageError, match='comma'): P._mounts(namespace)
