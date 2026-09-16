"""Continue an actual completed baseline migration; never repeat its import/CAS."""
from pathlib import Path
import argparse,contextlib,json,os,sys,time
SCHEMA='fleet-baseline-imported-tail/v1'
PENDING='fleet-baseline-imported-tail/v2'

def require(ok,message):
    if not ok:raise ValueError(message)

def module(name,row):
    import hashlib,importlib.util
    require(hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256'],'selected helper changed')
    s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

@contextlib.contextmanager
def context(bound,*,probe):
    import hashlib
    require(hashlib.sha256(Path(bound['path']).read_bytes()).hexdigest()==bound['sha256'],'tail plan changed')
    p=json.loads(Path(bound['path']).read_bytes());F=module('retained_baseline_migration',p['base_helper'])
    fields={'schema_version','entry','base_helper','prior_plan','migration_outcome','stopped_worker_job','stopped_worker_outcome','snapshot_reader','workbook','source','owner','home','operations'}
    pending=p.get('schema_version')==PENDING
    require(set(p)==(fields|{'completed_boundary','previous_tail'}if pending else fields) and p['schema_version']in(SCHEMA,PENDING) and p['entry']==F.binding(__file__),'closed completed migration continuation required')
    if pending:
        previous=F.checked(p['previous_tail']);require(previous['schema_version']==SCHEMA and all(previous[k]==p[k]for k in fields-{'schema_version','entry','stopped_worker_job','stopped_worker_outcome','home','operations'}),'pending continuation changed imported ancestry')
    prior=F.checked(p['prior_plan']);w=F.checked(prior['boundary']);old=F.checked(prior['original_job'])
    require(prior['entry']==p['base_helper'] and all(p[k]==prior[k]for k in ('workbook','source','owner')),'prior migration identity differs')
    outer=F.checked(p['stopped_worker_outcome']);intent=F.checked(outer['intent']);workerjob=F.checked(p['stopped_worker_job'])
    require(outer['schema_version']=='harbor-fleet-worker-outcome/v1' and outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1 and outer['automatic_retry'] is False and intent['job']==p['stopped_worker_job'] and intent['owner']==p['owner'] and workerjob['inputs'][0]==(p['previous_tail']if pending else p['prior_plan']),'exact stopped migration worker required')
    F.module('baseline_stdlib',prior['stdlib']).preload_inspect();snapshot=F.checked(prior['snapshot']);root=Path(snapshot['root']);sys.path.insert(0,str(root));os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_baseline_continuation as B,harbor_source_only_continuation as M
    B.selected_code(prior['snapshot']);F.retained_history(prior,w,probe=probe)
    migrated=F.validate_migration(prior,p['prior_plan'],p['migration_outcome'],C)
    config=F.checked(migrated['config']);M.layout(config,Path(config['run_root']),'environment')
    require(config['code']==prior['snapshot'] and Path(p['home']).parent==Path(prior['home']).parent and p['home']!=prior['home'],'tail namespace or selected snapshot differs')
    require(Path(p['operations']).parent==Path(config['run_root'])/'dispatcher-v2' and not Path(p['operations']).exists() and not Path(p['home']).exists(),'tail attempt exists; reconcile')
    require(not (Path(prior['home'])/'baseline').exists() and not Path(prior['operations']).exists(),'unselected original operation exists')
    if not pending:require(not (Path(config['run_root'])/'bootstrap/baseline').exists(),'baseline was already attempted')
    else:require(not Path(previous['operations']).exists(),'previous ordinary dispatcher already attempted')
    _,lease=C._prior(migrated['checkpoint'],migrated['descriptor'])
    current,_,_=C.selected(migrated['descriptor']);tracker=C.bootstrap._tracker(current.bootstrap,lease)
    require(tracker['leases']['task_agent']['expires_at']-time.time_ns()>(3330+60)*10**9,'insufficient current lease for baseline')
    D=F.module('baseline_retained_dispatcher',old['driver']);order=F.module('baseline_snapshot_order',p['snapshot_reader']);original=D.code_snapshot
    D.code_snapshot=order.ordered_reader(original,snapshot)
    try:
        job={**old,'config':migrated['config'],'operations':p['operations']};env=D.setup(job)
        if pending:validate_pending(p,previous,F,D,env,migrated)
        yield p,prior,F,C,D,migrated,job,env
    finally:D.code_snapshot=original

def validate_pending(p,previous,F,D,env,migrated):
    bound=p['completed_boundary'];require(Path(bound['path'])==Path(previous['home'])/'baseline/outcome.json','foreign completed baseline boundary')
    value=F.checked(bound);guard=F.checked(value['resource']);intent=F.checked(guard['intent'])
    require(intent['action']=='step' and intent['arguments']==dict(descriptor=migrated['descriptor'],checkpoint=migrated['checkpoint'],captures=None) and intent['dispatch']is False,'completed boundary must be the original non-dispatching environment step')
    checkpoint,result=D.successful(bound,env)
    require(result['status']=='PENDING_MODEL' and result['step']=='research' and result['descriptor']==migrated['descriptor'],'only actual first research request may continue')
    return checkpoint,result

def progressed(value):
    return value.get('status')=='PENDING_MODEL' and value.get('step')=='research' or value.get('status')=='STEP_COMPLETE' and value.get('step')=='baseline'

def probe(bound):
    with context(bound,probe=True)as(p,prior,F,C,D,migrated,job,env):
        return dict(status='BASELINE_IMPORTED_TAIL_VALIDATED',plan=bound,checkpoint=migrated['checkpoint'],models=0,lease_mutations=0,migration_replayed=False,original_dispatcher_setup=True)

def run(bound):
    with context(bound,probe=False)as(p,prior,F,C,D,migrated,job,env):
        F.active(prior);home=Path(p['home']);home.mkdir()
        if p['schema_version']==PENDING:first=p['completed_boundary']
        else:first=C.run('step',dict(descriptor=migrated['descriptor'],checkpoint=migrated['checkpoint'],captures=None),env[7],home/'baseline',dispatch=False)
        _,value=D.successful(first,env);require(progressed(value),'baseline held; preserve actual operation')
        actual=F.keep(home/'authoring-job.json',{**job,'outcome':first});D.run(actual)
        held=F.checked(F.binding(Path(p['operations'])/'hold.json'));checkpoint,value=D.successful(held['outcome'],env)
        result=F.keep(home/'result.json',dict(status=value['status'],plan=bound,config=migrated['config'],job=actual,outcome=held['outcome'],checkpoint=checkpoint,source_rebuilds=0,migration_replayed=False,publication_performed=False))
        require(value['status']=='PUBLICATION_READY','remaining authoring held; no automatic replay');return result

def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);v=a.parse_args();print(json.dumps(globals()[v.action](dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256))))
if __name__=='__main__':main()
