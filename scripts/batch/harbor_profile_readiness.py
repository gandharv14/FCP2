"""Root-bound public-profile call/capture verification; no retrieval or mutation.

Capture hashes and call journals must be selected by the root from its actual
observations. They are not provider signatures or agent-nominated attestations.
"""
import copy
import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_tracker_lifecycle import checked_record
from scripts.batch.harbor_model_review_provenance import Call, Policy, validate_call, require_independent
from scripts.batch.harbor_readiness import skill_module, ROOT

PRODUCER = 'Return one source-profile JSON fragment. Use only the redacted source and independently captured public pages supplied below. Describe terminology, dataset names, field conventions, document types and cadence; never workbook values. Follow the supplied source-profile schema. Keep review pending with null reviewer, reviewed_at and notes. Do not retrieve additional pages.'
REVIEWER = 'Independently review the supplied source-profile fragment against only its public capture evidence. Return the complete same fragment, changing only review fields to accepted or rejected with reviewer, UTC reviewed_at and a grounded reason. Do not repair producer prose, add facts, infer workbook values or retrieve other pages.'

@dataclass(frozen=True)
class ProfileCall:
    canonical_url: str
    producer: Call
    reviewer: Call
    captures: tuple
    producer_recovery: dict | None = None
    producer_derivation: dict | None = None

class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []
    def handle_starttag(self, tag, attrs):
        if tag.casefold() == 'a':
            self.links.extend(v for k,v in attrs if k.casefold() == 'href' and v)

def modules(root=ROOT):
    return (skill_module(root,'profile-mcp-sources','validate_source_profiles'),
            skill_module(root,'profile-mcp-sources','assemble_profiles'))

def bounded_display(label,canonical):
    prefix=label[:137].rsplit(' ',1)[0] or label[:137]
    suffix=hashlib.sha256(canonical.encode()).hexdigest()[:16].translate(str.maketrans('0123456789abcdef','abcdefghijklmnop'))
    return prefix.rstrip()+'… ['+suffix+']'


def same_captured_source(source,original):
    """A display-only projection preserves the exact captured source context."""
    if source==original:return True
    if set(original)!={'canonical_url','source_name','source_kind'} or set(source)!=set(original)|{'reviewed_source_names'}:return False
    names=source['reviewed_source_names']
    if not isinstance(names,list)or not names or any(not isinstance(x,str)or not x for x in names)or names!=sorted(set(names)):return False
    label='; '.join(names)
    return (len(label)>160 and label==original['source_name']
        and all(source[k]==original[k]for k in ('canonical_url','source_kind'))
        and source['source_name']==bounded_display(label,source['canonical_url']))


def worklist(spec, forbidden, *, root=ROOT):
    validator,_ = modules(root)
    rows = {}; aliases = {}
    for variable in spec['variables']:
        for source in variable['sources']:
            url = source.get('url')
            if not isinstance(url,str):
                raise ValueError('source URL is missing')
            parts=urlsplit(url)
            if parts.username is not None or parts.password is not None:
                continue
            canonical = validator.canonicalize_url(url)
            if validator.public_url_error(canonical,require_canonical=True):
                continue
            row = dict(source_name=source['name'],source_kind=source.get('kind',''),canonical_url=canonical)
            require(all(isinstance(v,str) and v for v in row.values()),'incomplete redacted source')
            require(all(not validator.CELL_RE.search(row[k]) and validator.leaked_value(row[k],forbidden) is None for k in ('source_name','source_kind')),'private data in source display name or kind')
            # A public URL identifies one capture target. Preserve every reviewed
            # label verbatim; internal source IDs and numerical bindings stay fixed.
            require(canonical not in rows or rows[canonical]['source_kind']==row['source_kind'],'ambiguous public source kinds')
            aliases.setdefault(canonical,set()).add(row['source_name']);rows[canonical]=row
    for canonical,names in aliases.items():
        label='; '.join(sorted(names))
        require(not validator.CELL_RE.search(label) and validator.leaked_value(label,forbidden)is None,'private data in combined source display name')
        if len(label)>160:
            # The profile schema bounds the display name, while the request must
            # preserve every reviewed label and caveat as source context.
            display=bounded_display(label,canonical)
            require(not validator.CELL_RE.search(display) and validator.leaked_value(display,forbidden)is None,'private data in bounded source display name')
            rows[canonical]=dict(rows[canonical],source_name=display,reviewed_source_names=sorted(names))
        else:rows[canonical]=dict(rows[canonical],source_name=label)
    for url,row in rows.items():
        if 'reviewed_source_names'in row:
            require(all(url==other or row['source_name']!=value['source_name']for other,value in rows.items()),'bounded source display identity collision')
    return [rows[k] for k in sorted(rows)]

