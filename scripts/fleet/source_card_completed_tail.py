"""Enqueue a retained, committed source-card dialogue without repeating a phase."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, sys
SCHEMA = 'fleet-source-card-completed-tail/v2'

def require(ok, message):
    if not ok: raise ValueError(message)

def binding(path):
    p=Path(path); require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)), 'regular tail binding required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'tail evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'tail module changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value

def keep(path,value):
    with Path(path).open('xb') as stream:stream.write((json.dumps(value,sort_keys=True,indent=2)+'\n').encode())
    return binding(path)

def context(row, *, probe):
    p=read(row)
    require(set(p)=={'schema_version','helper','prior_plan','prior_runtime','runtime','runtime_profile','projection','authorizing_outcome','completed_outcomes','failed_outcome','job','output','operations','owner','workbook','source','version','digest_cache','scope_reader','enqueue_recovery'} and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed completed tail required')
    prior=read(p['prior_plan']);old=read(p['prior_runtime']);new=read(p['runtime_profile']);projection=read(p['projection'])
    require(all(p[k]==prior[k] for k in ('owner','workbook','source','version','runtime')),'tail identity detached from authoring')
    cache=p['digest_cache'];require(set(cache)=={'module_name','original','selected','names'} and cache['module_name']=='scripts.batch.harbor_tracker_lifecycle' and cache['names']==['checked_record'] and cache['original']==projection['lifecycle'],'closed digest-only runtime extension required')
    scope_reader=p['scope_reader'];require(set(scope_reader)=={'module_name','original','selected','names'} and scope_reader['module_name']=='scripts.fleet.source_card_readiness' and scope_reader['names']==['static'],'only declared static scope reader supported')
    recovery=read(p['enqueue_recovery']);require(recovery['worker_run']==projection['worker_run'] and recovery['authority']==projection['authority'] and recovery['source']==p['source'],'enqueue recovery detached from completed authoring')
    require(new==dict(old,symbols=[*old['symbols'],cache,scope_reader],factories=[*old['factories'],dict(prior['factory'],reader=projection['implementation'],certificate=p['projection']),dict(prior['factory'],reader=recovery['implementation'],certificate=p['enqueue_recovery'])],journal_roots=[p['operations']]),'tail runtime must only add declared scope/enqueue readers and new journal')
    RT=module('source_card_tail_runtime',p['runtime']);scope=RT.installed(p['runtime_profile']);scope.__enter__()
    try:
        entry=RT.load_module('source_card_tail_entry',new['entrypoint']);loaded=entry.load(*new['read_call']['args'],probe=probe)
        _,_,reader,selected,original,env,runner=loaded
        C=selected['campaign'];W=selected['worker']
        env=(env[0],C,env[2],W,selected['renewal'],*env[5:])
        D=entry.dispatcher(original,selected,runner,plan_binding=new['read_call']['args'][0],entry=new['entrypoint'],reader=reader)
        M=module('source_card_tail_prior',prior['implementation'])
        _,authorized=M.authorization_outcome(p['prior_plan'],p['authorizing_outcome'],C)
        require(authorized['status']=='STEP_COMPLETE' and authorized['next_step']=='final-runtime','completed warning authorization required')
        require(len(p['completed_outcomes'])==2,'exact runtime and commit outcomes required')
        checkpoint=None;value=None
        for i,bound in enumerate(p['completed_outcomes']):
            require(Path(bound['path'])==Path(prior['operations'])/('%03d'%i)/'outcome.json','foreign completed stage')
            checkpoint,value=D.successful(bound,env)
            require(value['status']=='STEP_COMPLETE' and value['next_step']==('dialogue-commit','enqueue')[i],'completed stage order changed')
        failed=read(p['failed_outcome']);guard=read(failed['resource']);intent=read(guard['intent'])
        require(Path(p['failed_outcome']['path'])==Path(prior['operations'])/'002/outcome.json' and failed['status']=='RECONCILIATION_REQUIRED' and guard['failure']is None and guard['returncode']==1 and intent['action']=='step' and intent['models']is False and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint),'exact pre-enqueue failure required')
        worker,controls,recipe=C.selected(value['descriptor']);_,selection,lease=C._prior(checkpoint,value['descriptor'])
        require(str(worker.run)==projection['worker_run'] and worker.root==env[6] and controls==env[7] and reader.is_card(worker),'selected source-card worker differs')
        require(not (W.step_directory(worker,'enqueue')/'intent.json').exists(),'owning enqueue already attempted')
        W.active(worker,selection,lease)
        reader.historical_lease(worker,read(projection['after']))
        require(Path(p['operations']).parent==Path(env[5]['run_root'])/'dispatcher-v2' and p['operations']!=prior['operations'],'tail operation namespace differs')
        # Original publication_request retains every checkpoint/initial check;
        # only its ordinary instruction-home selection gains this bound permit.
        def selected_source(observed,observed_value):
            require(observed==env,'foreign publication environment')
            w,c,r=C.selected(observed_value['descriptor'])
            require(w==worker and c==controls and reader.is_card(w) and W.code(w)==reader.owned()['code'],'foreign source-card publication worker')
            require(w.campaign.model_slots==1 and w.campaign.slot_root==D.MODEL,'historical model pool differs')
            return w,r
        D.declared_namespace['selected']=selected_source;D.selected=selected_source
        return p,prior,RT,entry,loaded,D,env,checkpoint,value,scope
    except BaseException:scope.__exit__(*sys.exc_info());raise

def run(row):
    p,prior,RT,entry,loaded,D,env,checkpoint,value,scope=context(row,probe=False)
    try:
        M=module('source_card_tail_live',prior['implementation']);M.live_custody(p)
        require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','readonly tail cannot own')
        out=Path(p['output']);require(not out.exists(),'tail already attempted');D.operation_namespace(env[5],p['operations']);out.mkdir()
        old=read(p['job']);require(old['publish']is False,'tail cannot publish')
        job=keep(out/'job.json',dict(old,outcome=p['completed_outcomes'][-1],operations=p['operations'],models=False))
        def setup(bound):
            require(bound==read(job),'unselected tail job');return env
        D.setup=setup;D.declared_namespace['setup']=setup
        D.run(job)
        held=read(binding(Path(p['operations'])/'hold.json'));final,ready=D.successful(held['outcome'],env)
        require(ready['status']=='PUBLICATION_READY','enqueue requires reconciliation')
        D.publication_request(env,ready)
        return keep(out/'result.json',dict(schema_version=SCHEMA,workbook=p['workbook'],status='PUBLICATION_READY',plan=row,job=job,outcome=held['outcome'],checkpoint=final,runtime=p['runtime_profile'],publication_performed=False,models_dispatched=0))
    finally:scope.__exit__(None,None,None)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=('run','probe'));a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);x=a.parse_args();row=dict(path=x.plan,sha256=x.sha256)
    if x.action=='run':print(json.dumps(run(row)))
    else:
        *_,scope=context(row,probe=True);scope.__exit__(None,None,None);print(json.dumps(dict(status='COMPLETED_SOURCE_CARD_TAIL_VALIDATED',models=0,task_writes=0)))
