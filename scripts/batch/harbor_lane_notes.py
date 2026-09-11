"""Fixed source/segmentation lane operations and root-owned recoverable notes.

No arbitrary mutation payloads, validator callbacks or supplied PASS flags.
Other lane adapters remain closed until their production validators are wired.
"""
import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch.harbor_admission import record, read, require
from scripts.corpus import digest

SOURCE='harbor-source-warden'
SEGMENTATION='harbor-segmentation-analyst'


@dataclass(frozen=True)
class GateEvidence:
    kind: str
    manifest: dict
    normalizations: tuple = ()


@dataclass(frozen=True)
class RootContext:
    tracker_root: Path
    trusted_public_key: str = '/etc/harbor/excel-runner-public.pem'


def _files(directory):
    return [record(p) for p in sorted(directory.rglob('*')) if p.is_file()]


def _source(before, packet, context):
    from xl_source_publication import validate_source_generation,validate_ast_provenance,create_ast_provenance
    from xl_source_health import inspect_workbook
    from scripts.batch.harbor_source_evidence import validate_source_lineage
    value=read(packet.manifest);directory=Path(packet.manifest['path']).parent
    require(value.get('generation_id')==directory.name,'source generation identity mismatch')
    manifest=validate_source_generation(directory,expected_generation_id=directory.name)
    wb=before['workbook_id'];require(manifest['layout']['workbook_id']==wb,'foreign source generation')
    source=directory/manifest['layout']['source_workbook'];original=before['bindings']['original_raw_source']
    lifecycle.checked_record(original);lifecycle.checked_record(before['raw_source'])
    require(digest(source)==before['raw_source']['sha256'],'source differs from immutable admitted candidate')
    health=inspect_workbook(source);require(health==read(record(directory/'health.json')),'current source health changed')
    records=[read(r) for r in packet.normalizations]
    validate_source_lineage(original['path'],directory,records,trusted_public_key=context.trusted_public_key)
    ast=directory/manifest['layout']['ast_directory'];args=dict(production=True,workbook_id=wb,max_range_expand=100,include_constants=True,include_cached_values=True)
    old=validate_ast_provenance(source,ast,directory/'ast-provenance.json',builder_args=args)
    current=create_ast_provenance(source,ast,builder_args=args)
    project=lambda p:[{k:r[k] for k in ('name','sha256','size_bytes')} for r in p['builder']['code']]
    require(project(old)==project(current),'current AST builder changed')
    evidence=_files(directory)+list(packet.normalizations)
    for n in records:
        evidence += [record(n[k]) for k in ('plan','manifest','candidate','curation') if n.get(k)]
    bindings=dict(source_generation={'path':str(directory),'id':directory.name,'manifest_sha256':packet.manifest['sha256']},source_file=record(source),source_health=record(directory/'health.json'),ast_workbook_root={'path':str(ast)})
    return dict(lane=SOURCE,gate='source',passed=True,bindings=bindings,evidence=evidence,classification=None,terminal=False)


