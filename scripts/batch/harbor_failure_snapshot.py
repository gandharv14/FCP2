"""Preserve exact failed evidence before an owning mutable-state repair.

A snapshot is historical evidence only. It never establishes a repaired gate or
changes a semantic budget. Already overwritten evidence is rejected.
"""
import copy
import os
from pathlib import Path
import shutil
import time
import uuid
from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_lane_notes as notes
from scripts.batch.harbor_admission import read,record,require

SCHEMA='harbor-failure-snapshot/v1'
MAX_FILE=256*1024*1024
MAX_TOTAL=1024*1024*1024


def capture(before,directory):
    failure=before['promotion_failure'];require(failure is not None,'no synchronized failure to snapshot')
    notes.validate_notes_pair(before,Path(before['agent_notes']['path']).read_bytes())
    rows=notes.evidence_list(failure['evidence_paths']);lifecycle.check_records(rows)
    directory=schema.safe_path(directory);directory.mkdir(exist_ok=False)
    # These generations are immutable historical artifacts, not repair targets.
    immutable=[schema.safe_path(before['bindings'][k]['path']) for k in ('source_generation','segmentation_generation') if before['bindings'].get(k)]
    copies=[];total=0
    for index,item in enumerate(rows):
        source=schema.safe_path(item['path'])
        if any(source.is_relative_to(root) for root in immutable):
            copies.append({'original':item,'retained':item,'storage':'immutable_generation'});continue
        size=source.stat().st_size;total+=size
        require(size<=MAX_FILE and total<=MAX_TOTAL,'failure snapshot exceeds bounded storage budget')
        target=directory/('%05d.blob'%index)
        with source.open('rb') as src,target.open('xb') as dst:
            shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
        require(source.stat().st_ino!=target.stat().st_ino,'snapshot shares original inode')
        bound=record(target);require(bound['sha256']==item['sha256'],'source changed while snapshotting')
        target.chmod(0o444)
        copies.append({'original':item,'retained':bound,'storage':'independent_copy'})
    lifecycle.check_records(rows)
    document={'schema_version':SCHEMA,'workbook':before['workbook_id'],'diagnostic_signature':failure['diagnostic_signature'],
        'original_evidence':rows,'copies':copies,'original_repairs':copy.deepcopy(before['repairs']),
        'original_lane_attempts':{k:v.get('attempt') for k,v in before['lane_state'].items()}}
    notes._immutable(directory/'snapshot.json',lifecycle.canonical(document));notes._fsync_directory(directory)
    return record(directory/'snapshot.json')


def validate(before,binding):
    data=read(binding);failure=before['promotion_failure'];directory=Path(binding['path']).parent
    require(set(data)=={'schema_version','workbook','diagnostic_signature','original_evidence','copies','original_repairs','original_lane_attempts'},'snapshot schema changed')
    require(data['schema_version']==SCHEMA and data['workbook']==before['workbook_id'],'foreign snapshot')
    require(data['diagnostic_signature']==failure['diagnostic_signature'],'snapshot signature differs')
    expected=notes.evidence_list(failure['evidence_paths'])
    require(data['original_evidence']==expected and [c['original'] for c in data['copies']]==expected,'snapshot omitted or substituted original evidence')
    require(data['original_repairs']==before['repairs'] and data['original_lane_attempts']=={k:v.get('attempt') for k,v in before['lane_state'].items()},'snapshot consumed budgets differ')
    immutable=[schema.safe_path(before['bindings'][k]['path']) for k in ('source_generation','segmentation_generation') if before['bindings'].get(k)]
    retained=[]
    for index,item in enumerate(data['copies']):
        require(set(item)=={'original','retained','storage'},'invalid snapshot item')
        old,new=item['original'],item['retained'];lifecycle.checked_record(new)
        require(old['sha256']==new['sha256'],'snapshot bytes differ from original failed observation')
        if item['storage']=='immutable_generation':
            require(old==new and any(Path(old['path']).is_relative_to(root) for root in immutable),'unapproved immutable storage')
        else:
            require(item['storage']=='independent_copy' and Path(new['path'])==directory/('%05d.blob'%index),'snapshot path escaped or storage changed')
            require(Path(new['path']).is_file(),'snapshot file missing')
            original=Path(old['path'])
            if original.exists():require(original.stat().st_ino!=Path(new['path']).stat().st_ino,'snapshot shares original inode')
        retained.append(new)
    return notes.evidence_list(retained+[binding])


