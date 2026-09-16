"""Retain a sealed operand-projection failure before any second review request.

The completed semantic permit and both authors remain original. Only the failed
review's physical step directory and its declared source-operand reader change.
"""
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import hashlib,sys


def stopped(U,p,old,env,checkpoint,value,worker):
    C,I,_,W,_,config,root,controls=env
    failed=U.read(p['failed_guard']);directory=Path(p['failed_guard']['path']).parent
    U.require(directory.parent==Path(config['run_root'])/'dispatcher-v2'/Path(old['output']).name
        and set(q.name for q in directory.iterdir())=={'intent.json','resource.json','outcome.json','step.log','semantic-bootstrap.json','dependency.json'}
        and failed['status']=='RECONCILIATION_REQUIRED' and failed.get('result')is None
        and failed['automatic_retry']is False and failed['resource']==U.binding(directory/'resource.json'),
        'exact failed original review guard required')
    guard=U.read(failed['resource']);intent=U.read(guard['intent'])
    U.require(guard['returncode']==1 and guard['failure']is None
        and guard['intent']==U.binding(directory/'intent.json') and guard['log']==U.binding(directory/'step.log')
        and intent['schema_version']==I.SCHEMA and intent['action']=='step'
        and intent['arguments']==dict(checkpoint=checkpoint,descriptor=value['descriptor'])
        and intent['code']==U.binding(I.__file__) and intent['base_code']==U.binding(C.__file__)
        and intent['controls']==C.encode(controls) and intent['memory_limit_bytes']==8589934592
        and all(intent[k]is True for k in ('models','commands','containers')),
        'uncertain or different review guard cannot continue')
    U.require(U.retained_seconds('instruction',guard['intent'],env)==3300,
        'original review guard changed')
    side=U.read(U.binding(directory/'semantic-bootstrap.json'))
    command=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',guard['intent']['path'],guard['intent']['sha256']]
    U.require(side==dict(schema_version='fleet-retained-semantic-child/v1',plan=p['semantic_context']['plan'],
        helper=old['helper'],kind='instruction',intent=guard['intent'],original_command=command,
        seconds=3300,memory_bytes=8589934592,automatic_retry=False),'failed review bootstrap changed')
    trace=Path(guard['log']['path']).read_text()
    U.require(trace.rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: source method copied formula changed')
        and 'in dialogue_review_context' in trace and 'operands.project' in trace,
        'failure was not the selected operand projection')
    step=W.step_directory(worker,'dialogue-review')
    U.require(set(q.name for q in step.iterdir())=={'intent.json','exception.json'},
        'review step already has prepared or uncertain effects')
    original=U.read(U.binding(step/'intent.json'));exception=U.read(U.binding(step/'exception.json'))
    U.require(original['step']=='dialogue-review' and original['previous']==value['checkpoint']
        and original['selection']==value['selection'] and original['lease']==asdict(C.decode(value['lease']))
        and original['initial']==U.binding(worker.run/'initial.json')
        and exception==dict(type='AdmissionError',message_sha256=hashlib.sha256(b'source method copied formula changed').hexdigest(),automatic_replay=False),
        'original failed review intent or exception differs')
    return step


def absent(U,W,worker,selection,tracker):
    task=W.task(worker,selection,tracker)
    first=W.request_saved(W.step_directory(worker,'dialogue-review').parent/'request.json')
    before,identity=W.producer.task_context(task)
    expected=W.executor.SemanticScope(identity['workbook'],identity['original']['sha256'],W.producer.DIALOGUE,'dialogue-review')
    U.require(first.prepared.scope==expected and first.prepared.ordinal==1 and first.prepared.limit==2,
        'original first review logical scope differs')
    model=W.executor.location(first.prepared,worker.campaign).parent/'attempt-02'
    paths=[task.run/'requests/dialogue-review/attempt-02',task.run/'dialogue/review-call-02.json',model]
    U.require(all(not q.exists() and not q.is_symlink()for q in paths),
        'second review prepared or submitted; explicit model reconciliation required')
    return task


