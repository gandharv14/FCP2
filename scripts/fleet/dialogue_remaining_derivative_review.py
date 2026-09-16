"""The unused second independent review of a declared source-backed derivative.

Both author generations remain original. This consumes ordinary review 2/2,
never an extra review allowance and never a replacement author Call.
"""
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,sys,time,uuid
SCHEMA='fleet-dialogue-remaining-derivative-review/v1'
FIELDS={'schema_version','workbook','helper','stdlib','revision','semantic_context','source','golden','outcome','result','descriptor','blocked','tracker','claims','draft','candidate','selection','proof','home','owner','fence','env_file','checker'}
GUIDANCE='\nThis is the original remaining independent review, round 2 of 2. The candidate is a separately declared deterministic source-backed derivative of author generation 2, not a new author response. Its source_revision evidence proves a text transformation, not semantic approval. Independently assess every claim and every final senior turn, including all source formulas, calculations, signs, scope, leakage, coverage and naturalness requirements. Return round: 2. Do not waive any requirement.'

def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute() and p.is_file() and not any(q.is_symlink()for q in(p,*p.parents)),'canonical derivative evidence required')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'} and binding(row['path'])==row,'remaining derivative evidence changed');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'selected remaining derivative reader changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def plan(row):
 p=read(row);require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed remaining derivative plan required')
 require(set(p['semantic_context'])=={'helper','plan'} and set(p['checker'])=={'original','selected','proof'},'closed context and checker selection required')
 return p

def authoring_context(p):
 """Authenticate the completed semantic tail and its actual mechanical hold."""
 old=read(p['semantic_context']['plan']);require(old['helper']==p['semantic_context']['helper']
  and old['schema_version']=='fleet-completed-instruction-continuation/v1'
  and old['source']==p['source'] and old['workbook']==p['workbook'] and 'semantic_context'in old,
  'remaining derivative lost its original semantic tail')
 M=module('remaining_derivative_tail',old['helper']);loaded=M.tail_context(p['semantic_context']['plan'],continued=True)
 _,fresh,job,D,env,_,_=loaded;stack=ExitStack();stack.callback(D._semantic_tail_manager.__exit__,None,None,None)
 try:
  C,I,_,W,_,config,root,controls=env
  outcome=read(p['outcome']);checkpoint,value=D.successful(p['outcome'])
  guard=read(outcome['resource']);intent=read(guard['intent']);blocked=read(p['blocked'])
  require(checkpoint==p['result'] and value['status']=='DIALOGUE_REPAIR_REQUIRED'
   and value['next_step']=='dialogue-paraphraser' and value['checkpoint']==p['blocked']
   and value['descriptor']==p['descriptor'] and value['publication_performed']is False,
   'actual completed second paraphraser mechanical hold required')
  require(guard['returncode']==0 and guard['failure']is None and intent['action']=='step'
   and intent['code']==binding(I.__file__) and intent['base_code']==binding(C.__file__)
   and intent['controls']==C.encode(controls) and intent['arguments']['descriptor']==p['descriptor']
   and value['evidence'][0]==intent['arguments']['checkpoint']
   and intent['timeout_seconds']==I.guard_seconds('step',intent['arguments']) and intent['memory_limit_bytes']==8589934592,
   'original mechanical guard differs')
  directory=Path(p['outcome']['path']).parent;side=read(binding(directory/'semantic-bootstrap.json'))
  require(side['plan']==p['semantic_context']['plan'] and side['helper']==old['helper']
   and side['intent']==guard['intent'] and side['automatic_retry']is False,'mechanical guard lost its retained context')
  worker,observed,_=I.selected(p['descriptor']);selection=I.codec.decode(value['selection']);lease=C.decode(value['lease'])
  require(observed==controls and worker.root==root and worker.dialogue_semantic_retry is not None
   and worker.run==Path(config['run_root'])/'instruction' and selection.content.workbook==p['workbook']
   and selection.content.bindings['original_raw_source']==p['source'] and selection.content.bindings['source_file']==p['golden']
   and blocked['selection']==value['selection'] and blocked['lease']==asdict(lease)
   and p['blocked']==binding(W.step_directory(worker,'dialogue-paraphraser')/'blocked.json'),
   'second author hold lost exact source or lease')
  return fresh,job,D,env,worker,selection,lease,stack
 except BaseException:stack.__exit__(*sys.exc_info());raise