def check_gate(before,packet,context):
    from scripts.batch.harbor_completed_lanes import CompletionEvidence, evaluate_completed_gate
    if isinstance(packet,CompletionEvidence):
        return evaluate_completed_gate(before,packet,context)
    require(isinstance(packet,GateEvidence),'typed gate evidence required')
    if packet.kind=='source':return _source(before,packet,context)
    require(packet.kind=='segmentation','unsupported lane needs its concrete owning validator')
    require(before['lane_state'][SOURCE].get('state')=='passed','source lane has not passed')
    source_binding=before['bindings']['source_generation'];source=Path(source_binding['path'])
    source_packet=GateEvidence('source',record(source/'generation-manifest.json'),packet.normalizations)
    require(source_packet.manifest['sha256']==source_binding['manifest_sha256'],'bound source generation changed')
    source_result=_source(before,source_packet,context)
    from xl_seg.publication import resolve_generation_by_id
    from xl_preflight import classify
    declared=read(packet.manifest);directory=Path(packet.manifest['path']).parent
    require(declared.get('generation_id')==directory.name,'segmentation generation identity mismatch')
    gen,manifest=resolve_generation_by_id(directory.parent.parent,directory.name,
        source_path=source_result['bindings']['source_file']['path'],
        ast_dir=source_result['bindings']['ast_workbook_root']['path'],source_generation_dir=source,
        require_pass=False,validate_live_evidence=True)
    curation=record(gen/'curation.toml')
    old_cur=before['bindings'].get('curation')
    require(old_cur is None or old_cur['sha256']==curation['sha256'],'immutable curation changed')
    data=read(record(gen/'segments.json'));require(data['workbook']==before['workbook_id'],'foreign segmentation generation')
    verification=data['verification'];classification,_=classify(verification,True)
    if classification=='healthy':
        resolve_generation_by_id(directory.parent.parent,directory.name,source_path=source_result['bindings']['source_file']['path'],ast_dir=source_result['bindings']['ast_workbook_root']['path'],source_generation_dir=source,require_pass=True,validate_live_evidence=True)
    terminal=any(b.get('output_relevant') is not False and b.get('uniqueness')=='demonstrated_non_unique' for b in verification.get('iterative_blocks',[]))
    diagnosis={key:[{field:item.get(field) for field in fields} for item in verification.get(key,[])] for key,fields in [('failures',('cell','formula','verdict')),('iterative_blocks',('cells','uniqueness','output_relevant','errors','unresolved'))]}
    diagnostic_sha256=schema.sha(lifecycle.canonical(diagnosis))
    return dict(lane=SEGMENTATION,gate='segmentation',passed=classification=='healthy',classification=classification,
                terminal=terminal,diagnostic_sha256=diagnostic_sha256,evidence=source_result['evidence']+_files(gen),
                bindings=dict(segmentation_generation={'path':str(gen),'id':gen.name,'manifest_sha256':packet.manifest['sha256']},curation=curation))


def evidence_list(rows):
    values={r['path']:r for r in rows}
    require(all(values[r['path']]==r for r in rows),'conflicting evidence hashes')
    return [values[k] for k in sorted(values)]


def notes_evidence(tracker):
    latest = tracker['failure_history'][-1] if tracker['failure_history'] else None
    transition = next((h for h in reversed(tracker['handoffs'])
        if h.get('schema_version') == 'harbor-ready-transition/v1'), None)
    if transition and (latest is None or transition['revision'] >= latest['revision']):
        return transition['evidence_paths']
    require(latest is not None, 'canonical notes have no evidence event')
    return latest['evidence_paths']


def render_notes(tracker):
    """Render only root-selected status/classification and bound evidence paths."""
    failure=tracker['promotion_failure'];signature=failure['diagnostic_signature'] if failure else tracker['agent_notes']['diagnostic_signature']
    rows=['# Harbor workbook '+tracker['workbook_id'],'','Batch: '+tracker['batch'],'Status: '+tracker['execution_state'],'Diagnostic signature: '+signature,
          'Evidence set: '+tracker['agent_notes']['evidence_set_sha256'],'']
    if failure:
        rows += ['Blocker: '+failure['class']+'/'+failure['subclass'],'Stage: '+failure['stage'],'Fixability: '+failure['fixability'],'Next action: '+failure['next_action'],'']
    else:rows += ['No current promotion failure. Current status is bound to the evidence below.','']
    for item in tracker['failure_history']:
        f=item['failure']
        rows += [f"Revision {item['revision']}: {item['event']} {f['class']}/{f['subclass']} ({item['result']})"]
    rows += ['','Evidence:']
    for item in notes_evidence(tracker):
        rows += ['- '+item['path']+' (SHA256 '+item['sha256']+')']
    return ('\n'.join(rows)+'\n').encode()


