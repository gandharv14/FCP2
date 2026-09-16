"""Source-bound lossless presentation derivation of a sealed producer fragment."""
from pathlib import Path
import hashlib
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_tracker_lifecycle import checked_record

SCHEMA='harbor-profile-presentation-derivation/v1'
FIELDS={'schema_version','helper','presentation_helper','original_plan','stop','producer_correction',
        'source','phase1','inventory','captures','authorization'}


def validate(row,*,root,source=None,policy=None):
    from scripts.batch import harbor_profile_correction as Q,harbor_profile_readiness as R,harbor_model_executor as E
    q=read(row);require(set(q)==FIELDS and q['schema_version']==SCHEMA and q['helper']==record(Path(__file__).absolute()),'closed profile presentation derivation required')
    require(q['authorization']['sha256']==Q.AUTH,'explicit profile presentation authority required')
    for name in ('authorization','source','phase1','inventory','captures','presentation_helper'):checked_record(q[name])
    correction,original_input,original_job,current,pages,selected_policy,first=Q.validate(q['producer_correction'],root=root,source=source,policy=policy)
    require(all(q[k]==correction[k]for k in ('source','phase1','inventory','captures','authorization')),'derived fragment changed original source context')
    old=read(q['original_plan']);stop=read(q['stop'])
    require(bool(old['jobs']) and len({j['key']for j in old['jobs']})==len(old['jobs']) and old['schema_version']=='harbor-profile-production/v2' and old['status']=='PREPARED'
        and stop['plan']==q['original_plan']and stop['status']=='STOPPED'and stop['code']=='producer_fragment_invalid'
        and stop['automatic_retry']is False and len(stop['outcomes'])==1,'one sealed invalid second producer required')
    require(set(stop)=={'schema_version','status','code','error_type','error_sha256','plan','outcomes','automatic_retry'}
        and Path(q['stop']['path'])==Path(q['original_plan']['path']).parent/'producer_fragment_invalid-stop.json',
        'original stopped profile evidence differs')
    value=read(old['input'])
    require(value==dict(original_input,schema_version='harbor-profile-production-input/v2',producer_recovery=q['producer_correction']),
        'derived fragment detached from original corrected input')
    job=old['jobs'][0];require(job['key']==original_job['key']and job['source']==current and job['captures']==original_job['captures'],
        'derived fragment changed source/capture identity')
    expected=Q.request(q['producer_correction'],current,root=root,policy=selected_policy)
    require(read(job['producer_request'])==expected,'derived fragment original request differs')
    outcome=stop['outcomes'][0];attempt=Path(outcome['path']).parent;prepared=read(record(attempt/'plan.json'))
    require(attempt==Path(first.call.request['path']).parent.parent/'attempt-02'
        and prepared['scope']==read(first.call.submitted)['scope']and prepared['ordinal']==prepared['limit']==2
        and prepared['attempt_id']==correction['attempt_id']and read(record(attempt/'prior-attempts.json'))==[first.outcome],
        'derived fragment changed original two-call budget')
    actual=E.inspect_locked(attempt,prepared,expected);require(actual.status=='completed'and actual.outcome==outcome,'unknown producer cannot be projected')
    require(not (Path(q['original_plan']['path']).parent/job['key']/'reviewer-request.json').exists(),
        'original independent reviewer already started')
    originals={Path(r['path']).name:r for r in old['code']}
    require(all(sum(Path(r['path']).name==name for r in old['code'])==1 for name in ('harbor_profile_producer.py','harbor_profile_readiness.py')),'duplicate original producer or reader role')
    for r in old['code']:checked_record(r)
    P=Q.load('_derived_original_profile_producer',originals['harbor_profile_producer.py']);reader=Q.load('_derived_original_profile_reader',originals['harbor_profile_readiness.py']);P.profiles=reader;P.ROOT=Path(root)
    validator,assembler=R.modules(root);forbidden=validator.forbidden_values([Path(q['phase1']['path']),Path(q['inventory']['path'])])
    try:P.producer_fragment(actual.call,expected,job['source'],forbidden,selected_policy)
    except (Exception,SystemExit)as error:
        require(type(error).__name__==stop['error_type']and hashlib.sha256(str(error).encode()).hexdigest()==stop['error_sha256'],
            'original second fragment rejection not reproduced')
    else:raise ValueError('successful producer cannot be presented as rejected')
    raw=R.validate_call(actual.call,expected,policy=selected_policy)['review']
    presentation=Q.load('_selected_lossless_profile_presentation',q['presentation_helper'])
    fragment,mapping=presentation.split_conventions(raw)
    normalized=assembler.validate_fragment(Path(row['path']),fragment);errors=[];validator.validate_profile(normalized,0,errors,forbidden)
    require(not errors and normalized['canonical_url']==current['canonical_url']and normalized['source_name']==current['source_name'],
        'derived fragment failed original profile or source gates')
    require(fragment['review']==dict(status='pending',reviewer=None,reviewed_at=None,notes=None),'derivation cannot accept a profile')
    last=pages[-1];capture=fragment['capture']
    require(capture['attempted_at']==pages[0]['accessed_at']and capture['read_count']==len(pages)
        and all(capture[k]==last[j]for k,j in [('http_status','status'),('final_url','final_url'),('content_type','content_type')]),
        'derived fragment changed observed capture facts')
    attrs={a['attribution_id']:a for a in fragment['evidence']['attributions']}
    require(all(any(a['url']==p['final_url']and a['accessed_at']==p['accessed_at']for p in pages)for a in attrs.values()),'derived unobserved attribution')
    require(all(any(x['text']in p['body']for p in pages if p['final_url']==attrs[x['attribution_id']]['url'])for x in fragment['evidence']['excerpts']),
        'derived unobserved excerpt')
    return q,actual,fragment,mapping
