"""Append a bounded quarantine authority without restarting live HTTP callers.

A transactional compatibility view exposes one new, authenticated effective
record per original quarantine. The original table's rows remain byte-for-byte
in model_quarantine_originals. No operation, claim, attempt, or token changes.
"""
import json,math
from .coordinator import Conflict,canonical,digest,required,sha

SCHEMA='fleet-model-quarantine-renewal/v1'
TABLE_SQL='CREATE TABLE model_quarantine_renewals (change_id TEXT PRIMARY KEY, identity TEXT NOT NULL, revision INTEGER NOT NULL UNIQUE, original_change_id TEXT NOT NULL, previous_sha256 TEXT NOT NULL, record TEXT NOT NULL, effective_record TEXT NOT NULL)'
VIEW_SQL='CREATE VIEW model_quarantines AS SELECT o.change_id,o.identity,o.revision,COALESCE((SELECT r.effective_record FROM model_quarantine_renewals r WHERE r.original_change_id=o.change_id ORDER BY r.revision DESC LIMIT 1),o.record) AS record FROM model_quarantine_originals o'
TRIGGER_SQL='CREATE TRIGGER model_quarantines_append INSTEAD OF INSERT ON model_quarantines BEGIN INSERT INTO model_quarantine_originals(change_id,identity,revision,record) VALUES(NEW.change_id,NEW.identity,NEW.revision,NEW.record); END'

def require(ok,message):
    if not ok:raise Conflict(message)

