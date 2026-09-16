"""Exact historical cache verifier identity across a declared authoring successor.

The source verifier and every numerical implementation remain byte-identical.
Only its historical whole-bundle fingerprint is projected in one local call;
source policy, fresh formula proof and complete ZIP comparisons still execute.
"""
from pathlib import Path
import hashlib,json,types
SCHEMA='harbor-source-cache-history/v1'
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
ALLOWED={'.pipeline-code.json','scripts/batch/harbor_completed_lanes.py',
 'scripts/batch/harbor_dialogue_first_round_warning.py','scripts/batch/harbor_dialogue_source_warnings.py',
 'scripts/batch/harbor_dialogue_warning_requalification.py','scripts/batch/harbor_instruction_repair.py',
 'scripts/corpus_lineage.py','scripts/batch/harbor_source_cache_history.py'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'regular cache-history evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(type(row)is dict and set(row)=={'path','sha256'} and binding(row['path'])==row,'cache-history evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def snapshot(row):
    s=read(row);require(set(s)=={'root','files'},'closed cache-history code snapshot required')
    root=Path(s['root']);expected={r['relative']:r['sha256']for r in s['files']}
    require(len(expected)==len(s['files']) and root.is_absolute(),'duplicate cache-history code')
    actual={}
    for p in root.rglob('*'):
        require(not p.is_symlink(),'cache-history code symlink')
        if p.is_file() and '__pycache__'not in p.relative_to(root).parts:actual[str(p.relative_to(root))]=binding(p)['sha256']
    require(actual==expected,'complete cache-history code changed')
    manifest=json.loads((root/'.pipeline-code.json').read_bytes())
    require(manifest=={k:v for k,v in actual.items()if k!='.pipeline-code.json'},'cache-history code manifest differs')
    fingerprint=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return root,actual,fingerprint

def validate(row,*,root,manifest):
    p=read(row);require(set(p)=={'schema_version','authorization','original_snapshot','current_snapshot','changes','original_sha256'} and p['schema_version']==SCHEMA,'closed cache-history selection required')
    require(binding(p['authorization']['path'])==p['authorization'] and p['authorization']['sha256']==AUTH,'cache-history authority differs')
    before,old,digest=snapshot(p['original_snapshot']);current,new,_=snapshot(p['current_snapshot'])
    require(Path(root)==current and current!=before and manifest['source']['sha256']==p['original_sha256'],'cache-history source or current root differs')
    require(set(old)<=set(new),'cache-history successor removed implementation')
    changes={k:{'before':old.get(k),'after':v}for k,v in new.items()if v!=old.get(k)}
    require(changes and set(changes)<=ALLOWED and changes==p['changes'],'cache-history has undeclared or numerical code changes')
    require(manifest['implementation_sha256']==digest,'cache-history does not own original source proof')
    return digest,before,current

def verify(original,source,candidate,manifest,*,profile,root):
    digest,before,current=validate(profile,root=root,manifest=manifest)
    name='xl_source_cache_equivalence.py'
    require(binding(current/name)['sha256']==binding(before/name)['sha256']
        and Path(original.__code__.co_filename)==current/name
        and original.__name__=='verify','only unchanged original cache verifier may use history')
    namespace=dict(original.__globals__)
    namespace['implementation']=lambda:digest
    selected=types.FunctionType(original.__code__,namespace,original.__name__,original.__defaults__,original.__closure__)
    selected.__kwdefaults__=original.__kwdefaults__
    return selected(source,candidate,manifest)
