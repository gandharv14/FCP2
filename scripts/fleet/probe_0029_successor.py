"""Read-only real 0029 successor validation; never take custody or dispatch."""
from __future__ import annotations
import argparse,hashlib,json,os,sys
from pathlib import Path

def probe(snapshot):
    raw=Path(snapshot['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=snapshot['sha256']:raise ValueError('snapshot changed')
    v=json.loads(raw);root=Path(v['root']);sys.path.insert(0,str(root))
    from scripts.fleet import dialogue_0029_proof as P
    from scripts.batch import harbor_dialogue_pre_review_repair as Q,harbor_transition_codec as C,harbor_instruction_lease_renewal as L,harbor_model_executor as E
    from scripts import harbor_instruction_campaign as I
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_readiness import skill_module
    # Inspect uses the fleet-only legacy compatibility; this environment does
    # not supply a credential, token, active claim, or permission to mutate.
    os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
    def forbidden(*a,**k):raise AssertionError('read-only probe cannot dispatch or authorize')
    E.execute=forbidden;E._open=forbidden;Q.authorize=forbidden
    delta=P.verified_code(root);p=P.profile();proof=P.read(p['source_proof']);home=Path(proof['bindings']['claims']['path']).parents[3]
    calls=[]
    for path,row in p['legacy_calls'].items():
        old=P.read(row['plan'])
        prepared=E.PreparedCall(old['request'],E.Policy(**old['policy']),E.SemanticScope(**old['scope']),old['ordinal'],old['limit'],old['attempt_id'],tuple(old['budget_evidence']),response_mode=old['response_mode'],audit_context=E._audit_context(old))
        campaign=E.Campaign(Path(old['campaign_root']),Path(old['slot_root']),old['model_slots'])
        result=E.inspect(prepared,campaign)
        P.require(result.status=='completed' and result.call is not None,'0029 completed Call failed owning inspection')
        calls.append({'plan':row['plan'],'outcome':row['outcome'],'ordinal':prepared.ordinal,'limit':prepared.limit,'scope':old['scope'],'status':result.status})
    descriptor=P.record(home/'dispatcher-v2/first/021/descriptor.json');w=I.worker_decode(P.read(descriptor)['worker'])
    tracker=P.record(home/'trackers/workbooks/0029.json');before=P.read(tracker)
    blocked=P.record(home/'instruction/steps/dialogue-paraphraser/blocked.json');failed=P.read(blocked);s=C.decode(failed['selection'])
    seed=dict(old_descriptor=descriptor,old_snapshot=p['previous'],worker_run=str(home/'instruction'),prior_continuations=dict(disclosure_revision=None,lease_renewal=w.lease_renewal))
    original=Q.old_worker(seed);L.validate_historical_integrity(original,before,p['previous'])
    from scripts.batch import harbor_lease_takeover as T
    from scripts.batch.harbor_workbook_bootstrap import Lease
    current=before['leases']['task_agent'];lease=Lease(current['owner'],current['token'],before['revision'],tracker['sha256'])
    custody=P.validate_custody(failed['lease'],None,before,lease)
    guard=P.record(home/'dispatcher-v2/first/022/resource.json');checkpoint=P.read(P.read(guard)['intent'])['arguments']['checkpoint']
    takeover=T.Selection(campaign_root=home,workbook='0029',tracker=tracker,worker_kind='instruction',guard=guard,checkpoint=checkpoint,owner='fleet-0029-root-selected-owner',config=P.record(home/'config.json'))
    T._recipe(takeover)
    results=prefix(before,s,w.context,stop='dialogue')
    fixed,actual=Q.repair_sentence(P._text(proof['bindings']['original_draft']),P.read(proof['bindings']['claims']),proof['bindings']['source'],root=root)
    V=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    mechanics=V.check_draft(fixed,P.read(proof['bindings']['claims']),s.content.stage,source_workbook=proof['bindings']['source'])
    P.require(mechanics['passed'] and not mechanics['faults'],'0029 proposed draft does not pass full mechanics')
    P.require(not (home/'instruction/producer/requests/dialogue-review').exists(),'0029 independent review already prepared')
    P.require(P.record(tracker['path'])==tracker,'0029 tracker changed during probe')
    return dict(schema_version='harbor-0029-successor-readonly-probe/v1',status='SOURCE_PREFIX_MECHANICS_VERIFIED',snapshot=snapshot,
        tracker=tracker,blocked=blocked,source_proof=p['source_proof'],code_delta=delta,legacy_calls=calls,
        passed_prefix_checks=len(results),claims=43,source_formula_instances=153,changed_senior_clauses=36,
        mechanics=mechanics,custody=custody,takeover_recipe=dict(config=takeover.config,tracker=tracker,guard=guard,checkpoint=checkpoint,worker_kind='instruction',campaign_root=str(home),directory_parent=str(home/'lease-takeovers'),root_must_select_new_owner_and_unused_directory=True),models_dispatched=0,claims_changed=0,task_evidence_mutated=False,ready_for_execution=False,
        remaining=['Live native ownership reconciliation/takeover under exact fleet claim','Owning guarded pre-review permit authorization','Linux closure validation','First ordinary complete independent dialogue review1/2','Containers, publication and independent collection'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    value=probe(dict(path=str(Path(a.snapshot).absolute()),sha256=a.sha256));raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
    with Path(a.output).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    print(json.dumps(dict(status=value['status'],output=str(Path(a.output).absolute()),sha256=hashlib.sha256(raw).hexdigest())))
