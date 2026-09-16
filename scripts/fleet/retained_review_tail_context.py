"""Read a completed second review reached through a declared preparation repair.

All original review and author Calls remain authoritative. The selected original
continuation supplies its scoped formula projection; this adapter only exposes
that context to the existing deterministic-derivative review consumer.
"""
from pathlib import Path
import hashlib,importlib.util,json,sys
SCHEMA='fleet-retained-completed-review-tail/v1'
FIELDS={'schema_version','helper','context','source','workbook','outcome','result','descriptor','blocked'}


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(q.is_symlink()for q in(p,*p.parents)),'canonical retained review artifact required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'retained review bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def module(row):
    read_bytes=Path(row['path']).read_bytes();require(binding(row['path'])==row,'retained review implementation changed')
    spec=importlib.util.spec_from_file_location('selected_review_tail_'+row['sha256'][:12],row['path']);M=importlib.util.module_from_spec(spec);sys.modules[spec.name]=M;spec.loader.exec_module(M);return M


def selected(p):
    require(set(p)-{'completed_takeover'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__)
        and set(p['context'])=={'helper','plan'},'closed completed review tail required')
    cp=read(p['context']['plan']);U=module(p['context']['helper'])
    if cp.get('schema_version')=='fleet-ordinary-mechanical-dialogue/v1':
        require(cp['helper']==p['context']['helper'] and 'semantic_context'in cp
            and cp['workbook']==p['workbook']and cp['source']==p['source'],'different mechanical review authority')
        return U,cp
    require(cp['helper']==p['context']['helper']and cp['schema_version']==U.TAIL_SCHEMA
        and 'unprepared_review'in cp and cp['next_step']=='dialogue-review'
        and cp['workbook']==p['workbook']and cp['source']==p['source'],'different review preparation authority')
    return U,cp


def validated_authoring(p):
    U,cp=selected(p)
    mechanical=cp['schema_version']=='fleet-ordinary-mechanical-dialogue/v1'
    if mechanical:
        manager=U.context(p['context']['plan']);actual,original,D,env,worker,selection,lease,K,V,L=manager.__enter__()
        require(actual==cp,'completed mechanical context selection differs')
        D._retained_mechanical_review_manager=manager
        fresh,job=read(cp['fresh_plan']),read(cp['original_job'])
    else:
        _,fresh,job,D,env,_,_=U.tail_context(p['context']['plan'],continued=True)
    try:
        worker,_,_=env[1].selected(p['descriptor'])
        require(worker.root==env[6]and worker.dialogue_semantic_retry is not None
            and p['blocked']==binding(env[3].step_directory(worker,'dialogue-review')/'blocked.json'),
            'exact completed second review directory required')
        if mechanical:
            original.completed_failure(p,env[1],env[7])
            directory=Path(p['outcome']['path']).parent;guard=read(read(p['outcome'])['resource']);intent=read(guard['intent']);side=read(binding(directory/'semantic-bootstrap.json'))
            auth=U.authorization_outcome(p['context']['plan'],binding(Path(cp['output'])/'authorization/outcome.json'),env[1],K)[0]
            kind,bound=original.retained_command(side['original_command'],cp,D,env)
            require(directory==Path(env[5]['run_root'])/'dispatcher-v2'/Path(cp['output']).name/'000'
                and kind=='instruction'and bound==guard['intent']and intent['arguments']['checkpoint']==auth
                and side==dict(schema_version='fleet-retained-semantic-child/v1',plan=p['context']['plan'],helper=cp['helper'],kind=kind,intent=bound,original_command=side['original_command'],seconds=original.retained_seconds(kind,bound,env),memory_bytes=8589934592,automatic_retry=False),'mechanical review lost original author completion')
        else:U.completed_failure(p,env[1],env[7])
        D._retained_review_tail_selection=p
        return fresh,job,D,env
    except BaseException:
        close(D);raise


def completed_failure(p,I,controls):
    cp=read(p['semantic_context']['plan']);U,old=selected(cp)
    require(p['semantic_context']['helper']==binding(__file__)
        and all(p[k]==cp[k]for k in ('source','workbook','outcome','result','descriptor','blocked')),
        'derivative selected another completed rejection')
    if old['schema_version']=='fleet-ordinary-mechanical-dialogue/v1':
        original=module(old['utilities']);result=original.completed_failure(p,I,controls)
    else:result=U.completed_failure(p,I,controls)
    if 'completed_takeover'in cp:
        takeover=cp['completed_takeover'];reader=module(takeover['helper'])
        result=reader.project(p,takeover,*result)
    return result


def close(D):
    manager=getattr(D,'_retained_mechanical_review_manager',None)
    if manager is None:manager=D._semantic_tail_manager
    manager.__exit__(None,None,None)
