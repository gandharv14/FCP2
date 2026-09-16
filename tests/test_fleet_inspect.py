import json
import pytest
from scripts.fleet.inspect import binding,gather_jobs


@pytest.mark.parametrize('schema',['fleet-source-pool-start/v1','fleet-source-pool-start/v2','fleet-source-pool-start/v3','fleet-source-pool-start/v4'])
def test_started_source_pool_catalog_is_bound_and_does_not_infer_live_workers(tmp_path,schema):
    pool=tmp_path/'control/source-pool-001';pool.mkdir(parents=True)
    job=pool/'worker.json';job.write_text(json.dumps({'schema_version':'harbor-fleet-worker-job/v1'}))
    catalog=pool/'selected.json';catalog.write_text(json.dumps({'assignments':{'hhu-server-2':[binding(job)]}}))
    plan=pool/'plan.json';plan.write_text(json.dumps({'schema_version':schema,'prepared':binding(catalog)}))
    assert not any(gather_jobs(tmp_path).values())
    start=pool/'start-001';start.mkdir();(start/'intent.json').write_text(json.dumps({'plan':binding(plan)}))
    assert gather_jobs(tmp_path)['hhu-server-2']==[binding(job)]
    job.write_text(job.read_text()+'\n')
    assert not any(gather_jobs(tmp_path).values())


def test_native_historical_job_host_comes_from_bound_authority(tmp_path):
    job=tmp_path/'holds-0006/migrate-job-001.json';job.parent.mkdir()
    job.write_text(json.dumps({'schema_version':'harbor-fleet-worker-job/v1','run':'/home/user/recovery/worker'}))
    plans=tmp_path/'control/first-custody-001';plans.mkdir(parents=True)
    p={'schema_version':'harbor-fleet-first-historical-custody/v1','host':'hhu-server-8','job':binding(job)}
    for name in ['0006-launch-template.json','0006-launch-001.json']:
        (plans/name).write_text(json.dumps(p))
    result=gather_jobs(tmp_path)
    assert result['hhu-server-8']==[binding(job)]
    assert all(not jobs for host,jobs in result.items() if host!='hhu-server-8')
    job.write_text(job.read_text()+'\n')
    assert not any(gather_jobs(tmp_path).values())


def test_historical_worker_path_keeps_explicit_host(tmp_path):
    job=tmp_path/'holds-0190/successor/refresh-worker-job.json';job.parent.mkdir(parents=True)
    job.write_text(json.dumps({'schema_version':'harbor-fleet-worker-job/v1','run':'/original/workers/hhu-server-8/slot-1/task'}))
    assert gather_jobs(tmp_path)['hhu-server-8']==[binding(job)]


@pytest.mark.parametrize('schema', [
    'harbor-fleet-job-launch/v1',
    'harbor-fleet-job-launch/v2',
    'harbor-fleet-job-launch/v3',
    'harbor-fleet-source-initial-launch/v1',
    'harbor-fleet-first-historical-custody/v1',
    'harbor-fleet-first-historical-custody/v2',
    'fleet-source-reconstruction-custody/v2',
])
def test_successor_outside_plans_requires_complete_bound_launch_chain(tmp_path, schema):
    control=tmp_path/'control/successor';control.mkdir(parents=True)
    job=control/'worker.json'
    job.write_text(json.dumps({'schema_version':'harbor-fleet-worker-job/v1','run':'/native/worker'}))
    plan=control/'launch-plan.json'
    plan.write_text(json.dumps({'schema_version':schema,'host':'hhu-server','job':binding(job)}))
    intent=control/'intent.json';intent.write_text(json.dumps({'plan':binding(plan)}))
    receipt=control/'launched.json'
    receipt.write_text(json.dumps({'intent':binding(intent),'remote':{'job':binding(job),'root_intent':binding(intent)}}))
    assert gather_jobs(tmp_path)['hhu-server']==[binding(job)]
    value=json.loads(receipt.read_text());value['remote']['root_intent']['sha256']='0'*64
    receipt.write_text(json.dumps(value))
    assert not any(gather_jobs(tmp_path).values())

    value['remote']['root_intent']=binding(intent);receipt.write_text(json.dumps(value))
    job.write_text(job.read_text()+'\n')
    assert not any(gather_jobs(tmp_path).values())


def test_bulk_repair_launch_is_discovered_only_after_exact_custody(tmp_path):
    root=tmp_path/'vm-fleet';root.mkdir()
    control=tmp_path/'bulk-production/task/launch';control.mkdir(parents=True)
    job=control/'worker-job.json';job.write_text(json.dumps({'schema_version':'harbor-fleet-worker-job/v1','run':'/native/run'}))
    plan=control/'plan.json';plan.write_text(json.dumps({'schema_version':'harbor-fleet-job-launch/v3','host':'hhu-server-5','job':binding(job)}))
    intent=control/'intent.json';intent.write_text(json.dumps({'plan':binding(plan)}))
    assert not any(gather_jobs(root).values())
    launch=control/'launched.json';launch.write_text(json.dumps({'intent':binding(intent),'remote':{'job':binding(job),'root_intent':binding(intent)}}))
    assert gather_jobs(root)['hhu-server-5']==[binding(job)]
    plan.write_text(plan.read_text()+'\n')
    assert not any(gather_jobs(root).values())


@pytest.mark.parametrize('fault',[None,'bytes','native-hash','launch','outside','duplicate','symlink'])
def test_native_job_custody_is_exact_and_preserves_linux_locator(tmp_path,fault):
    control=tmp_path/'control/native-selected';control.mkdir(parents=True)
    copy=control/'job-copy.json';copy.write_text(json.dumps({'schema_version':'harbor-fleet-worker-job/v1','run':'/native/run'}))
    native={'path':'/home/gmahajan_labelbox_com/synthetic-data-fleet/task/worker-job.json','sha256':binding(copy)['sha256']}
    plan=control/'plan.json';plan.write_text(json.dumps({'schema_version':'harbor-fleet-job-launch/v1','host':'hhu-server-4','job':native}))
    intent=control/'intent.json';intent.write_text(json.dumps({'plan':binding(plan)}))
    launch=control/'launched.json';launch.write_text(json.dumps({'intent':binding(intent),'remote':{'job':native,'root_intent':binding(intent)}}))
    registry=tmp_path/'control/native-job-custody';registry.mkdir()
    record={'schema_version':'fleet-inspection-native-job-custody/v1','native_job':native,'local_copy':binding(copy),'launch_record':binding(launch)}
    if fault=='bytes':copy.write_text(copy.read_text()+'\n')
    elif fault=='native-hash':record['native_job']={**native,'sha256':'0'*64}
    elif fault=='launch':record['launch_record']={**binding(launch),'sha256':'0'*64}
    elif fault=='outside':record['local_copy']={**binding(copy),'path':str(tmp_path.parent/'outside-job.json')}
    elif fault=='symlink':
        link=control/'linked-copy.json';link.symlink_to(copy);record['local_copy']={**binding(copy),'path':str(link)}
    (registry/'selected.json').write_text(json.dumps(record))
    if fault=='duplicate':(registry/'duplicate.json').write_text(json.dumps(record))
    actual=gather_jobs(tmp_path)['hhu-server-4']
    assert actual==([native]if fault is None else [])