def captured_pages(bindings, canonical, forbidden, *, root=ROOT):
    if bindings and any(read(binding).get('schema_version') == 'harbor-retained-public-page/v1' for binding in bindings):
        from scripts.batch.harbor_public_projection_recovery import pages
        return pages(bindings, canonical, forbidden)
    # Preserve strict v1 behavior. V2 raw bodies are never sent to a model;
    # the owning current projection is independently re-derived from bytes.
    if bindings and any(read(binding).get('schema_version') == 'harbor-public-capture/v2' for binding in bindings):
        from scripts.batch.harbor_public_capture import pages
        return pages(bindings, canonical, forbidden)
    validator,_ = modules(root)
    require(1 <= len(bindings) <= 3,'public read budget exceeded or absent')
    pages=[]; evidence=[]; first_links=set(); previous_end=0
    for index,binding in enumerate(bindings):
        capture=read(binding); evidence.append(binding)
        require(set(capture)=={'schema_version','requested_url','final_url','redirects','status','content_type','started_at_ns','completed_at_ns','accessed_at','authentication','body'},'invalid public capture schema')
        require(capture['schema_version']=='harbor-public-capture/v1' and capture['authentication']=='none','unbound/authenticated public capture')
        start,end=capture['started_at_ns'],capture['completed_at_ns']
        require(type(start) is int and type(end) is int and previous_end <= start <= end and start>0,'capture chronology invalid'); previous_end=end
        require(validator.is_timestamp(capture['accessed_at']),'invalid capture access time')
        accessed=int(datetime.fromisoformat(capture['accessed_at'].replace('Z','+00:00')).timestamp()*10**9)
        require(start-10**9 <= accessed <= end+10**9,'capture access timestamp differs from observation')
        require(capture['status']==200 and capture['content_type'] in {'text/html','text/plain'},'access barrier or unsupported public content')
        urls=[capture['requested_url'],*capture['redirects'],capture['final_url']]
        require(isinstance(capture['redirects'],list),'invalid redirect chain')
        for url in urls:
            require(not validator.public_url_error(url,require_canonical=False) and validator.origin(url)==validator.origin(canonical),'nonpublic/cross-origin capture')
        requested=validator.canonicalize_url(capture['requested_url'])
        require(requested==canonical if index==0 else requested in first_links,'secondary capture was not linked by canonical page')
        checked_record(capture['body']); evidence.append(capture['body'])
        body=Path(capture['body']['path']).read_text()
        require(not validator.CELL_RE.search(body) and validator.leaked_value(body,forbidden) is None,'private workbook data in public review context')
        require(not any(term in body.casefold() for term in ('captcha','sign in','log in','subscriber-only','password required')),'public page contains an access barrier')
        if index==0 and capture['content_type']=='text/html':
            parser=_Links(); parser.feed(body)
            first_links={validator.canonicalize_url(urljoin(capture['final_url'],v)) for v in parser.links}
        pages.append({k:capture[k] for k in ('requested_url','final_url','status','content_type','accessed_at') } | {'body':body})
    return pages,evidence

def request(role, body, policy, *, root=ROOT):
    schema=(root/'.cursor/skills/profile-mcp-sources/SOURCE_PROFILES.md').read_text()
    return {'model':policy.model,'reasoning_effort':policy.reasoning_effort,'messages':[
        {'role':'system','content':(PRODUCER if role=='producer' else REVIEWER)+'\n\n'+schema},
        {'role':'user','content':json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))}]}

