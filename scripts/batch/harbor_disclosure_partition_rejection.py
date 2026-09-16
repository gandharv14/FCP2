"""Read a completed rejected partition prefix without replay or pass authority.

The final batch must have a real completed transport and substantive rejection.
Every earlier batch retains its original semantic completion. No completion file
is synthesized, and any later prepared or executor namespace is a barrier.
"""
from dataclasses import asdict
from pathlib import Path
import math
import importlib.util
import sys
import subprocess
import json
import os
import hashlib

SCHEMA='harbor-completed-disclosure-partition-rejection/v1'
COLD_VALIDATION_SECONDS=900


def require(ok,message):
    if not ok:raise ValueError(message)


def record(path):
    path=Path(path)
    require(path.is_absolute()and path.is_file()and not any(p.is_symlink()for p in(path,*path.parents)),
            'canonical rejected-history file required')
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def read(binding):
    require(record(binding['path'])==binding,'rejected-history bytes changed')
    return json.loads(Path(binding['path']).read_bytes())


def review_reader(selected):
    if selected is not None:return selected
    # Older completed builds predate the partition modules. Their owning
    # context installs the exact selected reader before this function runs.
    from scripts.batch import harbor_disclosure_partition_review
    return harbor_disclosure_partition_review


def inspect_retained(prepared,campaign,outcome,executor):
    """Reconstruct the sealed transport mode, independent of a probe's env."""
    sealed=read(outcome);path=Path(outcome['path']).parent/'plan.json'
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==sealed['evidence']['plan.json'],
            'retained executor plan changed')
    plan=json.loads(raw);mode=plan.get('fleet_transport_policy')
    require(mode in (None,'single-dispatch-durable-global-gate/v1'),
            'unknown retained transport mode')
    previous=os.environ.get('HARBOR_FLEET_URL')
    try:
        if mode:os.environ['HARBOR_FLEET_URL']='retained-read-only'
        else:os.environ.pop('HARBOR_FLEET_URL',None)
        return executor.inspect(prepared,campaign)
    finally:
        if previous is None:os.environ.pop('HARBOR_FLEET_URL',None)
        else:os.environ['HARBOR_FLEET_URL']=previous


def rejected_call(call,plan,index,policy,root,*,validated,review_module=None):
    """Authenticate exact request, scope, budget, and complete rejected findings."""
    from scripts.batch.harbor_disclosure_revision import rejected
    R=review_reader(review_module)
    p,projection,_=validated
    result=R.validate_call(call,R.payload(p,projection,index,policy),policy=policy)
    submitted=read(call.submitted)
    require(submitted.get('scope')==asdict(R.scope(p,index))
            and submitted.get('semantic_ordinal')==submitted.get('semantic_limit')==1
            and submitted.get('physical_repair') is None,
            'rejected partition scope or allowance changed')
    require(any(R.E.checked_bytes(b)==R.E.checked_bytes(plan)
                for b in submitted.get('budget_evidence',[])),
            'rejected partition omitted frozen reservation')
    batch=projection['batches'][index]
    blocked=rejected(result['review'],batch['manifest'],batch['manifest_sha256'])
    return dict(index=index,batch_id=batch['batch_id'],blocked_indices=blocked,
                review=result['review'],semantic_approval=False)


