"""Read-only historical validator for the exact root-selected v1 retirement seal.

No seal creation, code replay, monkeypatching or exception-based fallback. The
original installed 419e implementation stays immutable. Archived owning proof,
notes and canonical released terminal state are checked without rehashing old
code through the ordinary live quality-note validator. This is retirement only.
"""
from pathlib import Path
from scripts import harbor_slot_retirement as V1
from scripts import harbor_dialogue_terminal as T
from scripts import harbor_parallel_policy as P
from scripts.batch import harbor_lane_notes as N
from scripts.batch import harbor_tracker as S
from scripts.batch import harbor_tracker_lifecycle as L
from scripts.batch.harbor_promotion_evidence import after_releases

V1_SHA256 = '419e2cdd64bf4b40b95ee81c286aae5329d2c192bf7f7ac4b1541b5ec27d3de5'
SCHEMA = V1.SCHEMA
record, read, require = V1.record, P.read, P.require
_policy = V1._policy


def _historical_current(tracker, workbook, original, tracker_root, archived):
    path = Path(tracker_root)/(workbook+'.json')
    require(tracker == record(path), 'terminal tracker CAS differs')
    current = S.validate_tracker(read(tracker)); L.validate_lifecycle(current)
    require(current['workbook_id'] == workbook and
            current['bindings']['original_raw_source'] == original,
            'terminal original identity differs')
    L.checked_record(original)
    require(current['execution_state'] == 'STOPPED' and
            all(v is None for v in current['leases'].values()), 'released terminal tracker required')
    # Keep the synchronization check, but do not invoke live quality evidence
    # validation: every referenced row is independently checked in the archive.
    journal = Path(tracker_root)/(workbook+'-notes-transactions')
    for descriptor_path in journal.glob('*/transaction.json'):
        descriptor = read(record(descriptor_path))
        complete = read(record(descriptor_path.parent/'complete.json'))
        require(complete == dict(tracker_sha256=descriptor['after']['sha256'],
                                 notes_sha256=descriptor['notes']['sha256']), 'pending/changed notes transaction')
        L.checked_record(descriptor['after']); L.checked_record(descriptor['notes'])
    notes = current['agent_notes']
    note_binding = dict(path=notes['path'],sha256=notes['sha256'])
    note_bytes = archived(note_binding)
    require(record(notes['path']) == note_binding and Path(notes['path']).read_bytes() == note_bytes,
            'current terminal notes changed')
    require(note_bytes == N.render_notes(current) and S.sha(note_bytes) == notes['sha256'],
            'historical notes content/hash differs')
    rows = N.notes_evidence(current)
    require(notes['evidence_set_sha256'] == S.sha(L.canonical(rows)) and
            notes['diagnostic_signature'] == current['promotion_failure']['diagnostic_signature'],
            'historical notes evidence/signature differs')
    for row in rows:
        archived(row)
    return current


