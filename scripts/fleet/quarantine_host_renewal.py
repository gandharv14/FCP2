"""Atomically renew a quarantine and its two-slot host exception.

Original rows remain immutable. Separate append-only renewal ledgers expose
new effective records to the unchanged coordinator and frozen readers.
"""
import json, math
from .coordinator import Conflict, canonical, digest, required, sha
from . import quarantine_renewal as Q

SCHEMA='fleet-quarantine-host-renewal/v1'
TABLE='CREATE TABLE quarantine_host_renewals(change_id TEXT PRIMARY KEY,identity TEXT NOT NULL,revision INTEGER UNIQUE NOT NULL,original_change_id TEXT NOT NULL,record TEXT NOT NULL,effective_record TEXT NOT NULL)'
VIEW='CREATE VIEW quarantine_host_capacity AS SELECT o.change_id,o.identity,o.revision,COALESCE((SELECT r.effective_record FROM quarantine_host_renewals r WHERE r.original_change_id=o.change_id ORDER BY r.revision DESC LIMIT 1),o.record) AS record FROM quarantine_host_originals o'
TRIGGER='CREATE TRIGGER quarantine_host_capacity_append INSTEAD OF INSERT ON quarantine_host_capacity BEGIN INSERT INTO quarantine_host_originals VALUES(NEW.change_id,NEW.identity,NEW.revision,NEW.record); END'
MUTABLE={'change_id','created_at','expires_at','authorization','evidence','quarantine_change_id','quarantine_sha256'}

def require(ok,message):
    if not ok:raise Conflict(message)

