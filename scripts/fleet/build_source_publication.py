"""Freeze an explicit source-history publisher without changing task code."""
from pathlib import Path
import hashlib
import json
import sys
from scripts.fleet import source_publication as S

BASE_SHA = 'd8b9cc78312918e38040442f781dad755db7605292c6d04f0529f580e15c2149'


def build(output, original):
    out = Path(output).absolute()
    source = Path(original).read_bytes()
    if hashlib.sha256(source).hexdigest() != BASE_SHA:
        raise ValueError('exact reviewed runtime-corrected publisher required')
    out.mkdir(parents=True, exist_ok=False)
    def write(name, raw):
        path = out/name
        with path.open('xb') as f:
            f.write(raw if isinstance(raw, bytes) else raw.encode())
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    policy_path = Path(S.__file__).with_name('capacity_policy.py')
    if hashlib.sha256(policy_path.read_bytes()).hexdigest() != S.CAPACITY_POLICY_SHA:
        raise ValueError('unreviewed concurrency policy')
    capacity = write('capacity_policy.py', policy_path.read_bytes())
    adapter_source = Path(S.__file__).read_text()
    needle_policy = 'CAPACITY_POLICY = None  # The builder replaces this with the exact frozen copy.'
    if adapter_source.count(needle_policy) != 1:
        raise ValueError('capacity policy insertion differs')
    adapter = write('source_publication.py', adapter_source.replace(needle_policy, 'CAPACITY_POLICY = '+repr(capacity)))
    prefix = '''\n_source_publication_binding = %r
require(binding(_source_publication_binding['path']) == _source_publication_binding, 'source publication adapter changed')
_source_publication_spec = importlib.util.spec_from_file_location('fleet_selected_source_publication', _source_publication_binding['path'])
source_publication = importlib.util.module_from_spec(_source_publication_spec)
_source_publication_spec.loader.exec_module(source_publication)
\n''' % adapter
    code = S.patch(source.decode())
    needle = 'def history_binding(plan):\n'
    if code.count(needle) != 1:
        raise ValueError('source publisher insertion differs')
    publisher = write('publish.py', code.replace(needle, prefix+needle))
    builder = write('build.py', Path(__file__).read_bytes())
    return write('manifest.json', json.dumps(dict(
        schema_version='fleet-source-history-publication-successor/v1',
        original={'path': str(Path(original).absolute()), 'sha256': BASE_SHA},
        publisher=publisher, adapter=adapter, builder=builder, capacity_policy=capacity,
        task_code_changed=False, model_calls_allowed=False, owning_publication_unchanged=True,
        independent_verification_unchanged=True, collection_unchanged=True), indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    print(json.dumps(build(sys.argv[1], sys.argv[2])))
