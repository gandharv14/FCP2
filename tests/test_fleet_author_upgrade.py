import copy,json
from pathlib import Path
import pytest
from scripts.fleet import coordinator_upgrade as U,maintenance_pause as M


def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return U.binding(p)


def state(tmp_path):
    helper=U.binding(Path(M.__file__).resolve());claim=dict(workbook='0279',state='active',owner='hhu-server-7:slot-0:run',
        fence=6,claim_id='claim-6',source_sha256='a'*64,version='b'*64,expires=1200)
    s=dict(schema_version='fleet-coordinator/v1',publication_limit=1,operations=[],workbooks=[claim])
    rows=[]
    for host in ['hhu-server']+['hhu-server-'+str(i)for i in range(2,9)]:
        claims=[claim]if host=='hhu-server-7'else[]
        plan=save(tmp_path/(host+'-plan.json'),dict(schema_version='fleet-admission-drain-census/v1',helper=helper,host=host,active_claims=claims))
        rows.append(save(tmp_path/(host+'.json'),dict(schema_version='fleet-admission-drain-census/v1',status='ADMISSIONS_DRAINED',
            helper=helper,plan=plan,host=host,at=1000,model_calls=0,processes_signalled=0,
            workers=[dict(workbook=claim['workbook'],owner=claim['owner'])]if claims else[])))
    return s,rows,helper


def test_actual_author_heartbeat_can_advance_without_changing_selected_identity(tmp_path):
    s,rows,helper=state(tmp_path);s['workbooks'][0]['expires']+=20
    assert U.author_state(s,rows,helper,1010)
    assert s['workbooks'][0]['fence']==6


def test_quiescent_census_requires_no_fictional_frozen_worker(tmp_path):
    s,rows,helper=state(tmp_path);s['workbooks']=[]
    for row in rows:
        value=U.checked(row);plan=U.checked(value['plan']);plan['active_claims']=[]
        value['plan']=save(Path(value['plan']['path']),plan);value['workers']=[]
        row.update(save(Path(row['path']),value))
    assert U.author_state(s,rows,helper,1010)
    s['operations']=[dict(kind='publication',state='active')]
    with pytest.raises(ValueError,match='active model/publication'):U.author_state(s,rows,helper,1010)


@pytest.mark.parametrize('kind',['active_model','active_publication','missing_host','expired','foreign_owner','foreign_fence','new_claim','selected'])
def test_uncertain_or_uncovered_mutator_blocks_before_stop(tmp_path,kind):
    s,rows,h=state(tmp_path)
    if kind.startswith('active_'):s['operations']=[dict(state='active',kind=kind.removeprefix('active_'))]
    if kind=='missing_host':rows.pop()
    if kind=='expired':return check(s,rows,h,1061)
    if kind=='foreign_owner':s['workbooks'][0]['owner']='hhu-server-7:slot-1:other'
    if kind=='foreign_fence':s['workbooks'][0]['fence']+=1
    if kind=='new_claim':s['workbooks'].append(dict(s['workbooks'][0],workbook='0001'))
    if kind=='selected':s['author_capacity_policy']={}
    check(s,rows,h,1010)


def check(s,rows,h,now):
    with pytest.raises(ValueError):U.author_state(s,rows,h,now)


