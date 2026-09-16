"""Append finite physical-capacity renewals while preserving original records.

The materialized compatibility table presents a newly authenticated effective policy to the
unchanged listener. Original policies and every renewal remain separate ledgers.
No uncertain operation, workbook, claim, or author reservation is modified.
"""
import json, math
from .coordinator import Conflict, canonical, digest, required, sha

SCHEMA = 'fleet-physical-capacity-renewal/v1'
COMPOSED = 'fleet-physical-capacity-renewal/v2'
TABLE_SQL = 'CREATE TABLE physical_capacity_renewals (change_id TEXT PRIMARY KEY, identity TEXT NOT NULL, revision INTEGER UNIQUE NOT NULL, original_change_id TEXT NOT NULL, record TEXT NOT NULL, effective_record TEXT NOT NULL)'
EFFECTIVE_SQL = 'CREATE TABLE physical_capacity_extensions(change_id TEXT PRIMARY KEY,revision INTEGER UNIQUE NOT NULL,record TEXT NOT NULL)'
TRIGGER_SQL = 'CREATE TRIGGER physical_capacity_extensions_append AFTER INSERT ON physical_capacity_extensions BEGIN INSERT INTO physical_capacity_originals(change_id,revision,record) VALUES(NEW.change_id,NEW.revision,NEW.record); END'
MUTABLE = {'change_id', 'created_at', 'expires_at', 'authorization', 'evidence', 'identity'}
ARG_KEYS = ('host', 'prior_capacity_sha256', 'quarantines', 'physical_model_limit', 'maximum_fresh_models', 'authorization', 'evidence', 'change_id', 'expires_at')


def require(ok, message):
    if not ok: raise Conflict(message)


