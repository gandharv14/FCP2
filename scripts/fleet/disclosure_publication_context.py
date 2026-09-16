"""Read an actually completed source correction before original publication.

The original DCC context and source/review validators remain owning. No
authoring command, renewal, reservation or model operation is exposed here.
"""
from pathlib import Path
import sys

def ordinary_environment(selected):
    c=selected.get('context',{})
    return ('source_revision' not in selected and 'completed_takeover' not in selected
        and set(c)=={'helper','plan','function','kwargs','projection','manager'}
        and c['function']=='completed_context' and c['kwargs']=={'probe':True}
        and c['projection']==dict(dispatcher=1,environment=2) and c['manager'] is None)


def environment_history(pub,plan,p):
    c=p['context'];e=pub.checked(c['plan']);E=pub.load('completed_environment_publication_history',c['helper'])
    pub.require(ordinary_environment(p) and set(e)==E.FIELDS and e['schema_version']==E.SCHEMA
        and E.SCHEMA=='fleet-completed-environment-continuation/v1'
        and c['plan']==plan['fresh_authoring'] and e['original_job']==p['original_job']
        and e['workbook']==p['workbook']==plan['workbook'] and e['source']==p['source']
        and e['takeover_completion'] is not None and p['source']['sha256']==plan['source_sha256'],
        'ordinary disclosure detached from completed environment source')
    profile=pub.checked(p['profile']);old=pub.checked(profile['old_snapshot'])
    job=pub.checked(p['original_job']);config=pub.checked(job['config'])
    pub.require(config['code']==profile['old_snapshot'] and old['root']==plan['code_root']
        and profile['initial_tracker']['sha256']==p['tracker']['sha256']
        and profile['initial_tracker']['path']!=p['tracker']['path'],
        'ordinary disclosure original source or initial archive differs')
    return e


def version(plan,read):
    a=plan['disclosure_continuation']
    if set(a)!={'reader','plan','completed_reader'}:raise ValueError('closed completed disclosure history required')
    selected=read(a['plan']);profile=read(selected['profile']);snapshot=read(profile['snapshot']);old=read(profile['old_snapshot'])
    if(selected['schema_version']!='fleet-disclosure-command-continuation/v1'
        or selected['helper']!=a['reader']or not(selected.get('source_revision')is True or ordinary_environment(selected))
        or selected['workbook']!=plan['workbook']or selected['source']['sha256']!=plan['source_sha256']
        or old['root']!=plan['code_root']or not Path(snapshot['root']).is_absolute()):
        raise ValueError('publication version detached from source revision authority')
    return Path(snapshot['root']).name


def history(pub,plan):
    a=plan['disclosure_continuation']
    pub.require(set(a)=={'reader','plan','completed_reader'}
        and not any(k in plan for k in ('history','continuation_history','source_continuation',
            'legacy_research_history','semantic_continuation','source_revision','completed_checkpoint',
            'model_tail','preserved_gate','mechanical_tail','naturalization_continuation','completed_normalization')),
        'closed source disclosure publication authority required')
    for row in a.values():pub.require(pub.binding(row['path'])==row,'completed disclosure reader changed')
    p=pub.checked(a['plan']);c=p['context']
    if ordinary_environment(p):
        pub.require(p['schema_version']=='fleet-disclosure-command-continuation/v1' and p['helper']==a['reader']
            and not any(k in p for k in ('source_cache_resume','settled_permit','completed_step',
                'preparation_resume','stop_after_disclosure_build','renewal_preparation_failure')),
            'ordinary disclosure must complete its original remaining author job')
        environment_history(pub,plan,p)
        return a['plan']
    context=pub.checked(c['plan']);fresh=pub.checked(plan['fresh_authoring'])
    pub.require(p['schema_version']=='fleet-disclosure-command-continuation/v1'
        and p['helper']==a['reader']and p.get('source_revision')is True
        and 'completed_takeover'in p
        and not any(k in p for k in ('source_cache_resume','settled_permit','completed_step',
            'preparation_resume','stop_after_disclosure_build','renewal_preparation_failure'))
        and c==dict(helper=p['utilities'],plan=c['plan'],function='load',kwargs={},
            projection=dict(dispatcher=2,environment=3),manager=None)
        and context==dict(workbook=p['workbook'],fresh_plan=plan['fresh_authoring'],original_job=p['original_job'])
        and fresh['schema_version']=='harbor-fleet-fresh-plan/v1'
        and fresh['workbook']==p['workbook']==plan['workbook']
        and fresh['code_root']==plan['code_root']
        and p['source']['sha256']==plan['source_sha256'],
        'completed disclosure source or original context differs')
    return a['plan']


def guard(pub,a,p,ready,home):
    row=pub.binding(home/'permit-resource.json');value=pub.checked(row)
    pub.require(ready['permit_guard']==row
        and pub.checked(value['intent'])==dict(plan=a['plan'],helper=a['reader'],automatic_retry=False)
        and value['returncode']==0 and value['failure']is None
        and value['models_dispatched']==0 and value['seconds']==3300
        and value['memory_bytes']==8589934592
        and value['log']==pub.binding(home/'permit.log')
        and value['command']==[sys.executable,'-I','-B',a['reader']['path'],'permit-child',
            '--plan',a['plan']['path'],'--sha256',a['plan']['sha256']],
        'original source revision permission guard differs')
    return row


