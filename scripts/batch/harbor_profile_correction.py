"""One explicit remaining producer attempt after a sealed invalid profile.

The original request and completed response remain immutable. Corrections use
only the same observed pages and original two-attempt producer budget.
"""
from pathlib import Path
from dataclasses import asdict
import hashlib,importlib.util,json,sys,uuid
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_tracker_lifecycle import checked_record

SCHEMA='harbor-profile-producer-correction/v1'
FIELDS={'schema_version','helper','original_plan','stop','producer','reader','code_root','source','phase1','inventory','captures','authorization','attempt_id'}
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
GUIDANCE=('Return exactly one profile fragment, not a document envelope. Keep its review pending with null reviewer, reviewed_at and notes. '
          'Use the exact supplied source_name; complete reviewed names and caveats remain source context. '
          'Capture attempted_at is the first observed page time; read_count counts all supplied pages; final_url, http_status and content_type describe the last supplied page. '
          'Use only observed attributions and verbatim observed excerpts. Identify one source-backed dataset for the requested mapping; do not silently discard conflicting evidence or invent an unambiguous dataset. '
          'Do not retrieve pages or claim an independent review.')


def load(name,row):
    checked_record(row);spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def validate(row,*,root,source=None,policy=None):
    from scripts.batch import harbor_profile_readiness as R,harbor_model_executor as E
    q=read(row);require(set(q)==FIELDS and q['schema_version']==SCHEMA and q['helper']==record(__file__),'closed producer correction required')
    require(q['authorization']['sha256']==AUTH,'explicit retained correction authority required');checked_record(q['authorization'])
    require(str(Path(root))==q['code_root'] and str(uuid.UUID(q['attempt_id']))==q['attempt_id'],'correction root or identity differs')
    old=read(q['original_plan']);stop=read(q['stop']);require(old['schema_version']=='harbor-profile-production/v2' and old['status']=='PREPARED' and not old['blocked'] and old['automatic_semantic_retry']is False,'original prepared profile plan required')
    require(q['original_plan']==record(Path(q['original_plan']['path']).parent/'plan.json') and q['stop']==record(Path(q['original_plan']['path']).parent/'producer_fragment_invalid-stop.json'),'original stopped profile namespace differs')
    require(set(stop)=={'schema_version','status','code','error_type','error_sha256','plan','outcomes','automatic_retry'} and stop['schema_version']==old['schema_version'] and stop['status']=='STOPPED' and stop['code']=='producer_fragment_invalid' and stop['plan']==q['original_plan'] and stop['automatic_retry']is False and len(stop['outcomes'])==1,'one sealed invalid first producer required')
    for r in old['code']:checked_record(r)
    require(q['producer']in old['code'] and q['reader']in old['code'],'original profile code not selected')
    value=read(old['input'])
    require(value['schema_version']=='harbor-profile-production-input/v1' and all(value[k]==q[j]for k,j in [('original_source','source'),('phase1','phase1'),('inventory','inventory'),('captures','captures')]),'correction changed original source inputs')
    for k in ('source','phase1','inventory','captures'):checked_record(q[k])
    selected_policy=E.Policy(**value['policy']);require(policy is None or policy==selected_policy,'correction model policy differs')
    validator,_=R.modules(root);forbidden=validator.forbidden_values([Path(q['phase1']['path']),Path(q['inventory']['path'])]);sources=R.worklist(read(q['phase1']),forbidden,root=root)
    job=old['jobs'][0];current=next(x for x in sources if x['canonical_url']==job['source']['canonical_url'])
    require(R.same_captured_source(current,job['source']) and (source is None or source==current),'correction changed captured source identity or reviewed context')
    require(job['key']==hashlib.sha256(current['canonical_url'].encode()).hexdigest()[:24],'producer source scope differs')
    captures=read(q['captures']);by={x['canonical_url']:x['captures']for x in captures['sources']}
    require(job['captures']==by[current['canonical_url']],'producer capture bindings changed');pages,_=R.captured_pages(job['captures'],current['canonical_url'],forbidden,root=root)
    previous_reader=load('_original_profile_correction_reader',q['reader']);previous_producer=load('_original_profile_correction_producer',q['producer']);previous_producer.profiles=previous_reader;previous_producer.ROOT=Path(root)
    expected=previous_reader.request('producer',{'source':job['source'],'public_pages':pages},selected_policy,root=Path(root));require(read(job['producer_request'])==expected,'original source request differs from observed pages')
    outcome=stop['outcomes'][0];attempt=Path(outcome['path']).parent;actual_plan=read(record(attempt/'plan.json'))
    scope=dict(workbook_id=value['workbook_id'],original_sha256=q['source']['sha256'],lane='harbor-source-profiler',budget_id='profile-'+job['key']+'-producer')
    require(actual_plan['scope']==scope and actual_plan['ordinal']==1 and actual_plan['limit']==2 and actual_plan['attempt_id']==job['producer_attempt_id'] and actual_plan['policy']==value['policy'] and actual_plan['response_mode']=='json_object','original producer budget or policy differs')
    expected_attempt=Path(value['campaign']['root'])/E.sha(E.canonical(scope))/'attempt-01'
    require(attempt==expected_attempt and outcome==record(attempt/'outcome.json'),'producer journal outside original semantic scope')
    actual=E.inspect_locked(attempt,actual_plan,expected);require(actual.status=='completed' and actual.outcome==outcome,'unknown or rejected transport cannot become correction')
    try:previous_producer.producer_fragment(actual.call,expected,job['source'],forbidden,selected_policy)
    except (Exception,SystemExit)as error:
        require(type(error).__name__==stop['error_type'] and hashlib.sha256(str(error).encode()).hexdigest()==stop['error_sha256'],'original fragment failure was not reproduced')
    else:raise ValueError('successful producer cannot be repeated')
    for item in old['jobs']:
        folder=Path(q['original_plan']['path']).parent/item['key']
        require(not(folder/'reviewer-request.json').exists() and not(folder/'reviewed-fragment.json').exists(),'original independent review or accepted fragment already exists')
    return q,value,job,current,pages,selected_policy,actual


def request(row,source,*,root,policy):
    from scripts.batch import harbor_profile_readiness as R
    q,value,job,current,pages,selected_policy,actual=validate(row,root=root,source=source,policy=policy)
    body=dict(source=current,public_pages=pages,correction=dict(requirements=GUIDANCE,prior_completed_response_sha256=actual.call.response['sha256'],prior_failure_code='producer_fragment_invalid',remaining_semantic_attempt=2,semantic_limit=2))
    return R.request('producer',body,selected_policy,root=Path(root))


def check_input(row,value,*,root):
    q,old,job,*_=validate(row,root=root)
    expected=dict(old,schema_version='harbor-profile-production-input/v2',producer_recovery=row)
    require(value==expected,'corrected producer input changed source, campaign, policy or captures')
    return job
