"""Closed Harbor tracker writes; structural validity is not financial qualification.

The trusted dispatcher chooses writable fields and validates changed evidence.
Unknown legacy fields require an explicit migration, never an implicit alias.
"""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

LANES = tuple('harbor-' + x for x in ('source-warden','segmentation-analyst','research-auditor','normalization-engineer','source-profiler','environment-packager','disclosure-writer','instruction-naturalizer','independent-verifier','dialogue-author'))
BINDINGS = set('dependency_preflight original_raw_source raw_source source_health_before source_remediation_plan source_remediation_manifest source_health recalc_run restriction_inventory trusted_runner_public_key excel_isolation_attestation excel_sandbox_runner excel_engine_version source_publication_root source_generation_root source_generation source_root source_file ast_root ast_workbook_root curation segmentation_root segmentation_workbook_root segmentation_generation preflight_root preflight_report baseline_inputs_root baseline_inputs baseline_segmentation audit_root variable_source_run variable_source_audit variable_source_inventory variable_source_metadata audit_equivalence audit_current_inventory audit_validator_upgrade audit_upgrade_original_code normalization_audit_applicability normalization_original_locator_code normalization_partitioned_review normalization_prepared_manifest normalization_prepared_request normalization_draft normalizer normalized_phase1 normalized_final normalization_exclusions normalization_report plain_eligibility source_profiles source_profile_mapping maskability_report leakscan_report oracle_allowlist task_mode mcp_inputs_root mcp_inputs mcp_bundle mcp_mask_cells mcp_build_a mcp_build_b stage_root staged_bundle'.split())
CLASSES = set('source_policy source_recalculation source_integrity segmentation preflight research_audit normalization source_profile maskability_leak mcp_build_mask packaging disclosure naturalization faithfulness_review oracle_readiness oracle_semantic grader dialogue infrastructure resource_exhaustion confidence retry_budget publication_cas output_verification shared_pipeline_defect unknown_internal'.split())
TOP = set('schema_version revision batch requested_ids workbook_id raw_source execution_state current_gate last_gate lane_state bindings generation_ids artifact_hashes current_confidence confidence_history handoffs leases systemic_faults diagnostics repairs scheduler promotion_failure failure_history agent_notes terminal_verdict staged_path promotion_ids final_status stop_reason updated_at'.split())
LANE_FIELDS = set('state phase disposition attempt current_gate last_gate evidence_paths diagnostic_ids current_confidence confidence_history'.split())
LEASE_KEYS = set('owner token scope acquired_at heartbeat_at expires_at acquisition_revision'.split())
LEASE_KINDS = set('dependency_preflight task_agent lane infra_fixer publication'.split())
FAILURE_KEYS = set('schema_version class subclass stage lane gate fixability summary why_not_promoted first_seen last_seen occurrence_count diagnostic_signature evidence_paths repair_attempts next_action'.split())
HISTORY = ('confidence_history','handoffs','systemic_faults','diagnostics','failure_history')
HEX = re.compile(r'^[0-9a-f]{64}$')

class InvalidTracker(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__('; '.join(f'{p}: {reason}' for p, reason in issues))

class Conflict(ValueError): pass

def sha(raw): return hashlib.sha256(raw).hexdigest()
def integer(v): return type(v) is int and v >= 0
def strict_json(raw):
    def unique(pairs):
        out = {}
        for k,v in pairs:
            if k in out: raise ValueError('duplicate JSON object key: '+k)
            out[k]=v
        return out
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))

