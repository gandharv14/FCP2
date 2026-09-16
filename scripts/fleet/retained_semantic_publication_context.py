"""Read an actually completed second dialogue round over retained DCC history."""
from pathlib import Path
import sys
from dataclasses import asdict


def selection(plan,read):
    a=plan['semantic_continuation'];p=read(a['plan']);c=p['retained_context'];q=read(c['plan'])
    pre=c.get('kind')=='completed_pre_dispatch'
    if (set(a)!=({'reader','plan','completed_reader','renewal_before'}if pre else{'reader','plan','completed_reader'}) or p['helper']!=a['reader']
        or p['schema_version']!='fleet-instruction-semantic-continuation/v1'
        or c.get('kind')not in {'completed_disclosure','completed_pre_dispatch'} or p['fresh_plan']!=c['plan']
        or p['fresh_plan']!=plan['fresh_authoring']
        or q['schema_version']!=('fleet-retained-pre-dispatch-review/v1'if pre else'fleet-retained-completed-disclosure/v1')
        or q['helper']!=c['helper'] or q['snapshot']!=c['snapshot'] or q['config']!=c['config']
        or any(p[k]!=q[k] for k in ('workbook','source','original_job','outcome','result','descriptor','blocked'))
        or p['workbook']!=plan['workbook'] or p['source']['sha256']!=plan['source_sha256']
        or read(q['snapshot'if pre else'original_snapshot'])['root']!=plan['code_root']):
        raise ValueError('completed semantic source context differs')
    if pre and (a['renewal_before']['sha256']!=p['tracker']['sha256']or 'completed_takeover'in p):
        raise ValueError('completed pre-dispatch renewal archive differs')
    return a,p,q


def version(plan,read):
    _,_,q=selection(plan,read);root=Path(read(q['snapshot'])['root'])
    if not root.is_absolute():raise ValueError('absolute semantic successor required')
    return root.name


def history(pub,plan):
    a,p,q=selection(plan,pub.checked)
    pub.require(not any(k in plan for k in ('history','continuation_history','source_continuation',
        'legacy_research_history','disclosure_continuation','preserved_gate','model_tail',
        'completed_checkpoint','source_revision','mechanical_tail','completed_normalization')),
        'completed semantic history cannot mix authorities')
    for row in a.values():pub.require(pub.binding(row['path'])==row,'semantic reader changed')
    return a['plan']


def retained_live(pub,row,p,previous,semantic):
    """The unchanged native lease was retained, never renewed or replayed."""
    value=pub.checked(row);before=pub.checked(semantic['before'])
    pub.require(set(value)=={'schema_version','authority','lease','tracker','validated_evidence',
            'minimum_remaining_ns','observed_at_ns','original_expired_replacements','lease_operations',
            'task_guards_changed','semantic_budget_reset','automatic_retry'}
        and value['schema_version']=='fleet-retained-live-continuation-lease/v1'
        and value['authority']==p and value['lease']==asdict(previous)
        and value['tracker']['sha256']==previous.sha256==pub.binding(semantic['before']['path'])['sha256']
        and value['minimum_remaining_ns']==7200*10**9 and value['lease_operations']==0
        and value['task_guards_changed'] is False and value['semantic_budget_reset'] is False
        and value['automatic_retry'] is False,
        'completed retained-live authority differs')
    lease=before['leases']['task_agent']
    pub.require(before['revision']==previous.revision
        and (lease['owner'],lease['token'])==(previous.owner,previous.token)
        and lease['heartbeat_at']<=value['observed_at_ns']
        and lease['expires_at']-value['observed_at_ns']>=value['minimum_remaining_ns'],
        'retained-live custody did not cover its selected interval')
    for r in value['validated_evidence']:
        if r['path']==value['tracker']['path']:
            pub.require(r==value['tracker'],'historical tracker reference differs')
        else:pub.require(pub.binding(r['path'])==r,'retained semantic evidence changed')
    return value


