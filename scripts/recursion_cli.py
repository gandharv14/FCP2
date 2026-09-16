#!/usr/bin/env python3
"""Invoke the workspace Recursion CLI without exposing credentials in argv."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
env = os.environ.copy()
values = {}
for raw in (ROOT.parent / '.env').read_text().splitlines():
    line = raw.strip().removeprefix('export ')
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    values[key.strip()] = value.strip().strip('\"\'')
env['LABELBOX_API_KEY'] = (
    values.get('lbx_labelbox_api_key')
    or env.get('lbx_labelbox_api_key')
    or env.get('LBX_LABELBOX_API_KEY')
    or env.get('LABELBOX_API_KEY', '')
)
if not env['LABELBOX_API_KEY']:
    raise SystemExit('Labelbox API key is missing')
cli = ROOT / '.local/recursion-cli/node_modules/.bin/recursion'
os.execve(str(cli), [str(cli), *sys.argv[1:]], env)
