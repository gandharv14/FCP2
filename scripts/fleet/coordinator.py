"""Single-authority fleet leases. Serve on loopback and reach it by SSH tunnels.

SQLite lives on the coordinator's local disk, never on a shared filesystem.
Expired claims/operations become reconciliation holds: time alone never permits
another attempt. Authentication files and bearer values are never logged.
This ledger supplements, and does not rewrite, historical pipeline trackers.
"""
from __future__ import annotations

import argparse
import gzip
from contextlib import contextmanager
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import urllib.parse


class Conflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def required(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"nonempty bounded {name} required")
    return value


def sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError("lowercase SHA-256 required")
    return value


class Coordinator:
    def __init__(self, database, *, clock=time.time):
        self.database = str(database)
        self.clock = clock
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Refuse aliases so independent authorities cannot unknowingly use an alias.
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("coordinator database path must not contain symlinks")
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS workbooks (
                    workbook TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL, owner TEXT, token TEXT, fence INTEGER NOT NULL DEFAULT 0,
                    expires REAL, claim_id TEXT UNIQUE, version TEXT, checkpoint TEXT,
                    reason TEXT, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY, identity TEXT NOT NULL, workbook TEXT NOT NULL,
                    fence INTEGER NOT NULL, token TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS abandoned_claims (
                    claim_id TEXT PRIMARY KEY, workbook TEXT NOT NULL,
                    owner TEXT NOT NULL, version TEXT NOT NULL,
                    evidence TEXT NOT NULL, abandoned_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    kind TEXT NOT NULL, operation_id TEXT NOT NULL, identity TEXT NOT NULL,
                    workbook TEXT NOT NULL, owner TEXT NOT NULL, token TEXT NOT NULL,
                    fence INTEGER NOT NULL, state TEXT NOT NULL, started REAL NOT NULL,
                    expires REAL NOT NULL, ended REAL, evidence TEXT,
                    PRIMARY KEY(kind, operation_id));
                CREATE TABLE IF NOT EXISTS model_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    revision INTEGER NOT NULL, model_limit INTEGER,
                    authorization TEXT NOT NULL, change_id TEXT NOT NULL,
                    updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS model_policy_changes (
                    change_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_quarantines (
                    change_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    revision INTEGER NOT NULL UNIQUE, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quarantine_host_capacity (
                    change_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    revision INTEGER NOT NULL UNIQUE, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS author_capacity_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    identity TEXT NOT NULL, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS author_reservations (
                    claim_id TEXT PRIMARY KEY, workbook TEXT NOT NULL,
                    owner TEXT NOT NULL, fence INTEGER NOT NULL, host TEXT NOT NULL,
                    state TEXT NOT NULL, started REAL NOT NULL, ended REAL, evidence TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    action TEXT NOT NULL, payload TEXT NOT NULL);
            ''')
        # Opening an old/default database does not silently relax its index.
        # Only the explicit admin policy transaction below migrates that index.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM model_policy WHERE singleton=1').fetchone() is None:
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_global_operation ON operations(kind) WHERE state IN ('active','hold')")
            db.commit()
        os.chmod(path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            # Persist expiry independently: failures below cannot undo a hold.
            now = self.clock()
            expired = db.execute("SELECT workbook FROM workbooks WHERE state='active' AND expires<=?", (now,)).fetchall()
            db.execute("UPDATE workbooks SET state='hold',reason='lease expired; reconcile remote process and artifacts',updated=? WHERE state='active' AND expires<=?", (now, now))
            db.execute("UPDATE operations SET state='hold' WHERE state='active' AND expires<=?", (now,))
            db.execute("UPDATE author_reservations SET state='hold' WHERE state='active' AND claim_id IN (SELECT claim_id FROM workbooks WHERE state='hold')")
            for row in expired:
                self.event(db, 'lease_expired', {'workbook': row['workbook']})
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, action, payload):
        db.execute('INSERT INTO events(at,action,payload) VALUES(?,?,?)', (self.clock(), action, canonical(payload)))

    def enroll(self, workbook, source_sha256, evidence):
        """Admin-only. New entries are held pending explicit ownership reconciliation."""
        required(workbook, 'workbook'); sha(source_sha256); required(evidence, 'inventory evidence')
        with self.transaction() as db:
            old = db.execute('SELECT * FROM workbooks WHERE workbook=?', (workbook,)).fetchone()
            if old:
                if old['source_sha256'] != source_sha256:
                    raise Conflict('source hash differs from immutable inventory')
                return self.public(old)
            db.execute('INSERT INTO workbooks(workbook,source_sha256,state,reason,updated) VALUES(?,?,?,?,?)',
                       (workbook, source_sha256, 'hold', 'initial ownership reconciliation required', self.clock()))
            self.event(db, 'enroll', dict(workbook=workbook, source_sha256=source_sha256, evidence=evidence))
            return {'workbook': workbook, 'state': 'hold'}

    def reconcile(self, workbook, evidence, disposition='ready', checkpoint=None):
        """Admin attests process stopped + artifacts, budget and old owner reconciled.

        Evidence must name a retained report/hash, not a claim that elapsed time is
        sufficient. The API cannot independently inspect remote process evidence.
        """
        required(evidence, 'retained reconciliation evidence')
        if disposition not in {'ready', 'complete', 'hold'}:
            raise ValueError('invalid reconciliation disposition')
        with self.transaction() as db:
            row = self.row(db, workbook)
            if row['state'] == 'active':
                raise Conflict('active owner must finish or enter hold before reconciliation')
            if db.execute("SELECT 1 FROM operations WHERE workbook=? AND state IN ('active','hold')", (workbook,)).fetchone():
                raise Conflict('reconcile outstanding model/publication operations first')
            self.release_author(db,row,evidence)
            db.execute('UPDATE workbooks SET state=?,owner=NULL,token=NULL,expires=NULL,claim_id=NULL,checkpoint=COALESCE(?,checkpoint),reason=?,updated=? WHERE workbook=?',
                       (disposition, canonical(checkpoint) if checkpoint is not None else None, evidence, self.clock(), workbook))
            self.event(db, 'reconcile', dict(workbook=workbook, disposition=disposition, evidence=evidence, checkpoint=checkpoint))
            return self.public(self.row(db, workbook))

    @staticmethod
    def abandoned_claim_record(row):
        return dict(schema_version='fleet-abandoned-claim/v1',claim_id=row['claim_id'],
                    workbook=row['workbook'],owner=row['owner'],version=row['version'],
                    evidence=json.loads(row['evidence']),abandoned_at=row['abandoned_at'])

    def abandon_claim(self, workbook, owner, claim_id, version, evidence):
        """Admin closes an exact never-granted request, including late arrivals.

        The BEGIN IMMEDIATE transaction races atomically with claim(). A grant
        that won first remains owned and cannot be tombstoned. Retiring an ID
        does not reconcile a workbook or grant permission to run another job.
        """
        required(workbook,'workbook');required(owner,'owner')
        required(claim_id,'unique claim id');required(version,'code version')
        self.retained_binding(evidence)
        with self.transaction() as db:
            self.row(db,workbook)
            if db.execute('SELECT 1 FROM claims WHERE claim_id=?',(claim_id,)).fetchone():
                raise Conflict('claim was granted; reconcile its actual owner and effects')
            old=db.execute('SELECT * FROM abandoned_claims WHERE claim_id=?',(claim_id,)).fetchone()
            if old:
                if (old['workbook'],old['owner'],old['version'],old['evidence'])!=(workbook,owner,version,canonical(evidence)):
                    raise Conflict('abandoned claim id reused with different identity or evidence')
                return self.abandoned_claim_record(old)
            db.execute('INSERT INTO abandoned_claims VALUES(?,?,?,?,?,?)',
                       (claim_id,workbook,owner,version,canonical(evidence),self.clock()))
            result=self.abandoned_claim_record(db.execute('SELECT * FROM abandoned_claims WHERE claim_id=?',(claim_id,)).fetchone())
            self.event(db,'abandon_claim',result)
            return result

    def claim(self, workbook, owner, claim_id, version, lease_seconds=180):
        required(owner, 'owner'); required(claim_id, 'unique claim id'); required(version, 'code version')
        ttl = self.ttl(lease_seconds)
        identity = digest(dict(workbook=workbook, owner=owner, version=version))
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM abandoned_claims WHERE claim_id=?',(claim_id,)).fetchone():
                raise Conflict('claim id permanently abandoned; original request cannot be granted')
            old = db.execute('SELECT * FROM claims WHERE claim_id=?', (claim_id,)).fetchone()
            row = self.row(db, workbook)
            self.quarantine_custody(db,workbook,owner)
            if old:
                if old['identity'] != identity:
                    raise Conflict('claim id reused with different identity')
                if row['state'] != 'active' or row['claim_id'] != claim_id:
                    raise Conflict('claim no longer active; reconciliation required')
                return dict(self.public(row), token=old['token'])
            if row['state'] != 'ready':
                raise Conflict(f"workbook is {row['state']}")
            host=self.author_host(workbook,owner) if self.author_policy(db) is not None else None
            if host is not None:
                occupied=db.execute("SELECT COUNT(*) FROM author_reservations WHERE host=? AND state IN ('active','hold')",(host,)).fetchone()[0]
                if occupied>=self.author_limit(db,host):
                    raise Conflict('host author capacity occupied; await finish or exact stopped reconciliation')
            token = secrets.token_hex(32)
            fence = row['fence'] + 1
            db.execute("UPDATE workbooks SET state='active',owner=?,token=?,fence=?,expires=?,claim_id=?,version=?,reason=NULL,updated=? WHERE workbook=?",
                       (owner, token, fence, self.clock()+ttl, claim_id, version, self.clock(), workbook))
            db.execute('INSERT INTO claims VALUES(?,?,?,?,?)', (claim_id, identity, workbook, fence, token))
            if host is not None:
                db.execute('INSERT INTO author_reservations VALUES(?,?,?,?,?,?,?,?,?)',
                           (claim_id,workbook,owner,fence,host,'active',self.clock(),None,None))
            self.event(db, 'claim', dict(workbook=workbook, owner=owner, fence=fence, version=version, claim_id=claim_id))
            return dict(self.public(self.row(db, workbook)), token=token)

    @staticmethod
    def ttl(value):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not 10 <= value <= 3600:
            raise ValueError('lease_seconds must be between 10 and 3600')
        return value

    @staticmethod
    def row(db, workbook):
        row = db.execute('SELECT * FROM workbooks WHERE workbook=?', (workbook,)).fetchone()
        if row is None:
            raise Conflict('workbook not enrolled')
        return row

    @staticmethod
    def public(row):
        return {k: v for k, v in dict(row).items() if k != 'token'}

    def owned(self, db, workbook, owner, token, fence):
        row = self.row(db, workbook)
        if row['state'] != 'active' or row['owner'] != owner or row['fence'] != fence or not hmac.compare_digest(row['token'] or '', token):
            raise Conflict('not the exact active fenced owner; reconcile before retry')
        return row

    def heartbeat(self, workbook, owner, token, fence, lease_seconds=180, checkpoint=None):
        ttl = self.ttl(lease_seconds)
        with self.transaction() as db:
            self.owned(db, workbook, owner, token, fence)
            now = self.clock()
            db.execute('UPDATE workbooks SET expires=?,updated=?,checkpoint=COALESCE(?,checkpoint) WHERE workbook=?',
                       (now+ttl, now, canonical(checkpoint) if checkpoint is not None else None, workbook))
            db.execute("UPDATE operations SET expires=? WHERE workbook=? AND owner=? AND fence=? AND state='active'", (now+ttl, workbook, owner, fence))
            return self.public(self.row(db, workbook))

    def finish(self, workbook, owner, token, fence, evidence, disposition='complete'):
        required(evidence, 'retained outcome evidence')
        if disposition not in {'complete', 'hold'}:
            raise ValueError('finish must complete or hold, never silently requeue')
        with self.transaction() as db:
            row=self.owned(db, workbook, owner, token, fence)
            if disposition == 'complete' and db.execute("SELECT 1 FROM operations WHERE workbook=? AND state IN ('active','hold')", (workbook,)).fetchone():
                raise Conflict('cannot complete with outstanding operations')
            db.execute('UPDATE workbooks SET state=?,reason=?,updated=? WHERE workbook=?', (disposition, evidence, self.clock(), workbook))
            if disposition == 'hold':
                db.execute("UPDATE operations SET state='hold' WHERE workbook=? AND state='active'", (workbook,))
            # The existing worker invokes finish only after its guarded process
            # group has stopped and its immutable outcome has been retained.
            # Task/review uncertainty remains in the workbook/operation ledger.
            self.release_author(db,row,evidence)
            self.event(db, 'finish', dict(workbook=workbook, owner=owner, fence=fence, disposition=disposition, evidence=evidence))
            return self.public(self.row(db, workbook))

    @staticmethod
    def author_host(workbook,owner):
        """Legacy source workers count conservatively; publication is separate."""
        local=re.fullmatch(r'local:(collection|publication):([^:]+):([^:]+)',owner)
        if local:
            if local.group(2)!=workbook:raise Conflict('local publication owner workbook differs')
            return None
        if owner.startswith(('local:','local/')):
            raise Conflict('local authors require a separately authorized local resource policy')
        match=re.fullmatch(r'(hhu-server(?:-[2-8])?):publication:([^:]+):([^:]+)',owner)
        if match:
            if match.group(2)!=workbook:raise Conflict('publication owner workbook differs')
            return None
        match=re.match(r'^(hhu-server(?:-[2-8])?)(?=[:/])',owner)
        if match is None:raise Conflict('registered author host required')
        return match.group(1)

    @staticmethod
    def author_policy(db):
        row=db.execute('SELECT record FROM author_capacity_policy WHERE singleton=1').fetchone()
        return json.loads(row['record']) if row else None

    def release_author(self,db,row,evidence):
        db.execute("UPDATE author_reservations SET state='released',ended=?,evidence=? WHERE claim_id=? AND workbook=? AND owner=? AND fence=? AND state IN ('active','hold')",
                   (self.clock(),evidence,row['claim_id'],row['workbook'],row['owner'],row['fence']))

    def author_extensions(self,db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='author_capacity_extensions'").fetchone():return []
        return [json.loads(r['record'])for r in db.execute('SELECT record FROM author_capacity_extensions ORDER BY revision')]

    def author_limit(self,db,host):
        policy=self.author_policy(db);base=policy['authors_per_host']
        for row in reversed(self.author_extensions(db)):
            if (row['host']==host and row['author_policy_sha256']==digest(policy)
                    and row['created_at']<=self.clock()<row['expires_at']):
                return row['authors_per_host']
        return base

    def permit_author_capacity_extension(self,host,authors_per_host,author_policy_sha256,
            authorization,evidence,change_id,expected_revision,expires_at):
        """Finite, measured 2-to-3 author canary; original model policy stays fixed.

        The admin retains the native resource census and selected workload in
        evidence. This transaction changes no claim, reservation, operation,
        source identity or historical two-author policy. Expiry only prevents
        new admission above the base limit; existing claims must finish or be
        reconciled normally, and held reservations always remain occupied.
        """
        if host not in {'hhu-server',*('hhu-server-'+str(i)for i in range(2,9))}:raise ValueError('authorized author host required')
        if type(authors_per_host)is not int or authors_per_host!=3:raise ValueError('bounded three-author canary required')
        sha(author_policy_sha256);required(change_id,'author extension change ID')
        self.retained_binding(authorization);self.retained_binding(evidence)
        if type(expected_revision)is not int or expected_revision<0 or type(expires_at)not in(int,float)or not math.isfinite(expires_at):raise ValueError('finite author extension selection required')
        args=dict(host=host,authors_per_host=authors_per_host,author_policy_sha256=author_policy_sha256,
            authorization=authorization,evidence=evidence,change_id=change_id,
            expected_revision=expected_revision,expires_at=expires_at)
        identity=digest(args)
        with self.transaction()as db:
            rows=self.author_extensions(db);prior=next((r for r in rows if r['change_id']==change_id),None)
            if prior:
                if prior['identity']!=identity:raise Conflict('author extension ID reused with different authority')
                return prior
            if len(rows)!=expected_revision:raise Conflict('author extension revision changed')
            policy=self.author_policy(db)
            if policy is None or digest(policy)!=author_policy_sha256 or policy['authors_per_host']!=2 or policy['physical_models_per_host']!=2:raise Conflict('original two-author and two-model policy required')
            if not self.clock()<expires_at<=self.clock()+14400:raise ValueError('author extension exceeds four-hour authority')
            if any(q['host']==host for q in self.quarantines(db)):raise Conflict('author canary cannot change quarantined host capacity')
            occupied=db.execute("SELECT state FROM author_reservations WHERE host=? AND state IN ('active','hold')",(host,)).fetchall()
            if any(r['state']=='hold'for r in occupied)or len(occupied)>authors_per_host:raise Conflict('author reservation requires reconciliation before capacity change')
            record={k:v for k,v in args.items()if k!='expected_revision'}
            record.update(schema_version='fleet-author-capacity-extension/v1',revision=expected_revision+1,
                identity=identity,created_at=self.clock(),physical_models_per_host=2,
                publication_limit=1,held_reservations_retained=True,base_policy_changed=False)
            db.execute('CREATE TABLE IF NOT EXISTS author_capacity_extensions(change_id TEXT PRIMARY KEY,revision INTEGER UNIQUE NOT NULL,record TEXT NOT NULL)')
            db.execute('INSERT INTO author_capacity_extensions VALUES(?,?,?)',(change_id,expected_revision+1,canonical(record)))
            self.event(db,'permit_author_capacity_extension',record)
            return record

    def set_author_capacity(self,authors_per_host,authorization,evidence,change_id,expected_workbooks_sha256):
        """Activate once, atomically adopting every existing active VM claim.

        The admin evidence must contain a fresh all-host census proving old held
        claims have no surviving workers. Activation does not rewrite any claim,
        operation, expiry, token or fence. A maintenance freeze prevents stale
        observations between that census and this exact ledger comparison.
        """
        if type(authors_per_host)is not int or authors_per_host!=2:raise ValueError('exact two-author policy required')
        self.retained_binding(authorization);self.retained_binding(evidence)
        required(change_id,'author capacity activation ID');sha(expected_workbooks_sha256)
        args=dict(authors_per_host=authors_per_host,authorization=authorization,evidence=evidence,
                  change_id=change_id,expected_workbooks_sha256=expected_workbooks_sha256)
        identity=digest(args)
        with self.transaction()as db:
            existing=db.execute('SELECT * FROM author_capacity_policy WHERE singleton=1').fetchone()
            if existing:
                if existing['identity']!=identity:raise Conflict('author capacity already activated with different authority')
                return json.loads(existing['record'])
            rows=[self.public(row)for row in db.execute('SELECT * FROM workbooks ORDER BY workbook')]
            if digest(rows)!=expected_workbooks_sha256:raise Conflict('author activation workbook ledger changed')
            active=[];counts={}
            for row in rows:
                if row['state']!='active':continue
                host=self.author_host(row['workbook'],row['owner'])
                if host is None:continue
                counts[host]=counts.get(host,0)+1
                if counts[host]>authors_per_host:raise Conflict('existing authors exceed selected host capacity')
                active.append((row,host))
            if db.execute('SELECT 1 FROM author_reservations').fetchone():raise Conflict('unselected author reservation history exists')
            for row,host in active:
                db.execute('INSERT INTO author_reservations VALUES(?,?,?,?,?,?,?,?,?)',
                           (row['claim_id'],row['workbook'],row['owner'],row['fence'],host,'active',self.clock(),None,None))
            policy=dict(schema_version='fleet-author-capacity/v1',revision=1,**args,
                        expired_reservations='held-until-reconciled',legacy_source_workers='count-as-author',
                        publication_capacity='separate',physical_models_per_host=2,
                        local_authors='blocked-pending-local-resource-policy',activated_at=self.clock())
            db.execute('INSERT INTO author_capacity_policy VALUES(1,?,?)',(identity,canonical(policy)))
            self.event(db,'set_author_capacity',policy)
            return policy

    def begin(self, kind, operation_id, workbook, owner, token, fence, request_sha256):
        """Reserve before any side effect. A repeated ID never grants execution twice."""
        if kind not in {'model', 'publication'}:
            raise ValueError('kind must be model or publication')
        required(operation_id, 'operation id'); sha(request_sha256)
        identity = digest(dict(workbook=workbook, request_sha256=request_sha256))
        with self.transaction() as db:
            row = self.owned(db, workbook, owner, token, fence)
            if kind=='model'and self.author_policy(db)is not None and self.author_host(workbook,owner)is None:
                raise Conflict('publication-exempt claim cannot dispatch a model')
            self.quarantine_custody(db,workbook,owner)
            if kind=='model' and self.quarantines(db) and self.host(owner)=='local':raise Conflict('local collection identity cannot dispatch a model')
            old = db.execute('SELECT * FROM operations WHERE kind=? AND operation_id=?', (kind, operation_id)).fetchone()
            if old:
                if old['identity'] != identity:
                    raise Conflict('operation id reused with different request identity')
                return dict(execute=False, operation=self.public(old), reason='existing intent; inspect persisted result, never replay')
            occupied = db.execute("SELECT * FROM operations WHERE kind=? AND state IN ('active','hold') ORDER BY started", (kind,)).fetchall()
            if kind == 'publication':
                busy = occupied[0] if occupied else None
            else:
                # Global hold is the default. An explicit bounded quarantine
                # may isolate one retained uncertainty, never release or replay it.
                busy = next((op for op in occupied if op['state']=='hold' and not self.quarantined_hold(db,op,workbook,owner)), None)
                if busy is None:
                    busy = next((op for op in occupied if op['workbook']==workbook), None)
                if busy is None and self.author_policy(db)is not None:
                    host=self.author_host(workbook,owner)
                    on_host=[op for op in occupied if self.author_host(op['workbook'],op['owner'])==host]
                    if len(on_host)>=self.physical_model_limit(db,host):
                        busy=next((op for op in on_host if op['state']=='active'),on_host[0])
                if busy is None and self.host_capacities(db):
                    host=self.host(owner)
                    exception=self.host_capacity(db,host)
                    if exception is not None:
                        host_occupied=[op for op in occupied if self.host(op['owner'])==host]
                        if len(host_occupied)>=max(exception['physical_model_limit'],self.physical_model_limit(db,host)):
                            busy=next((op for op in host_occupied if op['state']=='active'),host_occupied[0])
                limit = self.model_policy(db)['model_limit']
                if busy is None and limit is not None and len(occupied) >= limit:
                    busy = occupied[0]
            if busy:
                return dict(execute=False, busy=True, reason='model/publication capacity or reconciliation barrier', state=busy['state'])
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (kind, operation_id, identity, workbook, owner, token, fence, 'active', self.clock(), row['expires'], None, None))
            self.event(db, 'begin', dict(kind=kind, operation_id=operation_id, workbook=workbook, owner=owner, fence=fence, request_sha256=request_sha256))
            return dict(execute=True, operation_id=operation_id)

    def end(self, kind, operation_id, workbook, owner, token, fence, evidence, outcome='confirmed'):
        """Only a persisted response or verified publication permits confirmed end."""
        required(evidence, 'persisted response/publication evidence')
        if outcome not in {'confirmed', 'uncertain'}:
            raise ValueError('invalid operation outcome')
        with self.transaction() as db:
            self.owned(db, workbook, owner, token, fence)
            op = db.execute('SELECT * FROM operations WHERE kind=? AND operation_id=?', (kind, operation_id)).fetchone()
            if op is None or (op['workbook'],op['owner'],op['fence']) != (workbook, owner, fence) or not hmac.compare_digest(op['token'],token):
                raise Conflict('not operation owner')
            if op['state'] == 'confirmed' and outcome == 'confirmed' and op['evidence'] == evidence:
                return self.public(op)
            if op['state'] != 'active':
                raise Conflict('operation requires reconciliation')
            state = 'confirmed' if outcome == 'confirmed' else 'hold'
            db.execute('UPDATE operations SET state=?,ended=?,evidence=? WHERE kind=? AND operation_id=?', (state,self.clock(),evidence,kind,operation_id))
            if outcome == 'uncertain':
                db.execute("UPDATE workbooks SET state='hold',reason=?,updated=? WHERE workbook=?", ('uncertain '+kind+'; reconcile without replay',self.clock(),workbook))
            self.event(db, 'end', dict(kind=kind, operation_id=operation_id, outcome=outcome, evidence=evidence, workbook=workbook))
            return {'state': state, 'operation_id': operation_id}

    def reconcile_operation(self, kind, operation_id, evidence, disposition):
        """Admin-only; terminal resolution never deletes consumed operation IDs.

        confirmed: verified persisted response/output; abandoned: stopped attempt
        with budget consumed and provider completion reconciled; never auto-replay.
        """
        required(evidence, 'retained external reconciliation evidence')
        if disposition not in {'confirmed','abandoned'}:
            raise ValueError('operation disposition must be confirmed or abandoned')
        with self.transaction() as db:
            op = db.execute('SELECT * FROM operations WHERE kind=? AND operation_id=?', (kind, operation_id)).fetchone()
            if op is None or op['state'] != 'hold':
                raise Conflict('only held operations may be reconciled')
            db.execute('UPDATE operations SET state=?,ended=?,evidence=? WHERE kind=? AND operation_id=?', (disposition,self.clock(),evidence,kind,operation_id))
            self.event(db, 'reconcile_operation', dict(kind=kind, operation_id=operation_id, disposition=disposition, evidence=evidence))
            return {'state':disposition, 'operation_id':operation_id}

    def model_policy(self, db):
        row = db.execute('SELECT * FROM model_policy WHERE singleton=1').fetchone()
        if row is None:
            return dict(schema_version='fleet-concurrency-policy/v1',revision=0,
                        model_limit=1,authorization=None,change_id=None,
                        per_workbook_model_limit=1,unknown_model_hold_barrier='global',publication_limit=1)
        return dict(schema_version='fleet-concurrency-policy/v1',revision=row['revision'],
                    model_limit=row['model_limit'],authorization=json.loads(row['authorization']),
                    change_id=row['change_id'],per_workbook_model_limit=1,
                    unknown_model_hold_barrier='global',publication_limit=1)

    def set_model_limit(self, model_limit, authorization, change_id, expected_revision):
        """Admin-only, explicit and durable; None removes the fleet model cap.

        The same change ID is idempotent after a lost reply. This transaction
        preserves all claims, operation IDs/evidence and publication capacity.
        It never dispatches/replays a call or reconciles a held operation.
        """
        if model_limit is not None and (type(model_limit) is not int or model_limit < 1):
            raise ValueError('model_limit must be a positive integer or null')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('nonnegative expected policy revision required')
        if not isinstance(authorization,dict) or set(authorization)!={'path','sha256'} or not isinstance(authorization['path'],str) or not Path(authorization['path']).is_absolute():
            raise ValueError('exact retained authorization binding required')
        sha(authorization['sha256'])
        required(change_id, 'unique policy change ID')
        identity=digest(dict(model_limit=model_limit,authorization=authorization,
                             expected_revision=expected_revision))
        with self.transaction() as db:
            previous=db.execute('SELECT * FROM model_policy_changes WHERE change_id=?',(change_id,)).fetchone()
            if previous:
                if previous['identity']!=identity:
                    raise Conflict('policy change ID reused with different identity')
                return json.loads(previous['result'])
            before=self.model_policy(db)
            if before['revision']!=expected_revision:
                raise Conflict('model policy revision changed; reconcile')
            count=db.execute("SELECT COUNT(*) FROM operations WHERE kind='model' AND state IN ('active','hold')").fetchone()[0]
            if model_limit is not None and count>model_limit:
                raise Conflict('new model cap is below retained occupied operations')
            # SQLite DDL is transactional here. A crash cannot expose a dropped
            # global index without its replacement constraints and policy row.
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_publication_operation ON operations(kind) WHERE kind='publication' AND state IN ('active','hold')")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_workbook_operation ON operations(kind,workbook) WHERE state IN ('active','hold')")
            db.execute('DROP INDEX IF EXISTS one_global_operation')
            db.execute('INSERT OR REPLACE INTO model_policy VALUES(1,?,?,?,?,?)',
                       (expected_revision+1,model_limit,canonical(authorization),change_id,self.clock()))
            after=self.model_policy(db)
            db.execute('INSERT INTO model_policy_changes VALUES(?,?,?)',(change_id,identity,canonical(after)))
            self.event(db,'set_model_limit',dict(before=before,after=after))
            return after

    @staticmethod
    def host(owner):
        match=re.fullmatch(r'(hhu-server(?:-[2-8])?)([:/])[^\s]+',owner) if isinstance(owner,str) else None
        value=match.group(1) if match else ('local' if isinstance(owner,str) and re.fullmatch(r'local:collection:[0-9]{4}:[0-9]{3}',owner) else None)
        if value is None:
            raise Conflict('explicit authorized host prefix required by quarantine')
        return value

    @staticmethod
    def retained_binding(value):
        if (not isinstance(value,dict) or set(value)!={'path','sha256'}
                or not isinstance(value['path'],str) or not Path(value['path']).is_absolute()
                or '..' in Path(value['path']).parts):
            raise ValueError('exact retained absolute evidence binding required')
        sha(value['sha256'])
        return value

    def quarantines(self,db):
        return [json.loads(row['record']) for row in db.execute('SELECT record FROM model_quarantines ORDER BY revision')]

    def host_capacities(self,db):
        return [json.loads(row['record']) for row in db.execute('SELECT record FROM quarantine_host_capacity ORDER BY revision')]

    def host_capacity(self,db,host):
        for row in self.host_capacities(db):
            if row['host']!=host or not row['created_at']<=self.clock()<row['expires_at']:continue
            q=next((q for q in self.quarantines(db) if q['change_id']==row['quarantine_change_id']),None)
            if q is None or digest(q)!=row['quarantine_sha256'] or not self.clock()<q['expires_at']:continue
            op=db.execute("SELECT * FROM operations WHERE kind='model' AND operation_id=?",(q['operation_id'],)).fetchone()
            current=self.row(db,q['workbook'])
            if (op is not None and op['state']=='hold' and digest(self.public(op))==q['operation_sha256']
                    and current['state']=='hold' and all(current[k]==q[k] for k in ('owner','fence','source_sha256','version'))):return row
        return None

    def permit_quarantine_host_capacity(self,host,quarantine_change_id,quarantine_sha256,
            physical_model_limit,authorization,evidence,change_id,expected_revision,expires_at):
        """Append a bounded host exception; uncertainty occupies one model slot."""
        required(host,'host');required(quarantine_change_id,'quarantine identity');sha(quarantine_sha256)
        required(change_id,'host capacity change ID');self.retained_binding(authorization);self.retained_binding(evidence)
        if physical_model_limit!=2 or type(physical_model_limit)is not int:raise ValueError('exact two-slot physical capacity required')
        if type(expected_revision)is not int or expected_revision<0:raise ValueError('host capacity revision required')
        if type(expires_at)not in(int,float) or not math.isfinite(expires_at):raise ValueError('finite host exception expiry required')
        fields=dict(host=host,quarantine_change_id=quarantine_change_id,quarantine_sha256=quarantine_sha256,
                    physical_model_limit=physical_model_limit,authorization=authorization,evidence=evidence,
                    change_id=change_id,expected_revision=expected_revision,expires_at=expires_at)
        identity=digest(fields)
        with self.transaction()as db:
            prior=db.execute('SELECT * FROM quarantine_host_capacity WHERE change_id=?',(change_id,)).fetchone()
            if prior:
                if prior['identity']!=identity:raise Conflict('host capacity ID reused with different authority')
                return json.loads(prior['record'])
            rows=self.host_capacities(db)
            if len(rows)!=expected_revision or any(x['host']==host for x in rows):raise Conflict('host capacity already selected or revision changed')
            q=next((q for q in self.quarantines(db)if q['change_id']==quarantine_change_id),None)
            if q is None or q['host']!=host or digest(q)!=quarantine_sha256:raise Conflict('exact retained host quarantine required')
            if not self.clock()<expires_at<=q['expires_at']:raise ValueError('host exception cannot extend quarantine authority')
            op=db.execute("SELECT * FROM operations WHERE kind='model' AND operation_id=?",(q['operation_id'],)).fetchone()
            current=self.row(db,q['workbook'])
            if (op is None or op['state']!='hold' or digest(self.public(op))!=q['operation_sha256']
                    or current['state']!='hold' or any(current[k]!=q[k]for k in ('owner','fence','source_sha256','version'))):raise Conflict('unknown held effect or custody changed')
            if any(self.host(x['owner'])==host for x in db.execute("SELECT owner FROM workbooks WHERE state='active'")):
                raise Conflict('host capacity activation requires stopped claims')
            if any(self.host(x['owner'])==host for x in db.execute("SELECT owner FROM operations WHERE state='active'")):
                raise Conflict('host capacity activation requires stopped operations')
            record={k:v for k,v in fields.items()if k!='expected_revision'}
            record.update(schema_version='fleet-quarantine-host-capacity/v1',revision=expected_revision+1,
                          created_at=self.clock(),uncertain_operation_retained=True,workbook_blocked=True)
            db.execute('INSERT INTO quarantine_host_capacity VALUES(?,?,?,?)',(change_id,identity,expected_revision+1,canonical(record)))
            self.event(db,'permit_quarantine_host_capacity',record)
            return record

    def physical_extensions(self,db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='physical_capacity_extensions'").fetchone():return []
        return [json.loads(r['record'])for r in db.execute('SELECT record FROM physical_capacity_extensions ORDER BY revision')]

    def physical_model_limit(self,db,host):
        policy=self.author_policy(db);base=policy['physical_models_per_host']if policy else 2
        for row in self.physical_extensions(db):
            if row['host']!=host or not row['created_at']<=self.clock()<row['expires_at']:continue
            capacity=self.host_capacity(db,host)
            if capacity is None or digest(capacity)!=row['prior_capacity_sha256']or digest(policy)!=row['author_policy_sha256']:continue
            qs=[q for q in self.quarantines(db)if q['host']==host]
            if {q['change_id']:digest(q)for q in qs}!=row['quarantines']:continue
            valid=True
            for q in qs:
                op=db.execute("SELECT * FROM operations WHERE kind='model'AND operation_id=?",(q['operation_id'],)).fetchone();w=self.row(db,q['workbook'])
                valid=valid and op is not None and op['state']=='hold'and digest(self.public(op))==q['operation_sha256']and w['state']=='hold'and all(w[k]==q[k]for k in ('owner','fence','source_sha256','version'))and self.clock()<q['expires_at']
            if valid:return row['physical_model_limit']
        return base

    def permit_physical_capacity_extension(self,host,prior_capacity_sha256,quarantines,
            physical_model_limit,maximum_fresh_models,authorization,evidence,change_id,expected_revision,expires_at):
        """Append a finite 2-unknown plus 1-new physical allowance; never alter v1."""
        required(host,'host');sha(prior_capacity_sha256);required(change_id,'physical capacity change ID')
        self.retained_binding(authorization);self.retained_binding(evidence)
        if type(physical_model_limit)is not int or physical_model_limit!=3 or type(maximum_fresh_models)is not int or maximum_fresh_models!=1:raise ValueError('exact two retained unknowns plus one fresh model required')
        if type(expected_revision)is not int or expected_revision<0 or type(expires_at)not in(int,float)or not math.isfinite(expires_at):raise ValueError('finite physical extension selection required')
        if not isinstance(quarantines,dict)or len(quarantines)!=2:raise ValueError('exact two retained quarantines required')
        for key,value in quarantines.items():required(key,'quarantine change ID');sha(value)
        args=dict(host=host,prior_capacity_sha256=prior_capacity_sha256,quarantines=quarantines,physical_model_limit=physical_model_limit,maximum_fresh_models=maximum_fresh_models,authorization=authorization,evidence=evidence,change_id=change_id,expected_revision=expected_revision,expires_at=expires_at)
        identity=digest(args)
        with self.transaction()as db:
            rows=self.physical_extensions(db);prior=next((r for r in rows if r['change_id']==change_id),None)
            if prior:
                if prior['identity']!=identity:raise Conflict('physical capacity ID reused with different authority')
                return prior
            if len(rows)!=expected_revision or any(r['host']==host for r in rows):raise Conflict('physical capacity extension already selected or revision changed')
            policy=self.author_policy(db);capacity=self.host_capacity(db,host)
            if policy is None or policy['authors_per_host']!=2 or policy['physical_models_per_host']!=2 or capacity is None or digest(capacity)!=prior_capacity_sha256:raise Conflict('original two-slot author and host capacity required')
            qs=[q for q in self.quarantines(db)if q['host']==host]
            if {q['change_id']:digest(q)for q in qs}!=quarantines:raise Conflict('retained quarantines changed')
            if not self.clock()<expires_at<=min(self.clock()+14400,capacity['expires_at'],*(q['expires_at']for q in qs)):raise ValueError('physical extension exceeds original or four-hour authority')
            occupied=[o for o in db.execute("SELECT * FROM operations WHERE kind='model'AND state IN('active','hold')")if self.host(o['owner'])==host]
            if len(occupied)!=2 or {o['operation_id']for o in occupied}!={q['operation_id']for q in qs}or any(o['state']!='hold'for o in occupied):raise Conflict('exact two unknown occupied physical slots required')
            for q in qs:
                op=next(o for o in occupied if o['operation_id']==q['operation_id']);w=self.row(db,q['workbook'])
                if digest(self.public(op))!=q['operation_sha256']or w['state']!='hold'or any(w[k]!=q[k]for k in ('owner','fence','source_sha256','version')):raise Conflict('unknown operation or workbook custody changed')
            if any(self.host(w['owner'])==host for w in db.execute("SELECT owner FROM workbooks WHERE state='active'")):raise Conflict('target host admission must be stopped before physical policy activation')
            record={k:v for k,v in args.items()if k!='expected_revision'}
            record.update(schema_version='fleet-physical-capacity-extension/v1',revision=expected_revision+1,identity=identity,created_at=self.clock(),author_policy_sha256=digest(policy),unknown_slots_consumed=2,unknown_operations_released=False,author_limit_changed=False)
            db.execute('CREATE TABLE IF NOT EXISTS physical_capacity_extensions(change_id TEXT PRIMARY KEY,revision INTEGER UNIQUE NOT NULL,record TEXT NOT NULL)')
            db.execute('INSERT INTO physical_capacity_extensions VALUES(?,?,?)',(change_id,expected_revision+1,canonical(record)))
            self.event(db,'permit_physical_capacity_extension',record)
            return record

    def quarantine_custody(self,db,workbook,owner):
        records=self.quarantines(db)
        if not records:return
        host=self.host(owner)
        # Expiration ends the exception for unrelated work, not isolation of
        # the original uncertain effect. Removal needs a future explicit policy.
        if any(q['workbook']==workbook or (q['host']==host and self.host_capacity(db,host)is None) for q in records):
            raise Conflict('quarantined workbook or host; uncertainty remains consumed')

    def quarantined_hold(self,db,op,workbook,owner):
        if op['kind']!='model' or op['state']!='hold':return False
        for q in self.quarantines(db):
            if q['operation_id']!=op['operation_id']:continue
            current=self.row(db,q['workbook'])
            exact=(q['operation_sha256']==digest(self.public(op))
                   and all(current[k]==q[k] for k in ('workbook','owner','fence','source_sha256','version'))
                   and current['state']=='hold' and q['created_at']<=self.clock()<q['expires_at'])
            if exact and workbook!=q['workbook'] and (self.host(owner)!=q['host'] or self.host_capacity(db,q['host'])is not None):return True
        return False

    def quarantine_model(self,operation_id,workbook,owner,fence,source_sha256,version,host,
                         operation_sha256,authorization,evidence,change_id,expected_revision,expires_at):
        """Admin-only: retain an uncertain effect and isolate its host/workbook.

        This appends authority; it never edits the claim, operation, consumed ID,
        request, evidence or attempts. Expiry restores the default global barrier.
        """
        required(operation_id,'operation ID');required(workbook,'workbook');required(owner,'owner');required(version,'version')
        sha(source_sha256);sha(operation_sha256);self.retained_binding(authorization);self.retained_binding(evidence)
        required(change_id,'quarantine change ID')
        if host=='local' or self.host(owner)!=host:raise ValueError('quarantine host differs from exact owner')
        if type(fence)is not int or fence<1 or type(expected_revision)is not int or expected_revision<0:raise ValueError('exact fence/revision required')
        if type(expires_at)not in(int,float) or not math.isfinite(expires_at):raise ValueError('finite quarantine expiry required')
        fields=dict(operation_id=operation_id,workbook=workbook,owner=owner,fence=fence,
                    source_sha256=source_sha256,version=version,host=host,operation_sha256=operation_sha256,
                    authorization=authorization,evidence=evidence,change_id=change_id,
                    expected_revision=expected_revision,expires_at=expires_at)
        identity=digest(fields)
        with self.transaction()as db:
            previous=db.execute('SELECT * FROM model_quarantines WHERE change_id=?',(change_id,)).fetchone()
            if previous:
                if previous['identity']!=identity:raise Conflict('quarantine ID reused with different authority')
                return json.loads(previous['record'])
            records=self.quarantines(db)
            if len(records)!=expected_revision:raise Conflict('quarantine revision changed')
            if not self.clock()<expires_at<=self.clock()+86400:raise ValueError('quarantine exception must expire within 24 hours')
            if any(q['operation_id']==operation_id or q['workbook']==workbook for q in records):raise Conflict('quarantine already retained; no implicit extension')
            op=db.execute("SELECT * FROM operations WHERE kind='model' AND operation_id=?",(operation_id,)).fetchone()
            row=self.row(db,workbook)
            if (op is None or op['state']!='hold' or digest(self.public(op))!=operation_sha256
                    or (op['workbook'],op['owner'],op['fence'])!=(workbook,owner,fence)
                    or row['state']!='hold' or any(row[k]!=fields[k] for k in ('owner','fence','source_sha256','version'))):
                raise Conflict('exact current held model and workbook required')
            for active in db.execute("SELECT owner FROM workbooks WHERE state='active'"):
                if self.host(active['owner'])==host:raise Conflict('quarantined host still has an active workbook')
            for active in db.execute("SELECT owner FROM operations WHERE state='active'"):
                if self.host(active['owner'])==host:raise Conflict('quarantined host still has an active operation')
            result={k:v for k,v in fields.items() if k!='expected_revision'}
            result.update(schema_version='fleet-model-quarantine/v1',revision=expected_revision+1,
                          created_at=self.clock(),state='quarantined_unknown',provider_completion='unknown',
                          consumed_operation_retained=True,workbook_blocked=True,host_blocked=True)
            db.execute('INSERT INTO model_quarantines VALUES(?,?,?,?)',(change_id,identity,expected_revision+1,canonical(result)))
            self.event(db,'quarantine_model',result)
            return result

    def renew_quarantine(self, **arguments):
        """Explicit append-only renewal; original held effect remains consumed."""
        from scripts.fleet.quarantine_renewal import renew
        return renew(self, **arguments)

    def status(self):
        with self.transaction() as db:
            return {'schema_version':'fleet-coordinator/v1', 'model_limit':self.model_policy(db)['model_limit'], 'publication_limit':1,
                    'model_policy':self.model_policy(db),
                    'quarantine_policy':dict(schema_version='fleet-model-quarantine-policy/v1',revision=len(self.quarantines(db)),
                        default_unknown_model_hold_barrier='global',maximum_exception_seconds=86400,
                        per_workbook_model_limit=1,publication_limit=1),
                    'quarantines':self.quarantines(db),
                    'quarantine_host_capacity':self.host_capacities(db),
                    'physical_capacity_extensions':self.physical_extensions(db),
                    'author_capacity_policy':self.author_policy(db),
                    'author_capacity_extensions':self.author_extensions(db),
                    'abandoned_claims':[self.abandoned_claim_record(r)for r in db.execute('SELECT * FROM abandoned_claims ORDER BY abandoned_at,claim_id')],
                    'author_reservations':[dict(r)for r in db.execute('SELECT * FROM author_reservations ORDER BY started,claim_id')],
                    'workbooks':[self.public(r) for r in db.execute('SELECT * FROM workbooks ORDER BY workbook')],
                    'operations':[self.public(r) for r in db.execute('SELECT * FROM operations ORDER BY started')],
                    'events':db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 'at':self.clock()}


ADMIN = {'abandon_claim','enroll', 'reconcile', 'reconcile_operation', 'set_model_limit', 'quarantine_model', 'permit_quarantine_host_capacity','set_author_capacity','renew_quarantine','permit_physical_capacity_extension','permit_author_capacity_extension'}
ACTIONS = ADMIN | {'claim','heartbeat','finish','begin','end','status'}


def serve(coordinator, host, port, worker_token, admin_token):
    if host not in {'127.0.0.1','::1'}:
        raise ValueError('only loopback binding is supported; use authenticated SSH tunnels')
    if len(worker_token) < 32 or len(admin_token) < 32 or worker_token == admin_token:
        raise ValueError('distinct tokens of at least 32 characters required')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            code = 200
            try:
                if self.path != '/v1':
                    raise ValueError('invalid route')
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 1048576:
                    raise ValueError('bounded request body required')
                body = json.loads(self.rfile.read(length))
                action = body['action']
                if action not in ACTIONS:
                    raise ValueError('unknown action')
                bearer = self.headers.get('Authorization','').removeprefix('Bearer ')
                permitted = hmac.compare_digest(bearer, admin_token) or (action not in ADMIN and hmac.compare_digest(bearer,worker_token))
                if not permitted:
                    code = 403; result = {'error':'forbidden'}
                else:
                    result = getattr(coordinator, action)(**body.get('arguments',{}))
            except Conflict as exc:
                code = 409; result = {'error':str(exc)}
            except (ValueError,TypeError,KeyError) as exc:
                code = 400; result = {'error':str(exc)}
            except Exception:
                code = 500; result = {'error':'coordinator error; reconcile uncertain mutations before retry'}
            data = canonical(result).encode()
            compressed = (code == 200 and action == 'status'
                          and self.headers.get('Accept-Encoding', '').strip().lower() == 'gzip')
            if compressed:
                data = gzip.compress(data, compresslevel=1, mtime=0)
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer((host,port),Handler)
    return server


class Client:
    """No automatic retries: a lost response may follow a committed mutation."""
    def __init__(self, url, token_file, *, timeout=30):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1','localhost','::1'}:
            raise ValueError('client requires loopback SSH tunnel URL')
        self.url = url.rstrip('/')+'/v1'
        self.token = Path(token_file).read_text().strip()
        self.timeout = timeout

    def call(self, action, **arguments):
        headers = {'Content-Type':'application/json','Authorization':'Bearer '+self.token}
        if action == 'status':
            headers['Accept-Encoding'] = 'gzip'
        req = urllib.request.Request(self.url, data=canonical(dict(action=action,arguments=arguments)).encode(),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response:
                if action == 'status':
                    encoding = response.headers.get('Content-Encoding', 'identity').strip().lower()
                    if encoding == 'gzip':
                        return json.loads(gzip.decompress(response.read()))
                    if encoding != 'identity':
                        raise ValueError('unsupported coordinator status encoding')
                return json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                value = json.load(exc)
            if exc.code == 409:
                raise Conflict(value['error']) from None
            raise RuntimeError(f"coordinator HTTP {exc.code}: {value['error']}") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('serve'); p.add_argument('--database',required=True); p.add_argument('--port',type=int,default=8765); p.add_argument('--worker-token-file',required=True); p.add_argument('--admin-token-file',required=True); p.add_argument('--legacy-lock',action='append',default=[])
    p = sub.add_parser('status'); p.add_argument('--database',required=True)
    p = sub.add_parser('call'); p.add_argument('--url',required=True); p.add_argument('--token-file',required=True); p.add_argument('action',choices=sorted(ACTIONS))
    args = parser.parse_args()
    if args.command == 'serve':
        import fcntl
        held=[]
        for path in args.legacy_lock:
            descriptor=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            held.append(descriptor)
        server=serve(Coordinator(args.database),'127.0.0.1',args.port,Path(args.worker_token_file).read_text().strip(),Path(args.admin_token_file).read_text().strip())
        server.serve_forever()
    elif args.command == 'status':
        print(canonical(Coordinator(args.database).status()))
    else:
        print(canonical(Client(args.url,args.token_file).call(args.action,**json.load(sys.stdin))))


if __name__ == '__main__':
    main()
