"""Renew two unknown reservations and their existing host/physical policy atomically."""
import json,math
from .coordinator import Conflict,canonical,digest,required,sha
from . import quarantine_renewal as Q,quarantine_host_renewal as H,physical_capacity_renewal as P

def require(ok,message):
    if not ok:raise Conflict(message)


def renew(authority,*,host,original_physical_id,previous_physical_sha256,expected_physical_revision,original_capacity_id,previous_capacity_sha256,expected_capacity_revision,quarantines,expected_quarantine_revision,quarantine_expires_at,physical_expires_at,authorization,evidence,change_id):
    for v in(host,original_physical_id,original_capacity_id,change_id):required(v,'composed renewal identity')
    for v in(previous_physical_sha256,previous_capacity_sha256):sha(v)
    authority.retained_binding(authorization);authority.retained_binding(evidence)
    require(all(type(v)is int and v>=0 for v in(expected_physical_revision,expected_capacity_revision,expected_quarantine_revision)),'exact renewal revisions required')
    require(all(type(v)in(int,float)and math.isfinite(v)for v in(quarantine_expires_at,physical_expires_at)),'finite renewal expiry required')
    require(isinstance(quarantines,dict)and len(quarantines)==2,'exact two original quarantines required')
    for k,v in quarantines.items():required(k,'original quarantine');sha(v)
    args=dict(host=host,original_physical_id=original_physical_id,previous_physical_sha256=previous_physical_sha256,expected_physical_revision=expected_physical_revision,original_capacity_id=original_capacity_id,previous_capacity_sha256=previous_capacity_sha256,expected_capacity_revision=expected_capacity_revision,quarantines=quarantines,expected_quarantine_revision=expected_quarantine_revision,quarantine_expires_at=quarantine_expires_at,physical_expires_at=physical_expires_at,authorization=authorization,evidence=evidence,change_id=change_id);identity=digest(args)
    with authority.transaction()as db:
        qo,qr,qe=Q.history(db);ho,hr,he=H.history(db);po,pr,pe=P.history(db)
        if P.installed(db):
            found=db.execute('SELECT identity,record FROM physical_capacity_renewals WHERE change_id=?',(change_id,)).fetchone()
            if found:require(found['identity']==identity,'composed renewal ID reused');return json.loads(found['record'])
        require((len(qr),len(hr),len(pr))==(expected_quarantine_revision,expected_capacity_revision,expected_physical_revision),'composed renewal revisions changed')
        roots=[q for q in qo if q['change_id']in quarantines];hroot=next((h for h in ho if h['change_id']==original_capacity_id),None);proot=next((p for p in po if p['change_id']==original_physical_id),None)
        require(len(roots)==2 and hroot and proot,'original composed policy missing')
        qs=[qe[q['revision']-1]for q in roots];cap=he[hroot['revision']-1];old=pe[proot['revision']-1];now=authority.clock()
        require({o['change_id']:digest(q)for o,q in zip(roots,qs)}==quarantines and digest(cap)==previous_capacity_sha256 and digest(old)==previous_physical_sha256,'composed effective predecessor changed')
        require(all(q['host']==host for q in qs)and cap['host']==old['host']==host
            and {q['change_id']:digest(q)for q in qs}==old['quarantines']and digest(cap)==old['prior_capacity_sha256']
            and any(q['change_id']==cap['quarantine_change_id']and digest(q)==cap['quarantine_sha256']for q in qs),'original policy references differ')
        require(max(q['expires_at']for q in qs)<quarantine_expires_at and cap['expires_at']<quarantine_expires_at and now<quarantine_expires_at<=now+86400
            and old['expires_at']<physical_expires_at and now<physical_expires_at<=min(now+14400,quarantine_expires_at)
            and max([old['created_at'],cap['created_at'],*[q['created_at']for q in qs]])<=now,'composed renewal must be bounded and extend authority')
        policy=authority.author_policy(db)
        require(policy and policy['authors_per_host']==2 and policy['physical_models_per_host']==2 and digest(policy)==old['author_policy_sha256']
            and cap['physical_model_limit']==2 and old['physical_model_limit']==3 and old['maximum_fresh_models']==1 and old['unknown_slots_consumed']==2
            and old['unknown_operations_released']is False and old['author_limit_changed']is False,'original limits changed')
        occupied=[o for o in db.execute("SELECT * FROM operations WHERE kind='model'AND state IN('active','hold')")if authority.host(o['owner'])==host]
        require(len(occupied)==2 and {o['operation_id']for o in occupied}=={q['operation_id']for q in qs}and all(o['state']=='hold'for o in occupied),'exact two unknown occupied slots required')
        for q in qs:
            op=next(o for o in occupied if o['operation_id']==q['operation_id']);w=authority.row(db,q['workbook'])
            require(digest(authority.public(op))==q['operation_sha256']and w['state']=='hold'and all(w[k]==q[k]for k in('owner','fence','source_sha256','version')),'unknown operation or workbook changed')
        require(not any(authority.host(w['owner'])==host for w in db.execute("SELECT owner FROM workbooks WHERE state='active'")),'target host has active author')
        ids={change_id,change_id+'-host',change_id+'-q1',change_id+'-q2'}
        require(not ids.intersection(r['change_id']for r in [*qo,*qe,*ho,*he,*po,*pe]),'composed renewal identities reused')
        excluded={'model_quarantines','model_quarantine_renewals','quarantine_host_capacity','quarantine_host_renewals','physical_capacity_extensions','physical_capacity_renewals','events'}
        tables=[r['name']for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'AND name NOT LIKE 'sqlite_%'")if r['name']not in excluded]
        def preserved():return {t:[tuple(r)for r in db.execute('SELECT * FROM "'+t.replace('"','""')+'" ORDER BY 1,2')]for t in tables}
        before=preserved()
        # Existing tables are archived once; original policy rows never change.
        if not Q.installed(db):db.execute('ALTER TABLE model_quarantines RENAME TO model_quarantine_originals');db.execute(Q.TABLE_SQL);db.execute(Q.VIEW_SQL);db.execute(Q.TRIGGER_SQL)
        if not H.installed(db):db.execute('ALTER TABLE quarantine_host_capacity RENAME TO quarantine_host_originals');db.execute(H.TABLE);db.execute(H.VIEW);db.execute(H.TRIGGER)
        if not P.installed(db):
            db.execute('ALTER TABLE physical_capacity_extensions RENAME TO physical_capacity_originals');db.execute(P.TABLE_SQL);db.execute(P.EFFECTIVE_SQL);db.execute('INSERT INTO physical_capacity_extensions SELECT * FROM physical_capacity_originals');db.execute(P.TRIGGER_SQL)
        changes=[]
        for i,(root,q)in enumerate(zip(roots,qs),1):
            nq=dict(q,change_id=change_id+'-q'+str(i),created_at=now,expires_at=quarantine_expires_at,authorization=authorization,evidence=evidence)
            row=dict(schema_version=Q.SCHEMA,revision=len(qr)+i,original_change_id=root['change_id'],previous_sha256=digest(q),previous_change_id=q['change_id'],effective=nq)
            db.execute('INSERT INTO model_quarantine_renewals VALUES(?,?,?,?,?,?,?)',(nq['change_id'],identity,len(qr)+i,root['change_id'],digest(q),canonical(row),canonical(nq)));changes.append(row)
        primary=next(r for r in changes if r['previous_change_id']==cap['quarantine_change_id']);ncap=dict(cap,change_id=change_id+'-host',created_at=now,expires_at=quarantine_expires_at,authorization=authorization,evidence=evidence,quarantine_change_id=primary['effective']['change_id'],quarantine_sha256=digest(primary['effective']))
        hrow=dict(schema_version=H.SCHEMA,revision=len(hr)+1,original_change_id=original_capacity_id,previous_sha256=digest(cap),quarantine_renewal=primary,effective=ncap)
        db.execute('INSERT INTO quarantine_host_renewals VALUES(?,?,?,?,?,?)',(ncap['change_id'],identity,len(hr)+1,original_capacity_id,canonical(hrow),canonical(ncap)))
        new=dict(old,change_id=change_id,created_at=now,expires_at=physical_expires_at,authorization=authorization,evidence=evidence,prior_capacity_sha256=digest(ncap),quarantines={q['effective']['change_id']:digest(q['effective'])for q in changes});new['identity']=P.policy_identity(new)
        row=dict(schema_version=P.COMPOSED,revision=len(pr)+1,original_change_id=original_physical_id,previous_sha256=digest(old),previous_change_id=old['change_id'],effective=new,authority_renewal=dict(quarantines=changes,host_capacity=hrow))
        db.execute('INSERT INTO physical_capacity_renewals VALUES(?,?,?,?,?,?)',(change_id,identity,len(pr)+1,original_physical_id,canonical(row),canonical(new)))
        changed=db.execute('UPDATE physical_capacity_extensions SET record=?WHERE change_id=?AND record=?',(canonical(new),original_physical_id,canonical(old)));require(changed.rowcount==1,'composed physical CAS failed')
        authority.event(db,'renew_quarantine_physical',row)
        require(preserved()==before,'composed renewal changed unrelated/original ledger')
        P.history(db);require(authority.host_capacity(db,host)==ncap and authority.physical_model_limit(db,host)==3,'original listener rejects composed renewal')
        return row