def validate(binding, *, contract, activation, workbook, original, tracker):
    """Verify a root-selected prior observation, including all archived bytes.

    No current authoring code replay: the selected hash is root authority over
    the previously observed full proof. Canonical terminal/release semantics
    are checked again, and changes to any archived original invalidate the seal.
    """
    p, lane = _policy(contract, activation, workbook)
    root = Path(p['campaign_root'])
    seal = read(binding)
    require(set(seal) == {'schema_version', 'contract', 'activation', 'workbook', 'lane',
                         'original', 'tracker', 'transaction', 'copies', 'observed_at_ns', 'implementation'},
            'closed retirement seal required')
    require(seal['schema_version'] == SCHEMA and seal['contract'] == contract and
            seal['activation'] == activation and seal['workbook'] == workbook and
            seal['original'] == original and seal['tracker'] == tracker and seal['lane'] == lane,
            'retirement selection differs')
    require(seal['implementation'] == record(V1.__file__) and seal['implementation']['sha256'] == V1_SHA256,
            'unrecognized original retirement implementation')
    directory = Path(binding['path']).parent
    require(directory.parent == root / 'parallel-policy' / 'retirements'
            and Path(binding['path']).name == 'seal.json', 'foreign retirement namespace')
    require(type(seal['observed_at_ns']) is int and seal['observed_at_ns'] > 0,
            'invalid retirement observation time')
    archive = {}
    require(type(seal['copies']) is list and seal['copies'], 'missing retirement archive')
    for index, item in enumerate(seal['copies']):
        require(set(item) == {'original', 'retained'}, 'closed archive mapping required')
        old, retained = item['original'], item['retained']
        require(set(old) == set(retained) == {'path', 'sha256'} and old['path'] not in archive,
                'duplicate or malformed archive identity')
        require(Path(retained['path']) == directory / 'files' / old['sha256']
                and old['sha256'] == retained['sha256'], 'archive path/hash differs')
        require(record(retained['path']) == retained, 'retained archive bytes changed')
        archive[old['path']] = (old, retained)

    def raw(selected):
        require(selected['path'] in archive and archive[selected['path']][0] == selected,
                'required archived evidence missing or detached')
        # Hash the exact bytes consumed, not just an earlier observation of the
        # same path. An archive may change after the initial inventory scan.
        return P.checked(archive[selected['path']][1])

    def obj(selected):
        return S.strict_json(raw(selected))

    tx = seal['transaction']; d = obj(tx)
    require(d['schema_version'] == T.SCHEMA and d['tracker_path'] == tracker['path']
            and d['contract'] == contract and d['activation'] == activation,
            'archived terminal descriptor differs')
    txroot = Path(tx['path']).parent
    require(txroot.parent == root / 'workbooks' / (workbook + '-notes-transactions'),
            'foreign archived terminal transaction')
    for field in ('before', 'after', 'notes', 'selection', 'proof'):
        require(Path(d[field]['path']) == txroot / (field + ('.md' if field == 'notes' else '.json')),
                'archived terminal artifact path differs')
    for path, sha in d['code'].items():
        raw(dict(path=path, sha256=sha))
    raw(d['adapter'])
    before, after, proof = obj(d['before']), obj(d['after']), obj(d['proof'])
    for value in (before, after):
        S.validate_tracker(value); L.validate_lifecycle(value)
    require(before['workbook_id'] == workbook and before['bindings']['original_raw_source'] == original
            and proof['classification'] == T.CLASSIFICATION and proof['attempt'] == 2
            and proof['passed'] is False and proof['terminal'] is True,
            'archived owning terminal identity differs')
    require(d['lease'] == dict(owner=before['leases']['task_agent']['owner'],
                              token=before['leases']['task_agent']['token'], revision=before['revision'],
                              sha256=d['before']['sha256']), 'archived lease detached')
    require(before['leases']['task_agent']['acquired_at'] <= d['now'] <
            before['leases']['task_agent']['expires_at'], 'archived terminal lease expired')
    require(N.evidence_list(proof['evidence']) == proof['evidence'], 'archive proof inventory malformed')
    for item in proof['evidence']:
        raw(item)
    raw(d['selection'])
    result = dict(proof, evidence=N.evidence_list(proof['evidence'] + [d['proof'], d['selection']]))
    expected, expected_notes = N._failure_after(before, result, d['now'], Path(d['notes_path']))
    require(after == expected and raw(d['notes']) == expected_notes, 'archived terminal projection differs')
    complete_path = str(txroot / 'complete.json')
    require(complete_path in archive, 'archived completion absent')
    require(obj(archive[complete_path][0]) == dict(tracker_sha256=d['after']['sha256'],
                                                notes_sha256=d['notes']['sha256']),
            'archived terminal completion differs')
    current = _historical_current(tracker, workbook, original, root / 'workbooks', raw)
    require(obj(tracker) == current, 'released terminal tracker changed')
    after_releases(after, current)
    # Registered code may move forward. Every non-code original remains exact;
    # the archive independently preserves all old code and response bytes.
    code_paths = set(d['code']) | {d['adapter']['path']}
    from scripts.batch import harbor_readiness
    repo = Path(V1.__file__).resolve().parents[1]
    for skill, modules in harbor_readiness._SKILL_FILES.items():
        code_paths.add(str(repo/'.cursor/skills'/skill/'SKILL.md'))
        code_paths.update(str(repo/'.cursor/skills'/skill/'scripts'/(name+'.py')) for name in modules)
    changes = []
    for old, retained in archive.values():
        live = record(old['path'])
        if live != old:
            require(old['path'] in code_paths, 'historical non-code original changed')
            changes.append(dict(path=old['path'], historical_sha256=old['sha256'], current_sha256=live['sha256']))
    return dict(workbook=workbook, lane=lane, tracker=tracker, seal=binding,
                terminal='STOPPED', source_invalidity=False, mutation_performed=False,
                historical_observation=True, current_qualification=False, code_changes=changes)