def validate_notes_pair(tracker,notes_bytes):
    expected=render_notes(tracker);require(notes_bytes==expected,'notes content differs from canonical tracker history')
    notes=tracker['agent_notes'];require(schema.sha(notes_bytes)==notes['sha256'],'notes hash mismatch')
    rows=notes_evidence(tracker);require(notes['evidence_set_sha256']==schema.sha(lifecycle.canonical(rows)),'notes evidence set mismatch')
    if tracker['promotion_failure']:
        require(notes['diagnostic_signature']==tracker['promotion_failure']['diagnostic_signature'],'notes diagnostic signature mismatch')
    lifecycle.check_records(rows)
    return True


def _fsync_directory(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def _immutable(path,raw):
    with path.open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())


def _replace(path,raw):
    fd,temp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path);_fsync_directory(path.parent)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def _failure_after(before,result,now,notespath):
    wb=before['workbook_id']
    evidence=evidence_list(result['evidence']);classification=result['classification']
    sig=schema.sha(lifecycle.canonical(dict(workbook=wb,lane=result['lane'],classification=classification,diagnostic_sha256=result['diagnostic_sha256'])))
    previous=before['promotion_failure']
    if not previous or previous['diagnostic_signature']!=sig:
        previous=next((item['failure'] for item in reversed(before['failure_history']) if item.get('failure',{}).get('diagnostic_signature')==sig),None)
    same=previous is not None
    failure=dict(schema_version=1,**{'class':result.get('failure_class','preflight')},subclass=classification,stage=result['gate'],lane=result['lane'],gate=result['gate'],fixability=result.get('fixability','terminal' if result['terminal'] else 'fixable'),summary='Required independent '+result['gate']+' gate failed',why_not_promoted='The owning validator did not accept current evidence',first_seen=previous['first_seen'] if same else now,last_seen=now,occurrence_count=previous['occurrence_count']+1 if same else 1,diagnostic_signature=sig,evidence_paths=evidence,repair_attempts=copy.deepcopy(previous['repair_attempts']) if same else [],next_action='Retain the independently established terminal verdict' if result['terminal'] else 'Route owning gate through its bounded repair protocol')
    after=copy.deepcopy(before);after['promotion_failure']=failure;after['revision']+=1;after['updated_at']=now;after['leases']['task_agent']['heartbeat_at']=now
    after['failure_history'].append(dict(event='stop' if result['terminal'] else ('replace' if before['promotion_failure'] and before['promotion_failure']['diagnostic_signature']!=sig else 'record'),revision=after['revision'],failure=copy.deepcopy(failure),result='terminal' if result['terminal'] else 'repair_required',evidence_paths=evidence))
    after['lane_state'][result['lane']].update(state='terminal' if result['terminal'] else 'repairing',current_confidence='low',evidence_paths=evidence)
    if result.get('attempt') is not None:
        require(result['attempt'] >= (after['lane_state'][result['lane']].get('attempt') or 0), 'consumed attempts decreased')
        after['lane_state'][result['lane']]['attempt']=result['attempt']
    after['lane_state'][result['lane']]['confidence_history'].append(dict(level='low',reason=classification,revision=after['revision']))
    levels=[k for k,v in after['lane_state'].items() if v.get('current_confidence')]
    after['current_confidence']=dict(level='low',reason_codes=[classification],contributors=levels,computed_at_revision=after['revision'])
    after['confidence_history'].append(copy.deepcopy(after['current_confidence']))
    after['current_gate']=result['gate'];after['last_gate']=result['gate']
    if result['terminal']:after.update(execution_state='STOPPED',terminal_verdict='STOPPED',final_status='STOPPED',stop_reason='nonunique_recurrence' if result['gate']=='segmentation' else result['classification'])
    notespath=schema.safe_path(notespath)
    after['agent_notes']=dict(path=str(notespath),sha256=None,diagnostic_signature=sig,evidence_set_sha256=schema.sha(lifecycle.canonical(evidence)),updated_at=now)
    notes=render_notes(after);after['agent_notes']['sha256']=schema.sha(notes)
    return after,notes