def installed(db):
    r = db.execute("SELECT type,sql FROM sqlite_master WHERE name='physical_capacity_extensions'").fetchone()
    require(r is not None, 'original physical capacity relation missing')
    present = db.execute("SELECT 1 FROM sqlite_master WHERE name='physical_capacity_originals' AND type='table'").fetchone()
    if not present:
        require(r['type'] == 'table' and not db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('physical_capacity_originals','physical_capacity_renewals','physical_capacity_extensions_append')").fetchone(), 'partial physical policy migration')
        return False
    require(r['type'] == 'table' and r['sql'] == EFFECTIVE_SQL, 'foreign physical policy projection')
    for name, sql in [('physical_capacity_renewals', TABLE_SQL), ('physical_capacity_extensions_append', TRIGGER_SQL)]:
        found = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        require(found and found['sql'] == sql, 'physical renewal schema differs')
    return True


def policy_identity(row):
    return digest(dict({k: row[k] for k in ARG_KEYS}, expected_revision=row['revision'] - 1))


def history(db):
    active = installed(db)
    table = 'physical_capacity_originals' if active else 'physical_capacity_extensions'
    originals = [json.loads(r['record']) for r in db.execute('SELECT record FROM ' + table + ' ORDER BY revision')]
    rows = [json.loads(r['record']) for r in db.execute('SELECT record FROM physical_capacity_renewals ORDER BY revision')] if active else []
    current = {r['change_id']: r for r in originals}; seen = set(current)
    for revision, row in enumerate(rows, 1):
        fields={'schema_version','revision','original_change_id','previous_sha256','previous_change_id','effective'}
        composed=row['schema_version']==COMPOSED
        require(set(row)==fields|({'authority_renewal'}if composed else set()) and row['schema_version']in(SCHEMA,COMPOSED) and row['revision']==revision,'renewal history differs')
        old = current.get(row['original_change_id']); new = row['effective']
        require(old and digest(old) == row['previous_sha256'] and old['change_id'] == row['previous_change_id'], 'renewal predecessor changed')
        require(set(new) == set(old) and all(new[k] == old[k] for k in set(old) - (MUTABLE|({'prior_capacity_sha256','quarantines'}if composed else set()))) and new['change_id'] not in seen, 'renewal changed capacity or unknown identity')
        require(all(type(new[k]) in (int,float) and math.isfinite(new[k]) for k in ('created_at','expires_at')) and old['created_at'] <= new['created_at'] and old['expires_at'] < new['expires_at'] and 0 < new['expires_at'] - new['created_at'] <= 14400 and new['identity'] == policy_identity(new), 'renewal clock or identity differs')
        if composed:composed_authority(db,old,new,row['authority_renewal'])
        current[row['original_change_id']] = new; seen.add(new['change_id'])
    actual_rows = list(db.execute('SELECT * FROM physical_capacity_extensions ORDER BY revision'))
    actual = [json.loads(r['record']) for r in actual_rows]
    require(actual == list(current.values()) and [(r['change_id'], r['revision']) for r in actual_rows] == [(r['change_id'], r['revision']) for r in originals], 'physical effective projection differs from renewal history')
    return originals, rows, actual


def composed_authority(db,old,new,proof):
    from . import quarantine_renewal as Q,quarantine_host_renewal as H
    require(set(proof)=={'quarantines','host_capacity'}and len(proof['quarantines'])==2,'exact composed authority required')
    _,qr,_=Q.history(db);_,hr,_=H.history(db);cap=proof['host_capacity'];qs=proof['quarantines']
    require(cap in hr and all(q in qr for q in qs)and len({q['original_change_id']for q in qs})==2,'composed renewal ledger missing')
    require(cap['previous_sha256']==old['prior_capacity_sha256']and digest(cap['effective'])==new['prior_capacity_sha256']
        and {q['previous_change_id']:q['previous_sha256']for q in qs}==old['quarantines']
        and {q['effective']['change_id']:digest(q['effective'])for q in qs}==new['quarantines']
        and cap['quarantine_renewal']in qs,'composed predecessor or references differ')
    require(all(q['effective']['host']==old['host']and q['effective']['created_at']==new['created_at']and new['expires_at']<=q['effective']['expires_at']for q in qs)
        and cap['effective']['host']==old['host']and cap['effective']['created_at']==new['created_at']and new['expires_at']<=cap['effective']['expires_at'],'composed renewal clock or host differs')


def renew(authority, *, original_change_id, previous_sha256, expected_revision, expires_at, authorization, evidence, change_id):
    required(original_change_id, 'original physical policy ID'); required(change_id, 'renewal ID'); sha(previous_sha256)
    authority.retained_binding(authorization); authority.retained_binding(evidence)
    require(type(expected_revision) is int and expected_revision >= 0 and type(expires_at) in (int,float) and math.isfinite(expires_at), 'finite bounded renewal required')
    args = dict(original_change_id=original_change_id, previous_sha256=previous_sha256, expected_revision=expected_revision, expires_at=expires_at, authorization=authorization, evidence=evidence, change_id=change_id)
    identity = digest(args)
    with authority.transaction() as db:
        originals, rows, effective = history(db)
        if installed(db):
            found = db.execute('SELECT identity,record FROM physical_capacity_renewals WHERE change_id=?', (change_id,)).fetchone()
            if found:
                require(found['identity'] == identity, 'renewal ID reused with different authority')
                return json.loads(found['record'])
        require(len(rows) == expected_revision and not any(r['change_id'] == change_id for r in originals), 'renewal revision or ID differs')
        roots = [r for r in originals if r['change_id'] == original_change_id]
        require(len(roots) == 1, 'exact original physical policy required')
        old = effective[roots[0]['revision'] - 1]; now = authority.clock()
        require(digest(old) == previous_sha256 and old['identity'] == policy_identity(old), 'effective predecessor changed')
        require(old['created_at'] <= now and old['expires_at'] < expires_at and now < expires_at <= now + 14400, 'renewal must extend within four hours')
        policy = authority.author_policy(db); cap = authority.host_capacity(db, old['host'])
        qs = [q for q in authority.quarantines(db) if q['host'] == old['host']]
        require(policy and policy['authors_per_host'] == 2 and policy['physical_models_per_host'] == 2 and digest(policy) == old['author_policy_sha256'] and cap and digest(cap) == old['prior_capacity_sha256'], 'original author or host capacity changed')
        require(old['physical_model_limit'] == 3 and old['maximum_fresh_models'] == 1 and old['unknown_slots_consumed'] == 2 and old['unknown_operations_released'] is False and old['author_limit_changed'] is False and len(qs) == 2 and {q['change_id']: digest(q) for q in qs} == old['quarantines'], 'original unknown capacity changed')
        require(expires_at <= min(cap['expires_at'], *(q['expires_at'] for q in qs)), 'renewal exceeds retained quarantine authority')
        occupied = [o for o in db.execute("SELECT * FROM operations WHERE kind='model' AND state IN ('active','hold')") if authority.host(o['owner']) == old['host']]
        require(len(occupied) == 2 and {o['operation_id'] for o in occupied} == {q['operation_id'] for q in qs} and all(o['state'] == 'hold' for o in occupied), 'exact two unknown occupied slots required')
        for q in qs:
            op = next(o for o in occupied if o['operation_id'] == q['operation_id']); w = authority.row(db, q['workbook'])
            require(digest(authority.public(op)) == q['operation_sha256'] and w['state'] == 'hold' and all(w[k] == q[k] for k in ('owner','fence','source_sha256','version')), 'unknown operation or workbook changed')
        require(not any(authority.host(w['owner']) == old['host'] for w in db.execute("SELECT owner FROM workbooks WHERE state='active'")), 'target host admission must be stopped')
        excluded = {'physical_capacity_extensions','physical_capacity_originals','physical_capacity_renewals','events'}
        tables = [r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'") if r['name'] not in excluded]
        def retained(): return {t: [tuple(r) for r in db.execute('SELECT * FROM "' + t.replace('"','""') + '" ORDER BY 1,2')] for t in tables}
        before = retained(); original_table = 'physical_capacity_originals' if installed(db) else 'physical_capacity_extensions'
        original_bytes = [tuple(r) for r in db.execute('SELECT * FROM ' + original_table + ' ORDER BY revision')]
        if not installed(db):
            db.execute('ALTER TABLE physical_capacity_extensions RENAME TO physical_capacity_originals'); db.execute(TABLE_SQL); db.execute(EFFECTIVE_SQL)
            db.execute('INSERT INTO physical_capacity_extensions SELECT * FROM physical_capacity_originals'); db.execute(TRIGGER_SQL)
        new = dict(old, change_id=change_id, created_at=now, expires_at=expires_at, authorization=authorization, evidence=evidence)
        new['identity'] = policy_identity(new)
        record = dict(schema_version=SCHEMA, revision=expected_revision+1, original_change_id=original_change_id, previous_sha256=previous_sha256, previous_change_id=old['change_id'], effective=new)
        db.execute('INSERT INTO physical_capacity_renewals VALUES(?,?,?,?,?,?)', (change_id,identity,expected_revision+1,original_change_id,canonical(record),canonical(new)))
        changed = db.execute('UPDATE physical_capacity_extensions SET record=? WHERE change_id=? AND record=?', (canonical(new), original_change_id, canonical(old)))
        require(changed.rowcount == 1, 'effective policy CAS failed')
        authority.event(db, 'renew_physical_capacity', record)
        require([tuple(r) for r in db.execute('SELECT * FROM physical_capacity_originals ORDER BY revision')] == original_bytes and retained() == before, 'renewal changed original ledger')
        history(db)
        require(authority.physical_model_limit(db, old['host']) == 3, 'original listener does not accept renewed policy')
        return record
