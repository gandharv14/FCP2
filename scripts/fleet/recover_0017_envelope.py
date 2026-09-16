"""Adapt only the completed 0017 authorization's coordinator envelope.

The failed guard and original malformed checkpoint remain immutable. This
sidecar does not authorize, take over a lease, call models, or advance a stage.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,sys
from dataclasses import asdict

SCHEMA='harbor-0017-completed-authorization-envelope/v1'
VERSION='74ce86dd50996d836d542247a83c0ed3954d79801bcd416159f80dfd62019371'
def require(v,message):
    if not v:raise ValueError(message)
def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)),'regular bound evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'retained envelope evidence changed')
    return json.loads(Path(row['path']).read_bytes())
def retain(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    return binding(path)
def envelope(permit,lease,descriptor,checkpoint,tracker,failed_guard,plan):
    return dict(schema_version=SCHEMA,permit=permit,lease=lease,descriptor=descriptor,
        predecessor_checkpoint=checkpoint,tracker=tracker,failed_guard=failed_guard,plan=plan,
        authorization_replayed=False,takeover_replayed=False,models_dispatched=0,
        original_guard_passed=False,semantic_budget_reset=False)
def validate_values(p,guard,outer,prior,descriptor,permit):
    require(guard['returncode']==1 and guard['failure'] is None and guard['log']==p['failure_log'],'exact failed serialization guard required')
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['checkpoint'] is None and outer['model_calls']==0
        and outer['resource']==p['failed_guard'] and outer['publication_performed'] is False,'failed outer outcome changed')
    require(prior['status']=='RETRY_READY' and prior['next_step']=='disclosure-build' and prior['descriptor']==p['descriptor']
        and prior['checkpoint']==p['permit'] and prior['publication_performed'] is False,'exact malformed completed-authorization checkpoint required')
    require(descriptor['worker']['code_repair']==p['permit'] and descriptor['initial']==dict(selection=prior['selection'],lease=prior['lease'])
        and descriptor['code']==p['campaign_module'],'retained descriptor/selection/permit differs')
    require(permit['schema_version']=='harbor-0017-disclosure-build-repair/v1' and permit['phase']=='disclosure-build'
        and permit['review_ordinal']==permit['review_limit']==1 and permit['models_added']==0 and permit['semantic_budget_reset'] is False
        and permit['tracker']['sha256']==p['tracker']['sha256'] and permit['takeover']==p['takeover'],'actual completed permit or current tracker differs')
def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','workbook','snapshot','campaign_module','descriptor','malformed_checkpoint','permit','failed_guard','failed_result','failure_log','tracker','takeover','prior_job','prior_process','owner','output'}
        and p['schema_version']==SCHEMA and p['workbook']=='0017','closed0017 envelope selection required')
    snapshot=checked(p['snapshot']);root=Path(snapshot['root']);require(root.name==VERSION,'owning0017 snapshot differs')
    require(all(binding(root/x['relative'])['sha256']==x['sha256'] for x in snapshot['files']),'owning code bytes changed')
    require(binding(p['campaign_module']['path'])==p['campaign_module'] and Path(p['campaign_module']['path'])==root/'scripts/harbor_instruction_campaign.py','exact frozen coordinator required')
    guard=checked(p['failed_guard']);outer=checked(p['failed_result']);prior=checked(p['malformed_checkpoint']);desc=checked(p['descriptor']);permit=checked(p['permit'])
    validate_values(p,guard,outer,prior,desc,permit)
    require(binding(p['failure_log']['path'])==p['failure_log'] and "KeyError: 'permit'" in Path(p['failure_log']['path']).read_text(),'recorded serialization exception required')
    require(binding(p['tracker']['path'])==p['tracker'],'current native tracker changed; reconcile')
    process=checked(p['prior_process']);job=checked(p['prior_job']);needles=[p['prior_job']['path'],job['run'],job['result']]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            fields=(entry/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0]=='Z':continue
            args=[x.decode(errors='replace') for x in (entry/'cmdline').read_bytes().split(b'\0') if x]
            require(not (int(entry.name)==process['pid'] and fields[19]==process['start_ticks']) and not any(x in needles for x in args),'prior preparation process remains active')
        except (FileNotFoundError,ProcessLookupError):continue
    sys.path.insert(0,str(root))
    from scripts import harbor_instruction_campaign as I
    from scripts.batch import harbor_instruction_worker as W
    from scripts.fleet import disclosure_0017_repair as Q
    require(Path(I.__file__)==Path(p['campaign_module']['path']),'foreign owning coordinator loaded')
    worker,controls,recipe=I.selected(p['descriptor']);selection=I.codec.decode(prior['selection']);lease=I.base.decode(prior['lease'])
    require(worker.code_repair==p['permit'] and asdict(lease)==permit['lease'] and lease.owner==p['owner'],'exact retained active lease/permit required')
    _,before,tracker=W.active(worker,selection,lease);require(tracker==p['tracker'],'current owning tracker differs')
    require(Q.validate(worker,before)==permit and not Q.producer_run(worker).exists(),'authorization changed or deterministic build already started')
    return p,I,worker,selection,lease
def prepare(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit nonexecuting recovery preparation required')
    p,I,worker,selection,lease=load(row);out=Path(p['output'])
    require(out.is_absolute() and not out.exists(),'new envelope namespace required; never replay')
    out.mkdir(parents=True)
    completion=retain(out/'completion.json',envelope(p['permit'],asdict(lease),p['descriptor'],p['malformed_checkpoint'],p['tracker'],p['failed_guard'],row))
    original=checked(p['malformed_checkpoint'])
    result=retain(out/'checkpoint.json',I._result('RETRY_READY',p['descriptor'],selection,lease,checkpoint=completion,next_step='disclosure-build',evidence=[*original['evidence'],p['malformed_checkpoint'],p['failed_guard'],completion]))
    I.selected(p['descriptor']);I._prior(result,p['descriptor'])
    return retain(out/'result.json',dict(status='COMPLETED_AUTHORIZATION_ENVELOPE_VALIDATED',workbook='0017',plan=row,checkpoint=result,completion=completion,
        descriptor=p['descriptor'],permit=p['permit'],tracker=p['tracker'],original_failed_guard=p['failed_guard'],
        original_guard_passed=False,model_calls=0,tracker_mutations=0,authorization_replayed=False,takeover_replayed=False,semantic_budget_reset=False,publication_performed=False))
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);x=a.parse_args()
    print(json.dumps(prepare(dict(path=str(Path(x.plan).absolute()),sha256=x.plan_sha256))))
