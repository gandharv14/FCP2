from pathlib import Path
import hashlib
import pytest
from scripts.fleet import takeover_control as T


@pytest.mark.parametrize('fault',[None,'owner','fence','host','source','version','launch','config','worker','guard','phase','controls','code','inventory','extra','symlink','historical','parent-owner'])
def test_retained_task_requires_actual_launch_and_both_code_identities(tmp_path,fault):
    root=tmp_path/'fleet-version';root.mkdir();fleet=root/'fleet.py';fleet.write_text('fleet runtime')
    task=tmp_path/'historical-task';instruction=task/'scripts/harbor_instruction_campaign.py';instruction.parent.mkdir(parents=True);instruction.write_text('original task')
    code=dict(path=str(instruction),sha256=hashlib.sha256(instruction.read_bytes()).hexdigest())
    source=dict(path='/source/0061.xlsx',sha256='s'*64);parent_row=dict(path='/selected/parent.json',sha256='p'*64)
    tracker=dict(path='/task/trackers/workbooks/0061.json',sha256='t'*64)
    p=dict(workbook='0061',source_sha256=source['sha256'],version=root.name,host='hhu-server-5',expected_owner='old',expected_fence=4,retained_fleet_context='edge')
    snapshot=dict(root=str(task),files=[dict(relative='scripts/harbor_instruction_campaign.py',sha256=code['sha256'])])
    original=tmp_path/'original-config-code';original.mkdir();original_file=original/'original.py';original_file.write_text('original immutable configuration code')
    original_snapshot=dict(root=str(original),files=[dict(relative='original.py',sha256=hashlib.sha256(original_file.read_bytes()).hexdigest())])
    config=dict(code='original-snapshot',controls={'env':None})
    profile=dict(original_snapshot='snapshot',selection=dict(worker_kind='instruction',config='config',tracker=tracker,guard='guard'))
    records=dict(snapshot=snapshot,config=config,**{'original-snapshot':original_snapshot},
        edge=dict(schema_version='fleet-launched-retained-task-context/v1',launch='launch',worker_outcome='outer',failed_outcome=dict(path='/operations/001/outcome.json',sha256='f')),
        launch=dict(automatic_retry=False,intent='launch-intent',remote=dict(automatic_retry=False,root_intent='launch-intent',job='job')),
        **{'launch-intent':dict(plan='plan',remote=dict(job='job'))},
        plan=dict(root_plan=parent_row,job='job',host=p['host'],new_owner='old',expected_fence=3),
        job=dict(workbook='0061',source=source,version=root.name,driver='parent-helper',command=['python','--plan',parent_row['path'],'--sha256',parent_row['sha256']]),
        parent=dict(schema_version='fleet-dialogue-source-revision-adoption/v1',workbook='0061',source=source,helper='parent-helper',owner='old',fence=4,operations='/operations',retained_context=dict(config='config',fleet_runtime='runtime')),
        outer=dict(intent='worker-intent',status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,automatic_retry=False),
        **{'worker-intent':dict(job='job',owner='old')},
        failed=dict(status='COMPLETED_OPERATION',resource='guard',result='result'),
        guard=dict(returncode=0,failure=None,intent='intent'),
        intent=dict(action='step',models=False,arguments=dict(checkpoint='prior',descriptor='descriptor'),controls=config['controls'],code=code),
        result=dict(status='OWNING_GATE_FAILED',descriptor='descriptor',next_step='dialogue-commit'),
        prior=dict(status='STEP_COMPLETE',descriptor='descriptor',next_step='dialogue-commit'),
        descriptor=dict(worker=dict(root=str(task),context=dict(tracker_root='/task/trackers/workbooks')),controls=config['controls'],code=code),
        runtime=dict(root=str(root),files={'fleet.py':dict(path=str(fleet),sha256=hashlib.sha256(fleet.read_bytes()).hexdigest())}))
    def read(row):
        if row==parent_row:return records['parent']
        if isinstance(row,dict)and row.get('path')=='/operations/001/outcome.json':return records['failed']
        return records[row]
    if fault=='owner':records['worker-intent']['owner']='different'
    elif fault=='fence':records['plan']['expected_fence']=1
    elif fault=='host':records['plan']['host']='hhu-server-7'
    elif fault=='source':records['job']['source']=dict(source,sha256='z'*64)
    elif fault=='version':records['job']['version']='other'
    elif fault=='launch':records['launch']['remote']['job']='foreign';records['foreign']=dict(records['job'])
    elif fault=='config':records['parent']['retained_context']['config']='other'
    elif fault=='worker':records['descriptor']['worker']['root']='/other-task'
    elif fault=='guard':records['guard']['returncode']=1
    elif fault=='phase':records['result']['next_step']='enqueue'
    elif fault=='controls':records['intent']['controls']={'env':'foreign'}
    elif fault=='code':instruction.write_text('changed')
    elif fault=='inventory':fleet.write_text('changed')
    elif fault=='extra':(root/'extra.py').write_text('unlisted')
    elif fault=='symlink':(root/'link').symlink_to(fleet)
    elif fault=='historical':original_file.write_text('changed')
    elif fault=='parent-owner':records['parent']['owner']='foreign'
    before=dict(workbook_id='0061',bindings=dict(original_raw_source=source))
    if fault:
        with pytest.raises(ValueError):T.native_identity(p,profile,before,read)
    else:
        T.native_identity(p,profile,before,read)
        assert config['code']=='original-snapshot'and snapshot['root']==str(task)