def accepted_profile(call, source, forbidden, *, policy, root=ROOT):
    require(isinstance(call,ProfileCall) and isinstance(policy,Policy),'root-selected profile call required')
    require(call.canonical_url==source['canonical_url'],'profile outside redacted worklist')
    validator,assembler=modules(root)
    pages,evidence=captured_pages(call.captures,call.canonical_url,forbidden,root=root)
    require(read(call.captures[-1])['completed_at_ns'] <= read(call.producer.submitted)['submitted_at_ns'],'producer predates public captures')
    expected=request('producer',{'source':source,'public_pages':pages},policy,root=root)
    if call.producer_recovery is not None:
        from scripts.batch import harbor_profile_correction as Q
        expected=Q.request(call.producer_recovery,source,root=root,policy=policy)
        q,_,_,_,_,_,old=Q.validate(call.producer_recovery,root=root,source=source,policy=policy)
        submitted=read(call.producer.submitted)
        require(submitted['semantic_ordinal']==submitted['semantic_limit']==2 and submitted['attempt_id']==q['attempt_id'] and submitted['scope']==read(old.call.submitted)['scope'] and read(old.call.completed)['completed_at_ns']<=submitted['submitted_at_ns'],'remaining producer order or budget differs')
        from scripts.batch import harbor_model_executor as E
        attempt=Path(call.producer.request['path']).parent
        require(attempt==Path(old.call.request['path']).parent.parent/'attempt-02' and read(record(attempt/'prior-attempts.json'))==[old.outcome],'remaining producer journal detached from original attempt')
        sealed=E.inspect_locked(attempt,read(record(attempt/'plan.json')),expected)
        require(sealed.status=='completed' and sealed.call==call.producer,'remaining producer completion differs')
        evidence.extend([call.producer_recovery,old.outcome,old.call.request,old.call.submitted,old.call.response,old.call.completed])
    producer=validate_call(call.producer,expected,policy=policy)
    original=producer['review']
    require(original['review']=={'status':'pending','reviewer':None,'reviewed_at':None,'notes':None},'producer self-accepted profile')
    if call.producer_derivation is not None:
        from scripts.batch import harbor_profile_derivation as D
        derived,actual,original,mapping=D.validate(call.producer_derivation,root=root,source=source,policy=policy)
        require(actual.call==call.producer and derived['producer_correction']==call.producer_recovery,
                'derived fragment detached from original completed producer')
        evidence.extend([call.producer_derivation,derived['original_plan'],derived['stop'],derived['presentation_helper']])
    produced=assembler.validate_fragment(Path(call.producer.response['path']),original)
    reviewer=validate_call(call.reviewer,request('reviewer',{'fragment':original,'public_pages':pages},policy,root=root),policy=policy)
    require_independent(reviewer,[producer])
    require(read(call.producer.completed)['completed_at_ns'] <= read(call.reviewer.submitted)['submitted_at_ns'],'reviewer predates producer')
    reviewed=reviewer['review'];left=copy.deepcopy(original);right=copy.deepcopy(reviewed)
    left.pop('review');review=right.pop('review')
    require(left==right,'reviewer altered source identity/capture/producer prose')
    require(review['status'] in {'accepted','rejected'} and review['reviewer'] and review['notes'] and validator.is_timestamp(review['reviewed_at']),'independent profile decision incomplete')
    reviewed_at=int(datetime.fromisoformat(review['reviewed_at'].replace('Z','+00:00')).timestamp()*10**9)
    require(read(call.reviewer.submitted)['submitted_at_ns']-10**9 <= reviewed_at <= read(call.reviewer.completed)['completed_at_ns']+10**9,'review time differs from independent call')
    accepted=assembler.validate_fragment(Path(call.reviewer.response['path']),reviewed)
    errors=[];validator.validate_profile(accepted,0,errors,forbidden)
    require(not errors,'profile schema or redaction failed')
    require(accepted['canonical_url']==source['canonical_url'] and accepted['source_name']==source['source_name'],'profile changed public source identity')
    capture=accepted['capture'];last=pages[-1]
    require(capture['attempted_at']==pages[0]['accessed_at'],'model capture timestamp differs from observation')
    require(capture['read_count']==len(pages) and capture['http_status']==last['status'] and capture['final_url']==last['final_url'] and capture['content_type']==last['content_type'],'model capture facts differ from observed reads')
    attrs={a['attribution_id']:a for a in accepted['evidence']['attributions']}
    for attr in attrs.values():
        require(any(attr['url']==p['final_url'] and attr['accessed_at']==p['accessed_at'] for p in pages),'unobserved public attribution')
    for excerpt in accepted['evidence']['excerpts']:
        attr=attrs[excerpt['attribution_id']]
        require(any(excerpt['text'] in p['body'] for p in pages if p['final_url']==attr['url']),'excerpt not in independently captured page')
    for item in (call.producer,call.reviewer):
        evidence += [item.request,item.submitted,item.response,item.completed]
        if item.parsed:evidence.append(item.parsed)
    return {'profile':accepted,'accepted':review['status']=='accepted','evidence':evidence,'model_calls':2+int(call.producer_recovery is not None)}