def installed(db):
    row=db.execute("SELECT type,sql FROM sqlite_master WHERE name='quarantine_host_capacity'").fetchone();require(row is not None,'host capacity missing')
    if row['type']=='table':
        require(not db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('quarantine_host_originals','quarantine_host_renewals','quarantine_host_capacity_append')").fetchone(),'partial host renewal schema');return False
    require(row['type']=='view'and row['sql']==VIEW,'foreign host renewal view')
    for name,sql in [('quarantine_host_renewals',TABLE),('quarantine_host_capacity_append',TRIGGER)]:
        actual=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone();require(actual and actual['sql']==sql,'host renewal schema changed')
    return True

def history(db):
    active=installed(db);originals=[json.loads(r['record'])for r in db.execute('SELECT record FROM '+('quarantine_host_originals'if active else'quarantine_host_capacity')+' ORDER BY revision')];rows=[json.loads(r['record'])for r in db.execute('SELECT record FROM quarantine_host_renewals ORDER BY revision')]if active else[]
    current={r['change_id']:r for r in originals};seen=set(current);qo,qr,qe=Q.history(db)
    for revision,r in enumerate(rows,1):
        require(set(r)=={'schema_version','revision','original_change_id','previous_sha256','quarantine_renewal','effective'}and r['schema_version']==SCHEMA and r['revision']==revision,'host renewal chain changed')
        old=current.get(r['original_change_id']);new=r['effective'];q=r['quarantine_renewal'];require(old and digest(old)==r['previous_sha256']and q in qr,'host renewal predecessor changed')
        require(set(new)==set(old)and all(new[k]==old[k]for k in set(old)-MUTABLE)and new['change_id']not in seen,'host renewal changed original capacity')
        require(all(type(new[k])in(int,float)and math.isfinite(new[k])for k in('created_at','expires_at'))and old['created_at']<=new['created_at']and old['expires_at']<new['expires_at']and 0<new['expires_at']-new['created_at']<=86400,'host renewal clock differs')
        require(new['quarantine_change_id']==q['effective']['change_id']and new['quarantine_sha256']==digest(q['effective'])and new['created_at']==q['effective']['created_at']and new['expires_at']==q['effective']['expires_at']and new['host']==q['effective']['host'],'host renewal detached from quarantine')
        current[r['original_change_id']]=new;seen.add(new['change_id'])
    actual=[json.loads(r['record'])for r in db.execute('SELECT record FROM quarantine_host_capacity ORDER BY revision')];require(actual==list(current.values()),'host renewal effective view changed')
    return originals,rows,actual

def renew(authority,*,original_quarantine_id,previous_quarantine_sha256,expected_quarantine_revision,original_capacity_id,previous_capacity_sha256,expected_capacity_revision,expires_at,authorization,evidence,change_id):
    for v in(original_quarantine_id,original_capacity_id,change_id):required(v,'renewal identity')
    for v in(previous_quarantine_sha256,previous_capacity_sha256):sha(v)
    authority.retained_binding(authorization);authority.retained_binding(evidence)
    require(type(expected_quarantine_revision)is int and expected_quarantine_revision>=0 and type(expected_capacity_revision)is int and expected_capacity_revision>=0,'exact renewal revisions required')
    require(type(expires_at)in(int,float)and math.isfinite(expires_at),'finite renewal expiry required')
    args=dict(original_quarantine_id=original_quarantine_id,previous_quarantine_sha256=previous_quarantine_sha256,expected_quarantine_revision=expected_quarantine_revision,original_capacity_id=original_capacity_id,previous_capacity_sha256=previous_capacity_sha256,expected_capacity_revision=expected_capacity_revision,expires_at=expires_at,authorization=authorization,evidence=evidence,change_id=change_id);identity=digest(args)
    with authority.transaction()as db:
        qo,qr,qe=Q.history(db);co,cr,ce=history(db)
        if installed(db):
            prior=db.execute('SELECT identity,record FROM quarantine_host_renewals WHERE change_id=?',(change_id,)).fetchone()
            if prior:require(prior['identity']==identity,'renewal ID reused');return json.loads(prior['record'])
        require(len(qr)==expected_quarantine_revision and len(cr)==expected_capacity_revision,'renewal revision changed')
        oq=next((q for q in qo if q['change_id']==original_quarantine_id),None);oc=next((c for c in co if c['change_id']==original_capacity_id),None);require(oq and oc,'original paired authority missing')
        q=qe[oq['revision']-1];cap=ce[oc['revision']-1];now=authority.clock()
        require(digest(q)==previous_quarantine_sha256 and digest(cap)==previous_capacity_sha256 and cap['quarantine_change_id']==q['change_id']and cap['quarantine_sha256']==digest(q)and cap['host']==q['host'],'exact effective paired authority changed')
        require(max(q['created_at'],cap['created_at'])<=now and max(q['expires_at'],cap['expires_at'])<expires_at and now<expires_at<=now+86400 and cap['physical_model_limit']==2,'bounded unchanged physical limit required')
        require(not any(r['host']==q['host']for r in authority.physical_extensions(db)),'physical extension needs explicit composed renewal')
        occupied=[r for r in db.execute("SELECT * FROM operations WHERE kind='model'AND state IN ('active','hold')")if authority.host(r['owner'])==q['host']]
        op=next((r for r in occupied if r['operation_id']==q['operation_id']),None);w=authority.row(db,q['workbook'])
        require(op and op['state']=='hold'and digest(authority.public(op))==q['operation_sha256']and w['state']=='hold'and all(w[k]==q[k]for k in('owner','fence','source_sha256','version'))and len(occupied)<=2 and sum(r['state']=='hold'for r in occupied)==1,'unknown custody or physical occupancy changed')
        require(not any(r['change_id']in {change_id,change_id+'-quarantine'}for r in [*qo,*qe,*co,*ce]),'renewal identity already exists')
        excluded={'model_quarantines','model_quarantine_originals','model_quarantine_renewals','quarantine_host_capacity','quarantine_host_originals','quarantine_host_renewals','events'}
        tables=[r['name']for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'AND name NOT LIKE 'sqlite_%'")if r['name']not in excluded]
        def preserved():return {t:[tuple(r)for r in db.execute('SELECT * FROM "'+t.replace('"','""')+'" ORDER BY 1,2')]for t in tables}
        before=preserved();qbytes=[tuple(r)for r in db.execute('SELECT * FROM '+('model_quarantine_originals'if Q.installed(db)else'model_quarantines')+' ORDER BY revision')];cbytes=[tuple(r)for r in db.execute('SELECT * FROM '+('quarantine_host_originals'if installed(db)else'quarantine_host_capacity')+' ORDER BY revision')]
        if not Q.installed(db):db.execute('ALTER TABLE model_quarantines RENAME TO model_quarantine_originals');db.execute(Q.TABLE_SQL);db.execute(Q.VIEW_SQL);db.execute(Q.TRIGGER_SQL)
        if not installed(db):db.execute('ALTER TABLE quarantine_host_capacity RENAME TO quarantine_host_originals');db.execute(TABLE);db.execute(VIEW);db.execute(TRIGGER)
        nq=dict(q,change_id=change_id+'-quarantine',created_at=now,expires_at=expires_at,authorization=authorization,evidence=evidence)
        qrow=dict(schema_version=Q.SCHEMA,revision=len(qr)+1,original_change_id=original_quarantine_id,previous_sha256=digest(q),previous_change_id=q['change_id'],effective=nq)
        db.execute('INSERT INTO model_quarantine_renewals VALUES(?,?,?,?,?,?,?)',(nq['change_id'],identity,len(qr)+1,original_quarantine_id,digest(q),canonical(qrow),canonical(nq)))
        nc=dict(cap,change_id=change_id,created_at=now,expires_at=expires_at,authorization=authorization,evidence=evidence,quarantine_change_id=nq['change_id'],quarantine_sha256=digest(nq))
        record=dict(schema_version=SCHEMA,revision=len(cr)+1,original_change_id=original_capacity_id,previous_sha256=digest(cap),quarantine_renewal=qrow,effective=nc)
        db.execute('INSERT INTO quarantine_host_renewals VALUES(?,?,?,?,?,?)',(change_id,identity,len(cr)+1,original_capacity_id,canonical(record),canonical(nc)));authority.event(db,'renew_quarantine_host',record)
        require(preserved()==before and [tuple(r)for r in db.execute('SELECT * FROM model_quarantine_originals ORDER BY revision')]==qbytes and [tuple(r)for r in db.execute('SELECT * FROM quarantine_host_originals ORDER BY revision')]==cbytes,'paired renewal changed original ledger')
        history(db);require(authority.host_capacity(db,q['host'])==nc,'original reader refused paired renewal');return record