def dispatcher(pub,pinned,plan,ready,job):
    history(pub,plan);a=plan['disclosure_continuation'];p=pub.checked(a['plan']);home=Path(p['output'])
    pub.require(ready['status']=='PUBLICATION_READY'and ready['workbook']==p['workbook']
        and ready['continuation']==a['plan']and ready['config']==job['config']
        and ready['publication_performed']is False and ready['automatic_retry']is False
        and ready['job']==pub.binding(home/'authoring-job.json'),
        'actual completed disclosure READY required')
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
        'unknown original disclosure dispatcher')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_source_disclosure_original')
    M=pub.load('publication_completed_source_disclosure',a['reader']);manager=M.context(a['plan'],continued=True)
    selected,U,D,env,w,selection,lease,K,raw,original=manager.__enter__()
    scope=None
    try:
        first=pub.binding(home/'first-step/outcome.json');ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name
        pub.require(selected==p and str(env[6])==plan['code_root']
            and original==pub.checked(p['original_job'])
            and original['driver']==(pub.checked(pub.checked(plan['fresh_authoring'])['original_job'])['driver']
                if ordinary_environment(p) else pub.checked(plan['fresh_authoring'])['dispatcher'])
            and job==dict(original,outcome=first,operations=str(ops)),
            'completed disclosure remaining job changed')
        guard(pub,a,p,ready,home)
        cp=pub.binding(home/'permit/result.json');handoff=pub.checked(cp)
        current,observed,_=env[1].selected(handoff['descriptor']);env[1]._prior(cp,handoff['descriptor'])
        ordinary=ordinary_environment(p)
        permit=current.code_repair if ordinary else current.disclosure_revision
        pub.require(handoff['status']=='RETRY_READY'and handoff['next_step']==('disclosure-build' if ordinary else 'disclosure-review')
            and handoff['checkpoint']==permit and observed==env[7]
            and current.root==w.root and current.run==w.run
            and handoff['evidence']==[p['initial_result'],p['profile'],permit],
            'source revision handoff changed')
        tracker=pub.checked(pub.binding(current.context.tracker_root/(p['workbook']+'.json')))
        K.validate(current,tracker)
        _,review=D.successful(first,env)
        if ordinary:
            pub.require(review['status']=='STEP_COMPLETE' and review['next_step']=='disclosure-review',
                'remaining deterministic disclosure suffix did not complete')
            _,review=D.successful(pub.binding(ops/'000/outcome.json'),env)
        pub.require(review['status']=='STEP_COMPLETE'and review['next_step']=='disclosure-commit',
            'corrected source disclosure review did not pass')
        checkpoint,end=D.successful(ready['outcome'],env)
        pub.require(checkpoint==ready['checkpoint']and end['status']=='PUBLICATION_READY',
            'original final enqueue has not qualified')
        # The same reviewed facade binds byte-identical P code to the fully
        # authenticated current worker dependencies. No author command router.
        selected_scope=pub.fresh_publication.checkpoint_publication_scope(env[2],env[3],
            dict(original=str(env[6]),selected=str(w.root)))
        selected_scope.__enter__();scope=selected_scope
        def setup(observed_job):
            pub.require(observed_job==job,'publication job differs from completed source author')
            return env
        D.setup=setup
    except BaseException:
        if scope is not None:scope.__exit__(*sys.exc_info())
        manager.__exit__(*sys.exc_info());raise
    D._disclosure_publication_manager=manager
    D._disclosure_publication_scope=scope
    return D


def dependencies(pub,plan):
    history(pub,plan);a=plan['disclosure_continuation'];p=pub.checked(a['plan'])
    M=pub.load('publication_disclosure_dependencies',a['reader'])
    rows=[*a.values(),*M.runtime_inputs(p),p['original_job'],p['initial_result'],p['failure_outcome'],
        p['descriptor'],p['context']['plan']]
    if ordinary_environment(p):
        e=environment_history(pub,plan,p)
        rows.extend(e[k] for k in ('entry','stdlib','original_job','original_worker_job','checkpoint',
            'outcome','takeover_runtime','takeover_profile','takeover_completion','source'))
        archived=pub.checked(p['profile'])['initial_tracker']
    else:
        completion=pub.checked(p['completed_takeover']['completion']);archived=completion['after']
    pub.require(archived['sha256']==p['tracker']['sha256']and archived['path']!=p['tracker']['path'],
        'source revision requires immutable completed takeover tracker archive')
    rows.append(archived)
    for key in ('profile','runtime_profile'):
        def add(value):
            if isinstance(value,dict):
                if set(value)=={'path','sha256'}:rows.append(value)
                else:
                    for child in value.values():add(child)
            elif isinstance(value,list):
                for child in value:add(child)
        add(pub.checked(p[key]))
    return rows
