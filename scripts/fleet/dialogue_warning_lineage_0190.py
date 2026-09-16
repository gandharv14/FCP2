"""Full-inventory reader for the explicitly selected 0190 warning successor."""
from pathlib import Path
import hashlib
import json

PROFILE = None
SELF = 'scripts/fleet/dialogue_warning_lineage_0190.py'
EXPECTED_OLD = 'c7ad14b03f7c0ec3c6364efbb5e388affd9036ac15d745131ea306602700808f'


def require(ok, message):
    if not ok: raise ValueError(message)


def checked(row):
    path=Path(row['path'])
    require(not any(p.is_symlink() for p in [path,*path.parents]), '0190 lineage symlink')
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==row['sha256'], '0190 lineage binding changed')
    return json.loads(raw)


def inventory(root):
    rows={}
    for p in root.rglob('*'):
        require(not p.is_symlink(), '0190 lineage code symlink')
        if '__pycache__' in p.parts:continue
        if p.is_file() and p.name!='.pipeline-code.json':
            rows[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
    require(json.loads((root/'.pipeline-code.json').read_bytes())==rows, '0190 code manifest changed')
    return rows


def predecessor(now=None):
    require(PROFILE is not None, '0190 warning successor profile missing')
    p=checked(PROFILE)
    require(set(p)=={'schema_version','old_snapshot','changes','additions','source_proof',
                    'semantic_budget_reset','models_dispatched'}
            and p['schema_version']=='fleet-0190-warning-code-lineage/v1'
            and p['semantic_budget_reset'] is False and p['models_dispatched']==0,
            '0190 warning lineage schema differs')
    old=checked(p['old_snapshot']);oldroot=Path(old['root']);root=Path(__file__).resolve().parents[2]
    require(oldroot.name==EXPECTED_OLD and root!=oldroot, '0190 warning parent/root differs')
    before=inventory(oldroot);actual=inventory(root)
    require(before=={r['relative']:r['sha256'] for r in old['files'] if r['relative']!='.pipeline-code.json'},
            '0190 old complete inventory differs')
    manifest_row=[r for r in old['files'] if r['relative']=='.pipeline-code.json']
    require(len(manifest_row)==1 and hashlib.sha256((oldroot/'.pipeline-code.json').read_bytes()).hexdigest()==manifest_row[0]['sha256'],
            '0190 old canonical manifest changed')
    expected=dict(before)
    for name, row in p['changes'].items():
        require(name in before and row['before']=={'path':str(oldroot/name),'sha256':before[name]},
                '0190 warning patch predecessor differs')
        raw=(oldroot/name).read_text()
        for a,b in row['edits']:
            require(raw.count(a)==1, '0190 warning patch anchor differs')
            raw=raw.replace(a,b,1)
        require(hashlib.sha256(raw.encode()).hexdigest()==row['after']['sha256']
                and Path(row['after']['path']).read_bytes()==raw.encode(),
                '0190 warning patch witness differs')
        expected[name]=row['after']['sha256']
    for name,row in p['additions'].items():
        require(name not in before and name!=SELF, '0190 warning added role differs')
        raw=Path(row['path']).read_bytes()
        require(hashlib.sha256(raw).hexdigest()==row['sha256'], '0190 warning addition changed')
        expected[name]=row['sha256']
    # SELF is pinned by the root-selected complete snapshot and its canonical
    # fingerprint; all other files must match the exact patch witnesses.
    expected[SELF]=actual.get(SELF)
    require(actual==expected and (now is None or now==actual), '0190 warning unrelated code delta')
    fingerprint=hashlib.sha256(json.dumps(actual,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    require(root.name==fingerprint, '0190 warning immutable fingerprint differs')
    checked(p['source_proof'])
    return before


def retained(worker,before,snapshot):
    predecessor()
    p=checked(PROFILE)
    require(snapshot==p['old_snapshot'], '0190 warning original snapshot differs')
    from scripts.fleet.dialogue_warning_history_0190 import validate_retained
    return validate_retained(worker,before,snapshot)