def sealed_rejected_batches(plan,directory,*,policy,root,campaign,validated=None,review_module=None):
    R=review_reader(review_module)
    checked=validated or R.validate_plan(plan,policy=policy,root=root,current_inputs=True)
    p,projection,_=checked
    require(p==read(plan),'rejected prefix plan changed')
    parent=Path(directory)
    require(parent.is_absolute() and parent.is_dir()
            and not any(x.is_symlink()for x in(parent,*parent.parents)),
            'canonical rejected round journal required')
    batches=parent/'batches'
    require(batches.is_dir() and not batches.is_symlink(),'actual rejected batches required')
    names=sorted(x.name for x in batches.iterdir())
    require(bool(names) and names==['%04d'%i for i in range(len(names))]
            and len(names)<=len(projection['batches']),
            'rejected batch prefix is empty, noncontiguous or exceeds reservation')
    calls=[];evidence=[];finding=None
    for index,name in enumerate(names):
        home=batches/name;last=index==len(names)-1
        expected={'request.json','admission.json','transport-complete.json'}
        if not last:expected.add('complete.json')
        require(home.is_dir() and not home.is_symlink()
                and {x.name for x in home.iterdir()}==expected
                and all(x.is_file()and not x.is_symlink()for x in home.iterdir()),
                'unsealed, completed or foreign rejected batch requires reconciliation')
        bindings={key:record(home/key)for key in sorted(expected)}
        values={key:read(row)for key,row in bindings.items()}
        transport=values['transport-complete.json'];request=values['request.json'];admitted=values['admission.json']
        require(set(transport)=={'index','plan','outcome','call','semantic_approval','automatic_retry'}
                and transport['index']==index and transport['plan']==plan
                and transport['semantic_approval']is False and transport['automatic_retry']is False,
                'rejected transport receipt differs or manufactures approval')
        require(set(admitted)=={'tracker','observed_at_ns','required_remaining_seconds','physical_policy','transport_started'}
                and admitted['transport_started']is False and admitted['required_remaining_seconds']==660
                and type(admitted['observed_at_ns'])is int and admitted['observed_at_ns']>0,
                'rejected batch admission differs')
        require(set(request)=={'prepared','context'},'closed rejected prepared request required')
        raw=request['prepared'];prepared=R.E.PreparedCall(**{**raw,'policy':R.E.Policy(**raw['policy']),
            'scope':R.E.SemanticScope(**raw['scope']),'budget_evidence':tuple(raw['budget_evidence'])})
        require(prepared.policy==policy and asdict(prepared.scope)==asdict(R.scope(p,index))
                and prepared.ordinal==prepared.limit==1 and prepared.physical_repair is None,
                'rejected request changes shard policy or allowance')
        context=read(request['context'])
        require(context==dict(schema_version=R.REQUEST,plan=plan,index=index,
                    batch_id=projection['batches'][index]['batch_id'],request=prepared.request,
                    budget_evidence=list(prepared.budget_evidence),scope=asdict(prepared.scope),ordinal=1,limit=1)
                and prepared.budget_evidence[0]['sha256']==admitted['tracker']['sha256'],
                'rejected request context or admission tracker differs')
        observed=inspect_retained(prepared,campaign,transport['outcome'],R.E)
        require(observed.status=='completed' and observed.call is not None
                and observed.outcome==transport['outcome'] and asdict(observed.call)==transport['call'],
                'rejected transport is absent, uncertain or changed')
        if last:
            finding=rejected_call(observed.call,plan,index,policy,root,validated=checked,review_module=R)
        else:
            complete=values['complete.json'];elapsed=complete.get('cached_input_recheck_seconds')
            require(set(complete)=={'index','plan','call','cached_input_recheck_seconds'}
                    and complete['index']==index and complete['plan']==plan and complete['call']==transport['call']
                    and type(elapsed)in(int,float)and math.isfinite(elapsed)and elapsed>=0,
                    'earlier completed partition changed')
            R.validate_batch(observed.call,plan,index,policy,root,_validated=checked)
        # One partition has one executor attempt. Hidden later attempts remain a barrier.
        execution=R.E.location(prepared,campaign)
        require({x.name for x in execution.parent.iterdir()}=={'scope.lock','attempt-01'},
                'additional rejected shard executor history remains')
        calls.append(observed.call);evidence.extend(bindings.values());evidence.append(request['context'])
    producer=Path(p.get('producer_run',str(Path(p['worker_run'])/'producer')))
    for index in range(len(names),len(projection['batches'])):
        scope=R.scope(p,index)
        require(not (Path(campaign.root)/R.E.sha(R.E.canonical(asdict(scope)))).exists()
                and not(producer/'requests'/scope.budget_id).exists(),
                'later partition preparation or execution remains unresolved')
    return calls,len(projection['batches']),evidence,finding


