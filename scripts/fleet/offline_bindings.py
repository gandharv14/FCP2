"""Read-only exact byte projection for native paths unavailable on the root host.

Only local reads are projected. Original path/hash records and native execution
remain unchanged; this is never imported by the remote preflight.
"""
from pathlib import Path
import json
from scripts.fleet.migration import binding,checked

def readers(selected):
    if selected is None:return checked,binding
    value=checked(selected)
    if set(value)!={'schema_version','copies'} or value['schema_version']!='fleet-offline-bindings/v1':raise ValueError('closed offline copies required')
    copies={}
    for row in value['copies']:
        if set(row)!={'native','copy'} or set(row['native'])!={'path','sha256'} or set(row['copy'])!={'path','sha256'}:raise ValueError('closed native/copy binding required')
        native=row['native'];copy=row['copy'];path=Path(native['path'])
        if not path.is_absolute() or '..'in path.parts or not path.is_relative_to('/home/gmahajan_labelbox_com/synthetic-data-fleet'):raise ValueError('only explicit native fleet paths may project')
        if native['path']in copies or native['sha256']!=copy['sha256'] or copy['path']==native['path'] or binding(copy['path'])!=copy:raise ValueError('duplicate or changed offline bytes')
        copies[native['path']]=row
    def bind(path):
        row=copies.get(str(path))
        if row is None:return binding(path)
        if binding(row['copy']['path'])!=row['copy']:raise ValueError('offline copy changed since selection')
        return dict(row['native'])
    def check(row):
        mapped=copies.get(row['path'])
        if mapped is None:return checked(row)
        if row!=mapped['native'] or bind(row['path'])!=row:raise ValueError('native requested binding differs from copy')
        return json.loads(Path(mapped['copy']['path']).read_bytes())
    return check,bind
