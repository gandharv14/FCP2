"""Original publication of an actually completed prepared-review continuation."""
from pathlib import Path
import sys


def require(ok,message):
    if not ok:raise ValueError(message)


def selected(plan,read):
    a=plan['disclosure_continuation'];p=read(a['plan']);old=read(p['previous']);job=read(old['original_job']);config=read(job['config']);snapshot=read(config['code']);bridge=read(p['bridge_profile'])
    require(set(a)=={'reader','plan','completed_reader'}and p['schema_version']=='fleet-completed-instruction-continuation/v1'
        and p.get('completed_review')is True and p['helper']==a['reader']and old.get('prepared_review')is True
        and p['source']==old['source']and p['workbook']==old['workbook']==plan['workbook']
        and p['source']['sha256']==plan['source_sha256']and bridge['implementation']==p['bridge']
        and bridge['source']==p['source']and bridge['workbook']==p['workbook']and bridge['outcome']==p['outcome']
        and snapshot['root']==bridge['worker_root']==plan['code_root']and Path(snapshot['root']).is_absolute(),
        'completed review publication lost original source or worker')
    return a,p,old,job,Path(snapshot['root'])


def version(plan,read):return selected(plan,read)[4].name


def history(pub,plan):
    require(not any(k in plan for k in ('history','continuation_history','source_continuation','legacy_research_history',
        'semantic_continuation','source_revision','completed_checkpoint','model_tail','preserved_gate','mechanical_tail',
        'naturalization_continuation','completed_normalization')),'completed review cannot mix source authorities')
    a,p,old,job,root=selected(plan,pub.checked)
    for row in a.values():require(pub.binding(row['path'])==row,'completed review reader changed')
    q=pub.checked(p['history_profile'])
    require(q==dict(pub.checked(old['previous']),helper=p['history'])and q['source']==p['source']
        and q['workbook']==p['workbook']and plan['fresh_authoring']==p['previous'],
        'completed review historical source context changed')
    return a['plan']


def ready_config(pub,plan,ready,job):
    history(pub,plan);a,p,old,original,root=selected(plan,pub.checked)
    require(ready['status']=='PUBLICATION_READY'and ready['workbook']==p['workbook']
        and ready['continuation']==a['plan']and ready['publication_performed']is False and ready['automatic_retry']is False
        and ready['job']==pub.binding(Path(p['output'])/'authoring-job.json')and pub.checked(ready['job'])==job
        and job['driver']==original['driver']and job['config']==original['config'],
        'completed review READY lost its authentic author job')
    return job['config']


def dispatcher(pub,pinned,plan,ready,job):
    ready_config(pub,plan,ready,job);a,p,old,original,root=selected(plan,pub.checked)
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_completed_review_original')
    M=pub.load('publication_completed_review_reader',a['reader']);manager=M.context(a['plan']);scope=None
    try:
        actual,derived,S,U,D,env,w,s,l,K,raw,initial,B,T,successful,current=manager.__enter__()
        require(actual==p and initial==original and str(env[6])==str(root)==str(w.root),
            'completed review original context changed')
        home=Path(p['output']);handoff=pub.binding(home/'handoff/handoff.json');first,_=B.completed(handoff,env[1])
        last=M.completed_review_prefix(a['plan'],p,U,D,env,first,initial)
        ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name;dirs=sorted(x for x in ops.iterdir()if x.is_dir())
        require(dirs and ready['outcome']==pub.binding(dirs[-1]/'outcome.json')and current==last==ready['checkpoint']
            and job==dict(original,outcome=handoff,operations=str(ops)), 'completed review did not retain full original tail')
        cp,value=D.successful(ready['outcome'],env)
        require(cp==last and value['status']=='PUBLICATION_READY'and value['next_step']is None,
            'completed original enqueue did not qualify')
        request=D.publication_request(env,value)
        require(request.selection.content.bindings['original_raw_source']==p['source'],'queued source differs')
        scope=pub.fresh_publication.checkpoint_publication_scope(env[2],env[3],dict(original=str(root),selected=str(root)))
        scope.__enter__();env[2]._paths(request,Path(plan['journal'])/'owning-publication');env[2]._checkpoint(request)
        env[0]._run_in_process_group=raw
        def setup(observed):require(observed==job,'publication changed completed author job');return env
        D.setup=setup
    except BaseException:
        if scope is not None:scope.__exit__(*sys.exc_info())
        manager.__exit__(*sys.exc_info());raise
    D._disclosure_publication_manager=manager;D._disclosure_publication_scope=scope
    return D


def dependencies(pub,plan):
    history(pub,plan);a,p,old,job,root=selected(plan,pub.checked);rows=[*a.values(),old['original_job'],job['config']]
    def add(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256'}:rows.append(v)
            else:
                for x in v.values():add(x)
        elif isinstance(v,list):
            for x in v:add(x)
    for key in ('previous','history','history_profile','bridge','bridge_profile','capacity_policy'):add(p[key])
    for key in ('helper','history','previous','bridge','bridge_profile','capacity_policy','utilities','shell','model_runtime','lifecycle_runtime','takeover_runtime'):add(old[key])
    return rows
