"""Resume after a fully verified prefix of actual review Calls.

A completed shard is read and revalidated, never dispatched again. Any request,
executor, or incomplete result beyond the sealed prefix prevents continuation.
"""
from pathlib import Path
from dataclasses import asdict
from scripts.batch.harbor_admission import read,record,require

SCHEMA='fleet-disclosure-completed-batch-prefix/v1'
FIELDS={'schema_version','plan','worker_outcome','process','tracker','completed_count'}


def authority(binding,p,w,s,lease,Q,W):
    v=read(binding);require(set(v)==FIELDS and v['schema_version']==SCHEMA,'closed completed batch prefix required')
    old=read(v['plan']);changes={'helper','owner','fence','output','completed_prefix','progress_reader','reserved_round','round_reader'}
    require('reserved_round'in old and 'completed_prefix'not in old and 'resume'not in old
        and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
        and p['completed_prefix']==binding and p['progress_reader']==record(__file__)
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
        'completed prefix changed review authority, source or budget')
    outer=read(v['worker_outcome']);wi=read(outer['intent']);job=read(wi['job']);worker=Path(v['worker_outcome']['path']).parent
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['result']is None and outer['automatic_retry']is False and outer['publication_performed']is False
        and wi['owner']==old['owner']and job['driver']==old['helper']and job['source']==old['source']
        and job['workbook']==old['workbook']and job['run']==str(worker)
        and job['result']==str(Path(old['output'])/'result.json')
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'], 'stopped prefix worker identity differs')
    log=Path(outer['log']['path']);require(record(log)==outer['log']
        and log.read_text().rstrip().endswith('ValueError: unreconciled model effect or quarantined custody')
        and 'U.custody(p,w.root);transport_checked(p,E)'in log.read_text(),
        'not a sealed admission refusal between completed batches')
    process=read(v['process']);argv=process['command']
    require(process['job']==wi['job']and process['automatic_retry']is False
        and argv[argv.index('--owner')+1]==old['owner']
        and argv[argv.index('--job')+1]==wi['job']['path']
        and argv[argv.index('--job-sha256')+1]==wi['job']['sha256'],'prefix process detached from original worker')
    from scripts.fleet.disclosure_reserved_round import stopped_process
    stopped_process(process,worker,v['plan'])
    home=Path(old['output']);require({x.name for x in home.iterdir()}=={'intent.json','continued.json','reserved.json','validation-metrics.json','batches'}
        and read(record(home/'intent.json'))==dict(plan=v['plan'],automatic_retry=False)
        and read(record(home/'continued.json'))==old['reserved_round'], 'prefix has unaccounted effects')
    reserved=read(record(home/'reserved.json'));require(set(reserved)=={'plan','automatic_retry'}and reserved['automatic_retry']is False,'original reserved plan changed')
    plan=reserved['plan'];value=read(plan);inputs=read(p['inputs'])
    require({k:x for k,x in value.items()if k!='review_round'}==inputs,'prefix review inputs changed')
    a=Q.validate(value['review_round'],inputs)
    require(a['implementation']==p['modules']['rounds']and a['allowance']==p['allowance']and a['build']==p['build']
        and a['prior_rejections']==p['prior_rejections']and (a['round'],a['limit'])==(p['round'],p['limit']),
        'original consumed round differs')
    _,_,tracker=W.active(w,s,lease);require(tracker==v['tracker'],'prefix custody changed')
    n=v['completed_count'];require(type(n)is int and 0<n<p['max_calls']
        and sorted(x.name for x in(home/'batches').iterdir())==['%04d'%i for i in range(n)],
        'prefix is not a closed proper batch prefix')
    return plan,v,old


def completed_calls(binding,p,w,s,lease,Q,W,R,validated):
    plan,v,old=authority(binding,p,w,s,lease,Q,W);calls=[];home=Path(old['output'])
    for index in range(p['max_calls']):
        if index>=v['completed_count']:
            scope=R.scope(validated[0],index)
            require(not (Path(w.campaign.root)/R.E.sha(R.E.canonical(asdict(scope)))).exists(),
                'later shard executor exists; reconcile without replay')
            continue
        out=home/'batches'/('%04d'%index)
        require({x.name for x in out.iterdir()}=={'request.json','admission.json','transport-complete.json','complete.json'},
            'completed shard has incomplete or additional effects')
        request=W.request_saved(out/'request.json');actual=R.E.inspect(request.prepared,w.campaign)
        require(actual.status=='completed'and actual.call is not None,'retained shard transport is uncertain')
        transport=read(record(out/'transport-complete.json'));complete=read(record(out/'complete.json'))
        require(transport==dict(index=index,plan=plan,outcome=actual.outcome,call=asdict(actual.call),semantic_approval=False,automatic_retry=False)
            and set(complete)=={'index','plan','call','cached_input_recheck_seconds'}
            and complete['index']==index and complete['plan']==plan and complete['call']==asdict(actual.call),
            'completed shard is not its actual executor Call')
        context=read(request.context)
        require(context['plan']==plan and context['index']==index and request.prepared.scope==R.scope(validated[0],index)
            and R.E.checked_bytes(request.prepared.request)==R.E.canonical(R.payload(validated[0],validated[1],index,s.content.model_policy)),
            'retained request changed source projection')
        R.validate_batch(actual.call,plan,index,s.content.model_policy,w.root,_validated=validated)
        calls.append(actual.call)
    return plan,calls
