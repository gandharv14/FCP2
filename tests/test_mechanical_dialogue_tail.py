import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import mechanical_dialogue_tail as T

def saved(tmp,name,v):
 p=tmp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return T.binding(p)

def archive_case(tmp):
 oldtext='Original instruction';newtext='Original instruction with accepted dialogue';instruction=tmp/'stage/instruction.md';instruction.parent.mkdir();instruction.write_text(oldtext);old=T.binding(instruction);archive=tmp/'submitted/budget-01.snapshot';archive.parent.mkdir();archive.write_text(oldtext);ab=T.binding(archive);instruction.write_text(newtext);current=T.binding(instruction)
 request=saved(tmp,'request.json',{});executor_request=saved(tmp,'executor/request.json',{});claims=saved(tmp,'claims.json',{'claims':[1]});context=saved(tmp,'context.json',dict(kind='dialogue-writer',request=request,evidence=[old],budget_evidence=[request,ab]));submitted=saved(tmp,'submitted.json',dict(request=executor_request,budget_evidence=[request,ab]));saved(instruction.parent,'marker.json',dict(skill='additional-assumptions-dialogue',applied=True,review_passed=True,draft_passed=True,instruction_sha256=current['sha256']))
 cert=saved(tmp,'certificate.json',dict(evidence=[old,context,submitted,claims],author_calls=[dict(submitted=submitted,request=executor_request)],claims=claims));h=dict(context=context,submitted=submitted,original=old,archive=ab,current=current)
 K=SimpleNamespace(binding=T.binding,checked=T.read)
 def validate(row):
  v=K.checked(row);assert all(K.binding(r['path'])==r for r in v['evidence']);return v
 K.validate=validate;V=SimpleNamespace(APPLIED_MARKER='marker.json',rewrite_instruction=lambda text,stage,c:newtext if text==oldtext else'wrong')
 return K,cert,h,V

def test_only_original_certificate_reads_authentic_submitted_instruction(tmp_path):
 K,c,h,V=archive_case(tmp_path);before=K.binding
 with T.instruction_history(K,c,h,V):
  assert K.binding(h['original']['path'])==h['original']
  assert T.binding(h['current']['path'])==h['current']
  assert Path(h['current']['path']).read_text()=='Original instruction with accepted dialogue'
 assert K.binding is before

@pytest.mark.parametrize('change',['archive','current','context','submitted','other_evidence','rewrite','marker'])
def test_instruction_history_refuses_unproven_changes(tmp_path,change):
 K,c,h,V=archive_case(tmp_path)
 if change in ('archive','current'):Path(h[change]['path']).write_text('changed')
 elif change=='context':h['context']=saved(tmp_path,'foreign-context.json',{})
 elif change=='submitted':h['submitted']=saved(tmp_path,'foreign-submitted.json',{})
 elif change=='other_evidence':Path(T.read(c)['claims']['path']).write_text('{}')
 elif change=='rewrite':V.rewrite_instruction=lambda*a:'foreign'
 else:(Path(h['current']['path']).parent/'marker.json').write_text('{}')
 with pytest.raises((ValueError,KeyError)):
  with T.instruction_history(K,c,h,V):pass
 assert K.binding is T.binding

def test_archive_projection_restored_after_downstream_failure(tmp_path):
 K,c,h,V=archive_case(tmp_path)
 with pytest.raises(RuntimeError):
  with T.instruction_history(K,c,h,V):raise RuntimeError()
 assert K.binding is T.binding and K.checked is T.read

@pytest.mark.parametrize('seconds',[1350,3300])
def test_original_final_runtime_guard_duration_is_preserved(tmp_path,seconds):
 old={'output':str(tmp_path/'old')};root=tmp_path/'config';home=root/'dispatcher-v2'/'old'/'001';home.mkdir(parents=True)
 cfile=tmp_path/'C.py';ifile=tmp_path/'I.py';cfile.write_text('C');ifile.write_text('I');review={'descriptor':saved(tmp_path,'descriptor.json',{})};result=saved(tmp_path,'review-result.json',{})
 intent=saved(home,'intent.json',dict(action='step',arguments=dict(descriptor=review['descriptor'],checkpoint=result),code=T.binding(ifile),base_code=T.binding(cfile),controls={'actual':True},timeout_seconds=seconds,memory_limit_bytes=8589934592))
 log=home/'step.log';log.write_text('ValueError: mechanical retained bytes changed\n');resource=saved(home,'resource.json',dict(returncode=1,failure=None,intent=intent,log=T.binding(log)));out=saved(home,'outcome.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,resource=resource));p=dict(failed_outcome=out,review_result=result)
 C=SimpleNamespace(__file__=str(cfile),encode=lambda x:x);I=SimpleNamespace(__file__=str(ifile),guard_seconds=lambda action,args:1350);W=SimpleNamespace(step_directory=lambda w,step:tmp_path/'unused-final');env=(C,I,None,W,None,{'run_root':str(root)},None,{'actual':True})
 if seconds==1350:T.failure(p,old,env,object(),review)
 else:
  with pytest.raises(ValueError,match='guard detached'):T.failure(p,old,env,object(),review)