def context(U,row,*,continued=False):
    p=U.read(row);extra={'semantic_context','failed_guard','unprepared_review'}
    U.require(set(p)==U.TAIL_FIELDS|extra and p['schema_version']==U.TAIL_SCHEMA
        and p['helper']==U.binding(U.__file__),'closed unprepared review tail required')
    chosen=p['semantic_context'];proof=p['unprepared_review']
    U.require(set(chosen)=={'helper','plan'} and set(proof)=={'reader','runtime','profile','process','proof'}
        and proof['reader']==U.binding(__file__),'closed review evidence reader required')
    old=U.read(chosen['plan'])
    U.require(old['helper']==chosen['helper'] and old['schema_version']==U.SCHEMA
        and old.get('retained_context',{}).get('kind')=='completed_disclosure'
        and p['fence']==old['fence']+1 and p['output']!=old['output']
        and all(p[k]==old[k]for k in ('fresh_plan','original_job','source','workbook','authorization'))
        and p['authorization']['sha256']==U.AUTHORIZATION_SHA,'unprepared review changed source authority')
    M=U.module('unprepared_review_parent',chosen['helper'])
    _,fresh,job,D,env,_,_,_,_,S,L=M.context(chosen['plan'],continued=True)
    stack=ExitStack();stack.callback(M.retained_manager(old,D,L).__exit__,None,None,None)
    try:
        C,I,_,W,_,config,root,controls=env
        checkpoint,value=D.successful(p['outcome'],None if continued else env)
        U.require(value['status']=='STEP_COMPLETE' and value['next_step']==p['next_step']=='dialogue-review'
            and value['publication_performed']is False,'actual completed second paraphraser required')
        worker,seen,_=I.selected(value['descriptor']);selection=I.codec.decode(value['selection']);lease=C.decode(value['lease'])
        U.require(seen==controls and worker.root==root and worker.run==Path(config['run_root'])/'instruction'
            and p['tracker']['path']==str(worker.context.tracker_root/(p['workbook']+'.json')),
            'unprepared review worker or tracker changed')
        original_step=stopped(U,p,old,env,checkpoint,value,worker)
        RT=U.module('unprepared_review_runtime',proof['runtime']);profile=RT.profile(proof['profile'])
        U.require(profile['schema_version']==RT.SCHEMA and not profile['patches'] and not profile['dependencies']
            and len(profile['symbols'])==1 and profile['symbols'][0]['module_name']=='scripts.batch.harbor_dialogue_operand_evidence'
            and profile['symbols'][0]['names']==['project'], 'only source operand projection may change')
        from scripts.batch import harbor_dialogue_operand_evidence as operands
        U.require(profile['symbols'][0]['original']==U.binding(operands.__file__), 'original operand reader differs')
        native=U.read(proof['proof'])
        U.require(native['status']=='PASS' and native['plan']==chosen['plan']
            and native['helper']==profile['symbols'][0]['selected']
            and native['models_dispatched']==native['task_mutations']==0,'full native review context proof required')
        output=Path(p['output']);operations=Path(config['run_root'])/'dispatcher-v2'/output.name
        U.require(output.is_absolute() and not output.is_relative_to(root) and p['operations']==str(operations),
            'isolated review continuation required')
        if continued:
            U.require(U.read(U.binding(output/'intent.json'))==dict(plan=row,prior_completed=p['outcome'],automatic_retry=False),
                'original review continuation intent required')
        else:
            I._prior(checkpoint,value['descriptor'])
            U.require(U.binding(p['tracker']['path'])==p['tracker'] and not output.exists(), 'review custody changed or continuation consumed')
            process=U.read(proof['process']);U.require(type(process['pid'])is int and process['pid']>1
                and not Path('/proc',str(process['pid'])).exists(),'prior worker still active')
            _,before,tracker=W.active(worker,selection,lease);S.validate(worker,before)
            task=absent(U,W,worker,selection,tracker)
            D.operation_namespace(config,p['operations'])
        stack.enter_context(RT.installed(proof['profile']))
        old_directory=W.step_directory
        def directory(current,phase):
            if current==worker and phase=='dialogue-review':return output/'review-step'
            return old_directory(current,phase)
        U.require(W._step.__globals__ is W.__dict__,'original review step directory binding changed')
        W.step_directory=directory;stack.callback(setattr,W,'step_directory',old_directory)
        D._semantic_tail_manager=stack
        return p,fresh,job,D,env,checkpoint,value
    except BaseException:stack.__exit__(*sys.exc_info());raise
