"""Credential routing after retiring the old Labelbox key name."""
from pathlib import Path
import runpy
from unittest import mock

import pytest

from xl_task_build import read_env_key
from xl_seg.adjudicate import read_key, via_proxy


def test_labelbox_key_replaces_retired_file_key(tmp_path, monkeypatch):
    monkeypatch.setattr('os.environ', {})
    env_file = tmp_path / '.env'
    env_file.write_text('lbx_api_key=retired\nlbx_labelbox_api_key=current\n')
    assert read_env_key(env_file) == 'current'
    assert read_key(env_file) == 'current'
    assert via_proxy(env_file)
    env_file.write_text('lbx_api_key=retired\n')
    assert not read_env_key(env_file)
    assert not read_key(env_file)
    assert not via_proxy(env_file)


@pytest.mark.parametrize('name', ['lbx_labelbox_api_key', 'LBX_LABELBOX_API_KEY'])
def test_labelbox_key_environment_aliases(tmp_path, monkeypatch, name):
    monkeypatch.setattr('os.environ', {
        'lbx_api_key': 'retired', 'LBX_API_KEY': 'retired', name: 'current',
    })
    assert read_env_key(tmp_path / 'missing.env') == 'current'


def test_retired_environment_key_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr('os.environ', {'lbx_api_key': 'retired', 'LBX_API_KEY': 'retired'})
    assert not read_env_key(tmp_path / 'missing.env')


def test_recursion_ignores_retired_key_selector(monkeypatch):
    monkeypatch.setattr('os.environ', {'FCP_RECURSION_KEY_NAME': 'lbx_api_key'})
    wrapper = Path(__file__).resolve().parents[1] / 'scripts/recursion_cli.py'
    with mock.patch.object(Path, 'read_text', return_value='lbx_api_key=retired\nlbx_labelbox_api_key=current\n'):
        with mock.patch('os.execve', side_effect=SystemExit) as execute:
            with pytest.raises(SystemExit):
                runpy.run_path(str(wrapper), run_name='__main__')
    assert execute.call_args.args[2]['LABELBOX_API_KEY'] == 'current'
