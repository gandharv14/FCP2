"""Explicit current requalification of an unchanged legacy promoted release.

No legacy creation record is inferred. Root authorizes the exact old tracker and
closed current selection. All current owning validators run before one tracker
CAS, and again on recovery/reporting. Old notes/release/journal remain immutable.
"""
import copy
import json
import os
import re
from pathlib import Path
import time
import uuid

from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_lane_notes as notes
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_complete_readiness import ReadinessSelection, complete_readiness
from scripts.batch.harbor_ready_transitions import ReleaseSelection
from scripts.batch.harbor_transition_codec import decode, encode
import xl_release_publication as release

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'harbor-legacy-requalification/v1'
UPGRADES = frozenset(('audit_equivalence','audit_upgrade_original_code','audit_validator_upgrade',
    'normalization_audit_applicability','normalization_original_locator_code'))
CHANGED = frozenset(('revision','updated_at','bindings','agent_notes','handoffs'))


def _baseline(before, tracker):
    require(before.get('execution_state') == 'PROMOTED' and before.get('promotion_failure') is None,
            'legacy promoted baseline required')
    require(schema.integer(before.get('revision')) and before['revision'] > 0 and
            tracker.name == before['workbook_id'] + '.json', 'legacy tracker identity differs')
    require(isinstance(before.get('handoffs'),list) and not any(e.get('schema_version') in
            (lifecycle.CREATE_SCHEMA,SCHEMA) for e in before['handoffs']),
            'strict or already requalified tracker is not a legacy baseline')
    require(set(before.get('leases',{})) == set(schema.LEASE_KINDS) and
            all(v is None for v in before['leases'].values()), 'legacy leases must be released')
    require(before.get('current_confidence',{}).get('level') == 'high' and
            set(before.get('lane_state',{})) == set(schema.LANES) and
            all(v.get('state') == 'passed' and v.get('current_confidence') == 'high'
                for v in before['lane_state'].values()), 'legacy lane evidence incomplete')
    lifecycle.checked_record({k:before['agent_notes'][k] for k in ('path','sha256')})
    lifecycle.checked_record(before['bindings']['original_raw_source'])


def _delta(before, selection):
    missing=set(before['bindings'])-set(selection.content.bindings)
    if missing:
        require(missing == {'oracle_allowlist'},'historical binding omitted from current selection')
        old=before['bindings']['oracle_allowlist']
        require(isinstance(old,dict) and set(old)=={'path','sha256'} and old['sha256'] is None and
                selection.content.allowlist is None,'only absent unbound allowlist sentinel may be omitted')
        path=schema.safe_path(old['path'])
        require(not path.exists() and not path.is_symlink(),'omitted allowlist path exists')
    changes={k:{'before':before['bindings'].get(k),'after':v}
             for k,v in selection.content.bindings.items() if before['bindings'].get(k) != v}
    require(set(changes) == UPGRADES, 'exact reviewed audit/applicability upgrade set required')
    return changes


