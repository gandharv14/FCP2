"""Explicit code-only refresh chain for completed legacy v1 qualifications.

Original v1 journals and APIs are untouched. This operation does not dispatch,
change task bytes, create a release, reset semantic budgets, or infer history.
"""
import copy
import json
from pathlib import Path
import time
import uuid
from scripts.batch import harbor_legacy_requalification as first
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_lane_notes as notes
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_transition_codec import encode,decode
from scripts.batch.harbor_complete_readiness import ReadinessSelection
import xl_release_publication as release
schema=first.schema
SCHEMA='harbor-legacy-requalification-chain/v2'
ROOT=first.ROOT


def _code():return sorted(first._code()+[record(__file__)],key=lambda row:row['path'])


def _drift(rows):
    own=str(Path(__file__).resolve());selected=[r for r in rows if r['path']!=own]
    changes=first._drift(selected)
    for row in rows:
        if row['path']==own and record(own)!=row:
            changes.append(dict(path=own,prior_sha256=row['sha256'],current_sha256=record(own)['sha256']))
    return changes


def _snapshot_changes(prior,code):
    current=code if prior['schema_version']==SCHEMA else [r for r in code if r['path']!=str(Path(__file__).resolve())]
    a={r['path']:r['sha256'] for r in prior['code']};b={r['path']:r['sha256'] for r in current}
    return [{'path':p,'before':a.get(p),'after':b.get(p)} for p in sorted(set(a)|set(b)) if a.get(p)!=b.get(p)]


def _delta(before,selection):
    missing=set(before['bindings'])-set(selection.content.bindings)
    if missing:
        require(missing=={'oracle_allowlist'},'historical binding omitted')
        old=before['bindings']['oracle_allowlist'];path=schema.safe_path(old['path'])
        require(set(old)=={'path','sha256'} and old['sha256'] is None and selection.content.allowlist is None and not path.exists() and not path.is_symlink(),'only absent allowlist sentinel may be omitted')
    changes={k:{'before':before['bindings'].get(k),'after':v} for k,v in selection.content.bindings.items() if before['bindings'].get(k)!=v}
    require(set(changes)<=first.UPGRADES,'refresh changed a non-audit/applicability binding')
    return changes


def _counts(proof):
    values=proof['semantic_attempts']
    require(set(values)=={'harbor-instruction-naturalizer','harbor-dialogue-author'} and all(type(v) is int and 1<=v<=2 for v in values.values()),'invalid retained semantic counts')
    return values


def _proof(before,baseline,previous_counts,selection,published,*,trusted_public_key):
    require(isinstance(selection,ReadinessSelection) and selection.runtime is not None and selection.observation is not None,'current runtime selection/observation required')
    require(selection.content.trusted_public_key==trusted_public_key,'root trust key changed')
    _delta(before,selection);publication=first._release(before,selection,published)
    # Owning runtime validation checks its exact current code/image/input hashes.
    # A prior observation can survive unrelated code changes; affected runtime
    # code cannot reuse it and must provide a separately executed fresh record.
    proof=first.complete_readiness(selection,root=ROOT)
    require(proof.get('schema_version')=='harbor-complete-readiness/v1' and proof.get('workbook')==before['workbook_id'] and proof.get('stage_sha256')==selection.content.stage_sha256 and proof.get('publication_ready') is True and proof.get('blockers')==[] and proof.get('checks') and all(v.get('checked') is True for v in proof['checks'].values()),'current complete owning readiness failed')
    observed=read(selection.observation.execution)
    require(type(observed.get('started_at_ns')) is int and observed['started_at_ns']>baseline['updated_at'],'runtime observation predates original legacy baseline')
    counts={'harbor-instruction-naturalizer':proof['checks']['naturalization_current_context']['result']['attempts_used'],'harbor-dialogue-author':proof['checks']['dialogue_review_provenance']['result']['round']}
    _counts({'semantic_attempts':counts})
    require(all(counts[k]>=max(previous_counts[k],before['lane_state'][k].get('attempt',0)) for k in counts),'previously consumed semantic budget regressed')
    return dict(readiness=proof,publication=publication,semantic_attempts=counts)