def after_snapshot(before,binding,now,notespath):
    evidence=validate(before,binding);after=copy.deepcopy(before);after['revision']+=1;after['updated_at']=now
    failure=after['promotion_failure'];failure['evidence_paths']=evidence
    after['failure_history'].append(dict(event='snapshot',revision=after['revision'],failure=copy.deepcopy(failure),result='exact_pre_repair_evidence_preserved',evidence_paths=evidence))
    after['handoffs'].append(dict(schema_version=SCHEMA,revision=after['revision'],snapshot=binding,original_failure=copy.deepcopy(before['promotion_failure']),budgets_unchanged=True))
    after['agent_notes'].update(path=str(notespath),sha256=None,evidence_set_sha256=schema.sha(lifecycle.canonical(evidence)),updated_at=now)
    rendered=notes.render_notes(after);after['agent_notes']['sha256']=schema.sha(rendered)
    schema.validate_tracker(after);lifecycle.validate_lifecycle(after);notes.validate_notes_pair(after,rendered)
    return after,rendered


class FailureSnapshots(notes.LaneNotes):
    def snapshot_failure(self,wb,*,expected_revision,expected_sha256,owner,token,_fault=lambda _:None):
        path=self._path(wb)
        with lifecycle.locked(path):
            self.require_synchronized(wb);raw=path.read_bytes();before=schema.strict_json(raw)
            now=self._active(before,raw,expected_revision,expected_sha256,owner,token,'workbook:'+wb)
            require(before['promotion_failure'] is not None,'no current failure')
            journal=path.parent/(wb+'-notes-transactions');journal.mkdir(exist_ok=True)
            tx=journal/(str(before['revision']+1)+'-snapshot-'+str(uuid.uuid4()));tx.mkdir()
            binding=capture(before,tx/'evidence')
            notespath=path.with_name(wb+'-agent-notes.md');after,rendered=after_snapshot(before,binding,now,notespath)
            notes._immutable(tx/'before.json',raw);notes._immutable(tx/'after.json',lifecycle.canonical(after));notes._immutable(tx/'notes.md',rendered)
            notes._immutable(tx/'prior-notes.md',notespath.read_bytes())
            descriptor={'schema_version':SCHEMA,'workbook':wb,'snapshot':binding,'before':record(tx/'before.json'),'after':record(tx/'after.json'),'notes':record(tx/'notes.md'),'prior_notes':record(tx/'prior-notes.md')}
            notes._immutable(tx/'transaction.json',lifecycle.canonical(descriptor));notes._fsync_directory(tx);notes._fsync_directory(journal);_fault('prepared')
            self._active(before,raw,expected_revision,expected_sha256,owner,token,'workbook:'+wb)
            require(path.read_bytes()==raw,'tracker changed before snapshot commit')
            # Until the pair is committed, originals must still equal observation.
            lifecycle.check_records(before['promotion_failure']['evidence_paths'])
            notes._replace(notespath,rendered);_fault('notes_replaced')
            self._active(before,raw,expected_revision,expected_sha256,owner,token,'workbook:'+wb)
            notes._replace(path,lifecycle.canonical(after));_fault('tracker_replaced')
            notes._immutable(tx/'complete.json',lifecycle.canonical(dict(tracker_sha256=record(path)['sha256'],notes_sha256=record(notespath)['sha256'])));notes._fsync_directory(tx)
            return dict(revision=after['revision'],sha256=record(path)['sha256'],tracker=after,journal=record(tx/'transaction.json'))

    def recover_snapshot(self,wb,journal,*,owner,token):
        path=self._path(wb);tx=Path(journal['path']).parent
        require(tx.parent==path.parent/(wb+'-notes-transactions') and Path(journal['path']).name=='transaction.json','foreign snapshot transaction')
        with lifecycle.locked(path):
            d=read(journal);require(d['schema_version']==SCHEMA and d['workbook']==wb,'snapshot transaction identity differs')
            for key,name in [('before','before.json'),('after','after.json'),('notes','notes.md'),('prior_notes','prior-notes.md')]:
                require(Path(d[key]['path'])==tx/name,'snapshot journal escape');lifecycle.checked_record(d[key])
            require(Path(d['snapshot']['path'])==tx/'evidence/snapshot.json','snapshot manifest escaped')
            before=read(d['before']);after=read(d['after']);schema.validate_tracker(before);lifecycle.validate_lifecycle(before)
            lease=before['leases']['task_agent'];require(lease and (lease['owner'],lease['token'],lease['scope'])==(owner,token,'workbook:'+wb),'wrong snapshot lease identity')
            require(lease['heartbeat_at']<=after['updated_at']<lease['expires_at'],'snapshot prepared outside lease')
            prior=Path(d['prior_notes']['path']).read_bytes()
            require(prior==notes.render_notes(before) and schema.sha(prior)==before['agent_notes']['sha256'],'original synchronized notes changed')
            rebuilt,rendered=after_snapshot(before,d['snapshot'],after['updated_at'],path.with_name(wb+'-agent-notes.md'))
            require(after==rebuilt and Path(d['notes']['path']).read_bytes()==rendered,'forged snapshot transition')
            old=Path(d['before']['path']).read_bytes();new=Path(d['after']['path']).read_bytes();require(path.read_bytes() in (old,new),'tracker diverged')
            notespath=Path(after['agent_notes']['path']);require(notespath.read_bytes() in (prior,rendered),'notes diverged')
            done=tx/'complete.json';expected=dict(tracker_sha256=d['after']['sha256'],notes_sha256=d['notes']['sha256'])
            if done.exists():require(read(record(done))==expected,'invalid completion marker')
            notes._replace(notespath,rendered);notes._replace(path,new)
            if not done.exists():notes._immutable(done,lifecycle.canonical(expected));notes._fsync_directory(tx)
            return dict(revision=after['revision'],sha256=record(path)['sha256'],tracker=after)

    def begin_repair(self,wb,*,expected_revision,expected_sha256,owner,token):
        """Invalidate the fixed owning/downstream lanes after exact archival.

        This authorizes no new semantic rounds and does not edit task content.
        The owning repair must still use its existing bounded protocol.
        """
        from scripts.batch.harbor_completed_lanes import STEPS
        path=self._path(wb);self.require_synchronized(wb)
        before=schema.strict_json(path.read_bytes());failure=before['promotion_failure']
        require(before['execution_state']=='running' and failure is not None,'active classified failure required')
        snapshot=next((h for h in reversed(before['handoffs']) if h.get('schema_version')==SCHEMA),None)
        require(snapshot is not None and snapshot['original_failure']['diagnostic_signature']==failure['diagnostic_signature'],'pre-repair immutable snapshot required')
        binding=snapshot['snapshot'];require(binding in failure['evidence_paths'],'snapshot is not bound to current failure');read(binding)
        kinds=[kind for kind,_ in STEPS];require(failure['gate'] in kinds,'owning repair gate is not supported')
        index=kinds.index(failure['gate']);affected={lane for _,lane in STEPS[index:]}
        def mutate(after):
            self.require_synchronized(wb)
            require(after['promotion_failure']==failure,'failure changed before invalidation')
            for lane in affected:
                row=after['lane_state'][lane]
                row.update(state='repairing' if lane==failure['lane'] else 'pending',current_confidence='low' if lane==failure['lane'] else None)
                row['confidence_history'].append(dict(level=row['current_confidence'],reason='fixed_downstream_invalidation',revision=expected_revision+1))
            normalization=after['lane_state']['harbor-normalization-engineer']
            if index<=kinds.index('normalization_phase1'):normalization['phase']=None
            elif index<=kinds.index('normalization_phase2'):normalization.update(state='running',phase='phase1_complete')
            levels=[(k,v['current_confidence']) for k,v in after['lane_state'].items() if v.get('current_confidence')]
            after['current_confidence']=dict(level=min((v for _,v in levels),key={'low':0,'medium':1,'high':2}.get),reason_codes=['fixed_downstream_invalidation'],contributors=[k for k,_ in levels],computed_at_revision=expected_revision+1)
            after['confidence_history'].append(copy.deepcopy(after['current_confidence']))
            after['handoffs'].append(dict(schema_version='harbor-fixed-repair-start/v1',revision=expected_revision+1,diagnostic_signature=failure['diagnostic_signature'],snapshot=binding,invalidated_lanes=sorted(affected),budgets_unchanged=True))
        return schema.cas_mutate(path,expected_revision=expected_revision,expected_sha256=expected_sha256,owner=owner,token=token,scope='workbook:'+wb,mutate=mutate,allowed_fields={'lane_state','current_confidence','confidence_history','handoffs'},allowed_lanes=affected)
