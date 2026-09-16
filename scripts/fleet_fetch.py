"""Fetch a selected remote evidence tree with verified absolute-path custody."""
from pathlib import Path
import argparse
import json
import shlex
import subprocess
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.fleet.migration import binding,import_files
from scripts.fleet.offline_custody import import_snapshot

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',required=True);p.add_argument('--release',required=True)
    p.add_argument('--tag',required=True);p.add_argument('--path',action='append',required=True)
    p.add_argument('--snapshot',action='store_true',help='Retain an offline evidence snapshot without replacing original-path state; never execute artifacts from this snapshot')
    a=p.parse_args()
    if a.host not in ['hhu-server',*[f'hhu-server-{i}' for i in range(2,9)]] or not a.tag.replace('-','').isalnum():p.error('invalid host/tag')
    root=Path(__file__).resolve().parents[1];out=root/'diagnostics/vm-fleet-20260912/returned'/a.tag;out.mkdir(parents=True,exist_ok=False)
    remote=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet');release=remote/'releases'/a.release
    export=remote/('export-'+a.tag)
    script="from pathlib import Path; import sys,json; sys.path.insert(0,"+repr(str(release/'code'))+"); from scripts.fleet.migration import export_files; roots="+repr(a.path)+"; dest=Path("+repr(str(export))+"); dest.mkdir(); files=[p for r in roots for p in Path(r).rglob('*') if p.is_file() and not p.is_symlink()]; print(json.dumps(export_files(files,dest/'evidence.tar.gz',dest/'manifest.json')))"
    common=['--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet']
    with (out/'transfer.log').open('xb') as log:
        result=subprocess.run(['gcloud','compute','ssh',a.host,*common,'--command',shlex.join([str(release/'venv/bin/python'),'-B','-c',script])],stdout=subprocess.PIPE,stderr=log,check=True)
        remote_manifest=json.loads(result.stdout)
        (out/'remote-manifest-binding.json').write_text(json.dumps(remote_manifest,indent=2)+'\n')
        subprocess.run(['gcloud','compute','scp',a.host+':'+str(export/'evidence.tar.gz'),a.host+':'+str(export/'manifest.json'),str(out),*common],stdout=log,stderr=subprocess.STDOUT,check=True)
    local=binding(out/'manifest.json')
    if local['sha256']!=remote_manifest['sha256']:raise RuntimeError('returned manifest changed')
    namespace=out/'offline-snapshot' if a.snapshot else Path('/')
    if a.snapshot:
        restored=import_snapshot(out/'evidence.tar.gz',local,out/'import-receipt.json',
            [root.parent,remote],namespace_root=namespace)
    else:
        restored=import_files(out/'evidence.tar.gz',local,out/'import-receipt.json',
            [root.parent],namespace_root=namespace)
    print(json.dumps(restored))

if __name__=='__main__':main()