@dataclass(frozen=True)
class ProfileSelection:
    phase1: dict
    inventory: dict
    profiles: dict
    mapping: dict
    calls: tuple
    policy: Policy
    skips: tuple = ()

def validate_profiles(selection, *, root=ROOT):
    """Closed profile document and mapping, with zero-call generic derivation."""
    require(isinstance(selection,ProfileSelection),'root-selected profile context required')
    s=selection; validator,_=modules(root)
    for binding in (s.phase1,s.inventory,s.profiles,s.mapping): checked_record(binding)
    forbidden=validator.forbidden_values([Path(s.phase1['path']),Path(s.inventory['path'])])
    spec=read(s.phase1);sources=worklist(spec,forbidden,root=root)
    document=read(s.profiles)
    require(not validator.validate_document(document,forbidden=forbidden,inventory_path=Path(s.inventory['path']),spec_path=Path(s.phase1['path'])),'profile document capture/hash/redaction failed')
    urls=[c.canonical_url for c in s.calls]
    skipped_urls=[r['canonical_url']for r in s.skips]
    require(all(set(r)=={'canonical_url','captures'}for r in s.skips) and len(skipped_urls)==len(set(skipped_urls)) and not set(urls)&set(skipped_urls),'duplicate or mixed skipped profile selection')
    require(len(urls)==len(set(urls)) and set(urls)|set(skipped_urls)=={r['canonical_url'] for r in sources},'profile calls and verified skips do not exactly cover public worklist')
    by_url={c.canonical_url:c for c in s.calls}; projected=[];evidence=[s.phase1,s.inventory,s.profiles,s.mapping]
    skip_by_url={r['canonical_url']:r for r in s.skips}
    for source in sources:
        if source['canonical_url']in skip_by_url:
            from scripts.batch.harbor_profile_skips import validate
            result=validate(source,skip_by_url[source['canonical_url']]['captures'],forbidden,phase1=s.phase1,inventory=s.inventory)
            errors=[];validator.validate_profile(result['profile'],0,errors,forbidden);require(not errors,'verified skipped fragment failed original schema')
            projected.append(result['profile']);evidence.extend(result['evidence']);continue
        result=accepted_profile(by_url[source['canonical_url']],source,forbidden,policy=s.policy,root=root)
        projected.append(result['profile']);evidence.extend(result['evidence'])
    require(len({p['source_id'] for p in projected})==len(projected),'duplicate profile source identity')
    projected.sort(key=lambda p:p['source_id'])
    require(document['profiles']==projected,'assembled profiles differ from reviewed fragments')
    from mcp_env.build import source_id,slug
    profiles={p['canonical_url']:p for p in projected if p['status']=='profiled' and p['review']['status']=='accepted'}
    mappings={}
    for variable in spec['variables']:
        for source in variable['sources']:
            url=validator.canonicalize_url(source['url']);profile=profiles.get(url)
            if profile is None:continue
            sid=source_id(source)
            # The normalized identity may hash private audit provenance. The
            # public profile has its own independently reviewed identity; the
            # explicit mapping joins them only through the exact canonical URL.
            require(profile['canonical_url']==url,'accepted public source URL differs')
            names=profile['dataset_names'] or [profile['source_name']+' versioned indicators']
            require(len(names)==1,'multiple public datasets need a separately reviewed mapping decision')
            row={'normalized_source_id':sid,'canonical_url':url,'profile_id':profile['source_id'],'dataset_key':slug(names[0])}
            require(sid not in mappings or mappings[sid]==row,'ambiguous source mapping')
            mappings[sid]=row
    expected={'schema_version':'harbor-source-profile-mapping/v1','phase_1_spec_sha256':s.phase1['sha256'],'profiles_sha256':s.profiles['sha256'],'mappings':[mappings[k] for k in sorted(mappings)]}
    require(read(s.mapping)==expected,'profile mapping is incomplete or changed')
    return {'public_sources':len(sources),'skipped_profiles':len(s.skips),'skip_reasons':[p['skip_reason']for p in projected if p['status']=='skipped'],'accepted_profiles':len(profiles),'model_calls':sum(2+int(c.producer_recovery is not None)for c in s.calls),'confidence':'high' if len(profiles)==len(sources) else 'medium','evidence':evidence,'phase1_sha256':s.phase1['sha256'],'mapping':expected}
