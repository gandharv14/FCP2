"""Read-only continuation of an existing source-card lease through a sealed T.

The old reader still validates its original permit, author Calls and initial
replacement. Only its final continuation-chain query selects the declared later
completion. This module cannot replace, renew, authorize, or dispatch anything.
"""
from pathlib import Path
from types import SimpleNamespace
import hashlib,importlib.util,json,sys,inspect
SCHEMA='harbor-retained-takeover-reader/v1'
def require(v,m):
    if not v:raise ValueError(m)
def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in (p,*p.parents)),'regular retained takeover file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
    require(binding(row['path'])==row,'retained takeover reader dependency changed');return json.loads(Path(row['path']).read_bytes())
def validate(profile):
    p=checked(profile);require(set(p)=={'schema_version','implementation','history','runtime','plan','completion','reader_plan','reader_module','prior_completion'}and p['schema_version']==SCHEMA and p['implementation']==binding(__file__),'closed retained takeover reader profile required')
    for key in ('history','runtime','plan','completion','reader_plan','reader_module','prior_completion'):require(binding(p[key]['path'])==p[key],'retained lease reader input changed')
    spec=importlib.util.spec_from_file_location('selected_completed_takeover_history',p['history']['path']);H=importlib.util.module_from_spec(spec);sys.modules[spec.name]=H;spec.loader.exec_module(H)
    _,T,lease,witness=H.load(p['runtime'],p['plan'],p['completion'])
    stop=checked(checked(p['completion'])['stopped_owner_proof'])
    require(stop['selection']['prior_completion']==p['prior_completion']and checked(p['reader_plan'])['takeover']==p['prior_completion'],'later takeover detached original reader permit')
    return p,T,lease,witness

def install_projection(worker,profile):
    p,T,_,_=validate(profile)
    reader=worker.declared_namespace['_source_cards']
    require(reader.plan_binding==p['reader_plan']and binding(inspect.getfile(type(reader)))==p['reader_module'],'foreign retained source reader')
    old=reader.takeover
    old.validate_completion(p['prior_completion'])
    def completed(row):
        if row==p['prior_completion']:return old.validate_completion(row)
        require(row==p['completion'],'unselected retained takeover completion');return T.validate_completion(row)
    def records(row):
        if row==p['prior_completion']:return old.completion_records(row)
        require(row==p['completion'],'unselected takeover records');return T.completion_records(row)
    def chain(*args,**kwargs):
        require(kwargs.get('takeover')==p['prior_completion'],'original reader selected a different takeover')
        validate(profile)
        return T.continuation_chain(*args,**dict(kwargs,takeover=p['completion']))
    def current_chain(*args,**kwargs):
        require(kwargs.get('takeover')==p['completion'],'unselected current takeover chain')
        validate(profile)
        return T.continuation_chain(*args,**kwargs)
    reader.takeover=SimpleNamespace(validate_completion=completed,completion_records=records,continuation_chain=chain,current_chain=current_chain)
    return worker
