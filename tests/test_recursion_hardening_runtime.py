"""Security contract tests; real Linux syscall test runs only as root."""
import importlib.util
import errno
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/recursion_hardening_runtime.py'
spec = importlib.util.spec_from_file_location('isolated_runtime', SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
identity_spec = importlib.util.spec_from_file_location('model_identity', SOURCE.with_name('recursion_model_identity.py'))
identity = importlib.util.module_from_spec(identity_spec)
identity_spec.loader.exec_module(identity)


@pytest.mark.parametrize('task', ['0004', 'Advanced_DCF_Model_-Multi-Scenario', 'Fin_Deriv_RK_v1.2'])
def test_preserves_numeric_and_named_tasks(task):
    assert runtime.identify_task(f'Use {task}-inputs.xlsx. Then fill {task}-inputs.xlsx.', []) == task


def test_conflicting_workbooks_fail_closed():
    with pytest.raises(RuntimeError, match='exactly one'):
        runtime.identify_task('0004-inputs.xlsx 0007-inputs.xlsx', [])


def test_solver_env_omits_platform_credentials_and_injection(tmp_path):
    raw = {'ANTHROPIC_API_KEY': 'model-secret', 'GEMINI_API_KEY': 'other-model-secret',
           'CALLBACK_AUTH_TOKEN': 'platform-secret', 'LABELBOX_API_KEY': 'another-secret',
           'GOOGLE_APPLICATION_CREDENTIALS': '/tmp/cloud-credentials',
           'PYTHONPATH': '/untrusted', 'LD_PRELOAD': '/untrusted.so',
           'PROXY_BASE_URL': 'https://model-proxy.invalid', 'HTTPS_PROXY': 'http://proxy.invalid',
           'NODE_EXTRA_CA_CERTS': '/etc/ssl/custom.pem', 'FCP_TASKS_ROOT': '/untrusted',
           'AGY_ADC_AUTH': 'true', 'GOOGLE_CLOUD_PROJECT': 'model-project', 'VERTEX_LOCATION': 'global',
           'NO_PROXY': '.internal.example', 'no_proxy': 'custom.local'}
    with mock.patch.dict(os.environ, raw, clear=True), mock.patch.object(runtime, 'safe_directory'):
        env = runtime.solver_environment()
    assert env['ANTHROPIC_API_KEY'] == 'model-secret'
    assert env['GEMINI_API_KEY'] == 'other-model-secret'
    assert env['PROXY_BASE_URL'] == raw['PROXY_BASE_URL']
    assert env['NODE_EXTRA_CA_CERTS'] == raw['NODE_EXTRA_CA_CERTS']
    assert env['AGY_ADC_AUTH'] == 'true'
    assert env['GOOGLE_CLOUD_PROJECT'] == 'model-project'
    assert env['VERTEX_LOCATION'] == 'global'
    assert env['NO_PROXY'] == env['no_proxy']
    assert set(env['NO_PROXY'].split(',')) == {'.internal.example', 'custom.local', '127.0.0.1', 'localhost', '::1'}
    assert not any(name in env for name in ('CALLBACK_AUTH_TOKEN', 'LABELBOX_API_KEY',
        'GOOGLE_APPLICATION_CREDENTIALS', 'PYTHONPATH', 'LD_PRELOAD', 'FCP_TASKS_ROOT'))
    assert env['HOME'] == '/home/fcp-solver'
    assert '/root' not in env['PATH']


def test_claude_max_effort_and_explicit_mcp(tmp_path):
    with mock.patch.object(runtime, 'HOME', tmp_path), mock.patch.object(os, 'chown'):
        command = runtime.model_command('claude', 'prompt', ['--model', 'opus-5'], {})
    assert command[command.index('--effort') + 1] == 'max'
    assert command[command.index('--model') + 1] == 'opus-5'
    assert json.loads((tmp_path / 'mcp.json').read_text())['mcpServers']['research']['url'] == runtime.MCP_URL


@pytest.mark.parametrize('args', [['--effort', 'high'], ['--effort'], ['--effort=max', '--effort=low'],
    ['--effort', 'max', '--effort=low'], ['--effort=max', '--effort', 'max']])
def test_claude_mismatched_effort_rejected(tmp_path, args):
    with mock.patch.object(runtime, 'HOME', tmp_path), mock.patch.object(os, 'chown'):
        with pytest.raises(RuntimeError, match='requires Claude'):
            runtime.model_command('claude', '', args, {})


def test_gemini_model_routed_exactly_and_mcp_enabled():
    env = {'FCP_OPENCODE_MODEL': 'google/gemini-3.8-flash'}
    command = runtime.model_command('opencode', 'task prompt', [], env)
    assert command[command.index('--model') + 1] == 'google/gemini-3.8-flash'
    config = json.loads(env['OPENCODE_CONFIG_CONTENT'])
    assert config['mcp']['research']['url'] == runtime.MCP_URL
    assert config['model'] == config['small_model'] == env['FCP_OPENCODE_MODEL']
    assert config['enabled_providers'] == ['google']
    assert set(config['agent']) == {'build', 'plan', 'general', 'explore', 'title', 'summary', 'compaction'}
    assert all(agent['model'] == env['FCP_OPENCODE_MODEL'] for agent in config['agent'].values())


def test_plain_tasks_do_not_configure_nonexistent_mcp(tmp_path):
    with mock.patch.object(runtime, 'HOME', tmp_path):
        claude = runtime.model_command('claude', '', ['--model', 'opus-5'], {}, research=False)
    assert '--mcp-config' not in claude
    assert not (tmp_path / 'mcp.json').exists()
    env = {'FCP_OPENCODE_MODEL': 'google-vertex/gemini-3.8-flash'}
    runtime.model_command('opencode', 'prompt', [], env, research=False)
    assert 'mcp' not in json.loads(env['OPENCODE_CONFIG_CONTENT'])


def test_plain_task_assets_are_valid_without_server(tmp_path):
    task_root = tmp_path / 'tasks' / '0004'
    task_root.mkdir(parents=True)
    (task_root / '0004-inputs.xlsx').write_bytes(b'PKtest')
    workspace, app, home = (tmp_path / name for name in ('workspace', 'app', 'home'))
    workspace.mkdir()
    app.mkdir()
    with mock.patch.multiple(runtime, TASKS=tmp_path / 'tasks', WORKSPACE=workspace, APP=app, HOME=home), \
         mock.patch.object(runtime, 'safe_directory'), mock.patch.object(os, 'chown'), \
         mock.patch.dict(os.environ, {}, clear=True):
        assert runtime.setup_files('0004') is None
    assert (workspace / '0004-inputs.xlsx').read_bytes() == b'PKtest'


def test_public_copy_refuses_symlink(tmp_path):
    real = tmp_path / 'real'
    real.write_text('do not expose')
    source = tmp_path / 'source'
    source.symlink_to(real)
    with pytest.raises(RuntimeError, match='regular file'):
        runtime.copy_public(source, tmp_path / 'destination')


def test_preexec_failure_reports_stage_and_errno_without_secrets():
    def kernel(option, arg=0, arg2=0):
        if option == 47:
            raise OSError(errno.EINVAL, 'unsupported')
        return 0
    with mock.patch.object(runtime, 'no_core_dumps'), mock.patch.object(runtime, 'prctl', side_effect=kernel), \
         mock.patch.object(runtime, 'privilege_status', return_value={'Uid': '0', 'CapBnd': '0000'}), \
         mock.patch.object(os, 'write') as write:
        with pytest.raises(OSError):
            runtime.drop_solver_privileges()
    diagnostic = json.loads(write.call_args.args[1].decode().split(' ', 1)[1])
    assert diagnostic['stage'] == 'clear_ambient_capabilities'
    assert diagnostic['errno'] == errno.EINVAL
    assert set(diagnostic) == {'stage', 'error_type', 'errno', 'privileges'}


def test_bounding_enumeration_skips_absent_capabilities():
    calls = []
    def kernel(option, arg=0, arg2=0):
        calls.append((option, arg))
        if option == 23:
            if arg == 3:
                raise OSError(errno.EINVAL, 'last capability reached')
            return 1 if arg == 1 else 0
        return 0
    status = dict.fromkeys(('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'), '0000')
    status['NoNewPrivs'] = '1'
    with mock.patch.object(runtime, 'no_core_dumps'), mock.patch.object(runtime, 'prctl', side_effect=kernel), \
         mock.patch.object(runtime, 'zero_capabilities'), mock.patch.object(runtime, 'privilege_status', return_value=status), \
         mock.patch.object(os, 'setgroups'), mock.patch.object(os, 'setresgid', create=True), \
         mock.patch.object(os, 'setresuid', create=True), mock.patch.object(os, 'getuid', return_value=60100), \
         mock.patch.object(os, 'umask'):
        runtime.drop_solver_privileges()
    assert [call for call in calls if call[0] == 24] == [(24, 1)]


def test_setid_preparation_rejects_initial_no_new_privileges():
    with mock.patch.object(runtime, 'prctl', return_value=1), \
         mock.patch.object(runtime, 'privilege_status', return_value={'NoNewPrivs': '1'}), \
         mock.patch.object(os, 'write') as write:
        with pytest.raises(RuntimeError, match='prevents downward'):
            runtime.prepare_setid_exec()
    diagnostic = json.loads(write.call_args.args[1].decode().split(' ', 1)[1])
    assert diagnostic['stage'] == 'verify_initial_no_new_privileges'


def test_setid_preparation_never_enables_nnp_before_file_transition():
    calls = []
    def kernel(option, arg=0, arg2=0):
        calls.append((option, arg))
        if option == 23 and arg == 2:
            raise OSError(errno.EINVAL, 'last cap reached')
        return 0
    with mock.patch.object(runtime, 'prctl', side_effect=kernel), mock.patch.object(runtime, 'no_core_dumps'), \
         mock.patch.object(os, 'getgroups', return_value=[0]), \
         mock.patch.object(runtime, 'privilege_status', return_value={'CapBnd': '0'}):
        runtime.prepare_setid_exec()
    assert not any(option == 38 for option, arg in calls)


def test_execve_command_is_resolved_before_helper_execution():
    with mock.patch.object(runtime.shutil, 'which', return_value='/usr/local/bin/claude'):
        command = runtime.resolve_executable(['claude', '--model', 'opus-5'], {'PATH': '/usr/local/bin'})
    assert command == ['/usr/local/bin/claude', '--model', 'opus-5']


def test_model_identity_extracts_flags_without_prompt_or_auth_values():
    argv = ['opencode', 'run', '--model', 'google-vertex/gemini-3.8-flash', 'private prompt']
    assert identity.identify_cli(argv) == 'opencode'
    assert identity.flag_value(argv, '--model') == 'google-vertex/gemini-3.8-flash'
    assert identity.flag_value(argv, '--effort') is None
    argv = ['/usr/bin/claude', '-p', '--model=claude-opus-5', '--effort', 'max']
    assert identity.identify_cli(argv) == 'claude'
    assert identity.flag_value(argv, '--model') == 'claude-opus-5'
    assert identity.flag_value(argv, '--effort') == 'max'


def test_model_identity_ignores_watchdog_and_rejects_duplicate_flags():
    assert identity.identify_cli(['python', '/usr/local/bin/fcp-inactivity-watchdog', '900',
                                  'opencode', '--model', 'google-vertex/gemini-3.8-flash']) is None
    assert identity.flag_value(['--model', 'one', '--model=two'], '--model') is None


@pytest.mark.parametrize('executable,argv,expected', [
    ('/usr/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe',
     ['/usr/bin/claude', '--model', 'claude-opus-5', '--effort', 'max'],
     {'harness': 'claude', 'model': 'claude-opus-5', 'effort': 'max', 'cli_pid': 20}),
    ('/usr/local/opencode/bin/opencode',
     ['opencode', 'run', '--model', 'google-vertex/gemini-3.8-flash', 'private prompt'],
     {'harness': 'opencode', 'model': 'google-vertex/gemini-3.8-flash', 'effort': None, 'cli_pid': 20}),
])
def test_actual_image_cli_paths_are_identified_through_tool_ancestry(tmp_path, executable, argv, expected):
    for pid, parent, arguments in [(30, 20, ['python', 'probe']), (20, 1, argv), (1, 0, ['root-runner'])]:
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / 'status').write_text(f'PPid:\t{parent}\n')
        (directory / 'cmdline').write_bytes(b'\0'.join(x.encode() for x in arguments) + b'\0')
    real_path = Path
    def path(value):
        return tmp_path if value == '/proc' else real_path(value)
    def readlink(value):
        if value.parent.name == '20': return executable
        raise PermissionError()
    with mock.patch.object(identity, 'Path', side_effect=path), mock.patch.object(os, 'getpid', return_value=30), \
         mock.patch.object(os, 'readlink', side_effect=readlink), \
         mock.patch.object(identity, 'trusted_executable', side_effect=lambda p: p == executable):
        assert identity.inspect_model_identity() == expected