def _render(before,d,tx):
    require(type(d['at']) is int and d['at']>before['updated_at'],'invalid chain chronology')
    selected=decode(d['selection']);changes=_delta(before,selected)
    text=('\n'.join(['# Harbor '+before['workbook_id']+' legacy code refresh','',
        'Task bytes, release, original legacy chronology and all prior transactions are unchanged.',
        'Previous qualification: '+d['previous']['path'],
        'Previous qualification SHA256: '+d['previous']['sha256'],
        'Prior tracker SHA256: '+d['before']['sha256'],
        'Current owning proof SHA256: '+d['proof']['sha256'],
        'No model calls, budget resets or strict historical creation are claimed.',''])).encode()
    after=copy.deepcopy(before);after['revision']+=1;after['updated_at']=d['at'];after['bindings'].update({k:v['after'] for k,v in changes.items()})
    after['handoffs'].append(dict(schema_version=SCHEMA,operation='refresh_legacy_requalification',revision=after['revision'],at=d['at'],prior_tracker_sha256=d['before']['sha256'],previous=d['previous'],sequence=d['sequence'],journal={'path':str(tx/'transaction.json'),'sha256':schema.sha(lifecycle.canonical(d))},release_id=before['promotion_ids']['release_id'],binding_changes=changes,strict_history_claimed=False))
    after['agent_notes']=dict(before['agent_notes'],path=str(tx/'notes.md'),sha256=schema.sha(text),updated_at=d['at'],diagnostic_signature=None,evidence_set_sha256=schema.sha(lifecycle.canonical(d['evidence'])))
    require(set(after)==set(before) and all(after[k]==before[k] for k in set(before)-first.CHANGED),'legacy history/budget/task fields changed')
    return after,text


def inspect_chain(tracker,journal,*,trusted_public_key):
    """Read-only integrity of every link, then current/stale owning qualification."""
    tracker=schema.safe_path(tracker);pending=[];seen=set();bound=journal
    while True:
        lifecycle.checked_record(bound);require(bound['path'] not in seen,'qualification chain cycle');seen.add(bound['path'])
        d=read(bound);tx=Path(bound['path']).parent;require(Path(bound['path'])==tx/'transaction.json','invalid chain journal path')
        if d.get('schema_version')==first.SCHEMA:break
        require(d.get('schema_version')==SCHEMA,'chain must anchor in a completed v1 transaction')
        pending.append((bound,d,tx));bound=d['previous']
    original,after,selection,published,changes,publication=first._check_descriptor(tracker,tx,d,trusted_public_key=trusted_public_key,allow_stale=True)
    require(after['handoffs'][-1]['journal']==bound,'original journal differs from sealed v1 event')
    require(read(record(tx/'complete.json'))=={'tracker_sha256':schema.sha((tx/'after.json').read_bytes())},'original v1 CAS is incomplete')
    counts=_counts(read(d['proof']));baseline=d['before'];sequence=0;previous=bound;prior=d;transactions=[bound]
    for bound,d,tx in reversed(pending):
        require(set(d)=={'schema_version','root_operation','workbook','tracker','at','sequence','previous','baseline','before','prior_notes','proof','selection','published','code','evidence','prior_code_changes'} and d['root_operation']=='refresh_legacy_requalification','unexpected chain transaction fields')
        require(d['workbook']==tracker.stem and Path(d['tracker'])==tracker and tx.parent==tracker.parent/(tracker.stem+'-legacy-refreshes'),'foreign chain transaction')
        require(d['sequence']==sequence+1 and d['previous']==previous and d['baseline']==baseline,'qualification ancestry changed')
        for key,name in (('before','before.json'),('prior_notes','prior-notes.md'),('proof','proof.json')):
            lifecycle.checked_record(d[key]);require(Path(d[key]['path'])==tx/name,'chain evidence escaped')
        require(read(d['before'])==after and (tx/'before.json').read_bytes()==lifecycle.canonical(after),'prior tracker is not previous completed qualification')
        require((tx/'prior-notes.md').read_bytes()==Path(after['agent_notes']['path']).read_bytes(),'prior notes changed')
        snapshot=_snapshot_changes(prior,d['code']);require(bool(snapshot) and snapshot==d['prior_code_changes'],'previous qualification was not code-only stale under selected snapshot')
        changes=_drift(d['code']+d['evidence'])
        if d['code']!=_code() and not changes:changes=[dict(kind='registered_code_inventory',prior=d['code'],current=_code())]
        selected,new_published=decode(d['selection']),decode(d['published'])
        require(new_published==published,'release selection changed between qualifications')
        expected,text=_render(after,d,tx)
        require(expected['handoffs'][-1]['journal']==bound,'chain journal differs from sealed event')
        require((tx/'after.json').read_bytes()==lifecycle.canonical(expected) and (tx/'notes.md').read_bytes()==text,'prepared chain result changed')
        publication=first._release(after,selected,published,live=not bool(changes))
        saved=read(d['proof']);new_counts=_counts(saved);require(all(new_counts[k]>=counts[k] for k in counts),'historical semantic consumption regressed')
        if not changes:
            actual=_proof(after,original,counts,selected,published,trusted_public_key=trusted_public_key)
            require(saved==actual,'current owning proof differs from sealed chain proof')
        require(selected.content.trusted_public_key==trusted_public_key,'root-selected trust key differs')
        complete=tx/'complete.json'
        # The caller distinguishes a prepared final link from committed ancestors.
        if bound!=journal:require(read(record(complete))=={'tracker_sha256':schema.sha((tx/'after.json').read_bytes())},'ancestor CAS is incomplete')
        after,selection,counts=expected,selected,new_counts;sequence+=1;previous=bound;prior=d;transactions.append(bound)
    return dict(after=after,baseline=baseline,original=original,selection=selection,published=published,changes=changes,publication=publication,counts=counts,sequence=sequence,descriptor=prior,transactions=transactions)