def ordinary_review(E,call,campaign,scope,ordinal,policy):
 p=read(binding(Path(call.submitted['path']).parent/'plan.json'))
 require(p['scope']==scope and p['ordinal']==ordinal and p['limit']==2 and p['policy']==asdict(policy)
  and p['response_mode']=='json_object' and all(p.get(k)is None for k in('physical_repair','audit_context','review_reservation')),
  'original ordinary review scope or budget differs')
 prepared=E.PreparedCall(p['request'],policy,E.SemanticScope(**scope),ordinal,2,p['attempt_id'],tuple(p['budget_evidence']))
 require(binding(E.location(prepared,campaign)/'plan.json')==binding(Path(call.submitted['path']).parent/'plan.json'),'review executor namespace differs')
 observed=E.inspect(prepared,campaign);require(observed.status=='completed' and observed.call==call,'review is not exactly completed')
 return prepared,observed

def material(p,*,root,task_dir,policy,campaign):
 """Historical proof uses original author text and separately derives the candidate."""
 from scripts.batch import harbor_dialogue_authoring as A,harbor_instruction_producer as P,harbor_model_executor as E,harbor_dialogue_operand_evidence as operands
 from scripts.batch.harbor_model_review_provenance import Call,validate_call,require_independent
 for key in ('source','golden','draft','candidate','revision','selection','proof'):
  require(binding(p[key]['path'])==p[key],'bound derivative input changed')
 claims=read(p['claims']);directory=Path(p['claims']['path']).parent
 require(p['draft']==binding(directory/'round-02/draft.md'),'original generation-two draft required')
 writers=[];pairs=[]
 for n in(1,2):
  pair=tuple(Call(**read(binding(directory/(kind+'-call-%02d.json'%n))))for kind in('writer','paraphraser'))
  proved=A.verify_pair(pair,policy=policy,claims=claims,draft=(directory/('round-%02d/draft.md'%n)).read_text(),round=n,root=root,workbook=p['workbook'],original_sha256=p['source']['sha256'])
  writers.extend(proved);pairs.append(pair)
 scope=dict(workbook_id=p['workbook'],original_sha256=p['source']['sha256'],lane=P.DIALOGUE,budget_id='dialogue-review')
 first=Call(**read(binding(directory/'review-call-01.json')));prepared,observed=ordinary_review(E,first,campaign,scope,1,policy)
 prior=validate_call(first,read(first.request),policy=policy);require_independent(prior,writers[:2])
 base_prompt=P.skill_prompt(root,'additional-assumptions-dialogue','You are reviewing notes from a deal-team conversation')
 body=json.loads(read(first.request)['messages'][1]['content'])
 require(read(first.request)['messages'][0]==dict(role='system',content=base_prompt)
  and len(read(first.request)['messages'])==2 and body=={'claims.json':claims,'draft.md':(directory/'round-01/draft.md').read_text(),'round':1}
  and prior['review']==read(binding(directory/'round-01/review.json')) and prior['review']['round']==1 and prior['review']['passed']is False,
  'first rejected review changed or was fabricated')
 revision=module('remaining_source_revision',p['revision']);fixed,proof=revision.repair(Path(p['draft']['path']).read_text(),claims,p['golden']['path'],read(p['selection']))
 require(fixed==Path(p['candidate']['path']).read_text() and proof==read(p['proof']),'source derivative differs')
 checker=module('remaining_selected_checker',p['checker']['selected']);V=P.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
 require(binding(root/'.cursor/skills/additional-assumptions-dialogue/scripts/aa_lib.py')==p['checker']['original'],'original checker changed')
 native=read(p['checker']['proof']);require(native['mechanics']['passed']is True and native['candidate']==p['candidate'] and native['proof']==p['proof']
  and native['validator']==p['checker']['selected'] and native['models']==native['task_mutations']==0,'actual full candidate/checker proof required')
 original_coverage=V.clause_covered
 try:
  V.clause_covered=checker.clause_covered
  mechanics=V.check_draft(fixed,claims,Path(task_dir),source_workbook=p['golden'])
 finally:V.clause_covered=original_coverage
 require(mechanics['passed']is True and not mechanics['faults'],'candidate mechanics failed')
 projection=operands.project(claims,fixed,source=p['golden'],answer_key=binding(Path(task_dir)/'tests/answer_key.json'),task_dir=Path(task_dir),root=root)
 body={'claims.json':claims,'draft.md':fixed,'round':2,'operand_evidence':projection,
  'source_revision':dict(proof=proof,original_review_rounds_consumed=1,derived_from_author_round=2,generated_author_calls_added=0,original_authored_draft=p['draft'],prior_review=prior['review'])}
 request=dict(model=policy.model,reasoning_effort=policy.reasoning_effort,messages=[dict(role='system',content=base_prompt+operands.GUIDANCE+GUIDANCE),dict(role='user',content=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False))])
 return E,V,claims,prepared,observed,prior,writers,pairs[1],proof,mechanics,request

