"""Read completed physical and semantic history before the original P gates.

This adapter creates no author authority and performs no lease or model action.
"""
from pathlib import Path
from dataclasses import asdict
import sys


def history(pub,plan):
    a=plan['semantic_continuation']
    pub.require(set(a)=={'reader','plan','completed_reader'}
        and pub.binding(a['completed_reader']['path'])==a['completed_reader'],
        'closed completed semantic reader required')
    p=pub.checked(a['plan']);q=pub.checked(p['physical_context'])
    pub.require(p['helper']==a['reader'] and pub.binding(a['reader']['path'])==a['reader']
        and p['workbook']==q['workbook']==plan['workbook']
        and p['source']==q['source'] and p['source']['sha256']==plan['source_sha256']
        and p['fresh_plan']==q['fresh_plan']==plan['fresh_authoring']
        and not any(k in p for k in ('completed_takeover','retained_context','normalization_context'))
        and q.get('takeover') is not None,
        'completed physical semantic source or custody differs')
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
    a=plan['semantic_continuation'];history(pub,plan)
    p=pub.checked(a['plan']);q=pub.checked(p['physical_context']);home=Path(p['output'])
    pub.require(ready['status']=='PUBLICATION_READY' and ready['workbook']==p['workbook']
        and ready['continuation']==a['plan'] and ready['plan']==p['fresh_plan']
        and ready['config']==job['config'] and ready['publication_performed'] is False
        and ready['automatic_retry'] is False,'actual completed semantic READY required')
    M=pub.load('completed_semantic_author_reader',a['reader'])
    fresh,original,expected_root,expected_config,handoff=M.authoring_context(p)
    pub.require(handoff is None and str(expected_root)==plan['code_root']
        and original['config']==expected_config==job['config'], 'original selected physical author changed')
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
        'unknown original author helper')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='fleet_completed_semantic_dispatcher')
    D=M.module('completed_semantic_original_dispatcher',original['driver']);env=D.setup(original)
    C,I,_,W,_,config,root,controls=env
    pub.require(root==expected_root and C.SECONDS==3300 and C.MEMORY==8589934592,
        'original semantic publication guards changed')
    first=pub.binding(home/'first-step/outcome.json');guardrow=pub.binding(home/'permit-resource.json')
    pub.require(job==dict(original,outcome=first,operations=str(Path(config['run_root'])/'dispatcher-v2'/home.name))
        and ready['permit_guard']==guardrow,'completed semantic job changed')
    guard=pub.checked(guardrow);intent=pub.checked(guard['intent'])
    pub.require(intent==dict(plan=a['plan'],helper=a['reader'],automatic_retry=False)
        and guard['returncode']==0 and guard['failure'] is None and guard['models_dispatched']==0
        and guard['seconds']==3300 and guard['memory_bytes']==8589934592
        and guard['log']==pub.binding(home/'permit.log')
        and guard['command']==[sys.executable,'-I','-B',a['reader']['path'],'permit-child','--plan',a['plan']['path'],'--sha256',a['plan']['sha256']],
        'original semantic permission guard differs')
    original_result,_,previous=M.completed_failure(p,I,controls)
    handoffrow=pub.binding(home/'permit/result.json');handoff=pub.checked(handoffrow)
    descriptor=pub.checked(handoff['descriptor']);w,observed,_=I.selected(handoff['descriptor'])
    I._prior(handoffrow,handoff['descriptor'])
    pub.require(observed==controls and handoff['status']=='RETRY_READY' and handoff['next_step']=='dialogue-writer'
        and handoff['evidence'][:2]==[p['result'],a['plan']]
        and handoff['checkpoint']==w.dialogue_semantic_retry
        and w.disclosure_repair==pub.binding(Path(q['output'])/'permit/permit.json'),
        'original physical and semantic permits detached')
    from scripts.batch import harbor_dialogue_semantic_retry as S
    current=pub.checked(pub.binding(w.context.tracker_root/(p['workbook']+'.json')))
    semantic=S.validate(w,current)
    pub.require(semantic['blocked']==p['blocked'],'semantic permission belongs to another rejection')
    renewal=pub.binding(home/'renewal/retained-live.json')
    pub.require(handoff['evidence']==[p['result'],a['plan'],renewal,w.dialogue_semantic_retry]
        and descriptor['origin'][-5:]==[p['descriptor'],p['result'],a['plan'],renewal,w.dialogue_semantic_retry],
        'semantic handoff origin changed')
    retained=retained_live(pub,renewal,a['plan'],previous,semantic)
    pub.require(C.lifecycle.validate_lifecycle(pub.checked(semantic['before']))
        ==retained['original_expired_replacements'], 'retained takeover consumption changed')
    _,start=D.successful(first,env)
    pub.require(start['status']=='STEP_COMPLETE' and start['next_step']=='dialogue-paraphraser',
        'second writer did not complete')
    checkpoint,end=D.successful(ready['outcome'],env)
    pub.require(checkpoint==ready['checkpoint'] and end['status']=='PUBLICATION_READY',
        'original completed semantic enqueue missing')
    return D
