"""Read original publication custody after a qualified second author pair."""
from pathlib import Path
import sys


def require(ok,message):
    if not ok:raise ValueError(message)


def selection(plan,read):
    a=plan['semantic_continuation'];p=read(a['plan']);spec=p['semantic_context']
    tail=read(spec['plan']);semantic=read(tail['semantic_context']['plan']);retained=semantic['retained_context'];q=read(retained['plan'])
    job=read(p['original_job']);config=read(job['config'])
    require(set(a)=={'reader','plan','completed_reader'} and p['helper']==a['reader']
        and p['schema_version']=='fleet-ordinary-mechanical-dialogue/v1'
        and set(spec)=={'reader','plan','outcomes','library','original_library','instruction_history'}
        and tail['schema_version']=='fleet-completed-instruction-continuation/v1'
        and 'checkpoint_lease'in tail and 'unstarted_semantic'in tail
        and p['utilities']==tail['helper'] and p['fence']==tail['fence']+1
        and semantic['schema_version']=='fleet-instruction-semantic-continuation/v1'
        and semantic['helper']==tail['semantic_context']['helper']
        and retained['kind']=='completed_disclosure' and retained['helper']==q['helper']
        and q['schema_version']in {'fleet-retained-completed-aggregate/v1','fleet-retained-completed-disclosure/v1'}
        and q['snapshot']==retained['snapshot'] and q['config']==retained['config']
        and all(p[k]==tail[k]==semantic[k]==q[k]for k in('workbook','source','original_job'))
        and p['fresh_plan']==tail['fresh_plan']==semantic['fresh_plan']==retained['plan']==plan['fresh_authoring']
        and p['workbook']==plan['workbook'] and p['source']['sha256']==plan['source_sha256']
        and config==read(q['config']) and read(config['code'])['root']==plan['code_root'],
        'completed mechanical author source authority differs')
    root=Path(read(q['snapshot'])['root']);require(root.is_absolute(),'absolute selected mechanical root required')
    return a,p,tail,semantic,q,job,root


def version(plan,read):return selection(plan,read)[-1].name


def history(pub,plan):
    require(not any(k in plan for k in ('history','continuation_history','source_continuation','disclosure_continuation',
        'legacy_research_history','preserved_gate','model_tail','completed_checkpoint','source_revision',
        'mechanical_tail','completed_normalization','naturalization_continuation')),'mechanical reader cannot mix authorities')
    a,*_=selection(plan,pub.checked)
    for row in a.values():require(pub.binding(row['path'])==row,'completed mechanical reader changed')
    return a['plan']


def chain(pub,U,D,env,p,row,ready,previous):
    ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
    folders=[x for x in sorted(ops.iterdir())if x.is_dir()]
    require(len(folders)>=1 and [d.name for d in folders]==['%03d'%i for i in range(len(folders))],
        'mechanical dispatcher history is not contiguous')
    for folder in folders:
        outrow=pub.binding(folder/'outcome.json');out=pub.checked(outrow);guard=pub.checked(out['resource']);intent=pub.checked(guard['intent'])
        side=pub.checked(pub.binding(folder/'semantic-bootstrap.json'));kind,bound=U.retained_command(side['original_command'],p,D,env)
        require(kind=='instruction' and bound==guard['intent'] and intent['arguments']['checkpoint']==previous
            and side==dict(schema_version='fleet-retained-semantic-child/v1',plan=row,helper=p['helper'],kind=kind,
                intent=bound,original_command=side['original_command'],seconds=U.retained_seconds(kind,bound,env),
                memory_bytes=8589934592,automatic_retry=False),'mechanical completed guard lost its predecessor')
        previous,value=D.successful(outrow,env)
    require(outrow==ready['outcome'] and previous==ready['checkpoint'] and value['status']=='PUBLICATION_READY',
        'mechanical chain does not end at actual original READY')
    return ops,value


def original_guard(pub,env,allowed):
    guard=env[0]._run_in_process_group
    for _ in range(8):
        if guard.__code__.co_name!='guarded':break
        closed=dict(zip(guard.__code__.co_freevars,(c.cell_contents for c in guard.__closure__ or ())))
        require(set(closed)=={'D','env','original','p','row'} and closed['env']==env
            and closed['row']in allowed and pub.checked(closed['row'])==closed['p']
            and Path(guard.__code__.co_filename)==Path(closed['p']['utilities']['path']if 'utilities'in closed['p']else closed['p']['helper']['path']),
            'publication guard is not an authenticated original semantic router')
        guard=closed['original']
    require(guard.__name__=='_run_in_process_group' and Path(guard.__code__.co_filename)==Path(env[0].__file__)
        and pub.binding(guard.__code__.co_filename)==pub.binding(env[0].__file__),
        'publication lost its original bounded process runner')
    return guard


def dispatcher(pub,pinned,plan,ready,job):
    history(pub,plan);a,p,tail,semantic,q,original,root=selection(plan,pub.checked);home=Path(p['output'])
    require(ready==dict(status='PUBLICATION_READY',workbook=p['workbook'],plan=a['plan'],job=pub.binding(home/'authoring-job.json'),
        outcome=ready['outcome'],checkpoint=ready['checkpoint'],publication_performed=False,automatic_retry=False),
        'actual mechanical READY result required')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_mechanical_semantic_original')
    M=pub.load('publication_mechanical_semantic',a['reader']);manager=M.context(a['plan']);scope=None
    try:
        selected,U,D,env,w,s,l,K,V,L=manager.__enter__()
        require(selected==p and w.root==root and Path(env[6])==root,'completed mechanical context differs')
        auth=pub.binding(home/'authorization/outcome.json');previous,accepted=M.authorization_outcome(a['plan'],auth,env[1],K)
        ops,end=chain(pub,U,D,env,p,a['plan'],ready,previous)
        require(job==dict(original,outcome=auth,operations=str(ops)),'mechanical completed author job changed')
        current,controls,_=env[1].selected(end['descriptor']);request=D.publication_request(env,end)
        require(controls==env[7] and current.root==root and request.selection.content.bindings['original_raw_source']==p['source'],
            'queued mechanical source or controls changed')
        scope=pub.fresh_publication.checkpoint_publication_scope(env[2],env[3],dict(original=plan['code_root'],selected=str(root)))
        scope.__enter__();env[2]._paths(request,Path(plan['journal'])/'owning-publication');env[2]._checkpoint(request)
        env[0]._run_in_process_group=original_guard(pub,env,[a['plan'],p['semantic_context']['plan'],tail['semantic_context']['plan']])
        def setup(actual):require(actual==job,'publication changed mechanical job');return env
        D.setup=setup
    except BaseException:
        if scope is not None:scope.__exit__(*sys.exc_info())
        manager.__exit__(*sys.exc_info());raise
    D._semantic_publication_manager=manager;D._semantic_publication_scope=scope
    return D


def dependencies(pub,plan):
    history(pub,plan);a,p,tail,semantic,q,_,_=selection(plan,pub.checked);rows=list(a.values());excluded={x['tracker']['path']for x in(p,tail,semantic)}
    def add(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256'}:
                if v['path']not in excluded:rows.append(v)
            else:
                for x in v.values():add(x)
        elif isinstance(v,list):
            for x in v:add(x)
    for value in(p,tail,semantic,q):add(value)
    M=pub.load('mechanical_selected_dependencies',a['reader']);add(M.runtime_inputs(p));return rows