def _validate_tracker(d):
    issues=[]
    def need(ok,p,msg):
        if not ok: issues.append((p,msg))
    def obj(v,p,keys,required=()):
        if not isinstance(v,dict): issues.append((p,'expected object')); return {}
        for k in v.keys()-keys: issues.append((p+'/'+k,'unknown field'))
        for k in set(required)-v.keys(): issues.append((p+'/'+k,'missing field'))
        return v
    def digest(v,p,nullable=True): need((nullable and v is None) or (isinstance(v,str) and bool(HEX.fullmatch(v))),p,'expected SHA-256')
    def text(v,p,nullable=True): need((nullable and v is None) or (isinstance(v,str) and bool(v.strip())),p,'expected nonempty text')
    def arr(v,p): need(isinstance(v,list),p,'expected array'); return v if isinstance(v,list) else []
    def binding(v,p,required=()):
        b=obj(v,p,{'path','id','value','sha256','manifest_sha256'},required)
        need(bool(b),p,'binding cannot be empty')
        if 'path' in b: need(b['path'] is None or (isinstance(b['path'],str) and Path(b['path']).is_absolute()),p+'/path','expected absolute path')
        for k in ('sha256','manifest_sha256'):
            if k in b: digest(b[k],p+'/'+k)
        if 'id' in b: digest(b['id'],p+'/id')
        if 'value' in b: need(b['value'] is None or type(b['value']) in (str,int,float,bool),p+'/value','expected scalar')
    d=obj(d,'',TOP,TOP)
    need(d.get('schema_version')==2 and type(d.get('schema_version')) is int,'/schema_version','expected version2')
    need(integer(d.get('revision')),'/revision','expected nonnegative integer')
    text(d.get('batch'),'/batch',False)
    wb=d.get('workbook_id');need(isinstance(wb,str) and bool(re.fullmatch(r'\d{4}',wb)),'/workbook_id','expected four-digit identity')
    ids=arr(d.get('requested_ids'),'/requested_ids');need(all(isinstance(x,str) and re.fullmatch(r'\d{4}',x) for x in ids) and len(set(x for x in ids if isinstance(x,str)))==len(ids) and wb in ids,'/requested_ids','expected unique complete IDs including workbook')
    obj(d.get('raw_source'),'/raw_source',{'path','sha256'},{'path','sha256'})
    binding(d.get('raw_source'),'/raw_source',('path','sha256'))
    for key in ('current_gate','last_gate','stop_reason','staged_path'): text(d.get(key),'/'+key)
    need(d.get('execution_state') in {'queued','running','systemic-paused','publication_queued','PROMOTED','STOPPED'},'/execution_state','invalid closed execution state')
    need(d.get('terminal_verdict') in {None,'PUBLICATION_READY','PROMOTED','STOPPED'},'/terminal_verdict','invalid closed verdict')
    need(d.get('final_status') in {None,'PROMOTED','STOPPED'},'/final_status','invalid closed final status')
    need(d.get('updated_at') is None or integer(d.get('updated_at')),'/updated_at','expected integer nanoseconds')
    lanes=obj(d.get('lane_state'),'/lane_state',set(LANES),LANES)
    levels=[]
    for name,v in lanes.items():
        p='/lane_state/'+name;v=obj(v,p,LANE_FIELDS)
        if not v: continue  # Explicit skill template permits queued empty records.
        need(v.get('state') in {'pending','ready','running','repairing','passed','terminal'},p+'/state','invalid lane state')
        need(v.get('current_confidence') in {None,'low','medium','high'},p+'/current_confidence','invalid confidence')
        if v.get('current_confidence') is not None: levels.append((name,v['current_confidence']))
        for k in ('phase','disposition','current_gate','last_gate'): text(v.get(k),p+'/'+k)
        if 'attempt' in v: need(v['attempt'] is None or integer(v['attempt']),p+'/attempt','invalid attempt')
        for k in ('evidence_paths','diagnostic_ids','confidence_history'):
            if k in v: arr(v[k],p+'/'+k)
        for i,e in enumerate(v.get('evidence_paths',[]) if isinstance(v.get('evidence_paths',[]),list) else []): binding(e,p+f'/evidence_paths/{i}',('path','sha256'))
    bindings=obj(d.get('bindings'),'/bindings',BINDINGS)
    for k,v in bindings.items(): binding(v,'/bindings/'+k)
    for group,keys in [('generation_ids',{'source','segmentation','task','release'}),('promotion_ids',{'task_generation_id','release_id','previous_release_id'})]:
        for k,v in obj(d.get(group),'/'+group,keys,keys).items():digest(v,'/'+group+'/'+k)
    hashes=d.get('artifact_hashes');need(isinstance(hashes,dict),'/artifact_hashes','expected object')
    if isinstance(hashes,dict):
        for p,v in hashes.items():
            text(p,'/artifact_hashes/key',False)
            if isinstance(v,dict):
                obj(v,'/artifact_hashes/'+p,{'path','sha256'},{'path','sha256'})
                text(v.get('path'),'/artifact_hashes/'+p+'/path',False)
                digest(v.get('sha256'),'/artifact_hashes/'+p+'/sha256',False)
            else:digest(v,'/artifact_hashes/'+p,False)
    c=obj(d.get('current_confidence'),'/current_confidence',{'level','reason_codes','contributors','computed_at_revision'},{'level','reason_codes','contributors','computed_at_revision'})
    need(c.get('level') in {None,'low','medium','high'},'/current_confidence/level','invalid confidence')
    reasons=arr(c.get('reason_codes'),'/current_confidence/reason_codes');need(all(isinstance(x,str) and x for x in reasons),'/current_confidence/reason_codes','expected nonempty reason strings');contributors=arr(c.get('contributors'),'/current_confidence/contributors')
    need(all(x in LANES for x in contributors) and len(set(x for x in contributors if isinstance(x,str)))==len(contributors),'/current_confidence/contributors','invalid contributors')
    need(integer(c.get('computed_at_revision')) and c.get('computed_at_revision',0)<=d.get('revision',-1),'/current_confidence/computed_at_revision','invalid revision')
    if c.get('level') is not None and levels:
        need(c['level']==min((level for _,level in levels),key={'low':0,'medium':1,'high':2}.get),'/current_confidence/level','must equal worst current lane')
        need(set(contributors)=={name for name,_ in levels},'/current_confidence/contributors','must identify all populated current lanes')
    for k in HISTORY:arr(d.get(k),'/'+k)
    leases=obj(d.get('leases'),'/leases',LEASE_KINDS,LEASE_KINDS)
    for k,v in leases.items():
        if v is None:continue
        p='/leases/'+k;v=obj(v,p,LEASE_KEYS,LEASE_KEYS)
        for field in ('owner','scope'):text(v.get(field),p+'/'+field,False)
        try:
            parsed=uuid.UUID(v.get('token',''))
            need(parsed.version==4,p+'/token','expected UUID4 token')
        except (ValueError,TypeError,AttributeError):issues.append((p+'/token','expected random UUID token'))
        for field in ('acquired_at','heartbeat_at','expires_at','acquisition_revision'):need(integer(v.get(field)),p+'/'+field,'expected integer')
        if all(integer(v.get(x)) for x in ('acquired_at','heartbeat_at','expires_at')):need(v['acquired_at']<=v['heartbeat_at']<v['expires_at'],p,'invalid lease chronology')
        if k=='task_agent':need(v.get('scope')=='workbook:'+str(wb),p+'/scope','wrong workbook lease scope')
        need(integer(v.get('acquisition_revision')) and v.get('acquisition_revision',0)<=d.get('revision',-1),p+'/acquisition_revision','future acquisition')
    repairs=obj(d.get('repairs'),'/repairs',{'count','by_signature','history'},{'count','by_signature','history'});need(integer(repairs.get('count')),'/repairs/count','expected integer');arr(repairs.get('history'),'/repairs/history');need(isinstance(repairs.get('by_signature'),dict),'/repairs/by_signature','expected object')
    if isinstance(repairs.get('by_signature'),dict):
        for signature,consumed in repairs['by_signature'].items():
            text(signature,'/repairs/by_signature/key',False)
            need(integer(consumed),'/repairs/by_signature/'+signature,'expected consumed nonnegative integer')
    scheduler=obj(d.get('scheduler'),'/scheduler',{'attempts','resource_exhausted_retries','resource_exhausted_first_at'},{'attempts','resource_exhausted_retries','resource_exhausted_first_at'})
    for k,v in scheduler.items():need((k=='resource_exhausted_first_at' and v is None) or integer(v),'/scheduler/'+k,'expected integer')
    notes=obj(d.get('agent_notes'),'/agent_notes',{'path','sha256','diagnostic_signature','evidence_set_sha256','updated_at'},{'path','sha256','diagnostic_signature','evidence_set_sha256','updated_at'})
    for k in ('sha256','diagnostic_signature','evidence_set_sha256'):digest(notes.get(k),'/agent_notes/'+k)
    text(notes.get('path'),'/agent_notes/path');need(notes.get('updated_at') is None or integer(notes.get('updated_at')),'/agent_notes/updated_at','expected nanoseconds')
    failure=d.get('promotion_failure')
    if failure is not None:
        f=obj(failure,'/promotion_failure',FAILURE_KEYS,FAILURE_KEYS);need(f.get('schema_version')==1,'/promotion_failure/schema_version','expected version1');need(f.get('class') in CLASSES,'/promotion_failure/class','unknown failure class');need(isinstance(f.get('subclass'),str) and bool(re.fullmatch(r'[a-z][a-z0-9_]*',f['subclass'])),'/promotion_failure/subclass','expected stable subclass');need(f.get('fixability') in {'fixable','terminal','exhausted'},'/promotion_failure/fixability','invalid fixability');digest(f.get('diagnostic_signature'),'/promotion_failure/diagnostic_signature',False)
        for k in ('first_seen','last_seen','occurrence_count'):need(integer(f.get(k)) and f.get(k,0)>0,'/promotion_failure/'+k,'expected positive integer')
        for k in ('evidence_paths','repair_attempts'):arr(f.get(k),'/promotion_failure/'+k)
        need(bool(f.get('evidence_paths')),'/promotion_failure/evidence_paths','current failure needs bound evidence')
        for i,e in enumerate(f.get('evidence_paths',[])):binding(e,f'/promotion_failure/evidence_paths/{i}',('path','sha256'))
        need(f.get('lane') is None or f.get('lane') in LANES,'/promotion_failure/lane','unknown canonical lane')
        for k in ('summary','why_not_promoted','next_action'):text(f.get(k),'/promotion_failure/'+k,False)
        if integer(f.get('first_seen')) and integer(f.get('last_seen')):need(f['first_seen']<=f['last_seen'],'/promotion_failure','reversed observation times')
    if d.get('execution_state')=='STOPPED':need(isinstance(failure,dict) and failure.get('fixability') in {'terminal','exhausted'} and d.get('terminal_verdict')=='STOPPED' and d.get('final_status')=='STOPPED','/execution_state','STOPPED requires matching terminal failure and verdict')
    if d.get('execution_state') in {'publication_queued','PROMOTED'}:
        need(failure is None and c.get('level')=='high' and len(levels)==len(LANES) and all(isinstance(lanes.get(x),dict) and lanes[x].get('state')=='passed' for x in LANES),'/execution_state','qualification requires all lanes passed/high and no failure')
    if d.get('execution_state')=='PROMOTED':need(d.get('terminal_verdict')==d.get('final_status')=='PROMOTED' and all(d.get('promotion_ids',{}).get(k) for k in ('task_generation_id','release_id')),'/execution_state','promotion identity or verdict missing')
    if issues:raise InvalidTracker(issues)
    return d