def _release(before, selection, published, *, live=True):
    require(isinstance(published,ReleaseSelection),'typed current release required')
    lifecycle.checked_record(published.pointer);lifecycle.checked_record(published.manifest)
    require(Path(published.pointer['path']) == published.publication_root/'current-release.json',
            'release pointer escaped selected root')
    resolver=release.resolve_current_release if live else release.resolve_current_release_integrity
    directory, manifest=resolver(published.publication_root,
        source_root=published.source_root,segmentation_root=published.segmentation_root,
        task_root=published.task_root,expected_release_id=published.release_id)
    require(record(directory/'release-manifest.json') == published.manifest,'release manifest differs')
    ids=before['generation_ids'];promotion=before['promotion_ids'];identity=manifest['identity']
    require(published.release_id == ids['release'] == promotion['release_id'] and
            identity['workbook_id'] == before['workbook_id'] == selection.content.workbook and
            identity.get('prior_release_id') == promotion['previous_release_id'], 'release history differs')
    for kind in ('source','segmentation','task'):
        require(identity[kind+'_generation']['generation_id'] == ids[kind], 'release component differs: '+kind)
    require(ids['task'] == promotion['task_generation_id'],'task generation differs')
    for kind in ('source','segmentation'):
        require(selection.content.bindings[kind+'_generation'] == before['bindings'][kind+'_generation'] and
                selection.content.bindings[kind+'_generation']['id'] == ids[kind], 'selected pair changed')
    for key in ('original_raw_source','source_file','curation'):
        require(selection.content.bindings[key] == before['bindings'][key], 'selected financial identity changed')
    stage_hash,stage=release._tree_hash(selection.content.stage)
    task=published.task_root/'task-generations'/ids['task']
    shipped=release._tree_hash(task)[1];shipped.pop('generation-manifest.json',None)
    require(stage_hash == selection.content.stage_sha256 and stage == shipped,
            'requalification cannot change published task bytes')
    return {'pointer':published.pointer,'manifest':published.manifest,
            'release_path':str(directory),'task_path':str(task),
            'published_tree_sha256':release._tree_hash(task)[0]}


def _historical(before, tracker, evidence):
    """Retain old publication evidence; code drift never excuses non-code drift."""
    wb=before['workbook_id'];base=tracker.parent.parent
    lookup={item['path']:item for item in evidence}
    paths={kind:base/(wb+'-publication-'+kind+'.json') for kind in ('evidence','journal')}
    require(all(str(path) in lookup for path in paths.values()),'original publication inventory and journal required')
    inventory=read(lookup[str(paths['evidence'])]);journal=read(lookup[str(paths['journal'])])
    require(isinstance(inventory,dict) and inventory,'historical publication inventory missing')
    events=journal.get('events');require(isinstance(events,list) and events,'historical publication journal missing')
    latest=events[-1];promotion=before['promotion_ids']
    require(latest.get('event')=='PROMOTED' and all(latest.get(k)==promotion[k] for k in
        ('release_id','previous_release_id','task_generation_id')),'historical publication tail differs')
    registered={row['path']:row['sha256'] for row in _code()};unchanged=[];drift=[]
    for path,sha in sorted(inventory.items()):
        require(isinstance(sha,str) and schema.HEX.fullmatch(sha),'invalid historical inventory hash')
        chosen=schema.safe_path(path)
        actual=({'path':str(chosen),'sha256':release._tree_hash(chosen)[0]}
                if chosen.is_dir() else record(chosen))
        if actual['sha256'] != sha:
            require(path in registered,'historical non-code publication evidence changed')
            drift.append({'path':path,'previous_sha256':sha,'current_sha256':actual['sha256']})
        else:unchanged.append(actual)
    output=latest.get('output',{}).get('path')
    compatibility=None
    if output is not None:
        require(Path(output)==base/'tasks_outputs'/(wb+'-outputs'),'historical compatibility locator differs')
        release.validate_task_generation(Path(output),expected_generation_id=before['generation_ids']['task'])
        compatibility={'path':output,'sha256':release._tree_hash(Path(output))[0]}
        unchanged.append(compatibility)
    return {'retained':_records([lookup[str(path)] for path in paths.values()]+unchanged),'historical_code_changes':drift,
            'compatibility':compatibility}


def _code():
    """Current maintained Python only; never execute retained historical code."""
    paths=(set(ROOT.glob('*.py')) | set((ROOT/'scripts/batch').glob('*.py')) |
           set((ROOT/'xl_seg').glob('*.py')) | set((ROOT/'.cursor/skills').rglob('*.py')))
    paths.add(ROOT/'scripts/corpus_qualification.py')
    return [record(p) for p in sorted(paths) if '__pycache__' not in p.parts]


