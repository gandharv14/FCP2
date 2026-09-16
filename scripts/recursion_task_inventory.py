#!/usr/bin/env python3
"""Record task generations and validate publication hashes before evaluation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def inventory(root):
    rows = []
    for folder in sorted(root.glob('[0-9][0-9][0-9][0-9]-outputs')):
        manifest = folder / 'generation-manifest.json'
        row = {'task_id': folder.name[:4], 'path': str(folder.resolve())}
        if not manifest.is_file():
            row['status'] = 'legacy_without_generation_manifest'
            rows.append(row)
            continue
        try:
            original = manifest.read_bytes()
            data = json.loads(original)
            errors = []
            for relative, artifact in data['artifacts'].items():
                path = (folder / relative).resolve()
                if not path.is_relative_to(folder.resolve()):
                    errors.append(f'Path outside task: {relative}')
                elif not path.is_file():
                    errors.append(f'Missing: {relative}')
                elif hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                    errors.append(f'Hash mismatch: {relative}')
            for required in ['instruction.md', 'task.toml', 'tests/run_grader.py', 'tests/answer_key.json']:
                if required not in data['artifacts']:
                    errors.append(f'Unmanifested required file: {required}')
            if original != manifest.read_bytes():
                errors.append('Manifest changed during inspection')
            row.update(generation_id=data['generation_id'],
                       manifest_sha256=hashlib.sha256(original).hexdigest(),
                       artifact_count=len(data['artifacts']), errors=errors,
                       status='manifest_verified' if not errors else 'not_ready')
        except (OSError, ValueError, KeyError) as exc:
            row.update(status='not_ready', errors=[str(exc)])
        rows.append(row)
    return {'checked_at': datetime.now(timezone.utc).isoformat(),
            'tasks_root': str(root.resolve()), 'tasks': rows,
            'total_tasks': len(rows),
            'verified_generations': sum(r['status'] == 'manifest_verified' for r in rows),
            'note': 'Artifact validation only; remote coverage and runtime probes remain required.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks-root', type=Path, default=Path(__file__).resolve().parents[2] / 'tasks_outputs')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.tasks_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'tasks'}, indent=2))
