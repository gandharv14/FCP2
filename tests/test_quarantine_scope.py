from pathlib import Path
import copy,json
import pytest
from scripts.fleet import quarantine_scope as Q,quarantine_renewal as R
from scripts.fleet.coordinator import Coordinator,Conflict,digest
from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256,CHANGE_ID,validate_quarantines,validate_host_capacities,model_admission_barriers


def setup(tmp_path):
    now=[1000.];c=Coordinator(tmp_path/'db',clock=lambda:now[0]);auth=dict(path='/auth.json',sha256=AUTHORIZATION_SHA256);c.set_model_limit(None,auth,CHANGE_ID,0);claims={}
    for wb,host in [('0135','hhu-server-2'),('0007','hhu-server-2'),('0010','hhu-server-3'),('0020','hhu-server-2')]:
        c.enroll(wb,'a'*64,'source');c.reconcile(wb,'ready','ready');v=c.claim(wb,host+':slot-0:'+wb,wb,'b'*64,3600);claims[wb]={k:v[k]for k in('workbook','owner','token','fence')}
    assert c.begin('model','healthy',request_sha256='c'*64,**claims['0007'])['execute'];assert c.begin('model','unknown',request_sha256='c'*64,**claims['0135'])['execute'];c.end('model','unknown',evidence='RemoteDisconnected',outcome='uncertain',**claims['0135'])
    op=next(o for o in c.status()['operations']if o['operation_id']=='unknown')
    args=dict(operation_id='unknown',operation_sha256=digest(op),workbook='0135',owner=claims['0135']['owner'],fence=1,source_sha256='a'*64,version='b'*64,host='hhu-server-2',authorization=auth,evidence=dict(path='/sealed-disconnect.json',sha256='d'*64),change_id='scope-001',expected_quarantine_revision=0,expected_host_revision=0,expires_at=2000)
    return c,now,claims,args


def test_atomic_scope_preserves_active_claim_and_counts_unknown_slot(tmp_path):
    c,now,claims,args=setup(tmp_path);before=c.status();r=Q.isolate(c,**args);after=c.status()
    assert before['workbooks']==after['workbooks']and before['operations']==after['operations']
    assert validate_quarantines(after)==[r['quarantine']]and validate_host_capacities(after)==[r['host_capacity']]
    assert not model_admission_barriers(after,'0010',claims['0010']['owner'])
    assert c.begin('model','otherhost',request_sha256='f'*64,**claims['0010'])['execute']
    assert not c.begin('model','overcapacity',request_sha256='f'*64,**claims['0020'])['execute']
    c.end('model','healthy',evidence='real preserved response',outcome='confirmed',**claims['0007'])
    assert c.begin('model','nextsamehost',request_sha256='f'*64,**claims['0007'])['execute']
    with pytest.raises(Conflict):c.begin('model','unknown',request_sha256='c'*64,**claims['0135'])
    assert Q.isolate(c,**args)==r


@pytest.mark.parametrize('change',[{'operation_sha256':'e'*64},{'owner':'hhu-server-2:foreign'},{'fence':2},{'source_sha256':'e'*64},{'version':'foreign'},{'expires_at':float('nan')},{'expires_at':100000},{'expected_quarantine_revision':1},{'expected_host_revision':1}])
def test_changed_or_unbounded_scope_leaves_both_policies_absent(tmp_path,change):
    c,now,claims,args=setup(tmp_path);before=c.status()
    with pytest.raises((ValueError,Conflict)):Q.isolate(c,**dict(args,**change))
    assert c.status()==before
    with c.connect()as db:assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='model_quarantine_scope_changes'").fetchone()


def test_failure_between_inserts_rolls_back_both_records(tmp_path,monkeypatch):
    c,now,claims,args=setup(tmp_path);before=c.status();monkeypatch.setattr(c,'event',lambda *args:(_ for _ in ()).throw(RuntimeError('failure')))
    with pytest.raises(RuntimeError):Q.isolate(c,**args)
    assert c.status()==before


def test_retained_other_quarantine_renewal_view_remains_identical(tmp_path):
    c,now,claims,args=setup(tmp_path)
    # Original VM1 isolation is independent of the pending VM2 scope.
    c.enroll('0413','e'*64,'source');c.reconcile('0413','ready','ready');v=c.claim('0413','hhu-server:slot-0:0413','0413','f'*64,3600)
    fields={k:v[k]for k in('workbook','owner','token','fence')}
    # Inject an authentic second unknown through original API after temporarily
    # isolating VM2 is intentionally avoided: retain real original SQLite shape.
    with c.transaction()as db:
        unknown=dict(db.execute("SELECT * FROM operations WHERE operation_id='unknown'").fetchone());unknown.update(operation_id='original-0413',workbook='0413',owner=v['owner'],token=v['token'],fence=v['fence'],identity='e'*64)
        cols=list(unknown);db.execute('INSERT INTO operations('+','.join(cols)+') VALUES('+','.join('?'for _ in cols)+')',[unknown[k]for k in cols]);db.execute("UPDATE workbooks SET state='hold' WHERE workbook='0413'")
    op=next(o for o in c.status()['operations']if o['operation_id']=='original-0413')
    q=c.quarantine_model(operation_id=op['operation_id'],workbook='0413',owner=v['owner'],fence=v['fence'],source_sha256='e'*64,version='f'*64,host='hhu-server',operation_sha256=digest(op),authorization=args['authorization'],evidence=args['evidence'],change_id='original-0413',expected_revision=0,expires_at=2000)
    renewed=R.renew(c,original_change_id=q['change_id'],previous_sha256=digest(q),expected_revision=0,expires_at=3000,authorization=args['authorization'],evidence=args['evidence'],change_id='renew-0413')
    result=Q.isolate(c,**dict(args,expected_quarantine_revision=1));assert c.status()['quarantines']==[renewed['effective'],result['quarantine']]
    with c.connect()as db:assert R.history(db)[1]==[renewed]

def test_generic_control_full_database_copy_preserves_real_original_state(tmp_path):
    from types import SimpleNamespace
    from scripts.fleet import quarantine_renewal_control as K
    c,now,claims,args=setup(tmp_path);before=c.status();p=dict(scope_operation=Q.SCHEMA,arguments=args)
    original=SimpleNamespace(Coordinator=lambda path:Coordinator(path,clock=lambda:now[0]))
    proof=K.copied_proof((p,dict(database=c.database),original,Q,before),tmp_path/'proof')
    assert proof['status']=='FULL_DATABASE_COPY_SCOPE_PASS'and c.status()==before
