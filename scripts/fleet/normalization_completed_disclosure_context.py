"""Read a completed imported-normalization author prefix for a new review round.

The original imported context validates source, package, refresh and takeover
history. This reader only authenticates the subsequent guarded instruction
prefix and its sealed HTTP rejection; it never starts a child or changes custody.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,importlib.util,json,sys

SCHEMA='fleet-normalization-completed-disclosure-context/v1'
FIELDS={'schema_version','helper','original','original_job','worker_outcome','build',
        'stopped_outcome','stopped_result','rounds','prior_rejections','source','workbook'}


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical completed normalization evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'completed normalization evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def module(name,row):
    require(binding(row['path'])==row,'completed normalization reader changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def child_lineage(A,M,q,row,env,D,intent):
    value=read(intent);kind='environment'if value.get('schema_version')=='harbor-coordinator-step/v1'else'instruction'
    producer=env[0]if kind=='environment'else env[1]
    side=read(binding(Path(intent['path']).parent/'declared-runtime-adapter.json'))
    original=side.get('original_command');require(isinstance(original,list)and original,'original child command required')
    seconds=A.contract(producer,env[0],kind,intent,original)
    expected=dict(schema_version=A.SCHEMA,adapter=binding(A.__file__),producer_code=binding(producer.__file__),
        base_code=binding(env[0].__file__),kind=kind,reader_plan=row,child_wrapper=q['helper'],
        reader_evidence=M.evidence(q,read(q['previous'])),intent=intent,original_command=original,
        selected_command=[original[0],'-I','-B',q['helper']['path'],'owning-child','--plan',row['path'],
                          '--plan-sha256',row['sha256'],'--kind',kind,'--intent',intent['path'],'--intent-sha256',intent['sha256']],
        timeout_seconds=seconds,memory_limit_bytes=8*1024**3,original_snapshot_unchanged=True,
        semantic_budget_reset=False,automatic_retry=False)
    require(side==expected,'completed child adapter changed source, code, command or authority')
    for evidence in [row,q['helper'],*expected['reader_evidence']]:
        require(binding(evidence['path'])==evidence,'completed child evidence bytes changed')
    return kind,value


def prefix(p,M,q,row,D,env,A):
    job=read(p['original_job']);home=Path(q['home']);outer=read(p['worker_outcome']);intent=read(outer['intent']);wj=read(intent['job']);claim=read(binding(Path(outer['intent']['path']).parent/'claim.json'))
    require(p['original_job']==binding(home/'authoring-job.json')and read(binding(home/'intent.json'))['plan']==row
        and outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False
        and wj['driver']==q['helper']and wj['inputs'][0]==row and wj['source']==q['source']
        and wj['workbook']==q['workbook']and intent['owner']==claim['owner']==q['owner']
        and intent['claim_id']==claim['claim_id']and claim['fence']==q['fence']and claim['version']==wj['version']
        and claim['source_sha256']==q['source']['sha256']and claim['workbook']==q['workbook']
        and wj['version']==Path(env[6]).name,
        'completed normalization outer claim or job differs')
    original=read(read(read(q['previous'])['single_author'])['job'])
    expected=dict(original,outcome=read(q['completed_package'])['completed'],operations=str(Path(env[5]['run_root'])/'dispatcher-v2'/(home.name+'-authoring')))
    require(job==expected,'completed normalization author job changed')
    ops=Path(job['operations']);dirs=sorted(x for x in ops.iterdir()if x.is_dir())
    require(3<=len(dirs)<=40 and [x.name for x in dirs]==['%03d'%i for i in range(len(dirs))]
        and read(binding(ops/'job.json'))==job and read(binding(ops/'hold.json'))['outcome']==p['stopped_outcome'],
        'completed normalization prefix is not contiguous or held at selected outcome')
    previous=read(job['outcome'])['result'];build_seen=False
    for i,d in enumerate(dirs):
        outcome=binding(d/'outcome.json');out=read(outcome)
        checkpoint,value=D.successful(outcome)if i==len(dirs)-1 else D.successful(outcome,env)
        guard=read(out['resource']);kind,ci=child_lineage(A,M,q,row,env,D,guard['intent'])
        require(guard['returncode']==0 and guard['failure']is None and ci['arguments'].get('checkpoint')==previous
            and (ci['action']!='step' or kind=='environment' or value['evidence'][0]==previous),
            'completed normalization guard skipped its predecessor')
        if outcome==p['build']['outcome']:
            require(i==len(dirs)-2 and kind=='instruction'and checkpoint==p['build']['result']
                and value['status']=='STEP_COMPLETE'and value['next_step']=='disclosure-review'
                and p['build']['dispatcher']==job['driver']and p['build']['instruction']==binding(env[1].__file__)
                and p['build']['descriptor']==value['descriptor'],'selected disclosure build is not the original penultimate guard')
            build_seen=True
        if i==len(dirs)-1:
            blocked=read(value['checkpoint']);built=read(p['build']['result'])
            require(build_seen and kind=='instruction'and outcome==p['stopped_outcome']and checkpoint==p['stopped_result']
                and value['status']==blocked['status']=='MODEL_RECONCILIATION_REQUIRED'
                and value['next_step']==blocked['step']=='disclosure-review'
                and value['descriptor']==built['descriptor']and value['selection']==built['selection']==blocked['selection']
                and value['publication_performed']is False
                and ci['controls']==env[0].encode(env[7])and ci['arguments']['descriptor']==built['descriptor']
                and all(ci[k]is True for k in ('models','commands','containers'))
                and value['checkpoint']==binding(Path(env[5]['run_root'])/'instruction/steps/disclosure-review/blocked.json'),
                'selected terminal is not the original rejected build review')
        previous=checkpoint
    return job


@contextmanager
def context(row,*,continued=True):
    p=read(row);require(continued is True and set(p)==FIELDS and p['schema_version']==SCHEMA
        and p['helper']==binding(__file__),'closed completed normalization review context required')
    c=p['original'];require(set(c)=={'helper','plan'},'closed original imported reader required');q=read(c['plan'])
    require(q['helper']==c['helper']and q['schema_version']=='fleet-normalization-imported-continuation/v2'
        and 'completed_package'in q and q['workbook']==p['workbook']and q['source']==p['source'],
        'completed imported package source differs')
    M=module('completed_disclosure_original_normalization',c['helper'])
    with M.context(c['plan'],initial=False,live=False)as(actual,prior,N,D,env,w,lease,A):
        require(actual==q,'original imported context changed');selected,selected_env=M.selected_dispatcher(q,prior,N,D,env,A,c['plan'])
        prefix(p,M,q,c['plan'],selected,selected_env,A)
        Q=module('normalization_completed_rounds',p['rounds']);worker,selection,current=Q.completed_build(p['build'])
        require(selection.content.workbook==p['workbook']and selection.content.bindings['original_raw_source']==p['source'],
            'completed review source differs')
        scope=Q.semantic_scope(dict(workbook=p['workbook'],original_source=p['source']))
        require(isinstance(p['prior_rejections'],list)and p['prior_rejections'],'exact sealed rejection history required')
        for i,rejection in enumerate(p['prior_rejections'],1):Q.closed_rejection(rejection,scope,i)
        Q._primary_history(worker.campaign,scope,len(p['prior_rejections'])+1,p['prior_rejections'])
        env[3].active(worker,selection,current)
        yield p,env,current