def validate_tracker(d):
    try:return _validate_tracker(d)
    except InvalidTracker:raise
    except (TypeError,KeyError,AttributeError) as exc:
        raise InvalidTracker([('', 'malformed nested value: '+type(exc).__name__)]) from exc

def safe_path(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise Conflict('symlink ancestry rejected')
    return path

def cas_mutate(path, *, expected_revision, expected_sha256, owner, token, scope,
               mutate, allowed_fields, allowed_lanes=(), binding_replacements=None,
               evidence_validator=None, quality_validator=None, failure_validator=None,
               lease_kind='task_agent', now_ns=None, fault_hook=lambda _:None):
    """One owned mutation. Dispatcher permissions are not supplied by model data.

    An exception after replace can mean a committed write (e.g. directory fsync
    failure); callers must re-read revision+hash, never blindly retry.
    """
    path=safe_path(path);lockpath=safe_path(str(path)+'.lock')
    if not integer(expected_revision) or not isinstance(expected_sha256,str) or not HEX.fullmatch(expected_sha256):raise Conflict('invalid CAS expectation')
    fd=os.open(lockpath,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        raw=path.read_bytes();before=validate_tracker(strict_json(raw));now=time.time_ns() if now_ns is None else now_ns
        if before['revision']!=expected_revision or sha(raw)!=expected_sha256:raise Conflict('revision/hash mismatch')
        lease=before['leases'].get(lease_kind)
        if not lease or (lease['owner'],lease['token'],lease['scope'])!=(owner,token,scope) or not lease['acquired_at']<=lease['heartbeat_at']<=now<lease['expires_at']:raise Conflict('inactive or mismatched lease')
        after=copy.deepcopy(before);mutate(after)
        for k in TOP:
            if after.get(k)!=before.get(k) and k not in allowed_fields:raise Conflict('unowned field: '+k)
        for k in ('schema_version','revision','batch','workbook_id','raw_source','requested_ids','leases','updated_at'):
            if after.get(k)!=before.get(k):raise Conflict('immutable/system field: '+k)
        for k in HISTORY:
            if after[k][:len(before[k])]!=before[k]:raise Conflict('history rewrite: '+k)
        if after['repairs']['history'][:len(before['repairs']['history'])]!=before['repairs']['history']:raise Conflict('repair history rewrite')
        for lane in LANES:
            if before['lane_state'][lane]!=after['lane_state'][lane] and lane not in allowed_lanes:raise Conflict('unowned lane: '+lane)
            old=before['lane_state'][lane].get('confidence_history',[]);new=after['lane_state'][lane].get('confidence_history',[])
            if new[:len(old)]!=old:raise Conflict('lane history rewrite')
            old_attempt=before['lane_state'][lane].get('attempt');new_attempt=after['lane_state'][lane].get('attempt')
            if integer(old_attempt) and (not integer(new_attempt) or new_attempt<old_attempt):raise Conflict('lane attempt budget decreased')
        for old,new in [(before['repairs']['count'],after['repairs']['count']),*[(before['scheduler'][k],after['scheduler'][k]) for k in ('attempts','resource_exhausted_retries')]]:
            if not integer(new) or new<old:raise Conflict('attempt/repair budget decreased')
        for signature,consumed in before['repairs']['by_signature'].items():
            current=after['repairs']['by_signature'].get(signature)
            if not integer(current) or current<consumed:raise Conflict('per-signature repair budget decreased: '+signature)
        changed={k for k in before['bindings'].keys()|after['bindings'].keys() if before['bindings'].get(k)!=after['bindings'].get(k)}
        permissions=binding_replacements or {}
        if changed-set(permissions):raise Conflict('undeclared binding replacement')
        for k in changed:
            if permissions[k]!=before['bindings'].get(k):raise Conflict('stale binding replacement')
            if k in {'curation','original_raw_source','raw_source'} and before['bindings'].get(k) is not None:raise Conflict('immutable source/curation binding')
        if changed:
            if evidence_validator is None:raise Conflict('changed bindings require independent evidence validation')
            if evidence_validator(copy.deepcopy(before),copy.deepcopy(after),frozenset(changed)) is not True:raise Conflict('independent evidence validator did not pass')
        after['revision']=expected_revision+1;after['updated_at']=now;after['leases'][lease_kind]['heartbeat_at']=now
        validate_tracker(after)
        failure_events=set()
        if before['promotion_failure']!=after['promotion_failure']:
            failure_events.add('clear' if after['promotion_failure'] is None else ('record' if before['promotion_failure'] is None else 'replace'))
        if after['execution_state']=='STOPPED':failure_events.add('stop')
        if before['execution_state']=='STOPPED' and after['execution_state']!='STOPPED':failure_events.add('resume_stopped')
        if failure_events:
            if failure_validator is None:raise Conflict('independent failure/evidence/notes validator required')
            if failure_validator(copy.deepcopy(before),copy.deepcopy(after),frozenset(failure_events)) is not True:raise Conflict('independent failure/evidence/notes validator did not pass')
        if after['execution_state'] in {'publication_queued','PROMOTED'}:
            if quality_validator is None:raise Conflict('current qualification validator required')
            if quality_validator(copy.deepcopy(after)) is not True:raise Conflict('current qualification validator did not pass')
        payload=(json.dumps(after,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
        tmp=None
        try:
            fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
            with os.fdopen(fd,'wb') as f:
                f.write(payload);f.flush();os.fsync(f.fileno())
            fault_hook('after_temp_fsync')
            safe_path(path);safe_path(lockpath)
            if path.read_bytes()!=raw:raise Conflict('tracker changed before replacement')
            commit_now=time.time_ns() if now_ns is None else now_ns
            if commit_now>=lease['expires_at']:raise Conflict('lease expired during validation')
            fault_hook('before_replace')
            os.replace(tmp,path);tmp=None
            fault_hook('after_replace')
            fd=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            if tmp is not None:os.unlink(tmp)
        return {'revision':after['revision'],'sha256':sha(payload),'tracker':after}

def migration_proposal(path,out_dir):
    """No automatic candidate: preserve exact bytes and enumerate every issue."""
    path=safe_path(path);raw=path.read_bytes();out=Path(out_dir);out.mkdir(parents=True,exist_ok=False)
    snapshot=out/'historical-tracker.json';snapshot.write_bytes(raw)
    with snapshot.open('rb') as f:os.fsync(f.fileno())
    try:validate_tracker(strict_json(raw));issues=[]
    except InvalidTracker as e:issues=e.issues
    except ValueError as e:issues=[('',str(e))]
    result={'schema_version':'harbor-tracker-migration-proposal/v1','historical_snapshot':{'path':str(snapshot.resolve()),'sha256':sha(raw)},'source_path':str(path),'issues':[{'pointer':p,'reason':r} for p,r in issues],'candidate':None,'status':'explicit_review_required' if issues else 'already_valid','requirements':['Do not apply this report to a live tracker.','Provide explicit reviewed decisions for every issue; no inferred aliases or failure relabeling.','Retain this exact snapshot and hash in canonical diagnostics/handoffs of any approved migration.','Independently revalidate financial evidence, failure classification, budgets and qualification after structural migration.']}
    (out/'proposal.json').write_text(json.dumps(result,indent=2)+'\n')
    assert path.read_bytes()==raw
    return result
