"""Create isolated inspection copies of legacy native sources; originals stay unchanged."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from recursion_reward_extremes_audit import AUDIT


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root = AUDIT / 'deep-review'
    records = []
    for row in json.loads((root / 'legacy-source-list.json').read_text()):
        source = Path(row['path'])
        source_hash = sha(source)
        dest = root / 'native-converted' / row['taskId']
        dest.mkdir(parents=True, exist_ok=True)
        output = dest / (source.stem + '.xlsx')
        receipt = dict(taskId=row['taskId'], originalPath=str(source), originalSha256=source_hash,
                       convertedPath=str(output),
                       limitation='LibreOffice may recalculate values and translate formulas. Bind original caches separately; converted results are not native-cache proof.')
        if not output.exists():
            command = ['/Applications/LibreOffice.app/Contents/MacOS/soffice',
                       '-env:UserInstallation=file:///tmp/recursion-deep-audit-lo-profile',
                       '--headless', '--convert-to', 'xlsx', '--outdir', str(dest), str(source)]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=50)
                receipt['exitCode'] = result.returncode
            except subprocess.TimeoutExpired:
                receipt['exitCode'] = 'timeout'
        assert sha(source) == source_hash
        receipt['status'] = 'converted' if output.is_file() else 'conversion_failed'
        if output.is_file():
            os.chmod(output, 0o600)
            receipt['convertedSha256'] = sha(output)
        records.append(receipt)
        (root / 'legacy-conversion-manifest.json').write_text(json.dumps(records, indent=2) + '\n')
        print(json.dumps({'taskId': row['taskId'], 'status': receipt['status']}), flush=True)


if __name__ == '__main__':
    main()
