"""Apply context allowlists and verify every task through the isolated runner.

Does not publish images, alter task content, or submit evaluations. The Recursion
security boundary is installed by the shared runner, not by .dockerignore.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile

import recursion_scaffold

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT.parent / 'run_recursion_eval.py'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def snapshot(task):
    result = {}
    for path in sorted(task.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink in task: {path}')
        if path.is_file():
            result[path.relative_to(task).as_posix()] = {'sha256': sha(path), 'mode': path.stat().st_mode & 0o7777}
    return result


def exclusions(task):
    env = task / 'environment'
    workbooks = list(env.glob('*-inputs.xlsx')) + list(env.glob('*-inputs.xlsm'))
    if len(workbooks) != 1:
        raise ValueError(f'Expected one input workbook: {task}')
    public = [workbooks[0].name]
    if (env / 'additional-assumptions.md').is_file():
        public.append('additional-assumptions.md')
    for name in public:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            raise ValueError(f'Unsupported Dockerignore filename: {name}')
    dockerfile = (env / 'Dockerfile').read_text()
    copies = re.findall(r'^COPY\s+(\S+)\s+/app/\S+\s*$', dockerfile, re.M)
    if sorted(copies) != sorted(public) or len(re.findall(r'^\s*(?:COPY|ADD)\b', dockerfile, re.M)) != len(public):
        raise ValueError(f'Unexpected main-container COPY/ADD: {task}')
    if any(p.name != '.dockerignore' for p in env.glob('*.dockerignore')):
        raise ValueError(f'Dockerfile-specific ignore overrides need review: {env}')
    main = '# Solver build context: only public task inputs. Research belongs to the separate service.\n**\n!Dockerfile\n!.dockerignore\n'
    result = {'environment/.dockerignore': main + ''.join('!' + name + '\n' for name in public)}
    sidecar = env / 'mcp-server'
    if sidecar.is_dir():
        if any(p.name != '.dockerignore' for p in sidecar.glob('*.dockerignore')):
            raise ValueError(f'Dockerfile-specific ignore overrides need review: {sidecar}')
        code = (sidecar / 'Dockerfile').read_text()
        if re.findall(r'^COPY\s+(\S+)\s+\S+\s*$', code, re.M) != ['server.py', 'runtime'] or re.search(r'^\s*ADD\b', code, re.M):
            raise ValueError(f'Unexpected research-service COPY/ADD: {task}')
        compose = (env / 'docker-compose.yaml').read_text()
        if 'context: ./mcp-server' not in compose or re.search(r'^\s*(volumes|privileged|pid|network_mode):', compose, re.M):
            raise ValueError(f'Research service isolation needs review: {task}')
        result['environment/mcp-server/.dockerignore'] = '# Research-service build context: exclude authoring and evaluator artifacts.\n**\n!Dockerfile\n!.dockerignore\n!server.py\n!runtime/\n!runtime/**\n'
    return result


def prepare(roots, report):
    report.mkdir(parents=True, exist_ok=True)
    plan_path = report / 'plan.json'
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        if plan['roots'] != [str(p.resolve()) for p in roots]:
            raise ValueError('Existing plan has different roots')
        return plan
    tasks = []
    for root in roots:
        root = root.resolve()
        for marker in sorted(root.rglob('task.toml')):
            task = marker.parent
            updates = exclusions(task)
            before = snapshot(task)
            for relative, content in updates.items():
                if relative in before and (task / relative).read_text() != content:
                    raise ValueError(f'Existing custom exclusions need review: {task / relative}')
            tasks.append({'root': str(root), 'path': str(task), 'taskId': task.name.removesuffix('-outputs'),
                          'research': (task / 'environment/mcp-server').is_dir(), 'before': before, 'updates': updates})
    if not tasks:
        raise ValueError('No tasks found')
    plan = {'createdAt': datetime.now(timezone.utc).isoformat(), 'roots': [str(p.resolve()) for p in roots],
            'runnerSha256': sha(HELPER), 'runtimeFiles': {dest: hashlib.sha256(data).hexdigest()
                for dest, data, _ in recursion_scaffold.runtime_files()}, 'tasks': tasks}
    save(plan_path, plan)
    return plan


def check_source(row, applied):
    expected = dict(row['before'])
    for relative, content in row['updates'].items():
        if applied:
            expected[relative] = {'sha256': hashlib.sha256(content.encode()).hexdigest(),
                                  'mode': row['before'].get(relative, {}).get('mode', 0o644)}
    if snapshot(Path(row['path'])) != expected:
        raise ValueError(f'Task changed outside the planned scaffolding edits: {row["path"]}')


def apply(plan):
    # Preflight every task before the first write. Reruns accept only exact,
    # already-planned files; a partial or unrelated change fails closed.
    for row in plan['tasks']:
        current = snapshot(Path(row['path']))
        before = row['before']
        extra = set(current) - set(before)
        if not extra <= set(row['updates']):
            raise ValueError(f'Unexpected new task files: {row["path"]}')
        if any(current.get(k) != v for k, v in before.items()):
            raise ValueError(f'Task changed since planning: {row["path"]}')
        for relative in extra:
            if (Path(row['path']) / relative).read_text() != row['updates'][relative] or current[relative]['mode'] != 0o644:
                raise ValueError('Unexpected existing isolation file')
    for row in plan['tasks']:
        for relative, content in row['updates'].items():
            path = Path(row['path']) / relative
            if not path.exists():
                with path.open('x') as stream:
                    stream.write(content)
                path.chmod(0o644)
        check_source(row, applied=True)


def verify(plan, report):
    if sha(HELPER) != plan['runnerSha256']:
        raise ValueError('Runner changed since planning')
    if plan['runtimeFiles'] != {dest: hashlib.sha256(data).hexdigest() for dest, data, _ in recursion_scaffold.runtime_files()}:
        raise ValueError('Runtime changed since planning')
    spec = importlib.util.spec_from_file_location('task_isolation_runner', HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    verified = []
    for row in plan['tasks']:
        task = Path(row['path'])
        check_source(row, applied=True)
        with tempfile.TemporaryDirectory(prefix='task-isolation-') as temporary:
            build = Path(temporary) / 'build'
            helper.prepare_build_context(build, {row['taskId']: task})
            manifest = recursion_scaffold.validate(build)
            packaged = build / 'tasks' / row['taskId']
            source_files = {p.relative_to(packaged).as_posix(): sha(p) for p in packaged.rglob('*') if p.is_file()}
            for relative, checksum in source_files.items():
                if checksum != sha(task / 'environment' / relative):
                    raise ValueError(f'Packaged bytes differ: {task}/{relative}')
            expected = {relative.removeprefix('environment/'): value['sha256']
                        for relative, value in snapshot(task).items()
                        if relative.startswith('environment/mcp-server/') or
                        (relative.startswith('environment/') and '/' not in relative[len('environment/'):] and
                         (relative.endswith(('-inputs.xlsx', '-inputs.xlsm')) or relative.endswith('/additional-assumptions.md')))}
            if source_files != expected:
                raise ValueError(f'Unexpected/missing packaged assets: {task}')
            verified.append({'path': str(task), 'root': row['root'], 'taskId': row['taskId'],
                             'research': row['research'], 'solverUid': manifest['solverUid'],
                             'runtimeFiles': len(manifest['files']), 'packagedFiles': len(source_files),
                             'contentPreserved': True, 'privateTaskDirectoryMode': '0700', 'privateTaskFileMode': '0600'})
        if len(verified) % 25 == 0:
            print(json.dumps({'verifiedTasks': len(verified), 'total': len(plan['tasks'])}), flush=True)
    # Detect concurrent source edits that occurred after individual verification.
    for row in plan['tasks']:
        check_source(row, applied=True)
    result = {'verifiedAt': datetime.now(timezone.utc).isoformat(), 'status': 'verified',
              'tasks': len(verified), 'researchTasks': sum(r['research'] for r in verified),
              'scaffoldFiles': sum(len(r['updates']) for r in plan['tasks']),
              'byRoot': {root: sum(r['root'] == root for r in verified) for root in plan['roots']},
              'planSha256': sha(report / 'plan.json'), 'verification': verified,
              'limits': ['Local source scaffolding and Recursion packaging verified; no images published or evaluations submitted.',
                         'Docker daemon was unavailable for a new local container smoke test. The shared runtime was previously live-tested in the 20-task pilot.']}
    save(report / 'verification.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'apply', 'verify'])
    parser.add_argument('--root', type=Path, action='append', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    plan = prepare(args.root, args.report)
    if args.action == 'apply':
        apply(plan)
    if args.action != 'plan':
        result = verify(plan, args.report)
        print(json.dumps({k: v for k, v in result.items() if k != 'verification'}), flush=True)
    else:
        print(json.dumps({'tasks': len(plan['tasks']), 'researchTasks': sum(r['research'] for r in plan['tasks']),
                          'plannedFiles': sum(len(r['updates']) for r in plan['tasks'])}), flush=True)


if __name__ == '__main__':
    main()
