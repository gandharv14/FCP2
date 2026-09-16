"""Atomically isolate one unknown model while retaining healthy host work.

Unknown effects consume a physical model slot. A new quarantine and its exact
host-capacity exception commit together; claims and operations never change.
"""
import json,math
from .coordinator import Conflict,canonical,digest,required,sha

SCHEMA='fleet-active-host-quarantine/v1'
TABLE='CREATE TABLE IF NOT EXISTS model_quarantine_scope_changes (change_id TEXT PRIMARY KEY, identity TEXT NOT NULL, record TEXT NOT NULL)'

def require(ok,message):
    if not ok:raise Conflict(message)

def isolate(authority,*,operation_id,operation_sha256,workbook,owner,fence,source_sha256,version,host,authorization,evidence,change_id,expected_quarantine_revision,expected_host_revision,expires_at):
    for value,name in ((operation_id,'operation'),(workbook,'workbook'),(owner,'owner'),(version,'version'),(change_id,'change ID')):required(value,name)
    for value in (operation_sha256,source_sha256):sha(value)
    authority.retained_binding(authorization);authority.retained_binding(evidence)
    require(type(fence)is int and fence>=1 and type(expected_quarantine_revision)is int and expected_quarantine_revision>=0 and type(expected_host_revision)is int and expected_host_revision>=0,'exact revisions and fence required')
    require(host!='local'and authority.host(owner)==host,'exact remote host required')
    require(type(expires_at)in(int,float)and math.isfinite(expires_at),'finite scope expiry required')
    args=dict(operation_id=operation_id,operation_sha256=operation_sha256,workbook=workbook,owner=owner,fence=fence,source_sha256=source_sha256,version=version,host=host,authorization=authorization,evidence=evidence,change_id=change_id,expected_quarantine_revision=expected_quarantine_revision,expected_host_revision=expected_host_revision,expires_at=expires_at);identity=digest(args)
    with authority.transaction()as db:
        db.execute(TABLE);old=db.execute('SELECT identity,record FROM model_quarantine_scope_changes WHERE change_id=?',(change_id,)).fetchone()
        if old:
            require(old['identity']==identity,'scope change ID reused');return json.loads(old['record'])
        tables=('workbooks','claims','operations','model_policy','model_policy_changes','author_capacity_policy','author_reservations')
        retained={name:[tuple(r)for r in db.execute('SELECT * FROM '+name+' ORDER BY 1,2')]for name in tables}
        quarantines=authority.quarantines(db);caps=authority.host_capacities(db)
        require(len(quarantines)==expected_quarantine_revision and len(caps)==expected_host_revision,'scope policy revision changed')
        require(not any(q['operation_id']==operation_id or q['workbook']==workbook or q['host']==host for q in quarantines)and not any(r['host']==host for r in caps),'host already has isolation authority; explicit composition required')
        now=authority.clock();require(now<expires_at<=now+86400,'scope expiry must be bounded to 24 hours')
        op=db.execute("SELECT * FROM operations WHERE kind='model' AND operation_id=?",(operation_id,)).fetchone();claim=authority.row(db,workbook)
        require(op is not None and op['state']=='hold'and digest(authority.public(op))==operation_sha256 and claim['state']=='hold'and all(op[k]==args[k]for k in('workbook','owner','fence'))and all(claim[k]==args[k]for k in('workbook','owner','fence','source_sha256','version')),'exact unknown operation and workbook required')
        physical=[o for o in db.execute("SELECT * FROM operations WHERE kind='model'AND state IN ('active','hold')")if authority.host(o['owner'])==host]
        require(1<=len(physical)<=2 and len([o for o in physical if o['state']=='hold'])==1,'unknown plus active physical occupancy exceeds two or another unknown exists')
        require(not any(o['workbook']==workbook and o['operation_id']!=operation_id for o in physical),'unknown workbook has another occupied model')
        for o in physical:
            if o['state']=='active':
                w=authority.row(db,o['workbook']);require(w['state']=='active'and all(w[k]==o[k]for k in('owner','fence','token')),'healthy physical operation lost its actual claim')
        qargs={k:args[k]for k in('operation_id','workbook','owner','fence','source_sha256','version','host','operation_sha256','authorization','evidence','expires_at')};qargs.update(change_id=change_id+'-quarantine',expected_revision=expected_quarantine_revision)
        q={k:v for k,v in qargs.items()if k!='expected_revision'};q.update(schema_version='fleet-model-quarantine/v1',revision=expected_quarantine_revision+1,created_at=now,state='quarantined_unknown',provider_completion='unknown',consumed_operation_retained=True,workbook_blocked=True,host_blocked=True)
        hargs=dict(host=host,quarantine_change_id=q['change_id'],quarantine_sha256=digest(q),physical_model_limit=2,authorization=authorization,evidence=evidence,change_id=change_id+'-host-capacity',expected_revision=expected_host_revision,expires_at=expires_at)
        h={k:v for k,v in hargs.items()if k!='expected_revision'};h.update(schema_version='fleet-quarantine-host-capacity/v1',revision=expected_host_revision+1,created_at=now,uncertain_operation_retained=True,workbook_blocked=True)
        db.execute('INSERT INTO model_quarantines VALUES(?,?,?,?)',(q['change_id'],digest(qargs),q['revision'],canonical(q)))
        db.execute('INSERT INTO quarantine_host_capacity VALUES(?,?,?,?)',(h['change_id'],digest(hargs),h['revision'],canonical(h)))
        record=dict(schema_version=SCHEMA,quarantine=q,host_capacity=h,physical_model_limit=2,unknown_slots_consumed=1,existing_physical_operation_ids=sorted(o['operation_id']for o in physical),original_work_sha256={k:digest(v)for k,v in retained.items()},claims_changed=0,operations_changed=0,provider_calls=0)
        db.execute('INSERT INTO model_quarantine_scope_changes VALUES(?,?,?)',(change_id,identity,canonical(record)));authority.event(db,'isolate_unknown_model_with_active_host',record)
        require({name:[tuple(r)for r in db.execute('SELECT * FROM '+name+' ORDER BY 1,2')]for name in tables}==retained,'scope policy changed original work')
        require(authority.quarantines(db)==quarantines+[q]and authority.host_capacities(db)==caps+[h],'scope changed prior quarantine policy')
        return record
