"""Declare a fresh enqueue journal after a proved, retained readiness failure.

The original worker still checks every phase, active lease and current readiness.
This reader changes only where the next enqueue attempt records its evidence.
"""
from pathlib import Path
import hashlib,json

SCHEMA='harbor-failed-enqueue-reader/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path)
    require(p.is_absolute() and p.is_file() and not any(q.is_symlink() for q in (p,*p.parents)),'regular enqueue evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'enqueue evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def validate(certificate):
    p=read(certificate)
    require(set(p)=={'schema_version','implementation','worker_run','authority','failed_intent','failed_exception','failed_readiness','predecessor','source','directory'} and p['schema_version']==SCHEMA and p['implementation']==binding(__file__),'closed enqueue continuation required')
    intent=read(p['failed_intent']);previous=read(p['predecessor']);proof=read(p['failed_readiness']);exception=read(p['failed_exception'])
    directory=Path(p['failed_intent']['path']).parent;target=Path(p['directory'])
    require(p['failed_intent']==binding(directory/'intent.json') and p['failed_exception']==binding(directory/'exception.json') and p['failed_readiness']==binding(directory/'readiness.json') and not(directory/'complete.json').exists(),'exact failed enqueue required')
    require(target.is_absolute() and target.parent==directory.parent and target!=directory and target.name.startswith('enqueue-') and not any(q.is_symlink()for q in (target,*target.parents)),'fresh sibling enqueue namespace required')
    require(intent['step']=='enqueue' and intent['previous']==p['predecessor'] and intent['initial']==binding(Path(p['worker_run'])/'initial.json') and previous['step']=='dialogue-commit' and previous['status']=='completed' and previous['selection']==intent['selection'] and previous['lease']==intent['lease'],'enqueue predecessor changed')
    require(exception['automatic_replay']is False and proof['publication_ready']is False and proof['blockers']==['context_bindings','context_bindings_final'] and proof['mutation_performed']is False,'only exact retained context readiness failure supported')
    require(read(p['authority'])['source']==p['source'],'enqueue source authority changed')
    for path,digest in intent['code'].items():require(binding(path)['sha256']==digest,'old enqueue code changed')
    return p

def install_projection(W,certificate):
    p=validate(certificate);reader=W.declared_namespace['_source_cards'];original=W.step_directory
    require(reader.authority==p['authority'],'enqueue belongs to another source-card authority')
    def directory(worker,phase):
        if phase!='enqueue' or str(worker.run)!=p['worker_run']:return original(worker,phase)
        checked=validate(certificate)
        require(reader.is_card(worker) and W.code(worker)==read(checked['failed_intent'])['code'],'enqueue worker or code changed')
        require(original(worker,phase)==Path(checked['failed_intent']['path']).parent,'original enqueue namespace changed')
        return Path(checked['directory'])
    W.step_directory=directory;W.declared_namespace['step_directory']=directory
    return W
