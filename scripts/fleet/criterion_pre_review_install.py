"""Scoped declared Q/producer/readiness namespaces for a criterion correction.

No original function globals or on-disk files change. Original worker instruction
selection, locks, active lease checks, preparation and dispatch remain unchanged;
the selected Q reader supplies truthful source and author provenance only.
"""
from contextlib import contextmanager
from types import SimpleNamespace
import sys
from scripts.fleet import criterion_pre_review as core
from scripts.fleet.source_card_readiness import namespace, static

NAMES={'q':'scripts.batch.harbor_dialogue_pre_review_repair',
       'producer':'scripts.batch.harbor_instruction_producer',
       'worker':'scripts.batch.harbor_instruction_worker',
       'static':'scripts.batch.harbor_readiness',
       'complete':'scripts.batch.harbor_complete_readiness',
       'campaign':'scripts.harbor_instruction_campaign'}


@contextmanager
def installed(originals,reader):
    core.require(set(originals)==set(NAMES),'complete criterion runtime namespace required')
    for key,name in NAMES.items():core.require(sys.modules.get(name) is originals[key],'foreign criterion namespace '+key)
    reader.dependencies()
    q=namespace(originals['q'])
    for name in ('validate','validate_task','context','historical_lease','validate_retained_request'):
        original=getattr(originals['q'],name);replacement=getattr(reader,name)
        def routed(value,*args,_old=original,_new=replacement,**kwargs):
            return _new(value,*args,**kwargs) if reader.is_card(value) else _old(value,*args,**kwargs)
        q[name]=routed
    for name in ('review_projection','verify_pair','validate_review_call'):
        original=getattr(originals['q'],name);replacement=getattr(reader,name)
        def routed(bound,*args,_old=original,_new=replacement,**kwargs):
            return _new(bound,*args,**kwargs) if bound==reader.authority else _old(bound,*args,**kwargs)
        q[name]=routed
    q['REVIEW_GUIDANCE']=core.review_guidance()
    # This cold process is selected for one criterion correction only. Other Q
    # semantics remain available, but another Q task cannot issue requests here.
    producer=namespace(originals['producer'])
    old_evidence=originals['producer'].code_evidence
    def code_evidence(task):
        evidence=old_evidence(task)
        if reader.is_card(task):
            evidence=[*evidence,*reader.dependencies()]
            core.require(len({r['path']for r in evidence})==len(evidence),'duplicate criterion code evidence')
        elif task.dialogue_pre_review_repair is not None:
            raise ValueError('another pre-review task cannot share the selected criterion runtime')
        return evidence
    producer['code_evidence']=code_evidence
    selected_static=static(originals['static'],reader)
    complete=namespace(originals['complete']);complete['static']=selected_static
    selected_complete=SimpleNamespace(**complete)
    worker=namespace(originals['worker']);worker.update(producer=SimpleNamespace(**producer),
        static=selected_static,complete_readiness=selected_complete.complete_readiness)
    selected_worker=SimpleNamespace(**worker)
    campaign=namespace(originals['campaign']);campaign['worker']=selected_worker
    selected=dict(q=SimpleNamespace(**q),producer=worker['producer'],worker=selected_worker,
        static=selected_static,complete=selected_complete,campaign=SimpleNamespace(**campaign))
    try:
        for key,name in NAMES.items():
            sys.modules[name]=selected[key];package,leaf=name.rsplit('.',1);setattr(sys.modules[package],leaf,selected[key])
        yield selected
    finally:
        for key,name in NAMES.items():
            sys.modules[name]=originals[key];package,leaf=name.rsplit('.',1);setattr(sys.modules[package],leaf,originals[key])
