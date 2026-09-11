"""Prepare/execute exact public-profile requests using retained root captures.

Never fetches pages or generates capture observations. The default CLI path is
read-only model inspection; --execute is an explicit root dispatch operation.
No tracker changes, source transformations, manual review acceptance or retries
outside the installed executor are provided.
"""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
from types import SimpleNamespace
import uuid

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.cursor/skills/profile-mcp-sources/SKILL.md').is_file())
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.batch import harbor_model_executor as executor
from scripts.batch import harbor_profile_readiness as profiles
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_model_review_provenance import Policy,Call,validate_call
from scripts.batch.harbor_tracker_lifecycle import checked_record

SCHEMA='harbor-profile-production/v1'
INPUT_SCHEMA='harbor-profile-production-input/v1'


def save(path,value):executor.save(path,value);return record(path)
def digest(value):return hashlib.sha256(executor.canonical(value)).hexdigest()
def retained(path,value):
    if path.exists():require(path.read_bytes()==executor.canonical(value),'retained producer artifact differs')
    else:executor.save(path,value)
    return record(path)


def input_context(binding):
    value=read(binding)
    require(set(value)=={'schema_version','workbook_id','original_source','phase1','inventory','captures','policy','campaign'},'profile input schema changed')
    require(value['schema_version']==INPUT_SCHEMA,'unsupported profile input schema')
    workbook=value['workbook_id'];require(isinstance(workbook,str) and len(workbook)==4 and workbook.isdigit(),'four-digit workbook ID required')
    for key in ('original_source','phase1','inventory','captures'):checked_record(value[key])
    spec=read(value['phase1']);run=Path(value['phase1']['path']).parent
    require(run.name==workbook+'-variable-sources' and spec.get('environment_id')=='fcp-'+workbook,'phase1 is not for the bound workbook/run')
    require(isinstance(spec.get('variables'),list),'phase1 variables missing')
    policy=Policy(**value['policy']);campaign=executor.Campaign(Path(value['campaign']['root']),Path(value['campaign']['slot_root']),value['campaign']['model_slots'])
    require(policy.endpoint=='https://litellm.labelbox.com/chat/completions' and policy.model=='openai/gpt-5.6-sol' and policy.reasoning_effort=='high','unsupported configured model policy')
    validator,_=profiles.modules(ROOT)
    forbidden=validator.forbidden_values([Path(value['phase1']['path']),Path(value['inventory']['path'])])
    worklist=profiles.worklist(spec,forbidden,root=ROOT)
    captures=read(value['captures']);require(set(captures)=={'schema_version','sources'} and captures['schema_version']=='harbor-profile-captures/v1','invalid root capture inventory')
    sources=captures['sources'];require(isinstance(sources,list),'capture source list required')
    require(all(isinstance(row,dict) and set(row)=={'canonical_url','captures'} for row in sources),'invalid capture source entry')
    urls=[row['canonical_url'] for row in sources]
    require(len(urls)==len(set(urls)) and set(urls)=={r['canonical_url'] for r in worklist},'actual captures do not exactly match redacted worklist')
    return value,spec,run,policy,campaign,forbidden,worklist,{row['canonical_url']:row['captures'] for row in sources}


def capture_failure(records):
    """Classify observed access failure without copying/extracting its body."""
    for binding in records:
        value=read(binding)
        if value.get('status') in (401,403):return 'http_'+str(value['status'])
        if value.get('status') is None:return 'unreachable'
        if value.get('content_type') not in {'text/html','text/plain'}:return 'unsupported_content'
        body=value.get('body')
        if isinstance(body,dict):
            checked_record(body);text=Path(body['path']).read_text().casefold()
            if any(x in text for x in ('captcha','bot challenge')):return 'bot_challenge'
            if any(x in text for x in ('paywall','subscriber-only')):return 'paywall'
            if any(x in text for x in ('sign in','log in','password required','single sign-on')):return 'auth_login_sso_password'
    return 'invalid_capture_or_redaction'


def safe_capture_failure(records):
    try:return capture_failure(records)
    except (ValueError,OSError,UnicodeError,TypeError,KeyError):return 'invalid_capture_or_redaction'


