"""Selected original renewal over a completed command repair and aggregate review."""
from contextlib import contextmanager
from pathlib import Path
import sys

@contextmanager
def installed(S,profile,env,worker,A,D):
    p=S.read(profile)
    S.require(set(p)-{'history_archive'}=={'schema_version','helper','renewal','history_helper','snapshot','allowance','duration_ns'}
        and p['schema_version']=='fleet-adoption-command-renewal/v1'
        and p['helper']==S.binding(__file__)and p['duration_ns']==7200*10**9,
        'closed bounded aggregate renewal required')
    from scripts.batch import harbor_disclosure_command_repair as K
    S.require(K.selected(worker)and A.selected(worker),'renewal requires exact command repair and adopted review')
    selected=S.read(S.read(worker.code_repair)['profile'])
    S.require(p['snapshot']==selected['snapshot']and Path(S.read(p['snapshot'])['root'])==worker.root,
        'renewal snapshot differs from completed command repair')
    from scripts.batch import harbor_continuation_allowance as allowance
    before=S.read(S.binding(worker.context.tracker_root/(selected['workbook']+'.json')))
    allowance.lease_limits(p['allowance'],before)
    import scripts.batch as package
    name='scripts.batch.harbor_instruction_lease_renewal'
    old=sys.modules.get(name);old_attr=getattr(package,'harbor_instruction_lease_renewal',None)
    from scripts.batch import harbor_disclosure_review_resume as disclosure
    saved_validate,saved_history=disclosure.validate,disclosure.validate_historical_integrity
    R=None;original_selected=D.selected
    try:
        R=S.module(name,p['renewal']);package.harbor_instruction_lease_renewal=R
        H=S.module('selected_aggregate_renewal_history',p['history_helper'])
        historical={}
        if 'history_archive'in p:
            G=S.module('selected_aggregate_renewal_archive',p['history_archive'])
            S.require(G.SCHEMA=='fleet-disclosure-semantic-archive/v1','unsupported historical archive reader')
            historical['historical_evidence']=G.historical_evidence
        authorize=R.authorize
        def bounded(w,s,l,checkpoint,old_snapshot,**kwargs):
            S.require(not kwargs and old_snapshot==env[5]['code']and w.root==worker.root and w.run==worker.run
                and w.code_repair==worker.code_repair and w.disclosure_repair==worker.disclosure_repair,
                'renewal detached from original dispatcher and completed repairs')
            return authorize(w,s,l,checkpoint,p['snapshot'],duration_ns=p['duration_ns'],allowance=p['allowance'])
        def history(w,b):
            if not A.selected(w):return saved_history(w,b)
            S.require(w.root==worker.root and w.run==worker.run and w.disclosure_repair==worker.disclosure_repair,
                'foreign adopted renewal history')
            return H.adoption_history(A,R,w,b,**historical)
        def validate(w,b):return history(w,b)[0]if A.selected(w)else saved_validate(w,b)
        R.authorize=bounded;disclosure.validate=validate;disclosure.validate_historical_integrity=history
        selected_env=(*env[:4],R,*env[5:])
        def selected(observed,value):
            S.require(observed==selected_env,'renewal dispatcher environment changed')
            return original_selected(env,value)
        D.selected=selected
        yield selected_env
    finally:
        D.selected=original_selected
        disclosure.validate,disclosure.validate_historical_integrity=saved_validate,saved_history
        if R is not None and 'authorize'in locals():R.authorize=authorize
        if old is None:sys.modules.pop(name,None)
        else:sys.modules[name]=old
        package.harbor_instruction_lease_renewal=old_attr
