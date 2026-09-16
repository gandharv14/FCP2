#!/app/.venv/bin/python -I
"""Adversarial in-container smoke check; emits booleans, never source records."""
import asyncio
import errno
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '/usr/local/bin')  # Root-owned, non-writable trusted modules.
from fcp_model_identity import inspect_model_identity
from fcp_solver_python import check_offline_install


checks = {}


def denied(name, operation):
    try:
        operation()
    except (PermissionError, OSError) as error:
        checks[name] = error.errno in (errno.EACCES, errno.EPERM)
    else:
        checks[name] = False


status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
checks['uid_unprivileged'] = os.getuid() == os.geteuid() == 60100
checks['gid_unprivileged'] = os.getgid() == os.getegid() == 60100
checks['supplemental_groups_limited'] = all(group == 0 for group in os.getgroups())
checks['saved_uids_unprivileged'] = os.getresuid() == (60100, 60100, 60100)
checks['saved_gids_unprivileged'] = os.getresgid() == (60100, 60100, 60100)
checks['no_new_privileges'] = status['NoNewPrivs'].strip() == '1'
checks['no_capabilities'] = all(int(status[name].strip(), 16) == 0 for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'))
checks['no_platform_credentials'] = not any(key in os.environ for key in
    ('CALLBACK_AUTH_TOKEN', 'CALLBACK_URL', 'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_CREDENTIALS',
     'KUBERNETES_SERVICE_HOST', 'AWS_SECRET_ACCESS_KEY', 'LABELBOX_API_KEY'))
model_identity = inspect_model_identity()
expected_harness = os.environ.get('FCP_EXPECTED_HARNESS')
if expected_harness == 'self-test':
    checks['model_identity_not_required_for_self_test'] = model_identity is None
else:
    expected_cli = {'claude': 'claude', 'opencode': 'opencode'}.get(expected_harness)
    expected_model = {'claude': 'claude-opus-5', 'opencode': 'google-vertex/gemini-3.8-flash'}.get(expected_harness)
    checks['model_cli_identified'] = bool(model_identity)
    checks['model_matches_requested'] = bool(model_identity and model_identity['harness'] == expected_cli
                                             and model_identity['model'] == expected_model)
    checks['model_effort_matches_requested'] = bool(model_identity and
        (expected_harness == 'opencode' or model_identity['effort'] == 'max'))
task = os.environ['FCP_TASK_ID']
pid = os.environ['FCP_RESEARCH_PID']
root = Path('/opt/fcp/tasks') / task
denied('corpus_listing_denied', lambda: list(root.iterdir()))
denied('server_read_denied', lambda: (root / 'mcp-server/server.py').read_bytes())
denied('runtime_read_denied', lambda: (root / 'mcp-server/runtime/records.jsonl').read_bytes())
denied('chmod_bypass_denied', lambda: os.chmod('/opt/fcp/tasks', 0o755))
denied('hardlink_bypass_denied', lambda: os.link(root / 'mcp-server/server.py', '/workspace/probe-hardlink'))
denied('root_home_denied', lambda: list(Path('/root').iterdir()))
denied('root_run_directory_denied', lambda: list(Path('/run').iterdir()))
denied('root_logs_denied', lambda: (Path('/run/fcp-private/research.log')).read_bytes())
for trusted in ('/usr/local/bin', '/usr/local/bin/fcp-isolated-runtime', '/app', '/app/.venv', '/etc/passwd'):
    checks['trusted_path_not_writable_' + trusted] = not os.access(trusted, os.W_OK)
for target in ('1', pid):
    denied(f'proc_{target}_environ_denied', lambda target=target: Path(f'/proc/{target}/environ').read_bytes())
    denied(f'proc_{target}_mem_denied', lambda target=target: Path(f'/proc/{target}/mem').open('rb'))
    denied(f'proc_{target}_fds_denied', lambda target=target: list(Path(f'/proc/{target}/fd').iterdir()))
    denied(f'proc_{target}_root_denied', lambda target=target: os.readlink(f'/proc/{target}/root'))
denied('research_signal_denied', lambda: os.kill(int(pid), 0))
denied('setuid_root_denied', lambda: os.setuid(0))
denied('setgid_root_denied', lambda: os.setgid(0))
child = subprocess.run([sys.executable, '-I', '-c',
    'import os;from pathlib import Path; print(os.getuid());print(Path("/proc/self/status").read_text())'],
    capture_output=True, text=True, check=True).stdout
checks['restrictions_survive_exec'] = child.startswith('60100\n') and 'NoNewPrivs:\t1' in child and 'CapBnd:\t0000000000000000' in child
workbooks = list(Path('/workspace').glob('*-inputs.xls*'))
checks['workbook_readable'] = bool(workbooks) and all(p.open('rb').read(2) == b'PK' for p in workbooks)
output = Path('/workspace/isolation-smoke-output.txt')
output.write_text('solver can write\n')
checks['output_writable'] = output.read_text() == 'solver can write\n'
output.unlink()
app_output = Path('/app/answers.json')
checks['app_output_alias'] = app_output.is_symlink() and os.readlink(app_output) == '/workspace/answers.json'


async def check_mcp():
    from fastmcp import Client
    async with Client('http://127.0.0.1:8000/mcp') as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        checks['mcp_tools_available'] = {'list_sources', 'search_documents', 'fetch_document', 'query_records'}.issubset(names)
        result = await client.call_tool('list_sources', {'kind': '', 'limit': 1})
        checks['mcp_sources_callable'] = not getattr(result, 'is_error', False) and bool(result.content)


if os.environ.get('FCP_RESEARCH_ENABLED') == '1':
    try:
        asyncio.run(check_mcp())
    except Exception as error:
        checks['mcp_failed'] = False
        checks['mcp_error_type'] = type(error).__name__
else:
    checks['plain_task_no_mcp_required'] = True
try:
    python_install = check_offline_install(Path(os.environ['VIRTUAL_ENV']))
    checks['offline_pip_install_supported'] = python_install['passed'] is True
except Exception as error:
    python_install = {'passed': False, 'error_type': type(error).__name__}
    checks['offline_pip_install_supported'] = False
passed = all(value is True for value in checks.values())
print(json.dumps({'isolation_passed': passed, 'identity': {'uid': os.getuid(), 'gid': os.getgid(),
    'supplemental_groups': os.getgroups()}, 'model_identity': model_identity,
    'python_install': python_install, 'checks': checks}, sort_keys=True))
sys.exit(0 if passed else 1)