def code_bindings():
    validator,assembler=profiles.modules(ROOT)
    from scripts.batch import harbor_model_review_provenance
    return [record(Path(path)) for path in (
        __file__,profiles.__file__,executor.__file__,harbor_model_review_provenance.__file__,
        validator.__file__,assembler.__file__,
        ROOT/'.cursor/skills/harbor-source-profiler/SKILL.md',
        ROOT/'.cursor/skills/profile-mcp-sources/SKILL.md',
        ROOT/'.cursor/skills/profile-mcp-sources/SOURCE_PROFILES.md',
        ROOT/'.cursor/skills/profile-mcp-sources/source_profiles.schema.json')]


def prepare(input_binding,output):
    value,spec,run,policy,campaign,forbidden,worklist,captures=input_context(input_binding)
    output=executor.safe(output);output.mkdir(exist_ok=False)
    worklist_binding=save(output/'worklist.json',worklist)
    pages={};blocked=[]
    for source in worklist:
        url=source['canonical_url']
        try:pages[url]=profiles.captured_pages(captures[url],url,forbidden,root=ROOT)[0]
        except (ValueError,OSError,UnicodeError) as exc:
            blocked.append({'canonical_url':url,'code':safe_capture_failure(captures[url]),'error_type':type(exc).__name__,'error_sha256':hashlib.sha256(str(exc).encode()).hexdigest(),'automatic_retry':False})
    jobs=[]
    if not blocked:
        for source in worklist:
            key=hashlib.sha256(source['canonical_url'].encode()).hexdigest()[:24]
            folder=output/key;folder.mkdir()
            request=save(folder/'producer-request.json',profiles.request('producer',{'source':source,'public_pages':pages[source['canonical_url']]},policy,root=ROOT))
            jobs.append({'source':source,'key':key,'captures':captures[source['canonical_url']],'producer_request':request,'producer_attempt_id':str(uuid.uuid4()),'reviewer_attempt_id':str(uuid.uuid4())})
    code=code_bindings()
    plan={'schema_version':SCHEMA,'input':input_binding,'worklist':worklist_binding,'code':code,'jobs':jobs,'blocked':blocked,'status':'STOPPED' if blocked else 'PREPARED','model_dispatches':0,'automatic_semantic_retry':False}
    return save(output/'plan.json',plan)


def checked_plan(binding):
    plan=read(binding);require(set(plan)=={'schema_version','input','worklist','code','jobs','blocked','status','model_dispatches','automatic_semantic_retry'} and plan['schema_version']==SCHEMA,'unsupported profile plan')
    require(plan['model_dispatches']==0 and plan['automatic_semantic_retry'] is False,'preparation cannot claim consumed calls or automatic retries')
    require(plan['code']==code_bindings(),'profile generator/validator/executor code changed')
    value,spec,run,policy,campaign,forbidden,worklist,captures=input_context(plan['input'])
    require(read(plan['worklist'])==worklist,'redacted worklist changed')
    require(plan['status']==('STOPPED' if plan['blocked'] else 'PREPARED'),'plan status changed')
    if plan['blocked']:
        require(plan['jobs']==[],'blocked plan must have no dispatchable jobs')
        return plan,(value,spec,run,policy,campaign,forbidden,worklist,captures)
    require([j['source'] for j in plan['jobs']]==worklist,'planned sources changed or omitted')
    for job in plan['jobs']:
        url=job['source']['canonical_url'];require(job['captures']==captures[url],'actual capture bindings changed')
        pages,_=profiles.captured_pages(captures[url],url,forbidden,root=ROOT)
        require(read(job['producer_request'])==profiles.request('producer',{'source':job['source'],'public_pages':pages},policy,root=ROOT),'producer request differs from exact public context')
        require(job['key']==hashlib.sha256(url.encode()).hexdigest()[:24],'source scope changed')
        for name in ('producer_attempt_id','reviewer_attempt_id'):require(str(uuid.UUID(job[name]))==job[name],'invalid retained attempt identity')
    return plan,(value,spec,run,policy,campaign,forbidden,worklist,captures)


def prepared(plan_binding,value,policy,job,role,request):
    # Stable original-workbook/source/role scope prevents a new output directory
    # from acquiring a fresh first-attempt budget in the same campaign.
    scope=executor.SemanticScope(value['workbook_id'],value['original_source']['sha256'],'harbor-source-profiler','profile-'+job['key']+'-'+role)
    return executor.PreparedCall(request,policy,scope,1,2 if role=='producer' else 1,job[role+'_attempt_id'],(plan_binding,value['phase1'],value['inventory'],value['captures']),response_mode='json_object')


def call(prepared,campaign,*,dispatch,credential):
    if executor.location(prepared,campaign).exists():return executor.inspect(prepared,campaign)
    if not dispatch:return None
    require(isinstance(credential,str) and credential,'root runtime credential required for explicit execution')
    return executor.execute(prepared,campaign,credential=credential)


