"""Original publication over a completed, renewed semantic checkpoint tail."""
from pathlib import Path
import sys


def require(ok,message):
    if not ok:raise ValueError(message)


def renewal_predecessor(p,read):
    if 'checkpoint_takeover'in p:
        require('interrupted_commit'in p and p['next_step']=='dialogue-commit'
            and not any(k in p for k in ('checkpoint_lease','unstarted_semantic','interrupted_enqueue','completed_enqueue'))
            and set(p['checkpoint_takeover'])=={'helper','profile'},
            'original completed commit takeover required')
        return p
    if 'completed_enqueue' not in p:
        require('checkpoint_lease' in p and (('unstarted_semantic' in p)^('interrupted_enqueue' in p)),
            'original renewed semantic checkpoint required')
        return p
    require(not any(k in p for k in ('checkpoint_lease','unstarted_semantic','interrupted_enqueue')),
        'completed enqueue cannot mix renewal authorities')
    previous=p['completed_enqueue']['previous'];require(set(previous)=={'helper','plan'},'closed enqueue predecessor required')
    old=read(previous['plan'])
    require(old['helper']==previous['helper'] and old['schema_version']==p['schema_version']
        and 'interrupted_enqueue' in old and 'checkpoint_lease' in old
        and not any(k in old for k in ('completed_enqueue','unstarted_semantic'))
        and p['fence']==old['fence']+1 and p['owner']!=old['owner'] and p['output']!=old['output']
        and all(p[k]==old[k] for k in ('semantic_context','fresh_plan','original_job','source','workbook','authorization','outcome','next_step')),
        'completed enqueue lost its actual renewed predecessor')
    return old


def selection(plan,read):
    a=plan['semantic_continuation'];p=read(a['plan']);chosen=p['semantic_context'];original=read(chosen['plan'])
    renewal_predecessor(p,read)
    c=original['retained_context'];q=read(c['plan']);job=read(p['original_job']);config=read(job['config'])
    require(set(a)-{'stopped_ready'}=={'reader','plan','completed_reader'} and p['helper']==a['reader']
        and p['schema_version']=='fleet-completed-instruction-continuation/v1'
        and set(chosen)=={'helper','plan'} and original['helper']==chosen['helper']
        and original['schema_version']=='fleet-instruction-semantic-continuation/v1'
        and c.get('kind')=='completed_disclosure' and original['fresh_plan']==c['plan']==p['fresh_plan']==plan['fresh_authoring']
        and q['schema_version']in {'fleet-retained-completed-aggregate/v1','fleet-retained-completed-disclosure/v1'}
        and q['helper']==c['helper'] and q['snapshot']==c['snapshot'] and q['config']==c['config']
        and all(p[k]==original[k]==q[k]for k in ('workbook','source','original_job'))
        and all(original[k]==q[k]for k in ('outcome','result','descriptor','blocked'))
        and p['workbook']==plan['workbook'] and p['source']['sha256']==plan['source_sha256']
        and config==read(q['config']) and read(config['code'])['root']==plan['code_root'],
        'completed semantic tail lost its original source authority')
    root=Path(read(q['snapshot'])['root']);require(root.is_absolute(),'absolute selected semantic root required')
    return a,p,original,q,job,root


def version(plan,read):return selection(plan,read)[5].name


def history(pub,plan):
    require(not any(k in plan for k in ('history','continuation_history','source_continuation','disclosure_continuation',
        'legacy_research_history','preserved_gate','model_tail','completed_checkpoint','source_revision',
        'mechanical_tail','completed_normalization','naturalization_continuation')),'semantic tail cannot mix authorities')
    a,*_=selection(plan,pub.checked)
    for row in [a[k]for k in ('reader','plan','completed_reader')]+list(a.get('stopped_ready',{}).values()):require(pub.binding(row['path'])==row,'completed semantic tail reader changed')
    return a['plan']