def test_model_cli_build_owner_is_normalized_before_solver_execution():
    target = mock.Mock()
    target.is_relative_to.return_value = True
    target.is_file.return_value = True
    target.stat.return_value = mock.Mock(st_uid=1001, st_mode=0o100755)
    with mock.patch.object(runtime.shutil, 'which', return_value='/usr/local/bin/opencode'), \
         mock.patch.object(runtime, 'Path') as path, mock.patch.object(os, 'chown') as chown:
        path.return_value.resolve.return_value = target
        runtime.protect_model_cli('opencode', {'PATH': '/usr/local/bin'})
    chown.assert_called_once_with(target, 0, 0)
    target.chmod.assert_called_once_with(0o755)


def test_static_helper_is_bound_to_current_source_and_build_receipt():
    binary = SOURCE.parents[1] / 'reports/recursion/gemini-high-score-audit-20260915/refactor/runtime/fcp-demote-exec-linux-amd64'
    receipt = json.loads(binary.with_suffix('.build.json').read_text())
    assert receipt['binarySha256'] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert receipt['sourceSha256'] == hashlib.sha256(SOURCE.with_name('recursion_demote_exec.c').read_bytes()).hexdigest()
    assert receipt['static'] is True and receipt['nonExecutableStack'] is True


@pytest.mark.skipif(sys.platform != 'linux' or os.geteuid() != 0, reason='requires isolated Linux root process')
def test_native_setid_helper_without_setuid_setgid_caps(tmp_path):
    helper = tmp_path / 'demote-exec'
    binary = SOURCE.parents[1] / 'reports/recursion/gemini-high-score-audit-20260915/refactor/runtime/fcp-demote-exec-linux-amd64'
    helper.write_bytes(binary.read_bytes())
    tmp_path.chmod(0o700)
    os.chown(helper, 60100, 60100)
    helper.chmod(0o6755)
    # Everything before the file-ID transition runs in a disposable child.
    code = f'''import ctypes,runpy,subprocess,sys,os
d=runpy.run_path({str(SOURCE)!r})
class H(ctypes.Structure):
 _fields_=[('version',ctypes.c_uint32),('pid',ctypes.c_int)]
class D(ctypes.Structure):
 _fields_=[('effective',ctypes.c_uint32),('permitted',ctypes.c_uint32),('inheritable',ctypes.c_uint32)]
libc=ctypes.CDLL(None,use_errno=True);h=H(0x20080522,0);v=(D*2)()
assert libc.capget(ctypes.byref(h),ctypes.byref(v))==0
v[0].effective &= ~((1<<6)|(1<<7));v[0].permitted &= ~((1<<6)|(1<<7))
assert libc.capset(ctypes.byref(h),ctypes.byref(v))==0
d['prepare_setid_exec']()
os.execv({str(helper)!r},[{str(helper)!r},sys.executable,'-c',"import os;from pathlib import Path;assert os.getresuid()==(60100,60100,60100);assert os.getresgid()==(60100,60100,60100);s=dict(x.split(':',1) for x in Path('/proc/self/status').read_text().splitlines() if ':' in x);assert s['NoNewPrivs'].strip()=='1';assert all(int(s[n].strip(),16)==0 for n in ['CapInh','CapPrm','CapEff','CapBnd','CapAmb'])"])
'''
    subprocess.run([sys.executable, '-c', code], check=True)


@pytest.mark.skipif(sys.platform != 'linux' or os.geteuid() != 0, reason='requires isolated Linux root process')
def test_real_linux_privilege_drop_and_exec():
    # Runs in a disposable child; the pytest parent never drops privileges.
    code = f'''import runpy,subprocess,sys,os
d=runpy.run_path({str(SOURCE)!r})
d['drop_solver_privileges']()
assert os.getuid()==60100
try:
 os.setuid(0)
except PermissionError:
 pass
else:
 raise AssertionError('root regained')
subprocess.run([sys.executable,'-c',"from pathlib import Path;import os;s=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line);assert os.getuid()==60100;assert s['NoNewPrivs'].strip()=='1';assert int(s['CapBnd'].strip(),16)==0"],check=True)
'''
    subprocess.run([sys.executable, '-c', code], check=True)
