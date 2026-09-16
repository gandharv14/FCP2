"""Read actual aggregate-adoption authoring history for original publication."""
from pathlib import Path
import sys

SCHEMA='fleet-disclosure-aggregate-adoption/v1'

def require(ok,message):
    if not ok:raise ValueError(message)


def selected(plan,read):
    a=plan['disclosure_continuation']
    require(set(a)=={'reader','plan','completed_reader'},'closed completed aggregate reader required')
    p=read(a['plan']);review=read(p['context']['plan']);job=read(p['original_job']);config=read(job['config']);old=read(config['code'])
    root=Path(p['owning_profile']['worker']['path']).parents[2]
    snapshots=[read(x)for x in review['code_snapshots']]
    require(p['schema_version']==SCHEMA and p['helper']==a['reader']
        and p['workbook']==review['workbook']==plan['workbook']
        and p['source']==review['source']and p['source']['sha256']==plan['source_sha256']
        and p['context']['helper']==review['helper']and old['root']==plan['code_root']
        and root.is_absolute()and sum(x['root']==str(root)for x in snapshots)==1,
        'aggregate publication detached from original source and selected worker')
    return a,p,review,job,root


def version(plan,read):return selected(plan,read)[4].name


def history(pub,plan):
    require(not any(k in plan for k in ('history','continuation_history','source_continuation','legacy_research_history',
        'semantic_continuation','source_revision','completed_checkpoint','model_tail','preserved_gate','mechanical_tail',
        'naturalization_continuation','completed_normalization')),'aggregate publication cannot mix authoring authorities')
    a,p,review,job,root=selected(plan,pub.checked)
    for row in a.values():require(pub.binding(row['path'])==row,'completed aggregate reader changed')
    preparation=pub.checked(review['retained']['plan']);loader=preparation['loader'];command=pub.checked(loader['plan']);original=pub.checked(command['context']['plan'])
    require(preparation['schema_version']=='fleet-disclosure-review-preparation/v1'
        and command['schema_version']=='fleet-disclosure-command-continuation/v1'
        and command.get('stop_after_disclosure_build')is True and command['helper']==loader['helper']
        and command['original_job']==p['original_job']and command['source']==p['source']
        and original==dict(workbook=p['workbook'],fresh_plan=plan['fresh_authoring'],original_job=p['original_job']),
        'aggregate review lost its authentic completed source build')
    return a['plan']


def completed_chain(pub,M,U,D,env,p,row,ready):
    home=Path(p['output']);first=pub.binding(home/'first-step/outcome.json');ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name
    directories=[x for x in sorted(ops.iterdir())if x.is_dir()]
    require(directories and [x.name for x in directories]==['%03d'%i for i in range(len(directories))],
        'aggregate tail is not an exact contiguous original dispatcher history')
    outcomes=[first,*[pub.binding(x/'outcome.json')for x in directories]]
    require(outcomes[-1]==ready['outcome'],'aggregate tail does not end at actual READY')
    preceding=pub.binding(M.adoption_home(p)/'permit/result.json')
    for index,outcome in enumerate(outcomes):
        value=pub.checked(outcome);resource=pub.checked(value['resource']);intent=pub.checked(resource['intent'])
        side=pub.checked(pub.binding(Path(resource['intent']['path']).parent/'aggregate-bootstrap.json'))
        kind,bound=U.retained_command(side['original_command'],p,D,env)
        seconds=env[1].guard_seconds('step',intent['arguments'])if kind=='instruction'else intent['seconds']
        require(bound==resource['intent']and side==dict(schema_version=M.SCHEMA,plan=row,helper=p['helper'],kind=kind,
            intent=bound,original_command=side['original_command'],seconds=seconds,memory_bytes=8589934592,automatic_retry=False),
            'completed aggregate child is detached from its original guard')
        require((intent['arguments']['checkpoint']if kind=='instruction'else intent['checkpoint'])==preceding,
            'completed aggregate child lost its exact predecessor')
        checkpoint,result=D.successful(outcome,env)
        if index==0:
            require(result['status']=='STEP_COMPLETE'and result['next_step']=='disclosure-commit'
                and all(intent[k]is False for k in ('models','commands','containers')),
                'actual zero-model aggregate consumption did not complete')
        preceding=value['result']
    require(checkpoint==ready['checkpoint']and result['status']=='PUBLICATION_READY','original aggregate enqueue did not qualify')
    return first,ops,result


def dispatcher(pub,pinned,plan,ready,job):
    history(pub,plan);a,p,review,original,root=selected(plan,pub.checked);home=Path(p['output'])
    require(ready['status']=='PUBLICATION_READY'and ready['workbook']==p['workbook']
        and ready['continuation']==a['plan']and ready['config']==original['config']
        and ready['publication_performed']is False and ready['automatic_retry']is False
        and ready['job']==pub.binding(home/'authoring-job.json')and ready['aggregate']==p['round_result'],
        'actual completed aggregate READY required')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_original_aggregate_dispatcher')
    M=pub.load('publication_completed_aggregate',a['reader']);manager=M.context(a['plan'],continued=True);scope=None
    try:
        actual,R,U,D,env,w,s,l,A,raw,retained=manager.__enter__()
        require(actual==p and retained==original and str(env[6])==plan['code_root']and w.root==root,
            'completed aggregate original context differs')
        first,ops,value=completed_chain(pub,M,U,D,env,p,a['plan'],ready)
        require(job==dict(original,outcome=first,operations=str(ops)), 'completed aggregate remaining job differs')
        current,controls,_=env[1].selected(value['descriptor']);request=D.publication_request(env,value)
        require(controls==env[7]and current.root==root and current.run==w.run and current.context==w.context
            and current.disclosure_repair==pub.binding(M.adoption_home(p)/'permit/permit.json')
            and request.selection.content.bindings['original_raw_source']==p['source'],
            'completed aggregate queued worker or source differs')
        scope=pub.fresh_publication.checkpoint_publication_scope(env[2],env[3],dict(original=str(env[6]),selected=str(root)))
        scope.__enter__();env[2]._paths(request,Path(plan['journal'])/'owning-publication');env[2]._checkpoint(request)
        A.validate(current,pub.checked(pub.binding(current.context.tracker_root/(p['workbook']+'.json'))))
        env[0]._run_in_process_group=raw
        def setup(observed):require(observed==job,'publication changed the completed aggregate job');return env
        D.setup=setup
    except BaseException:
        if scope is not None:scope.__exit__(*sys.exc_info())
        manager.__exit__(*sys.exc_info());raise
    D._aggregate_publication_manager=manager;D._aggregate_publication_scope=scope
    return D


def dependencies(pub,plan):
    history(pub,plan);a,p,review,job,root=selected(plan,pub.checked);rows=[*a.values(),p['original_job'],p['round_result']]
    def add(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256'}:rows.append(v)
            else:
                for child in v.values():add(child)
        elif isinstance(v,list):
            for child in v:add(child)
    for key in ('context','adoption','history','owning_profile','checkpoint_reader','initial_review_lease','semantic_archive',
        'utilities','capacity_policy','renewal_module','namespace_module','lifecycle_scope','interrupted_step'):add(p.get(key))
    for key in ('retained','modules','code_snapshots','oracle','transport','physical_module','physical_policy'):add(review.get(key))
    return rows