def chain(pub,U,D,env,p,row,ready,preceding,*,stopped=False):
    home=Path(p['output']);first=pub.binding(home/'first-step/outcome.json')
    ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name
    require(ops.exists()or stopped,'completed dispatcher namespace missing')
    directories=[x for x in sorted(ops.iterdir())if x.is_dir()]if ops.exists()else[]
    require([x.name for x in directories]==['%03d'%i for i in range(len(directories))],
        'semantic tail dispatcher history is not contiguous')
    outcomes=[first,*[pub.binding(d/'outcome.json')for d in directories]]
    require(outcomes[-1]==ready['outcome'],'semantic tail does not end at actual READY')
    for outcome in outcomes:
        raw=pub.checked(outcome);guard=pub.checked(raw['resource']);intent=pub.checked(guard['intent'])
        side=pub.checked(pub.binding(Path(guard['intent']['path']).parent/'semantic-bootstrap.json'))
        kind,bound=U.retained_command(side['original_command'],p,D,env)
        require(bound==guard['intent'] and side==dict(schema_version='fleet-retained-semantic-child/v1',
            plan=row,helper=p['helper'],kind=kind,intent=bound,original_command=side['original_command'],
            seconds=U.retained_seconds(kind,bound,env),memory_bytes=8589934592,automatic_retry=False)
            and (intent['arguments']['checkpoint']if kind=='instruction'else intent['checkpoint'])==preceding,
            'completed semantic tail detached from original guarded predecessor')
        checkpoint,value=D.successful(outcome,env);preceding=checkpoint
    require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY',
        'original semantic tail enqueue did not qualify')
    return first,ops,value


def dispatcher(pub,pinned,plan,ready,job):
    history(pub,plan);a,p,original,q,original_job,root=selection(plan,pub.checked);home=Path(p['output'])
    require(ready['status']=='PUBLICATION_READY' and ready['workbook']==p['workbook']
        and ready['continuation']==a['plan'] and ready['plan']==p['fresh_plan']
        and ready['config']==original_job['config'] and ('stopped_ready'in a or ready['job']==pub.binding(home/'authoring-job.json'))
        and ready['publication_performed']is False and ready['automatic_retry']is False,
        'actual completed semantic tail READY required')
    if 'stopped_ready'in a:
        stopped=a['stopped_ready'];require(set(stopped)=={'reader','certificate'}and ('completed_enqueue'in p or ('checkpoint_takeover'in p and 'interrupted_commit'in p)),'closed completed enqueue wrapper required')
        pub.load('stopped_semantic_ready',stopped['reader']).validate(pub,p,a['plan'],ready,job,stopped['certificate'])
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='completed_semantic_tail_dispatcher')
    U=pub.load('completed_semantic_tail',a['reader'])
    selected,_,retained,D,env,checkpoint,start=U.tail_context(a['plan'],continued=True)
    manager=D._semantic_tail_manager;scope=None
    try:
        require(selected==p and retained==original_job and Path(env[6])==root,
            'completed semantic tail context differs')
        scope=pub.fresh_publication.checkpoint_publication_scope(env[2],env[3],dict(original=plan['code_root'],selected=str(root)))
        scope.__enter__()
        first,ops,end=chain(pub,U,D,env,p,a['plan'],ready,checkpoint,stopped='stopped_ready'in a)
        require(job==dict(original_job,outcome=first,operations=str(ops)),
            'remaining semantic author job changed')
        worker,controls,_=env[1].selected(end['descriptor']);request=D.publication_request(env,end)
        require(controls==env[7] and worker.root==root
            and request.selection.content.bindings['original_raw_source']==p['source'],
            'queued semantic source or controls differ')
        env[2]._paths(request,Path(plan['journal'])/'owning-publication');env[2]._checkpoint(request)
        def setup(actual):require(actual==job,'publication changed completed semantic job');return env
        D.setup=setup
    except BaseException:
        if scope is not None:scope.__exit__(*sys.exc_info())
        manager.__exit__(*sys.exc_info());raise
    D._semantic_publication_manager=manager;D._semantic_publication_scope=scope
    return D


def dependencies(pub,plan):
    history(pub,plan);a,p,original,q,_,_=selection(plan,pub.checked);rows=[a[k]for k in ('reader','plan','completed_reader')]+list(a.get('stopped_ready',{}).values())
    excluded={p['tracker']['path'],original['tracker']['path']}
    def add(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256'}:
                if v['path']not in excluded:rows.append(v)
            else:
                for x in v.values():add(x)
        elif isinstance(v,list):
            for x in v:add(x)
    add(p);add(original);add(q)
    renewed=renewal_predecessor(p,pub.checked);add(renewed)
    home=Path(renewed['output']);handoff='checkpoint-takeover/handoff.json'if 'checkpoint_takeover'in renewed else'checkpoint-lease/handoff.json'
    add(pub.checked(pub.binding(home/handoff)))
    # Original retained context reader enumerates its selected runtime inputs.
    M=pub.load('semantic_tail_original_context',q['helper'])
    if callable(getattr(M,'runtime_inputs',None)):add(M.runtime_inputs(q))
    return rows
