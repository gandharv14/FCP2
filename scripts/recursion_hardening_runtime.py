#!/usr/bin/env python3
"""Trusted Linux bootstrap for the isolated September 2026 audit campaign.

Install root-owned at /usr/local/bin/fcp-isolated-runtime (0755). This program
requires root only before starting the solver. Never grant it setuid/file caps.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import pwd
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import time

SOLVER_UID = 60100
SOLVER_GID = 60100
TASKS = Path('/opt/fcp/tasks')
WORKSPACE = Path('/workspace')
APP = Path('/app')
HOME = Path('/home/fcp-solver')
PRIVATE = Path('/run/fcp-private')
DEMOTION_TEMPLATE = Path('/usr/local/libexec/fcp-demote-exec.template')
MCP_URL = 'http://127.0.0.1:8000/mcp'
SOLVER_ENV_KEYS = frozenset('''
ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_MODEL
ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL
CLAUDE_CODE_DISABLE_NONESSENTIAL CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
CLAUDE_CODE_MAX_OUTPUT_TOKENS CLAUDE_CODE_EFFORT_LEVEL MAX_THINKING_TOKENS IS_SANDBOX
GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENERATIVE_AI_API_KEY
GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_LOCATION GOOGLE_GENAI_USE_VERTEXAI
GOOGLE_USE_VERTEXAI VERTEXAI_PROJECT VERTEXAI_LOCATION VERTEX_LOCATION AGY_ADC_AUTH
OPENAI_API_KEY OPENAI_BASE_URL LLM_MODEL LLM_BASE_URL LLM_API_KEY
PROXY_BASE_URL PROXY_AUTH_TOKEN HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
http_proxy https_proxy all_proxy no_proxy
SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE NODE_EXTRA_CA_CERTS
VIRTUAL_ENV LANG LC_ALL TZ TERM NO_COLOR CI
FCP_OPENCODE_MODEL FCP_INACTIVITY_TIMEOUT_SECONDS AGENT_ARTIFACT_DIR
OPENCODE_DISABLE_AUTOUPDATE OPENCODE_DISABLE_DEFAULT_PLUGINS
'''.split())


def prctl(option: int, arg: int = 0, arg2: int = 0) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    # prctl's variadic arguments are unsigned long, including required zeros.
    # Declare full widths explicitly instead of relying on ctypes' int default.
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    result = libc.prctl(option, arg, arg2, 0, 0)
    if result < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    return result


def no_core_dumps() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    prctl(4, 0)  # PR_SET_DUMPABLE


def privilege_status() -> dict[str, str]:
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    return {name: status[name].strip() for name in ('Uid', 'Gid', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs') if name in status}


def drop_solver_privileges() -> None:
    """Applied in the child immediately before exec, inherited by every tool.

    Popen otherwise discards preexec exception details. Emit only the failed
    stage and non-sensitive kernel privilege state, never prompt/env content.
    """
    stage = 'disable_core_dumps'
    try:
        no_core_dumps()
        stage = 'set_no_new_privileges'
        prctl(38, 1)
        stage = 'clear_ambient_capabilities'
        prctl(47, 4)
        # Sandboxed kernels may omit /proc/sys/kernel/cap_last_cap. Query the
        # kernel directly; do not require CAP_SETPCAP for already-absent caps.
        for capability in range(64):
            stage = f'read_bounding_capability_{capability}'
            try:
                present = prctl(23, capability)  # PR_CAPBSET_READ
            except OSError as error:
                if error.errno == errno.EINVAL and capability > 0:
                    break
                raise
            if present:
                stage = f'drop_bounding_capability_{capability}'
                prctl(24, capability)
        stage = 'clear_supplemental_groups'
        os.setgroups([])
        stage = 'set_resgid'
        os.setresgid(SOLVER_GID, SOLVER_GID, SOLVER_GID)
        stage = 'set_resuid'
        os.setresuid(SOLVER_UID, SOLVER_UID, SOLVER_UID)
        stage = 'zero_capability_sets'
        zero_capabilities()
        stage = 'disable_unprivileged_core_dumps'
        no_core_dumps()
        os.umask(0o022)
        stage = 'verify_privilege_drop'
        status = privilege_status()
        if any(int(status[name], 16) for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')):
            raise RuntimeError('Solver capability set is not empty')
        if os.getuid() == 0 or status['NoNewPrivs'] != '1':
            raise RuntimeError('Solver privilege drop failed')
    except BaseException as error:
        diagnostic = {'stage': stage, 'error_type': type(error).__name__, 'errno': getattr(error, 'errno', None)}
        try:
            diagnostic['privileges'] = privilege_status()
        except BaseException:
            pass
        os.write(2, ('FCP_PRIVILEGE_DROP_FAILED ' + json.dumps(diagnostic, sort_keys=True) + '\n').encode())
        raise


def zero_capabilities() -> None:
    # setresuid drops effective/permitted capabilities. Explicitly zero the
    # inheritable set too (it can otherwise survive a UID change).
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                    ('inheritable', ctypes.c_uint32)]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.capset(ctypes.byref(Header(0x20080522, 0)), ctypes.byref((Data * 2)())) != 0:
        raise OSError(ctypes.get_errno(), 'capset failed')


def prepare_setid_exec() -> None:
    """No model code runs until the native helper has completed demotion.

    The platform egress policy removes SETUID/SETGID capabilities. Linux file
    exec can still transition downward to a non-root effective UID/GID. NNP
    must be set by that helper AFTER the file transition, never before it.
    """
    stage = 'verify_initial_no_new_privileges'
    try:
        if prctl(39) != 0:  # PR_GET_NO_NEW_PRIVS
            raise RuntimeError('Platform no_new_privs prevents downward file-ID demotion')
        stage = 'disable_core_dumps'
        no_core_dumps()
        stage = 'verify_supplemental_groups'
        if any(group != 0 for group in os.getgroups()):
            raise RuntimeError('Unexpected supplemental groups cannot be retained')
        for capability in range(64):
            stage = f'read_bounding_capability_{capability}'
            try:
                present = prctl(23, capability)
            except OSError as error:
                if error.errno == errno.EINVAL and capability > 0:
                    break
                raise
            if present:
                stage = f'drop_bounding_capability_{capability}'
                prctl(24, capability)
        stage = 'verify_empty_bounding_set'
        if int(privilege_status()['CapBnd'], 16):
            raise RuntimeError('Bounding set is not empty before set-ID exec')
    except BaseException as error:
        diagnostic = {'stage': stage, 'error_type': type(error).__name__, 'errno': getattr(error, 'errno', None)}
        try:
            diagnostic['privileges'] = privilege_status()
        except BaseException:
            pass
        os.write(2, ('FCP_SETID_PREPARE_FAILED ' + json.dumps(diagnostic, sort_keys=True) + '\n').encode())
        raise


def protect_trusted_runtime() -> None:
    """Inherited supplemental GID 0 must not grant writes to trusted code.

    Keep secret/control sockets behind root-only directories; remove group and
    other write permission from system/runtime paths before any model starts.
    No trusted component consumes executables from solver-writable directories.
    """
    safe_directory(Path('/run'), 0, 0, 0o700)
    roots = {Path(name).resolve() for name in ('/usr', '/bin', '/sbin', '/lib', '/lib64',
             '/etc', '/opt', '/app', '/var/lib', '/var/cache') if Path(name).exists()}
    for root in sorted(roots, key=str):
        for directory, directories, files in os.walk(root, followlinks=False):
            for path in [Path(directory), *(Path(directory) / name for name in files)]:
                if path.is_symlink():
                    continue
                info = path.stat()
                # Public input files and the solver's own output remain writable.
                if info.st_uid == SOLVER_UID:
                    continue
                if info.st_mode & 0o022:
                    path.chmod(info.st_mode & 0o7777 & ~0o022)


def build_demotion_helper() -> Path:
    helper = PRIVATE / 'demote-exec'
    if helper.exists() or helper.is_symlink():
        helper.unlink()
    if DEMOTION_TEMPLATE.is_symlink() or DEMOTION_TEMPLATE.stat().st_uid != 0:
        raise RuntimeError('Demotion template must be a root-owned regular file')
    data = DEMOTION_TEMPLATE.read_bytes()
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 62:
        raise RuntimeError('Expected the audited static Linux AMD64 demotion ELF')
    helper.write_bytes(data)
    os.chown(helper, SOLVER_UID, SOLVER_GID)
    helper.chmod(0o6755)
    return helper


def resolve_executable(command: list[str], env: dict[str, str]) -> list[str]:
    """The freestanding helper uses execve, which does not search PATH."""
    executable = shutil.which(command[0], path=env['PATH'])
    if not executable:
        raise RuntimeError('Solver executable is not installed on the public system PATH')
    return [str(Path(executable).absolute()), *command[1:]]


def safe_directory(path: Path, uid: int, gid: int, mode: int) -> None:
    if path.is_symlink():
        raise RuntimeError(f'Refusing symlink directory: {path}')
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid)
    path.chmod(mode)


def register_solver() -> None:
    try:
        entry = pwd.getpwuid(SOLVER_UID)
    except KeyError:
        with open('/etc/passwd', 'a') as stream:
            stream.write(f'fcp-solver:x:{SOLVER_UID}:{SOLVER_GID}:FCP solver:{HOME}:/bin/bash\n')
        with open('/etc/group', 'a') as stream:
            stream.write(f'fcp-solver:x:{SOLVER_GID}:\n')
    else:
        if entry.pw_name != 'fcp-solver':
            raise RuntimeError('Dedicated solver UID already belongs to another user')


def identify_task(prompt: str, args: list[str]) -> str:
    matches = re.findall(r'([A-Za-z0-9_.-]+)-inputs\.(?:xlsx|xlsm)', prompt + '\n' + '\n'.join(args))
    if not matches:
        matches = [p.name.rsplit('-inputs.', 1)[0] for p in WORKSPACE.glob('*-inputs.xls*')]
    if not matches or any(task != matches[0] for task in matches):
        raise RuntimeError('Expected exactly one task workbook identity')
    task = matches[0]
    if task in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9_.-]+', task):
        raise RuntimeError('Invalid task identity')
    return task


def copy_public(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f'Public input is not a regular file: {source.name}')
    destination.unlink(missing_ok=True)
    shutil.copyfile(source, destination)
    os.chown(destination, SOLVER_UID, SOLVER_GID)
    destination.chmod(0o644)


def setup_files(task: str) -> Path | None:
    # The parent must be protected too: otherwise a writable parent allows the
    # solver to rename/replace protected children. Corpus files retain root UID.
    safe_directory(Path('/opt/fcp'), 0, 0, 0o755)
    safe_directory(TASKS, 0, 0, 0o700)
    safe_directory(PRIVATE, 0, 0, 0o700)
    safe_directory(Path('/root'), 0, 0, 0o700)
    safe_directory(HOME, SOLVER_UID, SOLVER_GID, 0o700)
    safe_directory(WORKSPACE, SOLVER_UID, SOLVER_GID, 0o755)
    safe_directory(APP, 0, 0, 0o755)
    for path in (Path('/run/secrets'), APP / 'services'):
        if path.exists():
            safe_directory(path, 0, 0, 0o700)
    # Platform service-account files are not model API credentials. Protect
    # known file-backed credentials even if the runner wrote them into /tmp.
    for key in ('GOOGLE_APPLICATION_CREDENTIALS', 'AWS_SHARED_CREDENTIALS_FILE', 'KUBECONFIG'):
        value = os.environ.get(key)
        if value:
            credential = Path(value)
            if credential.is_symlink() or not credential.is_file():
                raise RuntimeError('Unsupported platform credential file layout')
            os.chown(credential, 0, 0)
            credential.chmod(0o600)
    # Incoming attachments are intentionally public. Do not follow symlinks.
    for root, directories, files in os.walk(WORKSPACE, followlinks=False):
        for name in directories + files:
            p = Path(root) / name
            os.chown(p, SOLVER_UID, SOLVER_GID, follow_symlinks=False)
    for directory in (WORKSPACE, APP):
        for pattern in ('*-inputs.xlsx', '*-inputs.xlsm', 'additional-assumptions.md', 'answers.json'):
            for path in directory.glob(pattern):
                path.unlink()
    task_root = TASKS / task
    if task_root.is_symlink() or not task_root.is_dir():
        raise RuntimeError('Missing or invalid task directory')
    workbook = [task_root / f'{task}-inputs.{ext}' for ext in ('xlsx', 'xlsm')]
    workbook = [p for p in workbook if p.is_file()]
    if len(workbook) != 1:
        raise RuntimeError('Task must contain exactly one input workbook')
    for source in [workbook[0], task_root / 'additional-assumptions.md']:
        if source.exists():
            for directory in (WORKSPACE, APP):
                copy_public(source, directory / source.name)
    (APP / 'answers.json').symlink_to(WORKSPACE / 'answers.json')
    server = task_root / 'mcp-server/server.py'
    if server.is_symlink() or (server.exists() and not server.is_file()):
        raise RuntimeError('Task research server is missing')
    # No privileged cleanup or server startup will consume solver-writable code.
    for root, directories, files in os.walk(TASKS, followlinks=False):
        for name in directories + files:
            p = Path(root) / name
            if p.is_symlink():
                raise RuntimeError('Symlinks are forbidden in task assets')
            os.chown(p, 0, 0)
            p.chmod(0o700 if p.is_dir() else 0o600)
    return server if server.is_file() else None


def solver_environment() -> dict[str, str]:
    # Explicitly omit callback tokens, cloud service-account JSON, Kubernetes
    # tokens, image-registry auth, task/grader configs and unrelated API keys.
    env = {key: value for key, value in os.environ.items() if key in SOLVER_ENV_KEYS}
    env.update(HOME=str(HOME), USER='fcp-solver', LOGNAME='fcp-solver',
               XDG_CONFIG_HOME=str(HOME / '.config'), XDG_CACHE_HOME=str(HOME / '.cache'),
               XDG_DATA_HOME=str(HOME / '.local/share'),
               PATH='/app/.venv/bin:/usr/local/opencode/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin',
               TMPDIR='/tmp', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    bypass = [part.strip() for value in (env.get('NO_PROXY', ''), env.get('no_proxy', ''))
              for part in value.split(',') if part.strip()]
    bypass = list(dict.fromkeys([*bypass, '127.0.0.1', 'localhost', '::1']))
    env['NO_PROXY'] = env['no_proxy'] = ','.join(bypass)
    # Neither root source imports nor a user-controlled startup hook is needed
    # by the CLI. Scientific packages resolve through the virtualenv normally.
    for key in ('PYTHONPATH', 'PYTHONSTARTUP', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'BASH_ENV', 'ENV'):
        env.pop(key, None)
    artifact = Path(env.get('AGENT_ARTIFACT_DIR', '/logs/agent'))
    if not artifact.is_absolute() or not artifact.is_relative_to('/logs'):
        raise RuntimeError('Agent artifact directory must be below /logs')
    safe_directory(artifact, SOLVER_UID, SOLVER_GID, 0o755)
    return env


def model_command(mode: str, prompt: str, args: list[str], env: dict[str, str], research: bool = True) -> list[str]:
    if mode == 'claude':
        mcp_args = []
        if research:
            config = HOME / 'mcp.json'
            config.write_text(json.dumps({'mcpServers': {'research': {'type': 'http', 'url': MCP_URL}}}))
            os.chown(config, SOLVER_UID, SOLVER_GID)
            mcp_args = ['--mcp-config', str(config)]
        # Runconfig owns model selection. Require max effort for this campaign;
        # duplicate/mismatched effort options are rejected rather than overridden.
        effort_values = [args[i + 1] if i + 1 < len(args) else None
                         for i, arg in enumerate(args) if arg == '--effort']
        effort_values += [arg.split('=', 1)[1] for arg in args if arg.startswith('--effort=')]
        if effort_values and effort_values != ['max']:
            raise RuntimeError('This campaign requires Claude --effort max')
        if not effort_values:
            args = args + ['--effort', 'max']
        return ['claude', '-p', '--verbose', '--output-format', 'stream-json',
                '--dangerously-skip-permissions', *args, *mcp_args]
    if mode == 'opencode':
        model = env.get('FCP_OPENCODE_MODEL')
        if not model:
            raise RuntimeError('FCP_OPENCODE_MODEL is required')
        # Stock OpenCode 1.2.27 otherwise chooses a different small model for
        # title/auxiliary work. Pin every built-in role to this attempt's model.
        config = {'permission': {'*': 'allow', 'external_directory': 'allow'},
            'model': model, 'small_model': model,
            'enabled_providers': [model.split('/', 1)[0]],
            'agent': {name: {'model': model} for name in
                      ('build', 'plan', 'general', 'explore', 'title', 'summary', 'compaction')}}
        if research:
            config['mcp'] = {'research': {'type': 'remote', 'url': MCP_URL, 'enabled': True, 'oauth': False, 'timeout': 600000}}
        env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
        return [sys.executable, '/usr/local/bin/fcp-inactivity-watchdog',
                env.get('FCP_INACTIVITY_TIMEOUT_SECONDS', '900'), 'opencode', 'run',
                '--format', 'json', '--model', model, prompt, *args]
    if mode == 'self-test':
        return [sys.executable, '-I', '/usr/local/bin/fcp-isolation-probe']
    raise RuntimeError('Unsupported solver mode')


def protect_model_cli(mode: str, env: dict[str, str]) -> None:
    """Normalize only the installed model entry point, before untrusted work.

    The stock OpenCode image installs its ELF as build UID 1001. It remains
    immutable to the solver, but trusted identity evidence requires root-owned
    code. Claude's public entry point resolves to its packaged claude.exe ELF.
    """
    if mode == 'self-test':
        return
    executable = shutil.which('claude' if mode == 'claude' else 'opencode', path=env['PATH'])
    if not executable:
        raise RuntimeError('Solver CLI is not installed on the public system PATH')
    target = Path(executable).resolve(strict=True)
    if not target.is_relative_to('/usr') or not target.is_file():
        raise RuntimeError('Solver CLI must resolve to a system-installed executable')
    info = target.stat()
    if info.st_uid == SOLVER_UID:
        raise RuntimeError('Refusing solver-owned model executable')
    os.chown(target, 0, 0)
    target.chmod(info.st_mode & 0o777 & ~0o022)


def main() -> int:
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Isolated bootstrap requires Linux root; no insecure fallback')
    mode, *args = sys.argv[1:]
    prompt = sys.stdin.read()
    no_core_dumps()
    register_solver()
    task = identify_task(prompt, args)
    server_path = setup_files(task)
    protect_trusted_runtime()
    demotion_helper = build_demotion_helper()
    from fcp_solver_python import prepare_solver_python
    python_settings = prepare_solver_python(HOME, SOLVER_UID, SOLVER_GID, sys.executable)
    env = solver_environment()
    env['PATH'] = python_settings.pop('PYTHON_BIN_DIRECTORY') + ':' + env['PATH']
    env.update(python_settings)
    env['FCP_EXPECTED_HARNESS'] = mode
    protect_model_cli(mode, env)
    command = model_command(mode, prompt, args, env, research=server_path is not None)
    command = resolve_executable(command, env)
    # Check CLI installation while root; the child still performs the actual
    # execution under its restricted UID. /root is deliberately never exposed.
    if mode != 'self-test' and not shutil.which('claude' if mode == 'claude' else 'opencode', path=env['PATH']):
        raise RuntimeError('Solver CLI is not installed on the public system PATH')
    server_env = {key: value for key, value in os.environ.items() if key in
                  ('PATH', 'VIRTUAL_ENV', 'SSL_CERT_FILE', 'LANG', 'LC_ALL')}
    server_env.update(HOME='/root', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    # Do not pass the model prompt or API credentials to the research service.
    with open(PRIVATE / 'research.log', 'wb') as log:
        server = None
        if server_path is not None:
            server = subprocess.Popen([sys.executable, str(server_path)], cwd=PRIVATE,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=server_env,
                close_fds=True, start_new_session=True, preexec_fn=no_core_dumps)
        solver = None
        def terminate(_signum=None, _frame=None):
            for process in (solver, server):
                if process is not None and process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        try:
            deadline = time.monotonic() + 30
            while server is not None:
                if server.poll() is not None:
                    raise RuntimeError('Research server exited; inspect root-private research.log')
                try:
                    with socket.create_connection(('127.0.0.1', 8000), timeout=.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Research server readiness timed out')
                    time.sleep(.2)
            env['FCP_RESEARCH_PID'] = str(server.pid) if server is not None else '1'
            env['FCP_RESEARCH_ENABLED'] = '1' if server is not None else '0'
            env['FCP_TASK_ID'] = task
            solver = subprocess.Popen([str(demotion_helper), *command], cwd=WORKSPACE, env=env,
                stdin=subprocess.PIPE, close_fds=True, start_new_session=True,
                preexec_fn=prepare_setid_exec)
            solver.communicate(prompt.encode() if mode == 'claude' else b'')
            return solver.returncode
        finally:
            terminate()
            if server is not None:
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # No environment or prompt dumps: auth is retained only in child env.
        print(f'FCP isolation bootstrap failed: {type(error).__name__}: {error}', file=sys.stderr)
        raise SystemExit(2)