def load(row,*,completed=False):
 p=plan(row);module('remaining_stdlib',p['stdlib']).preload_inspect();fresh,job,D,env,w,s,l,manager=authoring_context(p)
 try:
  require(p['tracker']==binding(w.context.tracker_root/(p['workbook']+'.json')),'current review tracker differs')
  _,before,tracker=env[3].active(w,s,l);task=env[3].task(w,s,tracker)
  require(p['claims']==binding(task.run/'dialogue/claims.json'),'original task claims differ')
  values=material(p,root=env[6],task_dir=task.stage,policy=task.policy,campaign=w.campaign)
  E=values[0];first=values[3];second=E.location(first,w.campaign).parent/'attempt-02'
  paths=[task.run/'requests/dialogue-review/attempt-02',task.run/'dialogue/review-call-02.json',Path(p['claims']['path']).parent/'round-02/review.json']
  require(all(not q.exists() and not q.is_symlink()for q in paths),'second producer review already exists')
  if not completed:require(not second.exists() and not second.is_symlink() and not Path(p['home']).exists(),'remaining review was already attempted')
  return p,w,task,values,manager
 except BaseException:manager.__exit__(*sys.exc_info());raise

def probe(row):
 p,w,task,values,manager=load(row)
 try:return dict(status='REMAINING_DERIVATIVE_VALIDATED_NOT_STARTED',plan=row,next_ordinal=2,limit=2,original_reviews=1,author_generations=2,claims=len(values[2]['claims']),mechanics=values[-2],request_bytes=len(values[0].canonical(values[-1])),models_dispatched=0,task_mutations=0,qualified=False)
 finally:manager.__exit__(None,None,None)

def run(row):
 p,w,task,values,manager=load(row)
 try:
  E,V,claims,first,prior,prior_proof,writers,pair,proof,mechanics,request=values
  from scripts.fleet import model_gate
  client,owner,_=model_gate.context();require(owner['workbook']==p['workbook'] and owner['owner']==p['owner'] and owner['fence']==p['fence'],'remaining review owner differs');client.call('heartbeat',**owner)
  home=E.safe(p['home']);home.mkdir(parents=True);E.save(home/'intent.json',dict(schema_version=SCHEMA,plan=row,automatic_retry=False,task_mutations=0));E.save(home/'request.json',request)
  prepared=E.PreparedCall(binding(home/'request.json'),task.policy,first.scope,2,2,str(uuid.uuid4()),tuple([row,p['proof'],p['draft'],p['candidate'],prior.outcome]))
  E.save(home/'prepared.json',asdict(prepared))
  from xl_task_build import read_env_key
  os.environ['HARBOR_MODEL_SLOTS']=str(w.campaign.model_slots);os.environ['HARBOR_MODEL_SLOT_ROOT']=str(w.campaign.slot_root)
  observed=E.execute(prepared,w.campaign,credential=read_env_key(Path(p['env_file'])))
  require(observed.status=='completed' and observed.call is not None,'remaining review uncertain; never replay')
  from scripts.batch.harbor_model_review_provenance import validate_call,require_independent
  after=material(p,root=Path(w.root),task_dir=task.stage,policy=task.policy,campaign=w.campaign)
  require(after[-1]==request and after[-3]==proof and after[-2]==mechanics,'source derivative changed during review')
  actual=validate_call(observed.call,request,policy=task.policy);require_independent(actual,[*writers,prior_proof]);review=actual['review'];require(review.get('round')==2,'remaining reviewer returned wrong ordinal')
  faults=V.review_faults(review,claims,draft_text=Path(p['candidate']['path']).read_text());status='SOURCE_DERIVATIVE_REVIEW_PASSED_NOT_ADOPTED'if not faults and V.review_passed(review,claims)else'SOURCE_DERIVATIVE_REVIEW_REJECTED'
  E.save(home/'result.json',dict(status=status,plan=row,call=asdict(observed.call),outcome=observed.outcome,review=review,faults=faults,mechanics=mechanics,prior_consumed=1,cumulative_consumed=2,task_adopted=False,qualified=False))
  return binding(home/'result.json')
 finally:manager.__exit__(*sys.exc_info())

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);v=a.parse_args();row=dict(path=v.plan,sha256=v.sha256);print(json.dumps((probe if v.action=='probe'else run)(row)))


