import pytest
from scripts.fleet.configure_model_environment import normalized, configure


def test_legacy_alias_preserved_and_original_reader_can_read(tmp_path):
    from xl_task_build import read_env_key
    path = tmp_path / 'model.env'
    original = '# retain existing configuration\nlbx_api_key="test-secret"\n'
    path.write_text(original); path.chmod(0o600)
    assert not read_env_key(path)
    result = configure(path, apply=True)
    assert result['changed'] and 'test-secret' not in str(result)
    assert path.read_text().startswith(original)
    assert read_env_key(path) == 'test-secret'
    assert not configure(path, apply=True)['changed']
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('raw', ['', 'lbx_api_key=a\nRL_API_KEY=b\n', 'lbx_api_key=a\nlbx_api_key=b\n'])
def test_conflicting_or_missing_credentials_fail_without_disclosure(raw):
    with pytest.raises(ValueError, match='missing or conflicting'):
        normalized(raw)


def test_public_permissions_rejected(tmp_path):
    path = tmp_path / 'model.env'; path.write_text('lbx_api_key=a\n'); path.chmod(0o644)
    with pytest.raises(ValueError, match='private'): configure(path, apply=True)
    assert path.read_text() == 'lbx_api_key=a\n'