def dispatcher(pub,pinned,plan,ready,job):
    history(pub,plan);a,p,q=selection(plan,pub.checked);home=Path(p['output'])
    pub.require(ready['status']=='PUBLICATION_READY' and ready['workbook']==p['workbook']
        and ready['continuation']==a['plan'] and ready['plan']==p['fresh_plan']
        and ready['config']==job['config'] and ready['publication_performed']is False
        and ready['automatic_retry']is False and ready['job']==pub.binding(home/'authoring-job.json'),
        'actual completed second-round READY required')
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
        'unknown original semantic dispatcher')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='retained_semantic_original_dispatcher')
    M=pub.load('retained_semantic_completed_author',a['reader'])
    selected,fresh,original,D,env,worker,selection_value,lease,recipe,S,L=M.context(a['plan'],continued=True)
    manager=M.retained_manager(p,D,L);scope=None
    try:
        first=pub.binding(home/'first-step/outcome.json');ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name
        pub.require(selected==p and str(env[6])==pub.checked(q['snapshot'])['root']
            and original==pub.checked(p['original_job']) and original['config']==job['config']
            and job==dict(original,outcome=first,operations=str(ops)),
            'original remaining dialogue job differs')
        guard=pub.checked(ready['permit_guard'])
        pub.require(ready['permit_guard']==pub.binding(home/'permit-resource.json')
            and pub.checked(guard['intent'])==dict(plan=a['plan'],helper=a['reader'],automatic_retry=False)
            and guard['returncode']==0 and guard['failure']is None and guard['models_dispatched']==0
            and guard['seconds']==3300 and guard['memory_bytes']==8589934592
            and guard['log']==pub.binding(home/'permit.log')
            and guard['command']==[sys.executable,'-I','-B',a['reader']['path'],'permit-child','--plan',a['plan']['path'],'--sha256',a['plan']['sha256']],
            'original semantic authorization guard differs')
        handoffrow=pub.binding(home/'permit/result.json');handoff=pub.checked(handoffrow)
        current,controls,_=env[1].selected(handoff['descriptor']);env[1]._prior(handoffrow,handoff['descriptor'])
        pub.require(controls==env[7] and current.root==worker.root and current.run==worker.run
            and handoff['status']=='RETRY_READY' and handoff['next_step']=='dialogue-writer'
            and handoff['checkpoint']==current.dialogue_semantic_retry,
            'original semantic permission handoff differs')
        tracker=pub.checked(pub.binding(current.context.tracker_root/(p['workbook']+'.json')))
        semantic=S.validate(current,tracker)
        pub.require(semantic['blocked']==p['blocked'],'semantic permit belongs to another first review')
        if 'completed_takeover'in p:
            custody=p['completed_takeover']['completion']
        elif p['retained_context']['kind']=='completed_pre_dispatch':
            custody=pub.binding(home/'renewal/complete.json')
            _,_,previous=M.completed_failure(p,env[1],env[7])
            L.validate_completed(life=S.life,read=pub.checked,record=pub.binding,
                path=p['tracker']['path'],previous=previous,completion=custody,
                before_binding=a['renewal_before'],after_binding=semantic['before'],authority_record=a['plan'])
        else:
            custody=pub.binding(home/'renewal/retained-live.json')
            # This exact validator was demonstrated by the original completed
            # physical/semantic publication path; no renewal is performed here.
            _,_,previous=M.completed_failure(p,env[1],env[7])
            retained_live(pub,custody,a['plan'],previous,semantic)
        descriptor=pub.checked(handoff['descriptor'])
        pub.require(handoff['evidence']==[p['result'],a['plan'],custody,current.dialogue_semantic_retry]
            and descriptor['origin'][-5:]==[p['descriptor'],p['result'],a['plan'],custody,current.dialogue_semantic_retry],
            'semantic handoff lost original custody or review')
        _,started=D.successful(first,env)
        pub.require(started['status']=='STEP_COMPLETE'and started['next_step']=='dialogue-paraphraser',
            'original second writer did not complete')
        checkpoint,end=D.successful(ready['outcome'],env)
        pub.require(checkpoint==ready['checkpoint']and end['status']=='PUBLICATION_READY',
            'original final enqueue did not qualify')
        selected_scope=pub.fresh_publication.checkpoint_publication_scope(env[2],env[3],
            dict(original=plan['code_root'],selected=str(worker.root)))
        selected_scope.__enter__();scope=selected_scope
        def setup(observed):
            pub.require(observed==job,'completed semantic publication job changed');return env
        D.setup=setup
    except BaseException:
        if scope is not None:scope.__exit__(*sys.exc_info())
        manager.__exit__(*sys.exc_info());raise
    D._semantic_publication_manager=manager;D._semantic_publication_scope=scope
    return D


def dependencies(pub,plan):
    history(pub,plan);a,p,q=selection(plan,pub.checked);rows=list(a.values())
    excluded={p['tracker']['path']}
    def add(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256'}:
                if value['path']not in excluded:rows.append(value)
            else:
                for child in value.values():add(child)
        elif isinstance(value,list):
            for child in value:add(child)
    if p['retained_context']['kind']=='completed_pre_dispatch':
        command=pub.checked(q['pre_dispatch']);excluded.add(command['tracker']['path'])
        add(p);add(q);add(command)
    else:
        command=pub.checked(q['context']['plan']);excluded.add(command['tracker']['path'])
        add(p);add(q)
        K=pub.load('retained_semantic_source_dependencies',q['context']['helper'])
        for row in K.runtime_inputs(command):add(row)
    handoff=pub.checked(pub.binding(Path(p['output'])/'permit/result.json'))
    semantic=pub.checked(handoff['checkpoint']);add(semantic)
    return rows
