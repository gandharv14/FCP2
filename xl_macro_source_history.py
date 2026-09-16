"""Revalidate retained macro proofs under their exact immutable original code.

Only explicitly registered, fully hashed code snapshots are executable. Cached
results are process-local and recheck every code/source/proof/policy byte before
reuse. A registry entry is historical validator selection, never a source pass.
"""
from pathlib import Path
import hashlib,json,subprocess,sys
from xl_source_cache_equivalence import binding,read,require,object_hash
REGISTRY=Path(__file__).resolve().parent/'verification_manifests/retained_macro_implementations.v1.json'
SCHEMA='retained-macro-implementations/v1'
_CACHE={}


def selection(implementation):
    registry=read(binding(REGISTRY))
    require(set(registry)=={'schema_version','snapshots'} and registry['schema_version']==SCHEMA,
            'closed retained macro implementation registry required')
    selected=registry['snapshots'].get(implementation)
    require(selected is not None,'macro implementation not explicitly retained')
    snapshot=read(selected);root=Path(snapshot['root']);expected=snapshot['bindings']
    require(set(snapshot)=={'root','fingerprint','bindings','parent'} and root.is_absolute()
            and root.name==snapshot['fingerprint'],'closed original macro snapshot required')
    actual={}
    for item in root.rglob('*'):
        require(not item.is_symlink(),'retained macro code symlink forbidden')
        if item.is_file():actual[str(item.relative_to(root))]=binding(item)
    require(actual==expected,'retained macro full code closure changed')
    records=read(binding(root/'.pipeline-code.json'))
    require(object_hash(records)==snapshot['fingerprint']
            and all(expected[name]['sha256']==digest for name,digest in records.items())
            and set(expected)==set(records)|{'.pipeline-code.json'},
            'retained macro implementation manifest differs')
    require(object_hash({k:v for k,v in records.items()
        if k!='verification_manifests/approved_source_inventories.v2.json'})==implementation,
        'retained macro implementation identity differs')
    return selected,snapshot


def inputs(source,candidate,manifest):
    rows=[binding(source),binding(candidate),manifest['source'],manifest['candidate'],
          manifest['proof'],manifest['policy']]
    policy=read(manifest['policy']);rows.append(policy['authorization'])
    for row in rows:require(binding(row['path'])==row,'retained macro input changed')
    return rows


def verify(source,candidate,manifest):
    selected,snapshot=selection(manifest['implementation_sha256'])
    rows=inputs(source,candidate,manifest)
    key=object_hash(dict(snapshot=selected,manifest=manifest,inputs=rows))
    if key in _CACHE:return dict(_CACHE[key])
    script='''from pathlib import Path
import sys,json,hashlib
p=json.load(sys.stdin);root=Path(p['root']);sys.path.insert(0,str(root))
import xl_macro_source_projection as M
assert Path(M.__file__)==root/'xl_macro_source_projection.py'
assert M.implementation()==p['implementation']
print(json.dumps(M.verify(p['source'],p['candidate'],p['manifest'])))
'''
    value=dict(root=snapshot['root'],implementation=manifest['implementation_sha256'],
               source=str(source),candidate=str(candidate),manifest=manifest)
    result=subprocess.run([sys.executable,'-I','-B','-c',script],input=json.dumps(value),
        text=True,capture_output=True,timeout=600)
    require(result.returncode==0,'original macro verifier failed: '+result.stderr[-1200:])
    require(selection(manifest['implementation_sha256'])==(selected,snapshot)
            and inputs(source,candidate,manifest)==rows,'macro inputs changed during original verification')
    checked=json.loads(result.stdout)
    require(checked['original_sha256']==rows[0]['sha256'] and checked['candidate_sha256']==rows[1]['sha256']
            and checked['macros_executed']is False and checked['source_qualified']is False,
            'original macro verifier returned a foreign verdict')
    _CACHE[key]=checked
    return dict(checked)