def producer_fragment(call,request,source,forbidden,policy):
    data=validate_call(call,request,policy=policy)['review'];validator,assembler=profiles.modules(ROOT)
    require(data.get('review')=={'status':'pending','reviewer':None,'reviewed_at':None,'notes':None},'producer cannot accept its own profile')
    normalized=assembler.validate_fragment(Path(call.response['path']),data)
    errors=[];validator.validate_profile(normalized,0,errors,forbidden)
    require(not errors,'producer profile schema or redaction failed')
    require(normalized['canonical_url']==source['canonical_url'] and normalized['source_name']==source['source_name'],'producer source identity differs')
    return data


def mapping(spec,document,phase1_hash,profiles_hash):
    validator,_=profiles.modules(ROOT)
    from mcp_env.build import source_id,slug
    accepted={p['canonical_url']:p for p in document['profiles'] if p['status']=='profiled' and p['review']['status']=='accepted'}
    rows={}
    for variable in spec['variables']:
        for source in variable['sources']:
            url=validator.canonicalize_url(source['url']);profile=accepted.get(url)
            if profile is None:continue
            names=profile['dataset_names'] or [profile['source_name']+' versioned indicators']
            require(len(names)==1,'ambiguous public dataset mapping requires independent decision')
            sid=source_id(source);row={'normalized_source_id':sid,'canonical_url':url,'profile_id':profile['source_id'],'dataset_key':slug(names[0])}
            require(sid not in rows or rows[sid]==row,'ambiguous normalized source identity')
            rows[sid]=row
    return {'schema_version':'harbor-source-profile-mapping/v1','phase_1_spec_sha256':phase1_hash,'profiles_sha256':profiles_hash,'mappings':[rows[key] for key in sorted(rows)]}