def _proof(before, selection, published, *, trusted_public_key):
    require(isinstance(selection,ReadinessSelection) and selection.runtime is not None and
            selection.observation is not None,'fresh root runtime observation required')
    require(selection.content.trusted_public_key == trusted_public_key,'trusted key selection differs')
    _delta(before,selection)
    publication=_release(before,selection,published)
    proof=complete_readiness(selection,root=ROOT)
    require(proof.get('schema_version') == 'harbor-complete-readiness/v1' and
            proof.get('workbook') == before['workbook_id'] and
            proof.get('stage_sha256') == selection.content.stage_sha256 and
            proof.get('publication_ready') is True and proof.get('blockers') == [] and
            proof.get('checks') and all(v.get('checked') is True for v in proof['checks'].values()),
            'current complete readiness failed')
    observed=read(selection.observation.execution)
    require(type(observed.get('started_at_ns')) is int and observed['started_at_ns'] > before['updated_at'],
            'runtime observation predates legacy baseline')
    counts={'harbor-instruction-naturalizer':proof['checks']['naturalization_current_context']['result']['attempts_used'],
            'harbor-dialogue-author':proof['checks']['dialogue_review_provenance']['result']['round']}
    for lane, count in counts.items():
        require(type(count) is int and 1 <= count <= 2 and count >= before['lane_state'][lane].get('attempt',0),
                'historical semantic attempt budget regressed')
    # Retain the actual fresh owning proof count without rewriting incomplete old counters.
    return {'readiness':proof,'publication':publication,'semantic_attempts':counts}


def _render(before, descriptor, tx):
    require(type(descriptor['at']) is int and descriptor['at'] > before['updated_at'],'invalid requalification time')
    selected=decode(descriptor['selection']);delta=_delta(before,selected)
    prior_notes={k:before['agent_notes'][k] for k in ('path','sha256')}
    rows=['# Harbor '+before['workbook_id']+' current legacy requalification','',
        'Existing task and release remain unchanged. No strict historical creation or promotion is claimed.',
        'Prior tracker SHA256: '+descriptor['before']['sha256'],
        'Prior notes: '+prior_notes['path'], 'Prior notes SHA256: '+prior_notes['sha256'],
        'Current proof SHA256: '+descriptor['proof']['sha256'],
        'Current evidence SHA256: '+schema.sha(lifecycle.canonical(descriptor['evidence'])),
        'All historical calls, budgets, failures and releases remain retained.','']
    rendered=('\n'.join(rows)).encode()
    after=copy.deepcopy(before);after['revision']+=1;after['updated_at']=descriptor['at']
    after['bindings'].update({k:v['after'] for k,v in delta.items()})
    event={'schema_version':SCHEMA,'operation':'requalify_existing_release','revision':after['revision'],
        'at':descriptor['at'],'prior_tracker_sha256':descriptor['before']['sha256'],
        'journal':{'path':str(tx/'transaction.json'),'sha256':schema.sha(lifecycle.canonical(descriptor))},
        'release_id':before['promotion_ids']['release_id'],'binding_changes':delta,
        'strict_history_claimed':False}
    after['handoffs'].append(event)
    after['agent_notes']=dict(before['agent_notes'],path=str(tx/'notes.md'),sha256=schema.sha(rendered),
        updated_at=descriptor['at'],diagnostic_signature=None,
        evidence_set_sha256=schema.sha(lifecycle.canonical(descriptor['evidence'])))
    require(set(after)==set(before) and all(after[k]==before[k] for k in set(before)-CHANGED),
            'legacy history or budget changed')
    return after,rendered


def _records(rows):
    result={}
    for row in rows:
        path=schema.safe_path(row['path'])
        if path.is_dir():
            require(set(row)=={'path','sha256'} and path.is_absolute() and
                    release._tree_hash(path)[0]==row['sha256'],'evidence tree changed')
        else:lifecycle.checked_record(row)
        require(row['path'] not in result or result[row['path']] == row['sha256'],'conflicting evidence')
        result[row['path']]=row['sha256']
    return [{'path':p,'sha256':h} for p,h in sorted(result.items())]


