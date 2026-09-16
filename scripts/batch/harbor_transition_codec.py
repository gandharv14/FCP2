"""Closed serialization for root-selected notes transaction evidence.

No import paths or executable callbacks are accepted from a saved journal.
"""
from dataclasses import fields,is_dataclass
from pathlib import Path
from scripts.batch.harbor_admission import require

def classes():
    from scripts.batch.harbor_lane_notes import GateEvidence
    from scripts.batch.harbor_completed_lanes import CompletionEvidence
    from scripts.batch.harbor_complete_readiness import ReadinessSelection,SemanticSelection
    from scripts.batch.harbor_readiness import Selection,PreDialogueWitness
    from scripts.batch.harbor_runtime_readiness import RuntimeSelection,RuntimeObservation
    from scripts.batch.harbor_semantic_readiness import HistoricalApplicability,HistoricalCall
    from scripts.batch.harbor_ready_transitions import ReleaseSelection
    from scripts.batch.harbor_profile_readiness import ProfileSelection,ProfileCall
    from scripts.batch.harbor_model_review_provenance import Call,Policy
    return {c.__name__:c for c in (GateEvidence,CompletionEvidence,ReadinessSelection,SemanticSelection,Selection,PreDialogueWitness,RuntimeSelection,RuntimeObservation,HistoricalApplicability,ProfileSelection,ProfileCall,Call,Policy,HistoricalCall,ReleaseSelection)}

def encode(value):
    if value is None or type(value) in (str,bool,int,float):return {'type':'scalar','value':value}
    if isinstance(value,Path):return {'type':'path','value':str(value)}
    if isinstance(value,dict):return {'type':'dict','value':[[encode(k),encode(v)] for k,v in value.items()]}
    if isinstance(value,(tuple,list)):return {'type':'tuple' if isinstance(value,tuple) else 'list','value':[encode(v) for v in value]}
    if is_dataclass(value):
        registry=classes();name=type(value).__name__
        require(registry.get(name) is type(value),'unsupported evidence class')
        return {'type':name,'value':{f.name:encode(getattr(value,f.name)) for f in fields(value)
            if not (name=='ProfileSelection' and f.name=='skips' and getattr(value,f.name)==())
            and not (name=='SemanticSelection' and f.name in {'dialogue_pre_review_repair','disclosure_partition_aggregate','dialogue_partition_aggregate'} and getattr(value,f.name) is None)}}
    raise ValueError('unsupported transaction evidence type')

def decode(value):
    require(isinstance(value,dict) and set(value)=={'type','value'},'invalid evidence encoding')
    kind=value['type'];v=value['value']
    if kind=='scalar':
        require(v is None or type(v) in (str,bool,int,float),'invalid scalar');return v
    if kind=='path':require(isinstance(v,str),'invalid path');return Path(v)
    if kind in {'list','tuple'}:
        require(isinstance(v,list),'invalid sequence');items=[decode(x) for x in v]
        return tuple(items) if kind=='tuple' else items
    if kind=='dict':
        require(isinstance(v,list),'invalid dictionary');result={}
        for row in v:
            require(isinstance(row,list) and len(row)==2,'invalid mapping row')
            key=decode(row[0]);require(isinstance(key,str) and key not in result,'invalid/duplicate mapping key')
            result[key]=decode(row[1])
        return result
    registry=classes();require(kind in registry,'unrecognized evidence class')
    cls=registry[kind]
    # Explicit old Selection contract predates scope receipts. Absence supplies
    # no evidence and cannot authorize a grouping transition. All other fields
    # remain closed; new encodings always carry the optional binding.
    if kind=='Selection' and isinstance(v,dict) and set(v)=={f.name for f in fields(cls)}-{'dialogue_scope_receipt'}:
        v={**v,'dialogue_scope_receipt':{'type':'scalar','value':None}}
    if kind=='ProfileSelection' and isinstance(v,dict) and set(v)=={f.name for f in fields(cls)}-{'skips'}:
        v={**v,'skips':{'type':'tuple','value':[]}}
    if kind=='SemanticSelection' and isinstance(v,dict):
        optional={'disclosure_revision','dialogue_pre_review_repair','disclosure_partition_aggregate','dialogue_partition_aggregate'}
        required={f.name for f in fields(cls)}-optional
        if required<=set(v)<={f.name for f in fields(cls)}:
            v={**{name:{'type':'scalar','value':None} for name in optional-set(v)},**v}
    require(isinstance(v,dict) and set(v)=={f.name for f in fields(cls)},'evidence fields differ')
    return cls(**{k:decode(x) for k,x in v.items()})
