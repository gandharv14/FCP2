"""One explicitly authorized retained source reconstruction; no model or publication.

The original corpus is immutable. A separate clone inherits every consumed build
before execute_one appends exactly one attempt. Required source generation forbids
falling back to rebuilding the source AST.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse,copy,fcntl,hashlib,json,os,shutil,sys,time

def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)), 'regular canonical file required')
    with p.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(path=str(p),sha256=sha)
def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'bound bytes changed')
    return json.loads(Path(row['path']).read_bytes())
def keep(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
    return binding(p)
def inventory(root):
    root=Path(root);rows={}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(),'corpus symlink forbidden')
        if p.is_file() and p.name!='.lock':rows[str(p.relative_to(root))]=binding(p)
    return rows

def validate_boundary(p,ledger,policy):
    old=ledger['build'];wb=p['workbook']
    require(ledger['workbook_id']==wb and ledger['source']==p['source'],'original workbook/source changed')
    if 'macro_projection_policy'in p:
        require('interrupted'not in p and ledger['state']=='STOPPED'
                and ledger['failure']=='source_policy_unsupported'
                and set(ledger['reason_codes'])=={'macros_present'}
                and old.get('normalization',{}).get('policy_version')=='source-vba-only-projection/v1'
                and old['bindings'].get('macro_projection_policy_sha256')==p['macro_projection_policy']['sha256'],
                'exact retained macro resource boundary required')
    else:require(ledger['state']=='READY' and ledger['route'] in ('pass','restricted_pass'),'source intake not admitted')
    if 'interrupted' in p:
        require(old['state']=='RUNNING' and old['stage']=='segmentation'
                and old.get('exit_code') is None and old.get('resources') is None,
                'unfinished segmentation required; completed failures need their own diagnosis')
        interrupted_boundary(p,ledger)
    else:
        require(old['state']=='STOPPED' and old['stage']=='segmentation' and old['exit_code']==124
                and old['resources']['limit_reason'] in ('wall_time','process_group_rss'),'exact resource-only boundary required')
    require(old['attempts']==p['previous_attempt'] and p['next_attempt']==p['previous_attempt']+1
            and p['next_attempt']<=p['cumulative_reconstruction_limit'],'monotonic reconstruction allowance exceeded')
    require(len(ledger.get('build_history',[]))==p['previous_attempt']-1,'retained attempt history incomplete')
    require(old['source_checkpoint']['state']=='complete' and old['source_generation_id']==p['generation'],'completed source checkpoint required')
    require(p['seconds']<=policy['limits']['stage_seconds'] and p['memory_bytes']<=policy['limits']['memory_bytes'],'resource allowance exceeded')
    require(p['models']==0 and p['automatic_retry'] is False,'zero-model once-only continuation required')

def interrupted_boundary(p,ledger):
    """Keep a stopped transport attempt RUNNING in history; never invent a result.

    The root launcher separately proves current central and native custody. This
    reader binds that stopped worker to the exact previously reserved source-only
    attempt and checks its namespace again on every native invocation.
    """
    selected=p['interrupted']
    require(type(selected)is dict and set(selected) in (
            {'plan','outcome','process','claim'},
            {'plan','outcome','process','claim','finish_uncertain'}),
            'closed interrupted reconstruction custody required')
    previous=read(selected['plan']);outcome=read(selected['outcome']);process=read(selected['process']);claim=read(selected['claim'])
    intent=read(outcome['intent']);job=read(intent['job'])
    require(previous['schema_version']=='fleet-resource-continuation/v1'
        and previous['workbook']==p['workbook'] and previous['source']==p['source']
        and previous['generation']==p['generation'] and previous['next_attempt']==p['previous_attempt']
        and previous['models']==0 and previous['automatic_retry']is False,
        'interrupted attempt is not the exact previous source reconstruction')
    home=Path(previous['home']);run=Path(job['run']);snap=read(previous['snapshot'])
    require(p['corpus']==str(home/'corpus') and p['ledger']==binding(home/'corpus/workbooks'/p['workbook']/'ledger.json')
        and p['home']!=previous['home'] and p['allowance']!=previous['allowance'],
        'a distinct explicitly authorized successor must retain the old ledger')
    require(job['driver']==previous['helper'] and job['inputs'][0]==selected['plan']
        and job['source']=={k:p['source'][k]for k in('path','sha256')}
        and job['workbook']==p['workbook'] and job['version']==snap['fingerprint']
        and job['command']==[job['command'][0],'-I','-B',previous['helper']['path'],'run','--plan',selected['plan']['path'],'--sha256',selected['plan']['sha256']]
        and job['result']==str(home/'result.json'), 'stopped worker selected a different operation')
    interrupted_error = outcome['failure_type']=='TimeoutError' and 'finish_uncertain'not in selected
    if 'finish_uncertain'in selected:
        # A sealed supervisor interruption plus an uncertain final claim update
        # is not a numerical verdict. Its cause remains unknown; only a new,
        # explicitly allowed reconstruction may proceed after native absence.
        require(selected['finish_uncertain']==binding(run/'finish-uncertain.json')
            and read(selected['finish_uncertain'])==dict(outcome=selected['outcome'],
                failure_type='RuntimeError',automatic_retry=False,reconciliation_required=True),
            'exact interrupted supervisor final-update evidence required')
        interrupted_error = outcome['failure_type']=='RuntimeError'
    require(outcome['schema_version']=='harbor-fleet-worker-outcome/v1'
        and outcome['status']=='RECONCILIATION_REQUIRED' and interrupted_error
        and outcome['returncode']is None and outcome['result']is None
        and outcome['automatic_retry']is False and outcome['publication_performed']is False
        and outcome['intent']==binding(run/'intent.json') and selected['outcome']==binding(run/'outcome.json')
        and outcome['log']==binding(run/'worker.log') and not Path(job['result']).exists(),
        'only an actually stopped heartbeat transport attempt can continue here')
    require(process['job']==intent['job'] and type(process['pid'])is int and process['pid']>0
        and intent['job']['path']in process['command'] and process['command'][-2:]==['--owner',intent['owner']]
        and claim['workbook']==p['workbook'] and claim['owner']==intent['owner']
        and claim['claim_id']==intent['claim_id'] and claim['source_sha256']==p['source']['sha256']
        and claim['version']==job['version'] and type(claim['fence'])is int and claim['fence']>0,
        'stopped process or original claim is detached from the source attempt')
    reservation=Path(previous['allowance']['path']).parent/('reconstruction-%s-%06d.json'%(p['workbook'],previous['next_attempt']))
    consumed=read(binding(reservation));native_intent=read(binding(home/'intent.json'))
    require(consumed['plan']==selected['plan'] and consumed['previous']==previous['ledger']
        and consumed['ordinal']==previous['next_attempt'] and consumed['cumulative_limit']==previous['cumulative_reconstruction_limit']
        and consumed['automatic_retry']is False and native_intent['plan']==selected['plan']
        and native_intent['previous']==previous['ledger'] and native_intent['allowance']==previous['allowance']
        and native_intent['next_attempt']==previous['next_attempt'] and native_intent['automatic_retry']is False,
        'original reconstruction reservation or native intent changed')
    old_ledger=read(previous['ledger'])
    require(ledger.get('build_history')==old_ledger.get('build_history',[])+[old_ledger['build']],
            'interrupted reconstruction lost consumed historical attempts')
    journal=run/'model-operations'
    require(not journal.exists()or not any(journal.iterdir()),'model operation must be reconciled separately')
    stopped_process_absence(process,(str(home),str(run),intent['job']['path']))

def stopped_process_absence(process,needles):
    proc=Path('/proc');require(proc.is_dir(),'native Linux process observation required')
    for entry in proc.iterdir():
        if not entry.name.isdigit()or int(entry.name)==os.getpid():continue
        try:
            state=(entry/'stat').read_text().rsplit(')',1)[1].split()
            if state[0]=='Z':continue
            command=(entry/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
        except(FileNotFoundError,ProcessLookupError):continue
        require(not(int(entry.name)==process['pid'] and state[19]==str(process['start_ticks']))
            and not any(needle in command for needle in needles), 'previous source process remains active')

def load(selected):
    p=read(selected)
    require(set(p)-{'interrupted','macro_projection_policy'}=={'schema_version','workbook','helper','snapshot','source','ledger','closure','corpus','inventory','restriction_inventory','inventory_batch_id','curation_root','home','allowance','previous_attempt','next_attempt','cumulative_reconstruction_limit','generation','seconds','memory_bytes','models','automatic_retry'},'closed resource plan required')
    require(p['schema_version']=='fleet-resource-continuation/v1' and p['helper']==binding(__file__),'selected helper changed')
    snap=read(p['snapshot']);root=Path(snap['root']);require(inventory(root)==snap['bindings'],'complete selected code changed')
    sys.path.insert(0,str(root))
    from scripts.batch import harbor_continuation_allowance as A
    policy=A.validate(p['allowance'],expected_scope=A.scope(p['workbook'],p['source']['sha256']),at=time.time_ns())
    ledger=read(p['ledger']);validate_boundary(p,ledger,policy)
    require(binding(p['source']['path'])=={k:p['source'][k] for k in ('path','sha256')},'source bytes changed')
    closure=read(p['closure']);require(closure['corpus']==p['corpus'] and closure_matches(p,closure),'original corpus changed')
    require(closure['files']['workbooks/'+p['workbook']+'/ledger.json']==p['ledger'],'ledger detached from corpus')
    for b in (p['inventory'],p['restriction_inventory']):read(b)
    home=Path(p['home']);require(home.is_absolute() and not home.is_relative_to(root) and not home.is_relative_to(Path(p['corpus'])),'disjoint continuation home required')
    from scripts import corpus,corpus_build as B
    require(Path(B.__file__).resolve().is_relative_to(root) and corpus.code_hash()==snap['fingerprint'],'foreign builder loaded')
    for b in ledger['build']['source_checkpoint']['current_builder']['code']:
        require(binding(b['path'])['sha256']==binding(root/b['name'])['sha256']==b['sha256'],'original source producer differs')
    if 'macro_projection_policy'in p:
        import xl_macro_source_projection as M
        M.policy(p['macro_projection_policy'],p['source']['path'])
        probe=dict(build={},build_history=[ledger['build']])
        B.retained_macro_normalization(arguments(p,p['corpus']),p['source'],probe,p['macro_projection_policy'])
    return p,ledger,closure,B,snap

def arguments(p,run):
    args=SimpleNamespace(inventory=p['inventory']['path'],run_root=str(run),curation_root=p['curation_root'],code_root=read(p['snapshot'])['root'],source_inventory=p['restriction_inventory']['path'],inventory_batch_id=p['inventory_batch_id'],recalc_root=None,trusted_public_key='/etc/harbor/excel-runner-public.pem',timeout=p['seconds'],memory_mb=p['memory_bytes']//1024**2,require_source_generation=p['generation'])
    if 'macro_projection_policy'in p:
        args.macro_projection_policy=p['macro_projection_policy']['path']
        args.macro_projection_policy_sha256=p['macro_projection_policy']['sha256']
    return args

def closure_matches(p,closure):
    if 'macro_projection_policy'in p:
        from scripts.fleet.reconstruction_continue import corpus_inventory
        return corpus_inventory(p['corpus'])=={k:closure[k]for k in('files','links')}
    return inventory(p['corpus'])==closure['files']

def inspect(selected):
    p,ledger,closure,B,snap=load(selected);require(not Path(p['home']).exists(),'continuation consumed; reconcile')
    args=arguments(p,p['corpus']);curation=B.retained_curation(args,p['source'],ledger)
    expected=dict(ledger['build']['bindings'],code_sha256=snap['fingerprint'],curation_sha256=B.digest(curation) if curation.is_file() else None)
    probe=dict(build=dict(bindings=expected),build_history=[copy.deepcopy(ledger['build'])])
    prepared=p['source']['path']
    if 'macro_projection_policy'in p:
        probe['build']['normalization']=copy.deepcopy(ledger['build']['normalization'])
        prepared=probe['build']['normalization']['candidate']
    reuse=B.reuse_completed_source(args,p['source'],probe,prepared,dict(inventory=p['restriction_inventory']['path'],inventory_batch_id=p['inventory_batch_id']),{})
    require(reuse is not None and reuse[2]['generation_id']==p['generation'],'exact prospective source reuse rejected: '+str(probe['build'].get('source_reuse_rejections')))
    require(closure_matches(p,closure),'read-only probe changed original corpus')
    return dict(status='SOURCE_REUSE_PREFLIGHT_PASS',plan=selected,generation=p['generation'],previous_attempt=p['previous_attempt'],next_attempt=p['next_attempt'],source_reused=True,numerical_segmentation_validated=False,models_dispatched=0,attempts_dispatched=0)

def active(p,snap):
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'],'explicit workbook custody required')
    claim=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
    require(claim['source_sha256']==p['source']['sha256'] and claim['version']==snap['fingerprint'],'claim source/code differs')

def clone(p,closure):
    if 'macro_projection_policy'in p:
        from scripts.fleet.reconstruction_continue import clone as clone_with_pointers
        return clone_with_pointers(p,closure)
    dest=Path(p['home'])/'corpus';require(not dest.exists(),'clone consumed; reconcile')
    for name,b in closure['files'].items():
        if name.endswith('/build.log'):continue
        require(binding(b['path'])==b,'original changed during clone')
        target=dest/name;target.parent.mkdir(parents=True,exist_ok=True)
        with Path(b['path']).open('rb') as src,target.open('xb') as out:shutil.copyfileobj(src,out,1024*1024);out.flush();os.fsync(out.fileno())
        require(binding(target)['sha256']==b['sha256'],'clone bytes differ')
    return dest

def run(selected):
    p,ledger,closure,B,snap=load(selected);active(p,snap)
    home=Path(p['home']);require(not home.exists(),'consumed namespace cannot replay')
    require(shutil.disk_usage(p['corpus']).free>=12*1024**3+sum(Path(b['path']).stat().st_size for b in closure['files'].values()),'source clone would breach disk reserve')
    home.mkdir(parents=True);keep(home/'intent.json',dict(plan=selected,previous=p['ledger'],allowance=p['allowance'],next_attempt=p['next_attempt'],automatic_retry=False,created_at_ns=time.time_ns()))
    # Original corpus lock also prevents competing selected copies from cloning the same boundary.
    with (Path(p['corpus'])/'workbooks'/p['workbook']/'.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(closure_matches(p,closure),'original changed before clone')
        reservation=Path(p['allowance']['path']).parent/('reconstruction-%s-%06d.json'%(p['workbook'],p['next_attempt']))
        keep(reservation,dict(plan=selected,previous=p['ledger'],ordinal=p['next_attempt'],cumulative_limit=p['cumulative_reconstruction_limit'],automatic_retry=False,created_at_ns=time.time_ns()))
        dest=clone(p,closure);active(p,snap);os.chdir(snap['root'])
        result=B.execute_one(arguments(p,dest),p['source'],snap['fingerprint'])
        after=json.loads((dest/'workbooks'/p['workbook']/'ledger.json').read_bytes())
        require(after['build_history']==ledger.get('build_history',[])+[ledger['build']] and after['build']['attempts']==p['next_attempt'],'consumed history was rewritten')
        require(closure_matches(p,closure),'original corpus changed')
        receipt=keep(home/'result.json',dict(status='RECONSTRUCTION_VERIFIED' if result.get('state')=='VERIFIED' else 'RECONSTRUCTION_HELD',result=result,ledger=binding(dest/'workbooks'/p['workbook']/'ledger.json'),plan=selected,models_dispatched=0,publication_performed=False,automatic_retry=False))
        require(result.get('source_reused') is True and result.get('source_generation_id')==p['generation'],'required source generation was not reused')
        require(result.get('state')=='VERIFIED','reconstruction held; inspect retained result')
        return receipt

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['inspect','run']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);x=a.parse_args();print(json.dumps(globals()[x.action](dict(path=str(Path(x.plan).absolute()),sha256=x.sha256))))
