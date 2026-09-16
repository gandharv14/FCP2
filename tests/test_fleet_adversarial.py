"""Independent process races and durable crash safety of the fleet authority."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.fleet.coordinator import Coordinator


def child(root, code, *arguments):
    result = subprocess.run([sys.executable, '-B', '-c', code, *map(str, arguments)],
        cwd=root, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_separate_processes_cannot_duplicate_claim_or_global_model(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = tmp_path/'authority.sqlite'
    c = Coordinator(database)
    for wb in ('0001', '0002'):
        c.enroll(wb, 'a'*64, 'immutable test inventory')
        c.reconcile(wb, 'test confirms no historical owner or attempts')
    code = '''
import json,sys
from scripts.fleet.coordinator import Coordinator,Conflict
c=Coordinator(sys.argv[1])
try:
 claim=c.claim('0001',sys.argv[2],sys.argv[2],'version',lease_seconds=180)
 print(json.dumps({'won':True}))
except Conflict: print(json.dumps({'won':False}))
'''
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: json.loads(child(root, code, database, f'process-{i}')), range(8)))
    assert sum(row['won'] for row in results) == 1
    # Model reservation commits, then the owning process exits without ending it.
    reserve = '''
import os,sys
from scripts.fleet.coordinator import Coordinator
c=Coordinator(sys.argv[1]);r=c.claim('0002','dead-worker','dead-claim','version',lease_seconds=180)
a={k:r[k] for k in ('workbook','owner','token','fence')}
assert c.begin('model','dead-model',request_sha256='b'*64,**a)['execute']
os._exit(0)
'''
    child(root, reserve, database)
    restarted = Coordinator(database)
    operations = restarted.status()['operations']
    assert len(operations) == 1 and operations[0]['state'] == 'active'
    # Time alone moves the slot to hold and does not permit fresh work.
    future = Coordinator(database, clock=lambda:operations[0]['expires']+1)
    report = future.status()
    assert report['operations'][0]['state'] == 'hold'
    assert all(row['state']=='hold' for row in report['workbooks'])
    assert len(report['operations']) == 1