def _drift(rows):
    registered={row['path'] for row in _code()};changes=[]
    for row in rows:
        path=schema.safe_path(row['path'])
        if path.is_dir():_records([row]);continue
        actual=record(path)
        if actual!=row:
            require(row['path'] in registered,'non-code requalification evidence changed')
            changes.append({'path':row['path'],'prior_sha256':row['sha256'],'current_sha256':actual['sha256']})
    return changes


def _check_descriptor(tracker, tx, descriptor, *, trusted_public_key, allow_stale=False):
    require(set(descriptor)=={'schema_version','workbook','tracker','at','before','prior_notes','proof',
            'selection','published','code','evidence','historical','root_operation'} and
            descriptor['root_operation']=='requalify_legacy','unexpected transaction fields or operation')
    require(descriptor['schema_version']==SCHEMA and descriptor['workbook']==tracker.stem and
            Path(descriptor['tracker'])==tracker and tx.parent==tracker.parent/(tracker.stem+'-legacy-requalifications'),
            'foreign legacy requalification journal')
    for key,name in (('before','before.json'),('prior_notes','prior-notes.md'),('proof','proof.json')):
        require(Path(descriptor[key]['path']) == tx/name,'transaction evidence escaped')
        lifecycle.checked_record(descriptor[key])
    before=read(descriptor['before']);_baseline(before,tracker)
    require(Path(descriptor['prior_notes']['path']).read_bytes()==Path(before['agent_notes']['path']).read_bytes(),
            'historical notes differ')
    # Check all non-code artifacts and sealed original history before classifying drift.
    changes=_drift(descriptor['code']+descriptor['evidence']+descriptor['historical']['retained'])
    if descriptor['code']!=_code() and not changes:
        changes=[{'kind':'registered_code_inventory','prior':descriptor['code'],'current':_code()}]
    selection,published=decode(descriptor['selection']),decode(descriptor['published'])
    after,rendered=_render(before,descriptor,tx)
    require((tx/'after.json').read_bytes()==lifecycle.canonical(after) and
            (tx/'notes.md').read_bytes()==rendered,'prepared requalification changed')
    historical=_historical(before,tracker,[row for row in descriptor['historical']['retained']
        if row['path'].endswith(('-publication-evidence.json','-publication-journal.json'))])
    require(historical['compatibility']==descriptor['historical']['compatibility'],
            'historical compatibility output changed')
    publication=_release(before,selection,published,live=not (allow_stale and changes))
    if changes:
        require(allow_stale,'current requalification code changed')
    else:
        require(historical==descriptor['historical'],'historical publication evidence differs')
        proof=_proof(before,selection,published,trusted_public_key=trusted_public_key)
        require(proof==read(descriptor['proof']),'current owning proof differs')
    require(selection.content.trusted_public_key==trusted_public_key,'trusted key selection differs')
    return before,after,selection,published,changes,publication