class LaneNotes:
    def __init__(self,context):self.context=context

    def _path(self,wb):
        require(isinstance(wb,str) and __import__('re').fullmatch(r'\d{4}',wb),'invalid workbook identity')
        return schema.safe_path(self.context.tracker_root/(wb+'.json'))

    def require_synchronized(self,wb):
        path=self._path(wb);journal=path.parent/(wb+'-notes-transactions')
        for descriptor_path in journal.glob('*/transaction.json'):
            complete=descriptor_path.parent/'complete.json'
            require(complete.is_file(),'pending notes transaction requires recovery')
            descriptor=read(record(descriptor_path));completion=read(record(complete))
            require(completion==dict(tracker_sha256=descriptor['after']['sha256'],notes_sha256=descriptor['notes']['sha256']),'invalid notes completion marker')
            lifecycle.checked_record(descriptor['after']);lifecycle.checked_record(descriptor['notes'])
        tracker=schema.strict_json(path.read_bytes())
        if tracker['agent_notes']['path']:
            validate_notes_pair(tracker,Path(tracker['agent_notes']['path']).read_bytes())
        return True

    def _active(self,before,raw,expected_revision,expected_sha256,owner,token,scope):
        schema.validate_tracker(before);lifecycle.validate_lifecycle(before)
        require(before['revision']==expected_revision and schema.sha(raw)==expected_sha256,'stale tracker CAS')
        lease=before['leases']['task_agent'];now=time.time_ns()
        require(lease and (lease['owner'],lease['token'],lease['scope'])==(owner,token,scope),'wrong lease identity')
        require(lease['acquired_at']<=lease['heartbeat_at']<=now<lease['expires_at'],'lease expired')
        return now

    def complete(self,wb,packet,*,expected_revision,expected_sha256,owner,token):
        path=self._path(wb);self.require_synchronized(wb);before=schema.strict_json(path.read_bytes());result=check_gate(before,packet,self.context)
        require(result['passed'],'owning gate did not pass')
        require(before['execution_state'] not in {'STOPPED','PROMOTED'}, 'terminal tracker requires root-owned resume')
        # A concrete re-pass may be recorded while the old failure remains.
        # Only the separate complete-readiness notes transaction can clear it.
        lane=result['lane'];scope='workbook:'+wb
        changed=any(before['bindings'].get(k)!=v for k,v in result['bindings'].items())
        from scripts.batch.harbor_completed_lanes import downstream as later_lanes
        downstream=later_lanes(packet.kind)-{lane}
        from scripts.batch.harbor_completed_lanes import pending_dependents
        require(not changed or pending_dependents(before,packet.kind),'dependent lanes need explicit invalidation before changed upstream binding')
        def evidence_validator(old,new,changed):
            fresh=check_gate(old,packet,self.context)
            require(fresh==result and fresh['passed'],'owning evidence changed')
            require(all(new['bindings'][k]==v for k,v in fresh['bindings'].items()),'derived bindings differ')
            lifecycle.check_records(fresh['evidence']);return True
        def mutate(after):
            self.require_synchronized(wb)
            fresh=check_gate(before,packet,self.context)
            require(fresh==result and fresh['passed'],'gate changed before lane commit')
            lifecycle.check_records(fresh['evidence'])
            after['bindings'].update(result['bindings'])
            row=after['lane_state'][lane]
            level=result.get('confidence','high')
            row.update(state='running' if result.get('phase')=='phase1_complete' else 'passed',current_confidence=level,current_gate=result['gate'],evidence_paths=evidence_list(result['evidence']))
            if result.get('attempt') is not None:
                require(result['attempt'] >= (row.get('attempt') or 0), 'consumed semantic attempts decreased')
                row['attempt']=result['attempt']
            if result.get('phase') is not None:row['phase']=result['phase']
            if result.get('disposition') is not None:row['disposition']=result['disposition']
            row['confidence_history'].append(dict(level=level,reason='current_independent_gate_pass',revision=expected_revision+1))
            after['last_gate']=result['gate'];after['current_gate']=None;after['execution_state']='running'
            levels=[(k,v['current_confidence']) for k,v in after['lane_state'].items() if v.get('current_confidence')]
            after['current_confidence']=dict(level=min((v for _,v in levels),key={'low':0,'medium':1,'high':2}.get),reason_codes=['current_independent_gate_pass'],contributors=[k for k,_ in levels],computed_at_revision=expected_revision+1)
            after['confidence_history'].append(copy.deepcopy(after['current_confidence']))
            if lane in {SOURCE,SEGMENTATION}:
                kind='source' if lane==SOURCE else 'segmentation'
                after['generation_ids'][kind]=result['bindings'][kind+'_generation']['id']
        return schema.cas_mutate(path,expected_revision=expected_revision,expected_sha256=expected_sha256,owner=owner,token=token,scope=scope,mutate=mutate,allowed_fields={'bindings','lane_state','last_gate','current_gate','execution_state','current_confidence','confidence_history','generation_ids'},allowed_lanes={lane},binding_replacements={k:before['bindings'].get(k) for k in result['bindings']},evidence_validator=evidence_validator)

    def record_failure(self,wb,packet,*,expected_revision,expected_sha256,owner,token,_fault=lambda _:None):
        path=self._path(wb)
        with lifecycle.locked(path):
            self.require_synchronized(wb)
            raw=path.read_bytes();before=schema.strict_json(raw);now=self._active(before,raw,expected_revision,expected_sha256,owner,token,'workbook:'+wb)
            result=check_gate(before,packet,self.context);require(not result['passed'],'cannot record failure for a passing gate')
            require(before['execution_state'] not in {'STOPPED','PROMOTED'},'terminal tracker needs explicit resume')
            notespath=schema.safe_path(path.with_name(wb+'-agent-notes.md'))
            after,notes=_failure_after(before,result,now,notespath)
            schema.validate_tracker(after);lifecycle.validate_lifecycle(after);validate_notes_pair(after,notes)
            journal=schema.safe_path(path.parent/(wb+'-notes-transactions'));journal.mkdir(exist_ok=True)
            tx=schema.safe_path(journal/(str(after['revision'])+'-'+schema.sha(raw)+'-'+str(uuid.uuid4())));tx.mkdir(exist_ok=False)
            _immutable(tx/'before.json',raw);_immutable(tx/'after.json',lifecycle.canonical(after));_immutable(tx/'notes.md',notes)
            oldnotes=notespath.read_bytes() if notespath.exists() else None
            if oldnotes is not None:
                require(before['agent_notes']['path']==str(notespath),'unbound existing notes file')
                validate_notes_pair(before,oldnotes)
            if oldnotes is not None:_immutable(tx/'prior-notes.md',oldnotes)
            descriptor=dict(schema_version='harbor-notes-transaction/v1',workbook=wb,before=record(tx/'before.json'),after=record(tx/'after.json'),notes=record(tx/'notes.md'),prior_notes=record(tx/'prior-notes.md') if oldnotes is not None else None,packet=(dict(kind=packet.kind,manifest=packet.manifest,normalizations=list(packet.normalizations)) if isinstance(packet,GateEvidence) else None), packet_v2=(None if isinstance(packet,GateEvidence) else __import__('scripts.batch.harbor_transition_codec',fromlist=['encode']).encode(packet)))
            _immutable(tx/'transaction.json',lifecycle.canonical(descriptor));_fsync_directory(tx);_fsync_directory(journal);_fault('prepared')
            require(path.read_bytes()==raw,'tracker changed before pair commit');self._active(before,raw,expected_revision,expected_sha256,owner,token,'workbook:'+wb)
            require(check_gate(before,packet,self.context)==result,'gate evidence changed before pair commit')
            _replace(notespath,notes);_fault('notes_replaced')
            self._active(before,raw,expected_revision,expected_sha256,owner,token,'workbook:'+wb)
            _replace(path,lifecycle.canonical(after));_fault('tracker_replaced')
            validate_notes_pair(after,notespath.read_bytes());_immutable(tx/'complete.json',lifecycle.canonical(dict(tracker_sha256=digest(path),notes_sha256=digest(notespath))))
            return dict(revision=after['revision'],sha256=digest(path),tracker=after,journal=record(tx/'transaction.json'))

    def recover(self,wb,journal,*,owner,token):
        """Finish only an exact prepared transaction; never grant or release a lease."""
        path=self._path(wb);tx=Path(journal['path']).parent
        require(tx.parent==path.parent/(wb+'-notes-transactions'),'foreign notes journal')
        with lifecycle.locked(path):
            descriptor=read(journal)
            require(Path(journal['path'])==tx/'transaction.json','invalid journal filename')
            for key,filename in [('before','before.json'),('after','after.json'),('notes','notes.md'),('prior_notes','prior-notes.md')]:
                if descriptor.get(key):require(Path(descriptor[key]['path'])==tx/filename,'journal payload escapes transaction directory')
            complete=tx/'complete.json'
            expected_complete=dict(tracker_sha256=descriptor['after']['sha256'],notes_sha256=descriptor['notes']['sha256'])
            if complete.exists():require(read(record(complete))==expected_complete,'invalid notes completion marker')
            require(descriptor['workbook']==wb and descriptor['schema_version']=='harbor-notes-transaction/v1','invalid journal identity')
            before=read(descriptor['before']);after=read(descriptor['after']);notespath=path.with_name(wb+'-agent-notes.md');notes=Path(descriptor['notes']['path']).read_bytes();lifecycle.checked_record(descriptor['notes'])
            schema.validate_tracker(before);lifecycle.validate_lifecycle(before)
            schema.validate_tracker(after);lifecycle.validate_lifecycle(after);validate_notes_pair(after,notes)
            require(after['revision']==before['revision']+1,'invalid transaction revision')
            lease=before['leases']['task_agent'];require((lease['owner'],lease['token'],lease['scope'])==(owner,token,'workbook:'+wb),'recovery lease identity changed')
            raw=path.read_bytes();before_raw=Path(descriptor['before']['path']).read_bytes();after_raw=Path(descriptor['after']['path']).read_bytes()
            require(raw in (before_raw,after_raw),'tracker diverged from prepared transaction')
            packet=(GateEvidence(**descriptor['packet']) if descriptor['packet'] is not None else __import__('scripts.batch.harbor_transition_codec',fromlist=['decode']).decode(descriptor['packet_v2']));result=check_gate(before,packet,self.context)
            require(not result['passed'],'failure evidence no longer matches journal')
            rebuilt,rebuilt_notes=_failure_after(before,result,after['updated_at'],notespath)
            require(lease['heartbeat_at']<=after['updated_at']<lease['expires_at'],'journal prepared outside live lease')
            require(after==rebuilt and notes==rebuilt_notes,'prepared failure transition was forged')
            prior=descriptor['prior_notes'];oldnotes=Path(prior['path']).read_bytes() if prior else None
            if prior:lifecycle.checked_record(prior)
            actualnotes=notespath.read_bytes() if notespath.exists() else None
            require(actualnotes in (oldnotes,notes),'notes diverged from prepared transaction')
            # Recovery repairs an already prepared pair even if time elapsed.
            # It preserves the exact expired lease; no work/replacement is authorized.
            _replace(notespath,notes);_replace(path,after_raw);validate_notes_pair(after,notespath.read_bytes())
            complete=tx/'complete.json'
            expected_complete=dict(tracker_sha256=digest(path),notes_sha256=digest(notespath))
            if not complete.exists():_immutable(complete,lifecycle.canonical(expected_complete))
            else:require(read(record(complete))==expected_complete,'invalid notes completion marker')
            return dict(revision=after['revision'],sha256=digest(path),tracker=after)
