"""Read an original enqueue READY when only its outer aggregation failed."""
from pathlib import Path


def require(ok,message):
    if not ok:raise ValueError(message)


def validate(pub,p,row,ready,job,certificate):
    c=pub.checked(certificate);outer=pub.checked(c['worker_outcome']);intent=pub.checked(outer['intent']);worker_job=pub.checked(c['job'])
    home=Path(p['output']);commit_tail='checkpoint_takeover'in p and 'interrupted_commit'in p
    folder=Path(p['operations'])/'000'if commit_tail else home/'first-step'
    guard=pub.checked(c['guard']);resource=pub.checked(guard['resource']);command=pub.checked(resource['intent']);checkpoint=pub.checked(c['checkpoint'])
    require(set(c)==({'schema_version','worker_outcome','job','guard','checkpoint','ready','failure_log'}| (set()if commit_tail and command['models']is False else {'enqueue_permission'}))
        and c['schema_version']=='fleet-stopped-wrapper-completed-ready/v1'
        and c['ready']==pub.binding(ready['projection_path'])
        and c['guard']==pub.binding(folder/'outcome.json')
        and c['checkpoint']==pub.binding(folder/'result.json')
        and c['worker_outcome']==pub.binding(Path(worker_job['run'])/'outcome.json')
        and worker_job['driver']==p['helper']and worker_job['inputs'][0]==row
        and worker_job['source']==p['source']and worker_job['workbook']==p['workbook']
        and worker_job['result']==str(home/'result.json')
        and intent['job']==c['job']and intent['owner']==p['owner']
        and outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1
        and outer['failure_type']is None and outer['result']is None and outer['automatic_retry']is False
        and outer['log']==c['failure_log']and pub.binding(c['failure_log']['path'])==c['failure_log']
        and Path(c['failure_log']['path']).read_text().rstrip().endswith('AdmissionError: queued worker code changed'),
        'only exact stopped post-READY aggregation is supported')
    require(not(home/'result.json').exists(),'stopped wrapper has a conflicting final result')
    original=pub.checked(p['original_job'])
    if commit_tail:
        require([x.name for x in sorted(Path(p['operations']).iterdir())if x.is_dir()]==['000']
            and pub.checked(pub.binding(Path(p['operations'])/'hold.json'))==dict(automatic_retry=False,error_type='AdmissionError',outcome=c['guard'],reason='prior evidence inspection failed'),
            'completed commit tail has extra dispatcher work')
        first=pub.binding(home/'first-step/outcome.json');first_guard=pub.checked(first);first_value=pub.checked(first_guard['result'])
        require(first_guard['status']=='COMPLETED_OPERATION'and first_value['status']=='STEP_COMPLETE'and first_value['next_step']=='enqueue'
            and command['arguments']['checkpoint']==first_guard['result']
            and job==pub.checked(pub.binding(home/'authoring-job.json'))==dict(original,outcome=first,operations=p['operations']),
            'completed commit predecessor or actual author job differs')
    else:
        require(not(home/'authoring-job.json').exists()and not Path(p['operations']).exists(),'stopped wrapper has further dispatcher work')
    require(guard['status']=='COMPLETED_OPERATION'and guard['result']==c['checkpoint']
        and resource['returncode']==0 and resource['failure']is None
        and checkpoint['status']=='PUBLICATION_READY'and checkpoint['next_step']is None
        and checkpoint['publication_performed']is False and command['action']=='step'
        and command['models']is (False if commit_tail else True)and all(command[k]is True for k in ('commands','containers')),
        'original guarded enqueue did not finish READY')
    complete=pub.checked(checkpoint['checkpoint'])
    e=c.get('enqueue_permission')or dict(schema_version='fleet-completed-enqueue-permission/v1',intent=resource['intent'],prior=command['arguments']['checkpoint'],descriptor=command['arguments']['descriptor'],completion=checkpoint['checkpoint'],step_intent=complete['intent'])
    step=pub.checked(e['step_intent'])
    step_home=Path(pub.checked(original['config'])['run_root'])/'instruction/steps/enqueue'if commit_tail else home/'enqueue-step'
    require(e==dict(schema_version='fleet-completed-enqueue-permission/v1',intent=resource['intent'],prior=command['arguments']['checkpoint'],descriptor=command['arguments']['descriptor'],completion=checkpoint['checkpoint'],step_intent=complete['intent'])
        and e['prior']in checkpoint['evidence']and e['completion']in checkpoint['evidence']
        and complete['step']=='enqueue'and complete['status']=='completed'
        and e['completion']==pub.binding(step_home/'complete.json')
        and e['step_intent']==pub.binding(step_home/'intent.json')and step['step']=='enqueue',
        'original completed enqueue provenance changed')
    require(job==dict(original,outcome=first if commit_tail else c['guard'],operations=p['operations'])
        and ready==dict(schema_version='fleet-stopped-wrapper-ready-projection/v1',status='PUBLICATION_READY',
            workbook=p['workbook'],plan=p['fresh_plan'],job=ready['job'],config=original['config'],outcome=c['guard'],checkpoint=c['checkpoint'],continuation=row,
            intent=pub.binding(home/'intent.json'),publication_performed=False,automatic_retry=False,projection_path=ready['projection_path']),
        'READY aggregation changed original job or checkpoint')
    return c
