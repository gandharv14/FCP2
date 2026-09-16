"""Construct a retained runtime from a completed allowance takeover.

The original runtime loader runs unchanged with one explicitly selected archived
tracker read and its recorded observation time. This is historical validation,
not a fresh takeover eligibility check. The selected owning T validator then
checks the entire completion and original source/guard/lease chain.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys
import types


def require(ok, message):
    if not ok:raise ValueError(message)


def checked(row):
    require(isinstance(row,dict) and set(row)=={'path','sha256'},'exact history binding required')
    path=Path(row['path'])
    require(path.is_absolute() and path.is_file() and not any(p.is_symlink() for p in (path,*path.parents)), 'regular historical file required')
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==row['sha256'],'historical bytes changed')
    return raw


def module(row):
    checked(row)
    spec=importlib.util.spec_from_file_location('_declared_allowance_history_runtime',row['path'])
    selected=importlib.util.module_from_spec(spec);sys.modules[spec.name]=selected;spec.loader.exec_module(selected)
    return selected


def load(runtime, plan, completion):
    p=json.loads(checked(plan));done=json.loads(checked(completion))
    stop=json.loads(checked(done['stopped_owner_proof']))
    archived=checked(stop['before']);observed=stop['observed_at']
    require(type(observed) is int and observed>0,'recorded takeover observation required')
    direct=p.get('schema_version')=='harbor-allowance-takeover-direct/v1'
    tracker=p['selection']['tracker'] if direct else p['tracker']
    require(stop['selection']['tracker']==tracker and stop['before']['sha256']==tracker['sha256'], 'completion does not belong to selected pre-takeover tracker')
    require(stop['selection'].get('allowance')==p['allowance'], 'completion allowance differs from runtime selection')
    selected=module(runtime)
    original_checked=selected.checked
    reads=[]
    def archived_checked(row):
        if row==tracker:
            reads.append(row);return archived
        return original_checked(row)
    namespace=dict(selected.load.__globals__)
    namespace.update(checked=archived_checked,time=types.SimpleNamespace(time_ns=lambda:observed))
    if direct:
        original_direct=selected.direct
        direct_namespace=dict(original_direct.__globals__)
        direct_namespace.update(checked=archived_checked,time=types.SimpleNamespace(time_ns=lambda:observed))
        namespace['direct']=types.FunctionType(original_direct.__code__,direct_namespace,original_direct.__name__,original_direct.__defaults__,original_direct.__closure__)
    historical=types.FunctionType(selected.load.__code__,namespace,selected.load.__name__,selected.load.__defaults__,selected.load.__closure__)
    value,T,selection,witness=historical(plan)
    require(reads==[tracker], 'historical runtime must consume exactly its declared archived tracker')
    require(T._selection_value(selection)==stop['selection'], 'completed takeover selection differs from retained runtime')
    before,after,lease=T.validate_completion(completion)
    require(T.life.canonical(before)==archived,'owning takeover validator selected a different prior tracker')
    return value,T,lease,dict(runtime=runtime,plan=plan,completion=completion,
                             archived_tracker=stop['before'],observed_at=observed,
                             dependency_witness=witness,eligibility_reapplied=False)