def requalify(tracker_path,selection,published,*,authority,expected_revision,expected_sha256,
              trusted_public_key,historical_evidence,clock=time.time_ns,fault_hook=lambda _:None):
    tracker=schema.safe_path(tracker_path)
    with lifecycle.locked(tracker),release._workbook_lock(published.publication_root):
        raw=tracker.read_bytes();before=json.loads(raw)
        require(before['revision']==expected_revision and schema.sha(raw)==expected_sha256,'legacy tracker CAS conflict')
        _baseline(before,tracker)
        request={'tracker_sha256':expected_sha256,'selection':encode(selection),'published':encode(published),
            'upgrades':_delta(before,selection),'historical_evidence':historical_evidence}
        lifecycle.authorize(authority,'requalify_legacy',tracker,before,request)
        evidence=_records(historical_evidence)
        require(evidence,'retained publication evidence/journal bindings required')
        historical=_historical(before,tracker,evidence)
        code=_code();proof=_proof(before,selection,published,trusted_public_key=trusted_public_key)
        txroot=tracker.parent/(tracker.stem+'-legacy-requalifications')
        if txroot.exists():
            require(not txroot.is_symlink() and not any(txroot.iterdir()),
                    'existing requalification transaction requires explicit recovery')
        else:txroot.mkdir()
        tx=txroot/(str(expected_revision+1)+'-'+uuid.uuid4().hex);tx.mkdir()
        notes._immutable(tx/'before.json',raw)
        notes._immutable(tx/'prior-notes.md',Path(before['agent_notes']['path']).read_bytes())
        notes._immutable(tx/'proof.json',lifecycle.canonical(proof))
        evidence=_records(evidence+proof['readiness']['evidence']+code)
        descriptor={'schema_version':SCHEMA,'workbook':tracker.stem,'tracker':str(tracker),
            'at':clock(),'before':record(tx/'before.json'),'prior_notes':record(tx/'prior-notes.md'),
            'proof':record(tx/'proof.json'),'selection':encode(selection),'published':encode(published),
            'code':code,'evidence':evidence,'historical':historical,'root_operation':'requalify_legacy'}
        after,rendered=_render(before,descriptor,tx)
        notes._immutable(tx/'after.json',lifecycle.canonical(after));notes._immutable(tx/'notes.md',rendered)
        fault_hook('before_descriptor')
        notes._immutable(tx/'transaction.json',lifecycle.canonical(descriptor));notes._fsync_directory(tx)
        notes._fsync_directory(txroot);fault_hook('prepared')
        _check_descriptor(tracker,tx,descriptor,trusted_public_key=trusted_public_key)
        require(tracker.read_bytes()==raw,'legacy tracker changed before CAS')
        notes._replace(tracker,lifecycle.canonical(after));fault_hook('after_tracker_cas')
        notes._immutable(tx/'complete.json',lifecycle.canonical({'tracker_sha256':record(tx/'after.json')['sha256']}))
        notes._fsync_directory(tx)
        return {'revision':after['revision'],'sha256':record(tracker)['sha256'],'journal':record(tx/'transaction.json')}


def recover(tracker_path,journal,*,authority,trusted_public_key):
    tracker=schema.safe_path(tracker_path);lifecycle.checked_record(journal)
    tx=Path(journal['path']).parent
    require(Path(journal['path'])==tx/'transaction.json','unexpected journal filename')
    descriptor=read(journal)
    published=decode(descriptor['published'])
    with lifecycle.locked(tracker),release._workbook_lock(published.publication_root):
        before,after,_,_,_,_=_check_descriptor(tracker,tx,descriptor,trusted_public_key=trusted_public_key)
        lifecycle.authorize(authority,'recover_legacy_requalification',tracker,before,{'journal':journal})
        raw=tracker.read_bytes();old=(tx/'before.json').read_bytes();new=(tx/'after.json').read_bytes()
        require(raw in (old,new),'tracker diverged from prepared legacy CAS')
        expected={'tracker_sha256':schema.sha(new)};complete=tx/'complete.json'
        if complete.exists():require(read(record(complete))==expected,'completion marker differs')
        if raw==old:notes._replace(tracker,new)
        if not complete.exists():notes._immutable(complete,lifecycle.canonical(expected));notes._fsync_directory(tx)
        return {'revision':after['revision'],'sha256':record(tracker)['sha256'],'journal':journal}


