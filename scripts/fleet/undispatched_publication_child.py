"""Explicit publication child dependency for retained normalization readers.

The owning publisher's original run bytecode, locks, journal, intent, CAS,
resume, resource guards and promotion decision are preserved. Only its guarded
child command is selected through this declared, hash-bound dependency.
"""
from pathlib import Path
from types import FunctionType, SimpleNamespace
import hashlib
import json
import os
import sys

SCHEMA = 'fleet-declared-publication-child-reader/v1'


def require(value, message):
    if not value: raise ValueError(message)


def binding(path):
    p = Path(path).absolute()
    require(not any(x.is_symlink() for x in (p, *p.parents)), 'symlink in publication reader evidence')
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def checked(row):
    require(set(row) == {'path', 'sha256'} and binding(row['path']) == row, 'publication reader evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def retain(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True)+'\n').encode()
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return binding(path)


def contract(producer, intent, command):
    value = checked(intent)
    require(set(value) == {'schema_version', 'request', 'queued_tracker', 'initial_output_sha256', 'code'}
            and value['schema_version'] == producer.SCHEMA and value['code'] == producer._codes(),
            'original owning publication intent/code differs')
    require(command == [sys.executable, '-B', str(Path(producer.__file__).resolve()),
                        '_child', intent['path'], intent['sha256']], 'original publication argv differs')
    require(producer.guard.SECONDS == 3300 and producer.guard.MEMORY == 8*1024**3,
            'original publication guard changed')


def clone(function, **dependencies):
    namespace = {**function.__globals__, **dependencies}
    selected = FunctionType(function.__code__, namespace, function.__name__,
                            function.__defaults__, function.__closure__)
    selected.__kwdefaults__ = function.__kwdefaults__
    return selected


def select(producer, *, reader_plan, child_wrapper, reader_evidence):
    """Select the dependency without changing the original producer module."""
    checked(reader_plan)
    producer_code = binding(producer.__file__)
    guard_code = binding(producer.guard.__file__)
    rows = [reader_plan, child_wrapper, producer_code, guard_code, *reader_evidence]
    for row in rows: require(binding(row['path']) == row, 'selected publication dependency changed')
    original = producer.guard._run_in_process_group

    def launch(command, **options):
        require(isinstance(command, list) and len(command) == 6, 'unexpected publication child')
        intent = dict(path=command[4], sha256=command[5]); contract(producer, intent, command)
        require(options.get('timeout_seconds') == 3300 and options.get('memory_limit_bytes') == 8*1024**3
                and options.get('grace_seconds') == 3 and Path(options.get('cwd')) == Path(producer.ROOT),
                'publication resource guard changed')
        for row in rows: require(binding(row['path']) == row, 'publication dependency changed before spawn')
        logfile = Path(options['stdout'].name).absolute()
        require(logfile.name == 'publication.log' and logfile.parent.name.startswith('invocation-')
                and logfile.parent.parent == Path(intent['path']).parent,
                'publication invocation is outside owning journal')
        sidecar = logfile.parent/'declared-publication-reader.json'
        selected = [sys.executable, '-I', '-B', child_wrapper['path'], 'publication-child',
                    '--plan', reader_plan['path'], '--plan-sha256', reader_plan['sha256'],
                    '--intent', intent['path'], '--intent-sha256', intent['sha256'], '--adapter', str(sidecar)]
        retain(sidecar, dict(schema_version=SCHEMA, adapter=binding(__file__), producer=producer_code,
            guard=guard_code, reader_plan=reader_plan, child_wrapper=child_wrapper,
            reader_evidence=list(reader_evidence), intent=intent, original_command=command,
            selected_command=selected, timeout_seconds=3300, memory_limit_bytes=8*1024**3,
            original_snapshot_unchanged=True, automatic_retry=False, models_allowed=False))
        return original(selected, **options)

    guard = SimpleNamespace(**producer.guard.__dict__); guard._run_in_process_group = launch
    inner = clone(producer._run_publication, guard=guard)
    result = clone(producer.run, _run_publication=inner)
    result.declared_runner = launch
    return result


def validate(producer, *, reader_plan, child_wrapper, reader_evidence, intent, sidecar):
    p = Path(sidecar).absolute(); value = checked(binding(p))
    require(set(value) == {'schema_version', 'adapter', 'producer', 'guard', 'reader_plan', 'child_wrapper',
            'reader_evidence', 'intent', 'original_command', 'selected_command', 'timeout_seconds',
            'memory_limit_bytes', 'original_snapshot_unchanged', 'automatic_retry', 'models_allowed'},
            'closed publication child dependency required')
    require(value['schema_version'] == SCHEMA and value['adapter'] == binding(__file__)
            and value['producer'] == binding(producer.__file__) and value['guard'] == binding(producer.guard.__file__)
            and value['reader_plan'] == reader_plan and value['child_wrapper'] == child_wrapper
            and value['reader_evidence'] == list(reader_evidence) and value['intent'] == intent,
            'publication child lineage differs')
    for row in [reader_plan, child_wrapper, *reader_evidence]:
        require(binding(row['path']) == row, 'publication child dependency changed')
    require(p.name == 'declared-publication-reader.json' and p.parent.name.startswith('invocation-')
            and p.parent.parent == Path(intent['path']).parent, 'publication child sidecar outside owning journal')
    contract(producer, intent, value['original_command'])
    expected = [sys.executable, '-I', '-B', child_wrapper['path'], 'publication-child',
                '--plan', reader_plan['path'], '--plan-sha256', reader_plan['sha256'],
                '--intent', intent['path'], '--intent-sha256', intent['sha256'], '--adapter', str(p)]
    require(value['selected_command'] == expected and sys.argv == expected[3:], 'actual publication child argv differs')
    require(value['timeout_seconds'] == 3300 and value['memory_limit_bytes'] == 8*1024**3
            and value['original_snapshot_unchanged'] is True and value['automatic_retry'] is False
            and value['models_allowed'] is False, 'publication child guard/capability changed')
    return binding(p)
