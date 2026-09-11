"""Validate exact retained, root-bound Labelbox call journals without networking."""
from dataclasses import dataclass
import json
import uuid
from scripts.batch.harbor_admission import read,require
from scripts.batch.harbor_tracker_lifecycle import checked_record


@dataclass(frozen=True)
class Call:
    request: dict
    submitted: dict
    response: dict
    completed: dict
    parsed: dict | None = None


@dataclass(frozen=True)
class Policy:
    project_id: str
    model: str = 'openai/gpt-5.6-sol'
    reasoning_effort: str | None = 'high'
    endpoint: str = 'https://litellm.labelbox.com/chat/completions'


def validate_call(call,expected_request,*,policy,json_response=True,audit_context=None,original_renderer_code=None):
    """Expected payload is independently reconstructed by the owning adapter.

    This validates retained transport evidence, not a provider signature. Root
    must supply the immutable hashes captured from its actual authorized call;
    a handoff cannot nominate new hashes and claim an observed network action.
    """
    require(isinstance(call,Call) and isinstance(policy,Policy),'root-selected call and policy required')
    for item in (call.request,call.submitted,call.response,call.completed):checked_record(item)
    request=read(call.request);submitted=read(call.submitted);response=read(call.response);completed=read(call.completed)
    require(request==expected_request,'request differs from independently reconstructed context')
    if audit_context is not None:
        from scripts.batch.harbor_audit_protocol import validate
        require(not json_response,'auditor response must remain text')
        validate(audit_context,request,policy,original_renderer_code=original_renderer_code)
    else:
        require(original_renderer_code is None,'renderer upgrade applies only to owning audit calls')
        require(request.get('model')==policy.model and request.get('reasoning_effort')==policy.reasoning_effort,'request model/configuration mismatch')
        require(set(request)=={'model','reasoning_effort','messages'},'unsupported persistent request state or fields')
        messages=request['messages']
        require(isinstance(messages,list) and len(messages)==2 and [m.get('role') for m in messages]==['system','user'],'independent stateless review request required')
        require(all(set(m)=={'role','content'} and isinstance(m['content'],str) and m['content'].strip() for m in messages),'invalid review message content')
    require(all(submitted.get(k)==getattr(policy,k) for k in ('model','reasoning_effort','endpoint','project_id')),'submitted call destination/configuration changed')
    require(submitted.get('semantic_budget_reset') is False,'model budget reset is not evidence')
    if 'response_mode' in submitted:
        mode='json_object' if json_response else 'text'
        require(submitted['response_mode']==mode and completed.get('response_mode')==mode,'root-selected response mode differs')
    require(str(uuid.UUID(submitted['attempt_id']))==submitted['attempt_id'],'invalid attempt identity')
    if 'request' in submitted:
        checked_record(submitted['request']);require(submitted['request']['sha256']==call.request['sha256'],'submitted request bytes differ')
    else:require(submitted.get('request_sha256')==call.request['sha256'],'submitted request hash differs')
    if 'response' in completed:
        checked_record(completed['response']);require(completed['response']==call.response,'completed response binding differs')
    else:require(completed.get('response_sha256')==call.response['sha256'],'completed response hash differs')
    submitted_at=submitted.get('submitted_at_ns');completed_at=completed.get('completed_at_ns')
    require(type(submitted_at) is int and type(completed_at) is int and 0<submitted_at<=completed_at,'call chronology invalid')
    require(response.get('model')==policy.model and isinstance(response.get('id'),str) and response['id'].strip(),'response model/identity mismatch')
    require(response.get('object')=='chat.completion','not a completed chat response')
    require(type(response.get('created')) is int and submitted_at//10**9-60 <= response['created'] <= completed_at//10**9+60,'response chronology inconsistent')
    choices=response.get('choices');require(isinstance(choices,list) and len(choices)==1,'exactly one completed model choice required')
    choice=choices[0];message=choice.get('message') or {}
    require(choice.get('finish_reason')=='stop' and message.get('role')=='assistant' and not message.get('tool_calls'),'incomplete/tool call response cannot be semantic review')
    require(not message.get('refusal') and isinstance(message.get('content'),str),'missing/refused model text')
    parsed=json.loads(message['content']) if json_response else message['content']
    require(isinstance(parsed,dict) if json_response else bool(parsed.strip()),'missing or invalid model content')
    if call.parsed:
        checked_record(call.parsed)
        saved=read(call.parsed) if json_response else __import__('pathlib').Path(call.parsed['path']).read_bytes().decode('utf-8')
        require(saved==parsed,'retained parsed review is not the actual model response')
        if 'review_sha256' in completed:require(completed['review_sha256']==call.parsed['sha256'],'completed parsed review changed')
    return {'response_id':response['id'],'attempt_id':submitted['attempt_id'],'review':parsed,'request_sha256':call.request['sha256'],'response_sha256':call.response['sha256']}


def require_independent(review,other_calls):
    require(all(review['response_id']!=other['response_id'] and review['attempt_id']!=other['attempt_id'] for other in other_calls),'review reused a writer or earlier reviewer invocation')
    return True