def installed(db):
    row=db.execute("SELECT type,sql FROM sqlite_master WHERE name='model_quarantines'").fetchone()
    require(row is not None,'original quarantine relation missing')
    if row['type']=='table':
        require(not db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('model_quarantine_originals','model_quarantine_renewals','model_quarantines_append')").fetchone(),'partial quarantine migration')
        return False
    require(row['type']=='view'and row['sql']==VIEW_SQL,'foreign quarantine compatibility view')
    for name,sql in [('model_quarantine_renewals',TABLE_SQL),('model_quarantines_append',TRIGGER_SQL)]:
        found=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone();require(found and found['sql']==sql,'quarantine renewal schema differs')
    return True

def history(db):
    active=installed(db);table='model_quarantine_originals'if active else'model_quarantines'
    originals=[json.loads(r['record'])for r in db.execute('SELECT record FROM '+table+' ORDER BY revision')]
    rows=[json.loads(r['record'])for r in db.execute('SELECT record FROM model_quarantine_renewals ORDER BY revision')]if active else[]
    current={q['change_id']:q for q in originals}
    seen=set(current)
    for revision,row in enumerate(rows,1):
        require(set(row)=={'schema_version','revision','original_change_id','previous_sha256','previous_change_id','effective'}and row['schema_version']==SCHEMA and row['revision']==revision,'quarantine renewal history differs')
        prior=current.get(row['original_change_id']);q=row['effective'];require(prior and digest(prior)==row['previous_sha256']and prior['change_id']==row['previous_change_id'],'quarantine renewal predecessor differs')
        mutable={'change_id','created_at','expires_at','authorization','evidence'}
        require(set(q)==set(prior)and all(q[k]==prior[k]for k in set(prior)-mutable)and q['change_id']not in seen,'quarantine renewal changed held identity')
        require(all(type(q[k])in(int,float)and math.isfinite(q[k])for k in('created_at','expires_at'))and prior['created_at']<=q['created_at']and prior['expires_at']<q['expires_at']and 0<q['expires_at']-q['created_at']<=86400,'quarantine renewal bounds differ')
        current[row['original_change_id']]=q;seen.add(q['change_id'])
    actual=[json.loads(r['record'])for r in db.execute('SELECT record FROM model_quarantines ORDER BY revision')]
    require(actual==list(current.values()),'effective quarantine view differs from authenticated chain')
    return originals,rows,actual

def renew(authority,*,original_change_id,previous_sha256,expected_revision,expires_at,authorization,evidence,change_id):
    required(original_change_id,'original quarantine ID');required(change_id,'renewal change ID');sha(previous_sha256)
    authority.retained_binding(authorization);authority.retained_binding(evidence)
    require(type(expected_revision)is int and expected_revision>=0,'exact renewal revision required')
    require(type(expires_at)in(int,float)and math.isfinite(expires_at),'finite quarantine renewal expiry required')
    fields=dict(original_change_id=original_change_id,previous_sha256=previous_sha256,expected_revision=expected_revision,expires_at=expires_at,authorization=authorization,evidence=evidence,change_id=change_id);identity=digest(fields)
    with authority.transaction()as db:
        originals,rows,effective=history(db)
        retained_tables=('workbooks','claims','operations','model_policy','model_policy_changes','quarantine_host_capacity','author_capacity_policy','author_reservations')
        retained={name:[tuple(r)for r in db.execute('SELECT * FROM '+name+' ORDER BY 1,2')]for name in retained_tables}
        if installed(db):
            previous=db.execute('SELECT identity,record FROM model_quarantine_renewals WHERE change_id=?',(change_id,)).fetchone()
            if previous:
                require(previous['identity']==identity,'renewal ID reused with different authority');return json.loads(previous['record'])
        require(len(rows)==expected_revision and not any(q['change_id']==change_id for q in originals),'renewal revision or ID differs')
        roots=[q for q in originals if q['change_id']==original_change_id];require(len(roots)==1,'exact original quarantine required')
        q=effective[roots[0]['revision']-1];require(digest(q)==previous_sha256,'effective predecessor changed')
        now=authority.clock();require(q['created_at']<=now and q['expires_at']<expires_at and now<expires_at<=now+86400,'renewal must extend expiry within 24 hours')
        op=db.execute("SELECT * FROM operations WHERE kind='model' AND operation_id=?",(q['operation_id'],)).fetchone();claim=authority.row(db,q['workbook'])
        require(op is not None and op['state']=='hold'and digest(authority.public(op))==q['operation_sha256']and claim['state']=='hold'and all(claim[k]==q[k]for k in('workbook','owner','fence','source_sha256','version'))and all(op[k]==q[k]for k in('workbook','owner','fence')),'unknown operation or held workbook changed')
        require(not any(authority.host(r['owner'])==q['host']for r in db.execute("SELECT owner FROM workbooks WHERE state='active'"))and not any(authority.host(r['owner'])==q['host']for r in db.execute("SELECT owner FROM operations WHERE state='active'")),'isolated host has active work')
        require(not any(r['host']==q['host']for r in authority.host_capacities(db)),'host-capacity exception needs separate explicit renewal composition')
        original_bytes=[tuple(r)for r in db.execute('SELECT * FROM '+('model_quarantine_originals'if installed(db)else'model_quarantines')+' ORDER BY revision')]
        if not installed(db):
            db.execute('ALTER TABLE model_quarantines RENAME TO model_quarantine_originals');db.execute(TABLE_SQL);db.execute(VIEW_SQL);db.execute(TRIGGER_SQL)
        new=dict(q,change_id=change_id,created_at=now,expires_at=expires_at,authorization=authorization,evidence=evidence)
        record=dict(schema_version=SCHEMA,revision=expected_revision+1,original_change_id=original_change_id,previous_sha256=previous_sha256,previous_change_id=q['change_id'],effective=new)
        db.execute('INSERT INTO model_quarantine_renewals VALUES(?,?,?,?,?,?,?)',(change_id,identity,expected_revision+1,original_change_id,previous_sha256,canonical(record),canonical(new)))
        authority.event(db,'renew_model_quarantine',record)
        require([tuple(r)for r in db.execute('SELECT * FROM model_quarantine_originals ORDER BY revision')]==original_bytes,'original quarantine bytes changed')
        history(db)
        require({name:[tuple(r)for r in db.execute('SELECT * FROM '+name+' ORDER BY 1,2')]for name in retained_tables}==retained,'renewal changed original work or capacity')
        return record
