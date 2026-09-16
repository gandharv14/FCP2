"""Once-only expired native custody replacement while central admission stays held.

The database admission mutex excludes local dispatchers; a central hold excludes
remote pools. The original takeover implementation owns expiry, stopped locks,
allowance consumption and tracker CAS. A missing response always needs recovery
from that implementation's journal, never a second invocation.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, shlex, subprocess, sys
if __package__ in (None, ''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding, checked
from scripts.fleet.pool import lock, persist
from scripts.fleet.admission_lock import path as admission_path
from scripts.fleet.coordinator import Client
from scripts.fleet.prepare_queue import HOSTS

FIELDS={'schema_version','helper','coordinator_receipt','runtime','profile','host','python',
        'workbook','expected_owner','expected_fence','source_sha256','version','native_directory'}

def require(ok,message):
    if not ok:raise ValueError(message)

def gate(status,p):
    rows=[w for w in status['workbooks']if w['workbook']==p['workbook']]
    require(len(rows)==1,'unique workbook required');w=rows[0]
    require(w['state']=='hold'and w['owner']==p['expected_owner']and w['fence']==p['expected_fence']
        and w['source_sha256']==p['source_sha256']and w['version']==p['version'],'held custody changed')
    require(not any(o['workbook']==p['workbook']and o['state']in ('active','hold')for o in status['operations']),
        'target operation active or uncertain; reconcile first')
    return w

def load(row):
    p=checked(row)
    require(set(p)-{'selected_snapshot','selected_environment','completed_checkpoint','retained_fleet_context'}==FIELDS and p['schema_version']=='fleet-held-native-takeover/v1'
        and p['helper']==binding(__file__),'closed selected controller required')
    require(p['host']in HOSTS and Path(p['python']).is_absolute(),'authorized host and native Python required')
    require(Path(p['native_directory']).is_absolute()and '..'not in Path(p['native_directory']).parts,'canonical new native journal required')
    checked(p['profile']);require(binding(p['runtime']['path'])==p['runtime'],'runtime changed')
    if 'retained_fleet_context'in p:
        require(not({'selected_snapshot','selected_environment','completed_checkpoint'}&set(p)),
            'retained fleet context cannot replace task successor ancestry')
        checked(p['retained_fleet_context'])
    if 'selected_snapshot'in p:checked(p['selected_snapshot'])
    if 'completed_checkpoint'in p:
        require('selected_snapshot'in p and 'selected_environment'not in p,'completed checkpoint requires instruction successor custody')
        completed=p['completed_checkpoint']
        require(set(completed)=={'path','sha256'} and Path(completed['path']).is_absolute()
            and '..'not in Path(completed['path']).parts
            and len(completed['sha256'])==64 and all(c in '0123456789abcdef'for c in completed['sha256']),
            'exact native completed checkpoint binding required')
    if 'selected_environment'in p:
        require('selected_snapshot'in p,'environment continuation requires selected snapshot')
        checked(p['selected_environment'])
    return p

def native_identity(p,profile,before,read):
    require(before['workbook_id']==p['workbook']
        and before['bindings']['original_raw_source']['sha256']==p['source_sha256'],
        'native source differs from central custody')
    snapshot=read(profile['original_snapshot']);config=read(profile['selection']['config'])
    if 'retained_fleet_context'in p:
        retained_fleet_identity(p,profile,config,snapshot,read);return
    require(read(config['code'])==snapshot,
        'native configured version differs from central custody')
    if 'selected_snapshot'not in p:
        require(Path(snapshot['root']).name==p['version'],'native configured version differs from central custody')
        return
    import hashlib
    selected=read(p['selected_snapshot']);root=Path(selected['root'])
    require(root.is_absolute()and root.is_dir()and not any(x.is_symlink()for x in(root,*root.parents))
        and root.name==p['version'],'selected code differs from current custody')
    if 'selected_environment'in p:
        require(profile['selection']['worker_kind']=='environment','environment code ancestry requires environment custody')
        environment_identity(p,profile,config,read)
    else:
        require(profile['selection']['worker_kind']=='instruction','selected snapshot requires an actual instruction checkpoint')
        checkpoint_row=profile['selection']['checkpoint'];checkpoint=read(checkpoint_row)
        if checkpoint_row==profile['selection'].get('prior_completion'):
            require('completed_checkpoint'not in p,'takeover ancestry cannot replace completed rejection ancestry')
            seen=[]
            while checkpoint.get('schema_version')=='harbor-expired-lease-takeover/v1':
                require(checkpoint_row not in seen and len(seen)<64
                    and set(checkpoint)=={'schema_version','stopped_owner_proof','after','lease'},
                    'cyclic or changed completed takeover ancestry')
                seen.append(checkpoint_row)
                prior=read(checkpoint['stopped_owner_proof'])['selection']
                require(prior['config']==profile['selection']['config']
                    and prior['guard']==profile['selection']['guard']
                    and prior['workbook']==p['workbook'] and prior['worker_kind']=='instruction',
                    'completed takeover lost original configured instruction guard')
                checkpoint_row=prior['checkpoint'];checkpoint=read(checkpoint_row)
                require(checkpoint.get('schema_version')!='harbor-expired-lease-takeover/v1'
                    or checkpoint_row==prior['prior_completion'],'unlisted completed takeover checkpoint')
        descriptor=read(checkpoint['descriptor'])
        guard=read(profile['selection']['guard']);intent=read(guard['intent'])
        predecessor=checkpoint_row
        if 'completed_checkpoint'in p:
            completed=read(p['completed_checkpoint'])
            require(completed['status']=='COMPLETED_OPERATION' and completed.get('automatic_retry',False)is False
                and completed['resource']==profile['selection']['guard']
                and completed['result']==profile['selection']['checkpoint']
                and guard['returncode']==0 and guard['failure']is None
                and checkpoint['status']=='DIALOGUE_REPAIR_REQUIRED'
                and checkpoint['next_step']=='dialogue-review' and checkpoint['publication_performed']is False,
                'completed rejection must retain its successful original guard')
            predecessor=checkpoint['evidence'][0]
            require(predecessor!=profile['selection']['checkpoint'],'completed checkpoint cannot precede itself')
        require(descriptor['worker']['root']==str(root)
            and intent['action']=='step'and intent['arguments']==dict(descriptor=checkpoint['descriptor'],checkpoint=predecessor)
            and intent['code']==descriptor['code'],'selected code detached from the actual stopped instruction')
    expected={row['relative']:row['sha256']for row in selected['files']}
    require(len(expected)==len(selected['files'])and bool(expected),'duplicate or empty selected code inventory')
    actual={}
    for path in root.rglob('*'):
        require(not path.is_symlink(),'selected code contains a symlink')
        if path.is_file()and '__pycache__'not in path.relative_to(root).parts:
            actual[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    require(actual==expected,'selected full code inventory changed')

def retained_fleet_identity(p,profile,config,snapshot,read):
    """Bind original task code to the fleet-only runtime of its actual stopped launch."""
    import hashlib
    edge=read(p['retained_fleet_context'])
    require(set(edge)=={'schema_version','launch','worker_outcome','failed_outcome'}
        and edge['schema_version']=='fleet-launched-retained-task-context/v1'
        and profile['selection']['worker_kind']=='instruction', 'closed retained instruction fleet context required')
    launch=read(edge['launch']);launch_intent=read(launch['intent']);plan=read(launch_intent['plan'])
    job=read(launch['remote']['job']);parent=read(plan['root_plan'])
    outer=read(edge['worker_outcome']);worker_intent=read(outer['intent'])
    require(launch['automatic_retry']is False and launch['remote']['automatic_retry']is False
        and launch['remote']['root_intent']==launch['intent']
        and launch['remote']['job']==launch_intent['remote']['job']==plan['job']==worker_intent['job']
        and plan['host']==p['host']and plan['new_owner']==p['expected_owner']==worker_intent['owner']
        and plan['expected_fence']+1==p['expected_fence']
        and parent['owner']==p['expected_owner']and parent['fence']==p['expected_fence']
        and job['workbook']==parent['workbook']==p['workbook']
        and job['source']==parent['source']and job['source']['sha256']==p['source_sha256']
        and job['version']==p['version']and job['driver']==parent['helper']
        and job['command'][job['command'].index('--plan')+1]==plan['root_plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==plan['root_plan']['sha256']
        and outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1
        and outer['failure_type']is None and outer['automatic_retry']is False,
        'retained task detached from actual stopped fleet claim')
    context=parent['retained_context'];require(context['config']==profile['selection']['config']
        and parent['schema_version']=='fleet-dialogue-source-revision-adoption/v1',
        'retained task configuration changed')
    # Retained review preparation may already have selected a later task
    # snapshot while preserving the original configuration bytes. Authenticate
    # both inventories; the actual stopped descriptor selects the executing one.
    for code_snapshot in (read(config['code']),snapshot):
        code_root=Path(code_snapshot['root']);expected_code={x['relative']:x['sha256']for x in code_snapshot['files']}
        require(code_root.is_absolute()and code_root.is_dir()and bool(expected_code)
            and len(expected_code)==len(code_snapshot['files'])and not any(x.is_symlink()for x in(code_root,*code_root.parents)),
            'retained historical task snapshot is not canonical')
        observed_code={}
        for file in code_root.rglob('*'):
            require(not file.is_symlink(),'retained task code contains symlink')
            if file.is_file()and '__pycache__'not in file.relative_to(code_root).parts:
                observed_code[str(file.relative_to(code_root))]=hashlib.sha256(file.read_bytes()).hexdigest()
        require(observed_code==expected_code,'retained historical or executing task snapshot changed')
    failed=read(edge['failed_outcome']);guard=read(failed['resource']);intent=read(guard['intent'])
    result=read(failed['result']);descriptor=read(result['descriptor']);prior=read(intent['arguments']['checkpoint'])
    require(Path(edge['failed_outcome']['path']).parent.parent==Path(parent['operations'])
        and failed['resource']==profile['selection']['guard']
        and failed['status']=='COMPLETED_OPERATION'and guard['returncode']==0 and guard['failure']is None
        and result['status']=='OWNING_GATE_FAILED'and prior['status']=='STEP_COMPLETE'
        and prior['next_step']==result['next_step']=='dialogue-commit'
        and intent['action']=='step'and intent['models']is False
        and intent['arguments']['descriptor']==result['descriptor']==prior['descriptor']
        and descriptor['worker']['root']==snapshot['root']
        and descriptor['worker']['context']['tracker_root']==str(Path(profile['selection']['tracker']['path']).parent)
        and descriptor['controls']==intent['controls']==config['controls']
        and descriptor['code']==intent['code']
        and intent['code']['path']==str(Path(snapshot['root'])/'scripts/harbor_instruction_campaign.py')
        and intent['code']['sha256']==hashlib.sha256(Path(intent['code']['path']).read_bytes()).hexdigest(),
        'retained task lost exact original failed instruction and code')
    runtime=read(context['fleet_runtime']);root=Path(runtime['root'])
    require(root.is_absolute()and root.is_dir()and root.name==p['version']
        and not any(x.is_symlink()for x in(root,*root.parents))and str(root)!=snapshot['root'],
        'retained fleet runtime differs from actual claim version')
    expected={}
    for relative,row in runtime['files'].items():
        require(Path(row['path'])==root/relative and Path(row['path']).is_relative_to(root),
            'retained fleet inventory escaped its selected root')
        expected[relative]=row['sha256']
    actual={}
    for path in root.rglob('*'):
        require(not path.is_symlink(),'retained fleet runtime contains symlink')
        if path.is_file()and '__pycache__'not in path.relative_to(root).parts:
            actual[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    require(bool(expected)and actual==expected,'retained fleet runtime inventory changed')


def environment_identity(p,profile,config,read):
    """Bind a stopped environment to its actual already-launched code successor."""
    edge=read(p['selected_environment'])
    require(set(edge)=={'schema_version','launch'}and edge['schema_version']=='fleet-launched-environment-code/v1',
        'closed launched environment ancestry required')
    launch=read(edge['launch']);intent=read(launch['intent']);plan=read(intent['plan'])
    job=read(launch['remote']['job']);continuation=read(plan['root_plan'])
    successor=read(continuation['terminal_rejection']['successor'])
    require(launch['automatic_retry']is False and launch['remote']['root_intent']==launch['intent']
        and launch['remote']['job']==intent['remote']['job']==plan['job']
        and plan['host']==p['host']and plan['new_owner']==p['expected_owner']
        and plan['expected_fence']+1==p['expected_fence']
        and job['workbook']==continuation['workbook']==p['workbook']
        and job['source']['sha256']==p['source_sha256']and job['version']==p['version'],
        'environment code detached from current stopped claim')
    require(successor['schema_version']=='normalization-terminal-code-successor/v1'
        and successor['original_snapshot']==profile['original_snapshot']
        and successor['original_config']==profile['selection']['config']
        and successor['snapshot']==p['selected_snapshot']
        and continuation['code_root']==read(p['selected_snapshot'])['root']
        and read(successor['config'])==dict(config,code=p['selected_snapshot']),
        'environment successor changed historical configuration or selected code')

def native_directory(p,selection,takeover):
    directory=Path(p['native_directory'])
    require(directory.parent==takeover._layout(selection)[1]/'lease-takeovers',
        'dedicated root takeover directory required')
    require(not directory.exists(),'native takeover attempted; reconcile original journal')
    return directory

def native(payload):
    # This same hash-bound module runs on Linux in a fresh interpreter. Its
    # imports are deliberately avoided by extracting only this function below.
    from pathlib import Path
    import hashlib, importlib.util, json, os, sys, time, inspect
    def need(ok,message):
        if not ok:raise ValueError(message)
    p=payload['plan'];r=p['runtime']
    need(hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256'],'runtime bytes changed')
    spec=importlib.util.spec_from_file_location('selected_takeover_runtime',r['path']);A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
    os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
    profile,T,s,deps=A.load(p['profile'])
    need(s.workbook==p['workbook']and s.owner!=p['expected_owner'],'takeover selection detached')
    before,old,run,evidence=T._recipe(s);T._prior_chain(s,before)
    native_identity(p,profile,before,T.read)
    need(old['expires_at']<=time.time_ns(),'native lease is still live')
    with T._stopped_locks(s):need(T.record(s.tracker['path'])==s.tracker,'native tracker changed')
    directory=native_directory(p,s,T)
    if not payload['execute']:
        return dict(status='VALIDATED_NOT_STARTED',workbook=s.workbook,tracker=s.tracker,models=0,lease_mutations=0)
    lease,completion=T.replace_expired(s,directory)
    previous,current,verified=T.validate_completion(completion)
    need(verified==lease,'original completion differs')
    return dict(status='NATIVE_CUSTODY_REPLACED',completion=completion,tracker=T.record(s.tracker['path']),
        replacement_count=T.life.validate_lifecycle(current),models=0,semantic_budget_reset=False,automatic_retry=False)

def transport(p,execute,directory):
    import inspect
    program='from pathlib import Path\n'+inspect.getsource(require)+'\n'+inspect.getsource(retained_fleet_identity)+'\n'+inspect.getsource(environment_identity)+'\n'+inspect.getsource(native_identity)+'\n'+inspect.getsource(native_directory)+'\n'+inspect.getsource(native)+'\nimport json,sys\nprint(json.dumps(native(json.load(sys.stdin))))\n'
    command=['/Users/gandharvmahajan/google-cloud-sdk/bin/gcloud','compute','ssh',p['host'],
        '--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',
        shlex.join([p['python'],'-I','-B','-c',program])]
    try:q=subprocess.run(command,input=json.dumps(dict(plan=p,execute=execute)).encode(),capture_output=True,timeout=180)
    except subprocess.TimeoutExpired as exc:
        for name,data in [('stdout',exc.stdout or b''),('stderr',exc.stderr or b'')]:
            target=directory/(name+'.bin');target.write_bytes(data);target.chmod(0o600)
        raise
    for name,data in [('stdout',q.stdout),('stderr',q.stderr)]:
        target=directory/(name+'.bin');target.write_bytes(data);target.chmod(0o600)
    require(q.returncode==0,'native execution failed; inspect retained response and reconcile before retry')
    return json.loads(q.stdout)

def run(row,journal,*,execute=False):
    p=load(row);receipt=checked(p['coordinator_receipt'])
    client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
    with lock(admission_path(receipt),blocking=False):
        before=gate(client.call('status'),p)
        directory=Path(journal).absolute();require(not directory.exists(),'root journal exists; reconcile without replay')
        directory.mkdir(parents=True,mode=0o700)
        intent=persist(directory/'intent.json',dict(plan=row,before=before,execute=execute,automatic_retry=False),immutable=True)
        try:
            result=transport(p,execute,directory)
            require(gate(client.call('status'),p)==before,'central custody changed during native operation')
            return persist(directory/'result.json',dict(intent=intent,result=result,central_unchanged=True),immutable=True)
        except BaseException as exc:
            persist(directory/'reconciliation.json',dict(intent=intent,failure_type=type(exc).__name__,automatic_retry=False),immutable=True)
            raise

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',required=True);parser.add_argument('--plan-sha256',required=True)
    parser.add_argument('--journal',required=True);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    print(json.dumps(run(dict(path=str(Path(args.plan).absolute()),sha256=args.plan_sha256),args.journal,execute=args.execute)))
