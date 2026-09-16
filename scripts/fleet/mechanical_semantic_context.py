"""Retain an actual second author pair for mechanical requalification only."""
from contextlib import ExitStack,contextmanager
from pathlib import Path
import sys


@contextmanager
def context(M,row,p,*,probe=False,invocation=None):
    spec=p['semantic_context']
    M.require(set(spec)=={'reader','plan','outcomes','library','original_library','instruction_history'}
        and spec['reader']==M.binding(__file__),'closed completed semantic mechanical context required')
    old=M.read(spec['plan']);U=M.module('mechanical_semantic_parent',old['helper'])
    M.require(old['schema_version']==U.TAIL_SCHEMA and 'checkpoint_lease'in old
        and 'unstarted_semantic'in old and old['next_step']=='dialogue-writer'
        and p['utilities']==old['helper'] and all(p[k]==old[k]for k in('workbook','source','fresh_plan','original_job'))
        and p['fence']==old['fence']+1,'original second-author tail authority differs')
    chosen,_,job,D,env,prior,initial=U.tail_context(spec['plan'],continued=True)
    stack=ExitStack();stack.callback(D._semantic_tail_manager.__exit__,None,None,None)
    try:
        M.require(chosen==old and job==M.read(p['original_job']),'original semantic job changed')
        I=env[1];W=env[3];home=Path(old['output']);ops=Path(old['operations'])
        expected=[M.binding(home/'first-step/outcome.json'),M.binding(ops/'000/outcome.json')]
        M.require(spec['outcomes']==expected,'exact original second author pair required')
        for n,outcome in enumerate(expected):
            raw=M.read(outcome);guard=M.read(raw['resource']);intent=M.read(guard['intent'])
            side=M.read(M.binding(Path(guard['intent']['path']).parent/'semantic-bootstrap.json'))
            kind,bound=U.retained_command(side['original_command'],old,D,env)
            M.require(kind=='instruction' and bound==guard['intent']
                and side==dict(schema_version='fleet-retained-semantic-child/v1',plan=spec['plan'],helper=old['helper'],
                    kind=kind,intent=bound,original_command=side['original_command'],seconds=U.retained_seconds(kind,bound,env),
                    memory_bytes=8589934592,automatic_retry=False)
                and intent['arguments']['checkpoint']==prior,'original second author guard lost predecessor')
            result,value=D.successful(outcome)
            if n==0:
                M.require(value['status']=='STEP_COMPLETE' and value['next_step']=='dialogue-paraphraser',
                    'second writer did not complete')
                I._prior(result,value['descriptor'])
            prior=result
        M.require(expected[-1]==p['outcome'] and result==p['result'] and value['descriptor']==p['descriptor']
            and value['status']=='DIALOGUE_REPAIR_REQUIRED' and value['next_step']=='dialogue-paraphraser'
            and value['checkpoint']==p['blocked'],'actual second paraphraser hold required')
        w,controls,_=I.selected(p['descriptor']);s=I.codec.decode(value['selection']);l=env[0].decode(value['lease'])
        M.require(controls==env[7] and w.root==env[6] and w.dialogue_semantic_retry is not None
            and initial['checkpoint']==w.dialogue_semantic_retry and s.content.bindings['original_raw_source']==p['source'],
            'second author source or semantic permission differs')
        K=M.module('mechanical_checkpoint_core',p['core']);L=M.module('mechanical_checkpoint_renewal',p['renewal'])
        M.require(K.SCHEMA=='harbor-dialogue-mechanical-requalification/v1' and L.DURATION_NS==7200*10**9,
            'original mechanical or renewal implementation differs')
        M.profile(p,w.root)
        V=W.producer.skill_module(w.root,'additional-assumptions-dialogue','validate_dialogue')
        M.require(M.binding(V.__file__)==p['validator'],'original dialogue validator changed')
        stack.enter_context(M.clause_library(V,spec['library'],spec['original_library'],atom='held flat'))
        stack.enter_context(M.checker_scope(W.producer,w.root,V))
        auth=Path(p['output'])/'authorization/outcome.json'
        if auth.exists():
            outcome=M.read(M.binding(auth));accepted=M.read(outcome['result'])
            certificate=M.read(accepted['checkpoint'])['mechanical_requalification'];proof=M.read(certificate)
            changed=[r for r in proof['evidence']if Path(r['path']).name=='instruction.md' and M.binding(r['path'])!=r]
            if changed:
                M.require(len(changed)==1,'multiple changed instruction witnesses')
                request=W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json');ctx=M.read(request.context)
                index=ctx['evidence'].index(changed[0]);call=proof['author_calls'][0]
                h=dict(context=request.context,submitted=call['submitted'],original=changed[0],
                    archive=ctx['budget_evidence'][index+1],current=M.binding(changed[0]['path']))
                T=M.module('mechanical_semantic_instruction_archive',spec['instruction_history'])
                stack.enter_context(T.instruction_history(K,certificate,h,V))
            M.install_mechanical_projection(K,W,certificate)
            _,accepted=M.authorization_outcome(row,M.binding(auth),I,K)
            _,s,l=I._prior(outcome['result'],accepted['descriptor']);w,_,_=I.selected(accepted['descriptor'])
        if invocation is not None:
            intent,side=invocation;side_value=M.read(side);kind,bound=U.retained_command(side_value['original_command'],p,D,env)
            M.require(bound==intent and side_value['plan']==row and side_value['helper']==p['helper'],
                'mechanical semantic current child differs')
            M.require(kind=='instruction','additional renewal requires explicit selected current boundary')
            args=M.read(intent)['arguments'];w,controls,_=I.selected(args['descriptor']);_,s,l=I._prior(args['checkpoint'],args['descriptor'])
            M.require(controls==env[7],'current mechanical controls differ')
        yield p,U,D,env,w,s,l,K,V,L
    finally:stack.__exit__(*sys.exc_info())