def run(plan_binding,*,dispatch=False,credential=None):
    plan,context=checked_plan(plan_binding);value,spec,run_dir,policy,campaign,forbidden,worklist,captures=context
    root=Path(plan_binding['path']).parent
    if plan['blocked']:return {'status':'STOPPED','blocking':plan['blocked'],'model_dispatches':0,'evidence':[plan_binding]}
    calls=[];fragments=[];outcomes=[]
    for job in plan['jobs']:
        folder=root/job['key'];prod=prepared(plan_binding,value,policy,job,'producer',job['producer_request'])
        result=call(prod,campaign,dispatch=dispatch,credential=credential)
        if result is None:return {'status':'PENDING_PRODUCER','source':job['key'],'prepared':asdict(prod),'evidence':[plan_binding]}
        outcomes.append(result.outcome)
        if result.status!='completed':return {'status':'STOPPED','code':'producer_'+result.status,'evidence':outcomes,'automatic_retry':False}
        try:fragment=producer_fragment(result.call,read(job['producer_request']),job['source'],forbidden,policy)
        except (Exception,SystemExit) as exc:return stopped(root,'producer_fragment_invalid',exc,plan_binding,outcomes)
        pages,_=profiles.captured_pages(job['captures'],job['source']['canonical_url'],forbidden,root=ROOT)
        reviewer_request=retained(folder/'reviewer-request.json',profiles.request('reviewer',{'fragment':fragment,'public_pages':pages},policy,root=ROOT))
        rev=prepared(plan_binding,value,policy,job,'reviewer',reviewer_request)
        reviewed=call(rev,campaign,dispatch=dispatch,credential=credential)
        if reviewed is None:return {'status':'PENDING_REVIEWER','source':job['key'],'prepared':asdict(rev),'evidence':outcomes}
        outcomes.append(reviewed.outcome)
        if reviewed.status!='completed':return {'status':'STOPPED','code':'reviewer_'+reviewed.status,'evidence':outcomes,'automatic_retry':False}
        combined=profiles.ProfileCall(job['source']['canonical_url'],result.call,reviewed.call,tuple(job['captures']))
        try:checked=profiles.accepted_profile(combined,job['source'],forbidden,policy=policy,root=ROOT)
        except (Exception,SystemExit) as exc:return stopped(root,'independent_profile_invalid',exc,plan_binding,outcomes)
        if not checked['accepted']:return stopped(root,'independent_profile_rejected',ValueError('independent reviewer rejected profile'),plan_binding,outcomes)
        # Save the actual replacement fragment, never a manually repaired object.
        fragments.append(retained(folder/'reviewed-fragment.json',validate_call(reviewed.call,read(reviewer_request),policy=policy)['review']))
        calls.append(combined)
    validator,assembler=profiles.modules(ROOT)
    candidate=root/'assembled-source-profiles.json'
    if not candidate.exists():
        assembler.cmd_init(SimpleNamespace(out=candidate,force=False,created_at=None,spec=Path(value['phase1']['path']),inventory=Path(value['inventory']['path'])))
    # An interrupted init/merge resumes only an existing valid subset of these
    # exact independently accepted profiles. Any unrelated content stays closed.
    expected=[profiles.accepted_profile(c,j['source'],forbidden,policy=policy,root=ROOT)['profile'] for c,j in zip(calls,plan['jobs'])]
    existing=read(record(candidate))
    require(not validator.validate_document(existing,forbidden=forbidden,inventory_path=Path(value['inventory']['path']),spec_path=Path(value['phase1']['path'])),'partial assembler envelope is invalid')
    require(all(p in expected for p in existing['profiles']),'partial assembler contains unrelated profiles')
    if fragments:
        require(assembler.cmd_merge(SimpleNamespace(doc=candidate,fragment=[Path(f['path']) for f in fragments]))==0,'owning profile assembler rejected fragments')
    expected=[profiles.accepted_profile(c,j['source'],forbidden,policy=policy,root=ROOT)['profile'] for c,j in zip(calls,plan['jobs'])]
    require(read(record(candidate))['profiles']==sorted(expected,key=lambda p:p['source_id']),'assembled profile catalog differs from actual accepted fragments')
    try:
        document=read(record(candidate));map_value=mapping(spec,document,value['phase1']['sha256'],record(candidate)['sha256'])
        mapping_record=retained(root/'assembled-mapping.json',map_value)
        selection=profiles.ProfileSelection(value['phase1'],value['inventory'],record(candidate),mapping_record,tuple(calls),policy)
        validated=profiles.validate_profiles(selection,root=ROOT)
        require(validated['confidence']=='high','profile confidence is not high')
    except (Exception,SystemExit) as exc:return stopped(root,'profile_mapping_or_final_validation',exc,plan_binding,outcomes)
    checked_plan(plan_binding)
    final_profiles=run_dir/'source_profiles.json';final_mapping=run_dir/'source_profile_mapping.json'
    for source,destination in ((candidate,final_profiles),(Path(mapping_record['path']),final_mapping)):
        raw=source.read_bytes()
        if destination.exists():require(destination.read_bytes()==raw,'existing profile output differs; explicit archival required')
        else:executor.retain(destination,raw)
    final=profiles.ProfileSelection(value['phase1'],value['inventory'],record(final_profiles),record(final_mapping),tuple(calls),policy)
    final_proof=profiles.validate_profiles(final,root=ROOT)
    receipt={'schema_version':SCHEMA,'status':'COMPLETE','plan':plan_binding,'selection':asdict(final),'outcomes':outcomes,'model_calls':len(calls)*2,'confidence':final_proof['confidence'],'tracker_mutated':False}
    complete=retained(root/'complete.json',receipt)
    return {'status':'COMPLETE','receipt':complete,'selection':final,'model_calls':len(calls)*2}


def stopped(root,code,error,plan,outcomes):
    value={'schema_version':SCHEMA,'status':'STOPPED','code':code,'error_type':type(error).__name__,'error_sha256':hashlib.sha256(str(error).encode()).hexdigest(),'plan':plan,'outcomes':outcomes,'automatic_retry':False}
    binding=retained(root/(code+'-stop.json'),value)
    return {'status':'STOPPED','code':code,'evidence':[binding]}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--input',type=Path,required=True);p.add_argument('--sha256',required=True);p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('run');p.add_argument('--plan',type=Path,required=True);p.add_argument('--sha256',required=True);p.add_argument('--execute',action='store_true');p.add_argument('--credential-env')
    args=parser.parse_args(argv)
    if args.command=='prepare':result=prepare({'path':str(args.input.absolute()),'sha256':args.sha256},args.out)
    else:
        require(not args.execute or args.credential_env,'explicit execution requires root credential environment name')
        result=run({'path':str(args.plan.absolute()),'sha256':args.sha256},dispatch=args.execute,credential=os.environ.get(args.credential_env) if args.execute else None)
    print(json.dumps(result,default=lambda x:asdict(x) if hasattr(x,'__dataclass_fields__') else str(x),sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
