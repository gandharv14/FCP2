"""Release only an authenticated stopped author's durable reservation.

Unknown model operations remain occupied, and their workbook remains held.
The original coordinator transaction and release_author implementation perform
the update; an append-only receipt makes this administrative action auditable.
"""
import json
from .coordinator import Conflict,canonical,digest,required,sha

SCHEMA='fleet-stopped-author-reconciliation/v1'
TABLE='stopped_author_reconciliations'


def require(ok,message):
    if not ok:raise Conflict(message)


def reconcile(authority,*,workbook,claim_id,owner,fence,reservation_sha256,
        workbook_sha256,quarantines,authorization,evidence,change_id):
    for value,name in ((workbook,'workbook'),(claim_id,'claim'),(owner,'owner'),(change_id,'change')):required(value,name)
    for value in (reservation_sha256,workbook_sha256):sha(value)
    require(type(fence)is int and fence>0,'positive exact fence required')
    require(isinstance(quarantines,dict)and len(quarantines)<=2,'exact retained workbook quarantines required')
    for key,value in quarantines.items():required(key,'quarantine');sha(value)
    authority.retained_binding(authorization);authority.retained_binding(evidence)
    args=dict(workbook=workbook,claim_id=claim_id,owner=owner,fence=fence,
        reservation_sha256=reservation_sha256,workbook_sha256=workbook_sha256,
        quarantines=quarantines,authorization=authorization,evidence=evidence,change_id=change_id)
    identity=digest(args)
    with authority.transaction()as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table'AND name=?",(TABLE,)).fetchone()
        if exists:
            old=db.execute('SELECT identity,record FROM '+TABLE+' WHERE change_id=?',(change_id,)).fetchone()
            if old:
                require(old['identity']==identity,'stopped author change ID reused');return json.loads(old['record'])
        w=authority.row(db,workbook);r=db.execute('SELECT * FROM author_reservations WHERE claim_id=?',(claim_id,)).fetchone()
        require(w['state']=='hold'and all(w[k]==v for k,v in dict(claim_id=claim_id,owner=owner,fence=fence).items())
            and digest(authority.public(w))==workbook_sha256,'original held workbook changed')
        require(r is not None and r['state']=='hold'and r['ended']is None and r['evidence']is None
            and all(r[k]==w[k]for k in ('workbook','claim_id','owner','fence'))
            and r['host']==authority.author_host(workbook,owner)and digest(dict(r))==reservation_sha256,
            'exact unreconciled held author reservation required')
        ops=list(db.execute("SELECT * FROM operations WHERE workbook=? AND state IN('active','hold')",(workbook,)))
        qs=[q for q in authority.quarantines(db)if q['workbook']==workbook]
        require({q['change_id']:digest(q)for q in qs}==quarantines
            and {o['operation_id']for o in ops}=={q['operation_id']for q in qs},'outstanding operations differ from exact quarantines')
        for q in qs:
            op=next(o for o in ops if o['operation_id']==q['operation_id'])
            require(op['kind']=='model'and op['state']=='hold'and digest(authority.public(op))==q['operation_sha256']
                and all(q[k]==w[k]for k in ('workbook','owner','fence','source_sha256','version'))
                and q['created_at']<=authority.clock()<q['expires_at'],'unknown operation or quarantine custody changed')
        # Compare every original relation except the selected reservation/event.
        names=[x['name']for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if x['name']not in('author_reservations','events','sqlite_sequence',TABLE)]
        before={n:list(map(tuple,db.execute('SELECT * FROM '+n)))for n in names}
        reservations=[dict(x)for x in db.execute('SELECT * FROM author_reservations ORDER BY claim_id')]
        started=authority.clock();authority.release_author(db,w,canonical(evidence))
        after=[dict(x)for x in db.execute('SELECT * FROM author_reservations ORDER BY claim_id')]
        expected=[dict(x,state='released',ended=authority.clock(),evidence=canonical(evidence))if x['claim_id']==claim_id else x for x in reservations]
        # The live clock may tick between release and comparison; use its actual
        # recorded end time, bounded by this transaction's observation.
        selected=next(x for x in after if x['claim_id']==claim_id)
        expected=[dict(x,ended=selected['ended'])if x['claim_id']==claim_id else x for x in expected]
        require(after==expected and isinstance(selected['ended'],(int,float))and started<=selected['ended']<=authority.clock(),'author release changed foreign reservation')
        require({n:list(map(tuple,db.execute('SELECT * FROM '+n)))for n in names}==before,'author release changed original model or workbook state')
        record=dict(schema_version=SCHEMA if quarantines else 'fleet-stopped-author-no-outstanding-operation/v2',identity=identity,arguments=args,at=authority.clock(),
            before_reservation=reservations[[x['claim_id']for x in reservations].index(claim_id)],
            after_reservation=selected,workbook_remains_held=True,unknown_operations_released=False)
        db.execute('CREATE TABLE IF NOT EXISTS '+TABLE+'(change_id TEXT PRIMARY KEY,identity TEXT NOT NULL,record TEXT NOT NULL)')
        db.execute('INSERT INTO '+TABLE+' VALUES(?,?,?)',(change_id,identity,canonical(record)))
        authority.event(db,'reconcile_stopped_author',record)
        return record