def closed_rejection(binding,scope,ordinal):
    """Reopen the exact prior runtime; grant no replay of its rejected round."""
    value=read(binding)
    require(set(value)=={'schema_version','implementation','run_plan','worker_outcome',
                        'review_plan','round','limit','final_batch'}
            and value['schema_version']==SCHEMA and value['implementation']==record(__file__)
            and type(value['round'])is int and value['round']==ordinal
            and type(value['final_batch'])is int and value['final_batch']>=0,
            'closed completed partition rejection required')
    selected=read(value['run_plan']);outcome=read(value['worker_outcome'])
    intent=read(outcome['intent']);job=read(intent['job'])
    require(selected['schema_version']=='fleet-disclosure-review-continuation/v1'
            and (selected['round'],selected['limit'])==(ordinal,value['limit'])
            and scope==dict(workbook_id=selected['workbook'],original_sha256=selected['source']['sha256'],
                           lane='harbor-disclosure-writer',budget_id='disclosure-review'),
            'completed rejection source or cumulative round differs')
    require(outcome['schema_version']=='harbor-fleet-worker-outcome/v1'
            and outcome['status']=='RECONCILIATION_REQUIRED' and outcome['returncode']==1
            and outcome['failure_type']is None and outcome['result']is None
            and outcome['automatic_retry']is False and intent['owner']==selected['owner']
            and job['driver']==selected['helper'] and job['source']==selected['source']
            and job['result']==str(Path(selected['output'])/'result.json')
            and job['inputs'][0]==value['run_plan'],
            'completed rejection lacks its stopped owning worker')
    log=Path(outcome['log']['path'])
    require(record(log)==outcome['log'] and log.read_text().rstrip().endswith(
                'scripts.batch.harbor_admission.AdmissionError: blocking or unknown sentence verdict')
            and not Path(job['result']).exists(),'not an exact completed substantive partition rejection')
    # A live round's context has installed newer lease dependencies. Historical
    # readers require their original imports, so reopen them in a cold process.
    snapshot=read(selected['code_snapshots'][0])
    script='''from pathlib import Path
import sys,json,hashlib,importlib.util
p=json.load(sys.stdin);sys.path.insert(0,p['root']);r=p['reader']
assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']
s=importlib.util.spec_from_file_location('cold_partition_rejection',r['path']);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
assert m._cold_validate(p['binding']) is True
print(json.dumps(dict(binding=p['binding'],completed_rejection=True,models_dispatched=0)))
'''
    payload=dict(root=snapshot['root'],reader=record(__file__),binding=binding)
    process=subprocess.run([sys.executable,'-I','-B','-c',script],input=json.dumps(payload),
        text=True,capture_output=True,timeout=COLD_VALIDATION_SECONDS)
    require(process.returncode==0,'cold completed rejection reader failed: '+process.stderr[-1400:])
    require(json.loads(process.stdout)==dict(binding=binding,completed_rejection=True,models_dispatched=0),
            'cold completed rejection returned a foreign result')
    return True


def _cold_validate(binding):
    value=read(binding);selected=read(value['run_plan'])
    require(value['implementation']==record(__file__),'cold rejection implementation changed')
    helper=selected['helper'];require(record(helper['path'])==helper,'prior review helper changed')
    name='retained_rejected_partition_runner_'+helper['sha256']
    spec=importlib.util.spec_from_file_location(name,helper['path'])
    runner=importlib.util.module_from_spec(spec);sys.modules[name]=runner;spec.loader.exec_module(runner)
    with runner.context(value['run_plan'])as(p,U,env,w,s,lease,inputs,modules):
        actual=read(record(Path(p['output'])/'reserved.json'))
        require(actual==dict(plan=value['review_plan'],automatic_retry=False),
                'rejected round changed its original reservation')
        cache=modules['cache'].ValidationCache(value['review_plan'],policy=s.content.model_policy,
            root=w.root,code_snapshots=p['code_snapshots'])
        checked=cache.get(policy=s.content.model_policy,root=w.root)
        calls,total,evidence,finding=sealed_rejected_batches(value['review_plan'],Path(p['output']),
            policy=s.content.model_policy,root=w.root,campaign=w.campaign,validated=checked,
            review_module=modules['review'])
        require(len(calls)==value['final_batch']+1 and total==p['max_calls']
                and finding['index']==value['final_batch'] and finding['semantic_approval']is False,
                'completed rejected prefix count changed')
    return True