def validate_review(profile,result,*,root,claims,draft,workbook,original_source,golden,policy,task_dir):
 """Pure historical validation; no live lease or additional allowance is inferred."""
 p=plan(profile);r=read(result)
 require(p['workbook']==workbook and p['source']==original_source and p['golden']==golden
  and p['claims']==claims and p['candidate']==draft,'remaining derivative historical selection changed')
 require(r['plan']==profile and result==binding(Path(p['home'])/'result.json')
  and r['status']in('SOURCE_DERIVATIVE_REVIEW_REJECTED','SOURCE_DERIVATIVE_REVIEW_PASSED_NOT_ADOPTED')
  and r['prior_consumed']==1 and r['cumulative_consumed']==2 and r['task_adopted']is False and r['qualified']is False
  and 'reservation'not in r,'actual ordinary remaining review required')
 from scripts.batch import harbor_model_executor as E
 from scripts.batch.harbor_model_review_provenance import Call,Policy,validate_call,require_independent
 first=Call(**read(binding(Path(claims['path']).parent/'review-call-01.json')))
 original=read(binding(Path(first.submitted['path']).parent/'plan.json'))
 campaign=E.Campaign(Path(original['campaign_root']),Path(original['slot_root']),original['model_slots'])
 values=material(p,root=root,task_dir=task_dir,policy=policy,campaign=campaign)
 E,V,claim_values,prior_call,prior,prior_proof,writers,pair,source_proof,mechanics,request=values
 saved=read(binding(Path(p['home'])/'prepared.json'))
 require(saved['ordinal']==saved['limit']==2 and saved['policy']==asdict(policy) and saved['scope']==asdict(prior_call.scope)
  and all(saved.get(k)is None for k in('physical_repair','audit_context','review_reservation'))
  and saved['budget_evidence']==[profile,p['proof'],p['draft'],p['candidate'],prior.outcome],
  'remaining review lost original budget or derivative provenance')
 prepared=E.PreparedCall(saved['request'],Policy(**saved['policy']),E.SemanticScope(**saved['scope']),2,2,saved['attempt_id'],tuple(saved['budget_evidence']))
 require(saved['request']==binding(Path(p['home'])/'request.json') and read(saved['request'])==request,'remaining review request changed')
 observed=E.inspect(prepared,campaign);call=Call(**r['call'])
 require(observed.status=='completed' and observed.call==call and observed.outcome==r['outcome'],'remaining review is uncertain or foreign')
 ordinary_review(E,call,campaign,asdict(prior_call.scope),2,policy)
 actual=validate_call(call,request,policy=policy);require_independent(actual,[*writers,prior_proof])
 require(actual['review']==r['review'] and actual['review']['round']==2,'remaining review result differs')
 faults=V.review_faults(actual['review'],claim_values,draft_text=Path(draft['path']).read_text())
 require(r['faults']==faults and r['mechanics']==mechanics,'recorded review findings or mechanics changed')
 return dict(schema_version='harbor-reviewed-source-derivative-history/v1',profile=profile,result=result,claims=len(claim_values['claims']),round=2,author_generation=2,provenance=actual,writer_calls=pair,source_proof=source_proof,original_reviews=[prior.outcome],reservation=None,evidence=[profile,result,p['source'],p['golden'],p['claims'],p['draft'],p['candidate'],p['proof'],p['selection'],p['revision'],p['helper'],*p['checker'].values(),prior.outcome,r['outcome']])