def refresh(tracker_path,previous,selection,published,*,authority,expected_revision,expected_sha256,trusted_public_key,clock=time.time_ns,fault_hook=lambda _:None):
    tracker=schema.safe_path(tracker_path)
    with lifecycle.locked(tracker),release._workbook_lock(published.publication_root):
        raw=tracker.read_bytes();before=json.loads(raw)
        require(before['revision']==expected_revision and schema.sha(raw)==expected_sha256,'refresh tracker CAS conflict')
        parent=inspect_chain(tracker,previous,trusted_public_key=trusted_public_key)
        require(before==parent['after'] and raw==lifecycle.canonical(parent['after']),'tracker differs from selected prior chain head')
        oldtx=Path(previous['path']).parent
        require(read(record(oldtx/'complete.json'))=={'tracker_sha256':schema.sha(raw)},'prior qualification CAS incomplete')
        require(parent['changes'],'current qualification is not code-only stale')
        require(published==parent['published'],'root-selected release changed')
        lifecycle.authorize(authority,'refresh_legacy_requalification',tracker,before,dict(previous=previous,selection=encode(selection),published=encode(published),tracker_sha256=expected_sha256))
        code=_code();proof=_proof(before,parent['original'],parent['counts'],selection,published,trusted_public_key=trusted_public_key)
        txroot=tracker.parent/(tracker.stem+'-legacy-refreshes');txroot.mkdir(exist_ok=True);schema.safe_path(txroot)
        old_paths={Path(row['path']).parent for row in parent['transactions'][1:]}
        require(set(txroot.iterdir())==old_paths,'orphan or incomplete refresh requires explicit recovery')
        tx=txroot/(str(expected_revision+1)+'-'+uuid.uuid4().hex);tx.mkdir()
        notes._immutable(tx/'before.json',raw);notes._immutable(tx/'prior-notes.md',Path(before['agent_notes']['path']).read_bytes());notes._immutable(tx/'proof.json',lifecycle.canonical(proof))
        evidence=first._records(proof['readiness']['evidence']+code)
        d=dict(schema_version=SCHEMA,root_operation='refresh_legacy_requalification',workbook=tracker.stem,tracker=str(tracker),at=clock(),sequence=parent['sequence']+1,previous=previous,baseline=parent['baseline'],before=record(tx/'before.json'),prior_notes=record(tx/'prior-notes.md'),proof=record(tx/'proof.json'),selection=encode(selection),published=encode(published),code=code,evidence=evidence,prior_code_changes=_snapshot_changes(parent['descriptor'],code))
        after,text=_render(before,d,tx);notes._immutable(tx/'after.json',lifecycle.canonical(after));notes._immutable(tx/'notes.md',text)
        fault_hook('before_descriptor');notes._immutable(tx/'transaction.json',lifecycle.canonical(d));notes._fsync_directory(tx);notes._fsync_directory(txroot);fault_hook('prepared')
        checked=inspect_chain(tracker,record(tx/'transaction.json'),trusted_public_key=trusted_public_key);require(not checked['changes'],'refresh code changed before CAS')
        require(tracker.read_bytes()==raw,'tracker changed before refresh CAS')
        notes._replace(tracker,lifecycle.canonical(after));fault_hook('after_tracker_cas')
        notes._immutable(tx/'complete.json',lifecycle.canonical({'tracker_sha256':schema.sha(lifecycle.canonical(after))}));notes._fsync_directory(tx)
        return dict(revision=after['revision'],sha256=record(tracker)['sha256'],journal=record(tx/'transaction.json'))