def test_readonly_admission_census_requires_pool_drain_and_exact_native_claim(tmp_path,monkeypatch):
    root=tmp_path/'pool';run=tmp_path/'worker';run.mkdir()
    claim=dict(workbook='0279',state='active',owner='hhu-server-7:slot-0:run',fence=6,source_sha256='a'*64,version='b'*64)
    save(run/'claim.json',claim)
    job=save(tmp_path/'job.json',dict(workbook='0279',source=dict(sha256='a'*64),version='b'*64,run=str(run)))
    worker=dict(pid=101,ppid=1,start_ticks='11',state='S',fleet={},command=['python','/selected/worker.py','--job',job['path'],'--job-sha256',job['sha256'],'--owner',claim['owner']])
    entry=tmp_path/'supervisor.py';entry.write_text('# exact frozen supervisor fixture\n')
    config=save(tmp_path/'config.json',dict(host='hhu-server-7'))
    command=['python',str(entry),'slot','--run',str(root),'--config',config['path'],'--config-sha256',config['sha256'],'--generation','selected','--slot','0']
    pool=dict(pid=100,ppid=1,start_ticks='10',state='S',fleet={},command=command)
    generation=dict(generation='selected',host='hhu-server-7',config=config,supervisor=U.binding(entry))
    save(root/'generations/selected/generation.json',generation)
    save(root/'current.json',dict(generation,slots=[dict(slot=0,command=command,identity=dict(pid=100,start='10'))]))
    monkeypatch.setattr(M,'inventory',lambda:{100:pool,101:worker})
    p=save(tmp_path/'census.json',dict(schema_version='fleet-admission-drain-census/v1',helper=M.binding(Path(M.__file__).resolve()),host='hhu-server-7',active_claims=[claim]))
    with pytest.raises(FileNotFoundError):M.admission_census(p)
    save(root/'stop.json',dict(force=False,requested_at=1000))
    v=M.admission_census(p);assert v['status']=='ADMISSIONS_DRAINED'and v['processes_signalled']==0 and len(v['workers'])==1
    save(run/'old.json',claim);(run/'claim.json').write_text(json.dumps(dict(claim,fence=5)))
    with pytest.raises(ValueError,match='fenced'):M.admission_census(p)


def test_orphan_owner_process_is_not_implicitly_safe(tmp_path,monkeypatch):
    p=save(tmp_path/'census.json',dict(schema_version='fleet-admission-drain-census/v1',helper=M.binding(Path(M.__file__).resolve()),host='hhu-server-7',active_claims=[]))
    monkeypatch.setattr(M,'inventory',lambda:{1:dict(pid=1,ppid=0,start_ticks='1',state='S',fleet={'HARBOR_FLEET_OWNER':'old'},command=['python','other.py'])})
    with pytest.raises(ValueError,match='orphan'):M.admission_census(p)


def test_existing_author_policy_upgrade_requires_exact_policy_and_zero_http(tmp_path):
    s,rows,helper=state(tmp_path);policy=dict(schema_version='fleet-author-capacity/v1',authors_per_host=2,physical_models_per_host=2)
    s['author_capacity_policy']=policy
    assert U.author_state(s,rows,helper,1010,existing_policy=policy)
    with pytest.raises(ValueError):U.author_state(s,rows,helper,1010)
    with pytest.raises(ValueError):U.author_state(s,rows,helper,1010,existing_policy=dict(policy,authors_per_host=3))
    for kind in ('model','publication'):
        s['operations']=[dict(kind=kind,state='active')]
        with pytest.raises(ValueError,match='active model/publication'):U.author_state(s,rows,helper,1010,existing_policy=policy)


def test_extended_ledger_binds_original_views_and_capacity_history(tmp_path):
    from scripts.fleet.coordinator import Coordinator
    from scripts.fleet import quarantine_renewal as Q
    from tests.test_physical_capacity_extension import fixture
    c,_,_,args=fixture(tmp_path);before=U.policy_ledger(c.database,extended=True)
    assert {'author_capacity_policy','author_reservations','physical_capacity_extensions','model_quarantine_originals','model_quarantine_renewals'}<=set(before)
    c.permit_physical_capacity_extension(**args);after=U.policy_ledger(c.database,extended=True)
    assert after['physical_capacity_extensions']['count']==before['physical_capacity_extensions']['count']+1
    assert after['events']['count']==before['events']['count']+1
    for k in before:
        if k not in ('events','physical_capacity_extensions'):assert after[k]==before[k]
    assert U.policy_ledger(c.database,prefix=before,extended=True)==before
