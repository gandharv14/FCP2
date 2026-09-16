"""Retain a stopped aggregate consumer that wrote only its original step intent."""
from contextlib import contextmanager
from pathlib import Path

SCHEMA='fleet-disclosure-adoption-interrupted-step/v1'
FIELDS={'schema_version','plan','worker_outcome','process','observation','step_intent','instruction_intent','sidecar','dependency','worker','step_directory_function'}
SEALED='fleet-disclosure-adoption-sealed-unconsumed/v1'

def direct_permit(S,old):
    home=Path(old['output']);permit=S.binding(home/'permit/permit.json');checkpoint=S.binding(home/'permit/result.json');descriptor=S.binding(home/'permit/descriptor.json')
    value=S.read(permit);result=S.read(checkpoint)
    S.require(result['status']=='RETRY_READY'and result['next_step']=='disclosure-review'
        and result['checkpoint']==permit and result['descriptor']==descriptor
        and value['round_plan']==old['context']['plan']and value['round_result']==old['round_result']
        and value['implementation']==old['adoption'],'sealed original adoption handoff differs')
    return dict(permit=permit,checkpoint=checkpoint,descriptor=descriptor,tracker=value['tracker'])

def validate(S,p,*,continued=False):
    selected=p['interrupted_step'];S.require(set(selected)=={'helper','proof'},'closed interrupted consumer reader required')
    S.require(selected['helper']==S.binding(__file__),'interrupted consumer implementation changed')
    v=S.read(selected['proof']);sealed=v.get('schema_version')==SEALED
    S.require((set(v)==FIELDS|{'failed_outcome','step_exception'}if sealed else set(v)==FIELDS and v['schema_version']==SCHEMA),'closed interrupted consumer proof required')
    old=S.read(v['plan']);changes={'helper','owner','fence','output','interrupted_step'}
    S.require('interrupted_step'not in old and (sealed or 'resume'in old) and 'completed_step'not in old and 'completed_tail'not in old
        and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
        and p['owner']!=old['owner']and p['fence']==old['fence']+1 and p['output']!=old['output'],
        'interrupted consumer changed original authority')
    saved=direct_permit(S,old)if sealed and 'resume'not in old else S.resume_state(old,continued=True)
    home=Path(old['output']);outer=S.read(v['worker_outcome']);wi=S.read(outer['intent']);job=S.read(wi['job'])
    S.require(outer['status']=='RECONCILIATION_REQUIRED'
        and ((outer['failure_type']is None and outer['returncode']==1)if sealed else(outer['failure_type']=='TimeoutError'and outer['returncode']is None))
        and outer['result']is None and outer['automatic_retry']is False
        and wi['owner']==old['owner']and job['driver']==old['helper']and job['source']==old['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'], 'interrupted consumer worker custody differs')
    process=S.read(v['process']);observed=S.read(v['observation'])
    S.require(process['command'][process['command'].index('--owner')+1]==old['owner']and process['job']==wi['job']
        and observed['schema_version']=='fleet-stopped-adoption-observation/v1'
        and observed['process']==v['process']and observed['worker_outcome']==v['worker_outcome']
        and set(observed)=={'schema_version','process','worker_outcome','processes','tracker','observed_at_ns'}
        and observed['processes']==[]and observed['tracker']==saved['tracker']
        and type(observed['observed_at_ns'])is int,'interrupted consumer lacks exact stopped observation')
    S.require(S.read(S.binding(home/'intent.json'))==dict(schema_version=S.SCHEMA,plan=v['plan'],automatic_retry=False),
              'interrupted parent differs')
    instruction=S.read(v['instruction_intent']);side=S.read(v['sidecar'])
    S.require(v['instruction_intent']==S.binding(home/'first-step/intent.json')
        and instruction['action']=='step'and instruction['arguments']==dict(descriptor=saved['descriptor'],checkpoint=saved['checkpoint'])
        and instruction['models']is False and instruction['commands']is False and instruction['containers']is False,
        'interrupted consumer is not the original zero-model adoption child')
    S.require(v['sidecar']==S.binding(home/'first-step/aggregate-bootstrap.json')
        and side['plan']==v['plan']and side['helper']==old['helper']and side['intent']==v['instruction_intent']
        and side['kind']=='instruction'and side['automatic_retry']is False
        and v['dependency']==S.binding(home/'first-step/dependency.json'), 'interrupted child detached from parent')
    S.read(v['dependency'])
    step=Path(saved['permit']['path']).parent/'steps/disclosure-review'
    S.require(v['step_intent']==S.binding(step/'intent.json')
        and sorted(x.name for x in step.iterdir())==(['exception.json','intent.json']if sealed else['intent.json']),
        'aggregate consumer has effects or an unresolved nested operation')
    if sealed:
        import hashlib
        S.require(v['step_exception']==S.binding(step/'exception.json')
            and S.read(v['step_exception'])==dict(type='ValueError',message_sha256=hashlib.sha256(b'original typed inventory reader changed').hexdigest(),automatic_replay=False),
            'sealed step is not the exact original historical reader exception')
    intent=S.read(v['step_intent']);permit=S.read(saved['permit'])
    S.require(intent['step']=='disclosure-review'and intent['previous']==saved['permit']
        and intent['selection']==permit['selection']and intent['lease']==permit['lease']
        and v['worker']==p['owning_profile']['worker'], 'interrupted step does not select the original permit')
    config=S.read(S.read(old['original_job'])['config'])
    S.require(not(Path(config['run_root'])/'dispatcher-v2'/home.name).exists(),'interrupted owner has a later dispatcher')
    if sealed:
        failed=S.read(v['failed_outcome']);resource=S.read(failed['resource']);log=Path(resource['log']['path'])
        S.require(v['failed_outcome']==S.binding(home/'first-step/outcome.json')
            and failed['status']=='RECONCILIATION_REQUIRED'and failed.get('result')is None
            and failed['automatic_retry']is False and resource['returncode']==1 and resource.get('failure')is None
            and resource['intent']==v['instruction_intent']and failed['resource']==S.binding(home/'first-step/resource.json')
            and S.binding(log)==resource['log']and log.read_text().rstrip().endswith('ValueError: original typed inventory reader changed')
            and 'archived_prefix_reader' in log.read_text()and 'consuming_aggregate' in log.read_text()
            and not(home/'first-step/result.json').exists(),'sealed failure is not the exact unconsumed historical reader boundary')
    else:
        S.require(not(home/'first-step/outcome.json').exists()and not(home/'first-step/resource.json').exists(),
            'interrupted owner has a later completion; retain it')
    if not continued:S.require(S.binding(saved['tracker']['path'])==saved['tracker'],'interrupted live tracker changed')
    return saved,v

@contextmanager
def installed(S,p,W,A,*,continued=False):
    saved,v=validate(S,p,continued=continued)
    S.require(S.binding(W.__file__)==v['worker']and A.function_sha(W.step_directory)==v['step_directory_function'],
              'original step directory witness changed')
    prior=W.step_directory;target=Path(p['output'])/'adoption-steps/disclosure-review'
    def directory(worker,phase):
        if worker.disclosure_repair==saved['permit']and phase=='disclosure-review':return target
        return prior(worker,phase)
    W.step_directory=directory
    try:yield
    finally:W.step_directory=prior