def recover(tracker_path,journal,*,authority,trusted_public_key):
    tracker=schema.safe_path(tracker_path);d=read(journal);published=decode(d['published']);tx=Path(journal['path']).parent
    with lifecycle.locked(tracker),release._workbook_lock(published.publication_root):
        result=inspect_chain(tracker,journal,trusted_public_key=trusted_public_key)
        lifecycle.authorize(authority,'recover_legacy_refresh',tracker,read(d['before']),dict(journal=journal))
        raw=tracker.read_bytes();old=(tx/'before.json').read_bytes();new=(tx/'after.json').read_bytes()
        require(raw in (old,new),'tracker diverged from prepared refresh CAS')
        if raw==old:require(not result['changes'],'uncommitted stale proof cannot be promoted');notes._replace(tracker,new)
        complete=tx/'complete.json';expected={'tracker_sha256':schema.sha(new)}
        if complete.exists():require(read(record(complete))==expected,'completion marker differs')
        else:notes._immutable(complete,lifecycle.canonical(expected));notes._fsync_directory(tx)
        return dict(revision=result['after']['revision'],sha256=record(tracker)['sha256'],journal=journal,state='STALE' if result['changes'] else 'PROMOTED')


def validate(source,tracker_path,*,trusted_public_key):
    tracker=schema.safe_path(tracker_path);raw=tracker.read_bytes();current=json.loads(raw)
    require(current['execution_state']=='PROMOTED' and source['workbook_id']==current['workbook_id'] and record(source['path'])==current['bindings']['original_raw_source'] and source['sha256']==current['bindings']['original_raw_source']['sha256'],'original tracker identity changed')
    last=current['handoffs'][-1];require(last.get('schema_version')==SCHEMA,'current tracker is not a refresh-chain head')
    journal=last['journal'];result=inspect_chain(tracker,journal,trusted_public_key=trusted_public_key)
    require(current==result['after'] and raw==(Path(journal['path']).parent/'after.json').read_bytes(),'tracker diverged from sealed chain')
    require(read(record(Path(journal['path']).parent/'complete.json'))=={'tracker_sha256':schema.sha(raw)},'current refresh CAS incomplete')
    require(tracker.read_bytes()==raw,'tracker changed during qualification');changed=bool(result['changes'])
    return dict(state='STALE' if changed else 'PROMOTED',prior_promotion=True,verified_promoted=not changed,revalidation_required=changed,code_changes=result['changes'],qualification_kind='current_legacy_refresh_chain',tracker=str(tracker),tracker_sha256=schema.sha(raw),failure=None,generation_ids=current['generation_ids'],release_id=result['published'].release_id,release_path=str(result['published'].publication_root/'releases'/result['published'].release_id),output_path=result['publication']['task_path'],output_sha256=result['publication']['published_tree_sha256'],task_content_sha256=result['selection'].content.stage_sha256,evidence_sha256=journal['sha256'],sequence=result['sequence'])
