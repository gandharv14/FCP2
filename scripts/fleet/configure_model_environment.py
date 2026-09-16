"""Normalize the deployed LBX credential name without emitting its value."""
import argparse
import os
from pathlib import Path
import tempfile


def normalized(raw):
    values = {}
    recognized = {'lbx_api_key', 'lbx_labelbox_api_key', 'alignerr_org_key',
                  'aligerr_org_key', 'rl_api_key'}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip().lower()
        if key in recognized:
            value = value.strip().strip('"').strip("'")
            if value:
                values.setdefault(key, set()).add(value)
    distinct = set().union(*values.values()) if values else set()
    if len(distinct) != 1:
        raise ValueError('credential missing or conflicting; preserve configuration')
    if any(key != 'lbx_api_key' for key in values):
        return raw
    value = next(iter(distinct))
    if any(c in value for c in '\r\n\x00'):
        raise ValueError('unsupported credential serialization')
    return raw + ('' if raw.endswith('\n') else '\n') + 'RL_API_KEY=' + value + '\n'


def configure(path, *, apply=False):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('regular absolute credential path required')
    if path.stat().st_mode & 0o077:
        raise ValueError('credential file must be private')
    raw = path.read_text(); selected = normalized(raw)
    changed = selected != raw
    if changed and apply:
        fd, name = tempfile.mkstemp(prefix='.credential-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(selected); stream.flush(); os.fsync(stream.fileno())
            if path.read_text() != raw:
                raise ValueError('credential changed concurrently')
            os.replace(name, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)
    return dict(status='CONFIGURED' if apply else 'CHECKED', alias_required=changed,
                changed=changed and apply, credential_disclosed=False)


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument('path'); parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(configure(args.path, apply=args.apply)))
