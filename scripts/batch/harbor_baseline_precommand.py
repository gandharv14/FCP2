"""Evidence for an interrupted baseline that never created its command directory.

This only validates a retained boundary. It neither imports a checkpoint nor
creates an allowance, claim, command, model operation or task directory.
"""
from pathlib import Path
from scripts.batch.harbor_admission import require,read,record


def validate(home, tracker, checkpoint, outcome):
    home=Path(home)
    require(home.is_absolute() and home.is_dir() and not any(p.is_symlink() for p in (home,*home.parents)),
            'canonical retained authoring home required')
    for name in ('bootstrap/baseline','model-campaign','instruction'):
        require(not (home/name).exists() and not (home/name).is_symlink(),'baseline or downstream execution namespace exists')
    lanes=tracker['lane_state'];passed={'harbor-source-warden','harbor-segmentation-analyst'}
    require(passed<=set(lanes),'both source lanes required')
    for lane,v in lanes.items():
        if lane in passed:require(v['state']=='passed','source/segmentation not passed')
        else:require(v['state']=='pending' and v['attempt']==0 and not v['confidence_history'] and v['current_confidence']is None,'downstream lane consumed')
    names={p.name for p in(home/'environment').iterdir()}
    failures={x for x in names if x.startswith('unresolved-baseline-') and x.endswith('.json')}
    require(len(failures)==1 and names=={'selection.json',*failures},'later environment artifact exists')
    failure=record(home/'environment'/next(iter(failures)));f=read(failure)
    require(f['status']=='UNRESOLVED' and f['step']=='baseline' and f['automatic_retry']is False
            and f['source_invalidity_claimed']is False and f['error_type']in ('TimeoutError','ConnectionError','URLError'),
            'not a retained precommand transport failure')
    require({p.name for p in(home/'bootstrap').iterdir()}<=
            {'admission.json','ledger.snapshot.json','prepared.json','source-normalization.json'},'later bootstrap state exists')
    q=read(checkpoint);o=read(outcome)
    require(q['status']=='UNRESOLVED' and q['step']=='baseline' and q['publication_ready']is False
            and failure in q['evidence'] and o['status']=='COMPLETED_OPERATION' and o['result']==checkpoint
            and o['publication_performed']is False,'exact completed failure checkpoint required')
    resource=read(o['resource']);intent=read(resource['intent'])
    require(resource['returncode']==0 and resource['failure']is None and resource['log']==record(Path(outcome['path']).parent/'step.log'),
            'failure was not completely retained by its original guard')
    require(intent['action']=='step' and intent['arguments']['descriptor']==q['descriptor'],
            'foreign baseline invocation')
    prior=read(intent['arguments']['checkpoint'])
    require(prior['status']=='STEP_COMPLETE' and prior['step']=='segmentation' and prior['descriptor']==q['descriptor']
            and prior['lease']==q['lease'],'baseline interruption changed the source-passed lease')
    return dict(status='STOPPED_BEFORE_BASELINE_COMMAND',failure=failure,checkpoint=checkpoint,outcome=outcome,
                absent_namespaces=['bootstrap/baseline','model-campaign','instruction'],models=0,claims=0)