def reconcile_preparation(tracker_path,directory,*,authority,expected_revision,expected_sha256,clock=time.time_ns):
    """Archive a pre-descriptor interruption; never adopt it as completed proof.

    The active owner holds the same tracker lock, so reconciliation cannot race
    a live preparation. A complete descriptor requires recover(), not this API.
    """
    tracker=schema.safe_path(tracker_path);tx=schema.safe_path(directory)
    with lifecycle.locked(tracker):
        raw=tracker.read_bytes();before=json.loads(raw)
        require(before['revision']==expected_revision and schema.sha(raw)==expected_sha256,
                'preparation reconciliation tracker CAS conflict')
        _baseline(before,tracker)
        require(tx.is_dir() and tx.parent==tracker.parent/(tracker.stem+'-legacy-requalifications') and
                re.fullmatch(str(expected_revision+1)+r'-[0-9a-f]{32}',tx.name), 'foreign preparation directory')
        require(not (tx/'transaction.json').exists() and not (tx/'complete.json').exists(),
                'durable transaction requires validated recovery')
        allowed={'before.json','prior-notes.md','proof.json','after.json','notes.md'}
        files=sorted(tx.iterdir())
        require(all(p.name in allowed and p.is_file() and not p.is_symlink() for p in files),
                'unexpected pre-descriptor artifacts')
        retained=[record(p) for p in files]
        request={'directory':str(tx),'tracker_sha256':expected_sha256,'retained':retained}
        lifecycle.authorize(authority,'reconcile_legacy_preparation',tracker,before,request)
        archive=tracker.parent/(tracker.stem+'-legacy-incomplete-preparations')
        archive.mkdir(exist_ok=True);schema.safe_path(archive)
        destination=archive/tx.name;require(not destination.exists(),'preparation already archived')
        require(tracker.read_bytes()==raw,'tracker changed before preparation reconciliation')
        os.rename(tx,destination);notes._fsync_directory(tx.parent);notes._fsync_directory(archive)
        receipt={'schema_version':'harbor-legacy-preparation-interruption/v1','at':clock(),
            'tracker':record(tracker),'prior_directory':str(tx),'retained_directory':str(destination),
            'retained':[{'path':str(destination/Path(row['path']).name),'sha256':row['sha256']} for row in retained],
            'qualification':False,'budget_reset':False}
        notes._immutable(destination/'interruption.json',lifecycle.canonical(receipt));notes._fsync_directory(destination)
        return record(destination/'interruption.json')


def validate(source,tracker_path,*,trusted_public_key):
    """Concrete read-only corpus qualifier; does not use the legacy PASS flag."""
    tracker=schema.safe_path(tracker_path);raw=tracker.read_bytes();current=json.loads(raw)
    require(source['workbook_id']==current['workbook_id'] and current['execution_state']=='PROMOTED',
            'foreign or nonpromoted legacy requalification')
    require(record(source['path'])==current['bindings']['original_raw_source'] and
            source['sha256']==current['bindings']['original_raw_source']['sha256'],'original identity changed')
    events=[e for e in current['handoffs'] if e.get('schema_version')==SCHEMA]
    require(len(events)==1 and current['handoffs'][-1]==events[0],'ambiguous or subsequently changed requalification')
    journal=events[0]['journal'];lifecycle.checked_record(journal)
    tx=Path(journal['path']).parent
    require(Path(journal['path'])==tx/'transaction.json','unexpected journal filename')
    descriptor=read(journal)
    _,after,selection,published,changes,publication=_check_descriptor(tracker,tx,descriptor,trusted_public_key=trusted_public_key,allow_stale=True)
    require(current==after and raw==(tx/'after.json').read_bytes(),'current tracker differs from requalification')
    require(read(record(tx/'complete.json'))=={'tracker_sha256':schema.sha(raw)},'requalification CAS incomplete')
    require(tracker.read_bytes()==raw,'tracker changed during qualification')
    return {'state':'STALE' if changes else 'PROMOTED','prior_promotion':True,
        'verified_promoted':not changes,'revalidation_required':bool(changes),'code_changes':changes,
        'qualification_kind':'current_legacy_requalification','tracker':str(tracker),'tracker_sha256':schema.sha(raw),
        'failure':None,'generation_ids':current['generation_ids'],'release_id':published.release_id,
        'release_path':str(published.publication_root/'releases'/published.release_id),
        'output_path':publication['task_path'],'output_sha256':publication['published_tree_sha256'],
        'task_content_sha256':selection.content.stage_sha256,
        'compatibility_output':descriptor['historical']['compatibility'],
        'evidence_sha256':journal['sha256']}
