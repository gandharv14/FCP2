"""Read only safe model-selection flags from trusted CLI ancestors."""
from __future__ import annotations

import os
from pathlib import Path


def flag_value(arguments: list[str], flag: str) -> str | None:
    values = []
    for index, argument in enumerate(arguments):
        if argument == flag and index + 1 < len(arguments):
            values.append(arguments[index + 1])
        elif argument.startswith(flag + '='):
            values.append(argument[len(flag) + 1:])
    # Duplicate options are ambiguous even if the CLI would choose a last one.
    return values[0] if len(values) == 1 else None


def identify_cli(arguments: list[str]) -> str | None:
    if not arguments:
        return None
    first = Path(arguments[0]).name
    if first in ('claude', 'opencode'):
        return first
    if first in ('node', 'nodejs', 'bun') and len(arguments) > 1:
        script = Path(arguments[1])
        if script.name == 'claude' or '@anthropic-ai' in script.parts:
            return 'claude'
        if script.name == 'opencode' or 'opencode-ai' in script.parts:
            return 'opencode'
    return None


def trusted_executable(path: str) -> bool:
    try:
        target = Path(path).resolve(strict=True)
        info = target.stat()
    except OSError:
        return False
    return target.is_relative_to('/usr') and info.st_uid == 0 and not info.st_mode & 0o022


def inspect_model_identity() -> dict | None:
    pid = os.getpid()
    seen = set()
    result = None
    # Prefer the outermost CLI ancestor, so an incidental nested CLI launched
    # by a tool cannot replace the identity of the actual evaluation process.
    for _ in range(32):
        if pid <= 0 or pid in seen:
            break
        seen.add(pid)
        process = Path('/proc') / str(pid)
        try:
            status = dict(line.split(':', 1) for line in (process / 'status').read_text().splitlines() if ':' in line)
            pid = int(status['PPid'].strip())
        except (OSError, KeyError, ValueError):
            break
        try:
            executable = os.readlink(process / 'exe')
            if Path(executable).name not in ('claude', 'claude.exe', 'opencode', 'node', 'nodejs', 'bun'):
                continue
            if not trusted_executable(executable):
                continue
            # Do not read shell/wrapper/root-service argument vectors or any
            # process environment. Emit only parsed CLI model/effort flags.
            arguments = [part.decode('utf-8', 'replace') for part in (process / 'cmdline').read_bytes().split(b'\0') if part]
            kind = identify_cli(arguments)
            if Path(executable).name in ('node', 'nodejs', 'bun') and (
                    len(arguments) < 2 or not trusted_executable(arguments[1])):
                continue
            if kind:
                result = {'harness': kind, 'model': flag_value(arguments, '--model'),
                          'effort': flag_value(arguments, '--effort'), 'cli_pid': int(process.name)}
        except OSError:
            continue
    return result
