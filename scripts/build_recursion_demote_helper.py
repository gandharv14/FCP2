#!/usr/bin/env python3
"""Cross-build and attest the tiny Linux AMD64 demotion ELF; no downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--clang', default=shutil.which('clang'))
    parser.add_argument('--lld', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).with_name('recursion_demote_exec.c').resolve()
    destination = args.output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fcp-demote-build-') as temporary:
        object_file = str(Path(temporary) / 'demote.o')
        compile_command = [args.clang, '--target=x86_64-unknown-linux-gnu', '-ffreestanding',
            '-fno-builtin', '-fno-stack-protector', '-fno-pic', '-nostdinc', '-O2',
            '-Wall', '-Wextra', '-Werror', '-c', str(source), '-o', object_file]
        link_command = [args.lld, '-flavor', 'gnu', '-static', '--entry=_start',
            '--build-id=none', '-z', 'noexecstack', '--strip-all', '-o', str(destination), object_file]
        subprocess.run(compile_command, check=True, capture_output=True)
        subprocess.run(link_command, check=True, capture_output=True)
    destination.chmod(0o555)
    data = destination.read_bytes()
    assert data[:6] == b'\x7fELF\x02\x01'
    assert struct.unpack_from('<H', data, 18)[0] == 62
    offset, size, count = struct.unpack_from('<Q', data, 32)[0], struct.unpack_from('<H', data, 54)[0], struct.unpack_from('<H', data, 56)[0]
    headers = [struct.unpack_from('<IIQQQQQQ', data, offset + index * size) for index in range(count)]
    assert not any(header[0] == 3 for header in headers), 'ELF must not have an interpreter'
    assert any(header[0] == 0x6474e551 and not header[1] & 1 for header in headers), 'Stack must be non-executable'
    receipt = {'source': str(source), 'sourceSha256': sha(source), 'binary': str(destination),
        'binarySha256': sha(destination), 'binaryBytes': len(data), 'architecture': 'linux/amd64',
        'static': True, 'nonExecutableStack': True, 'compileCommand': compile_command, 'linkCommand': link_command,
        'clangVersion': subprocess.check_output([args.clang, '--version'], text=True).splitlines()[0],
        'lldVersion': subprocess.check_output([args.lld, '-flavor', 'gnu', '--version'], text=True).strip()}
    destination.with_suffix('.build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({key: receipt[key] for key in ('binarySha256', 'binaryBytes', 'architecture', 'static', 'nonExecutableStack')}))


if __name__ == '__main__':
    main()
