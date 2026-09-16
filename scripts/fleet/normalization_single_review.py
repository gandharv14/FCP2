"""Reuse one completed normalization review after an exact RSS guard failure."""
from pathlib import Path
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace
import ast, hashlib, importlib, json

SCHEMA = 'fleet-normalization-single-review/v1'
MEMORY = 12*1024**3
SECONDS = 3300
AUTH = 'acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'


def require(ok,message):
    if not ok: raise ValueError(message)


def delta(N,p,root):
    r=N.read(p['runtime_profile'])
    coherent='prepared_refresh'in p
    require(r['runtime']==p['runtime'] and not any(r.get(k)for k in ('patches','factories','historical_imports','guard_modules','child_plans','journal_roots')),'only selected review/refresh corrections allowed')
    dependencies=r.get('dependencies',[])
    require((coherent and 'completed_emission'in p and len(r['symbols'])==1 and len(dependencies)==1)
        or (not coherent and not dependencies and len(r['symbols'])==(2 if 'completed_emission'in p else 1)),
        'coherent refresh dependency requires retained preparation')
    declarations=list(r['symbols'])
    if coherent:
        dependency=dependencies[0]
        require(set(dependency)=={'module_name','original','selected'},'closed coherent refresh dependency required')
        declarations.append(dict(dependency,names=['unchanged','prepare']))
    allowed=[('scripts.batch.harbor_review_context',['normalization_review'],'scripts/batch/harbor_review_context.py'),('scripts.batch.harbor_environment_refresh',['unchanged','prepare'],'scripts/batch/harbor_environment_refresh.py')]
    for s,(name,functions,path) in zip(declarations,allowed):
        original=N.binding(Path(root)/path)
        require(s['module_name']==name and s['names']==functions and s['original']==original,'wrong single review correction')
        trees=[]
        for row in (original,s['selected']):
            require(N.binding(row['path'])==row,'single review correction changed')
            t=ast.parse(Path(row['path']).read_text())
            for function in functions:
                matches=[n for n in t.body if isinstance(n,ast.FunctionDef)and n.name==function];require(len(matches)==1,'unique original function required');t.body.remove(matches[0])
            trees.append(ast.dump(t))
        require(trees[0]==trees[1],'single review correction changed other functions')


def resource_policy(N,p,o):
    policy=N.read(p['memory_policy']);auth=policy.get('authorization')
    require(set(policy)=={'schema_version','authorization','workbook','source','failed_guard','memory_bytes','seconds','models_repeated','original_outputs_preserved'} and policy['schema_version']=='fleet-normalization-memory-continuation/v1' and policy['workbook']==p['workbook'] and policy['source']==p['source'] and policy['failed_guard']==o['failed_guard'] and policy['memory_bytes']==MEMORY and policy['seconds']==SECONDS and policy['models_repeated']==0 and policy['original_outputs_preserved']is True,'bounded emission resource policy differs')
    require(auth['sha256']==AUTH and N.binding(auth['path'])==auth,'original resource authorization changed')
    tp=N.read(p['takeover_plan']);allowance=N.read(tp['allowance'])
    require(allowance['authorization']==auth and allowance['scope']==dict(workbook=p['workbook'],original_sha256=p['source']['sha256']) and allowance['limits']['memory_bytes']>=MEMORY and allowance['limits']['stage_seconds']>=SECONDS,'original allowance does not cover selected resource guard')


def original(N,p):
    o=N.read(p['single_author'])
    require(set(o)=={'schema_version','job','research','failed_outcome','failed_guard','completed_review'} and o['schema_version']==SCHEMA,'closed single review custody required')
    job=N.read(o['job']);D=N.module('single_review_original_dispatcher',job['driver']);env=D.setup(job);base=env[0]
    research=N.read(o['research']);require(research['status']=='STEP_COMPLETE'and research['step']=='research','original completed research required')
    base._prior(o['research'],research['descriptor']);w,_,controls=base.selected(research['descriptor'])
    content=base.content_codec.decode(research['selection']).content;producer=base.worker.normalization
    require(controls==env[-1] and content.workbook==w.bootstrap.workbook==p['workbook'] and content.bindings['original_raw_source']==p['source'] and Path(producer.__file__).is_relative_to(Path(env[6])),'single normalization source or original producer differs')
    selected=producer.Selection(content.workbook,content.bindings,content.normalization_run,content.model_policy,w.normalization_budget)
    require(p['prepared']==N.binding(selected.run/'prepared.json') and Path(o['research']['path']).parent.parent==Path(job['operations']) and Path(p['home']).parent==Path(job['operations']).parent and Path(p['home'])!=Path(job['operations']) and not Path(p['home']).is_relative_to(w.run),'single normalization namespace or preparation differs')
    return o,job,D,env,producer,w,selected


def load(N,p):
    values=original(N,p);o,job,D,env,producer,w,selected=values
    delta(N,p,env[6]);resource_policy(N,p,o)
    return values


def stopped(N,p,o,job,env,producer,selected):
    guard=N.read(o['failed_guard']);intent=N.read(guard['intent']);outcome=N.read(o['failed_outcome'])
    require(outcome['status']=='RECONCILIATION_REQUIRED' and outcome['resource']==o['failed_guard'] and guard['returncode']is None and guard['failure']['type']=='ConversionTimeout' and guard['metrics']['limit_reason']=='process_group_rss' and guard['metrics']['peak_rss_bytes']>8*1024**3,'exact completed-review RSS stop required')
    require(intent['action']=='step' and intent['arguments']==dict(descriptor=N.read(o['research'])['descriptor'],checkpoint=o['research'],captures=None) and intent['controls']==env[0].encode(env[-1]) and intent['memory_limit_bytes']==8*1024**3 and intent['timeout_seconds']==SECONDS and intent['dispatch']is True,'original resource stop identity differs')
    require(Path(o['failed_outcome']['path']).parent==Path(o['failed_guard']['path']).parent and int(Path(o['failed_guard']['path']).parent.name)==int(Path(o['research']['path']).parent.name)+1,'failed stage is not direct research successor')
    outer=N.read(p['prior_outcome']);wi=N.read(outer['intent']);worker=N.read(wi['job']);stop=N.read(p['stopped'])
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1 and outer['failure_type']is None and outer['automatic_retry']is False and worker['source']==p['source'] and worker['workbook']==p['workbook'] and outer['publication_performed']is False,'original outer resource stop differs')
    require(set(stop)=={'at','claims','files','models','processes'} and stop['claims']==stop['models']==0 and stop['processes']==[],'original worker still active')
    inventory={x['native']['path']:x['native']['sha256']for x in stop['files']}
    require(len(inventory)==len(stop['files']) and all(inventory.get(x['path'])==x['sha256']for x in (o['job'],o['research'],o['failed_outcome'],o['failed_guard'],guard['intent'],guard['log'],p['prior_outcome'],outer['intent'],wi['job'],outer['result'])),'stopped evidence does not cover original owning boundary')
    for item in stop['files']:
        require(N.binding(item['native']['path'])==item['native'] and N.binding(item['copy']['path'])==item['copy'] and item['native']['sha256']==item['copy']['sha256'],'stopped evidence changed')
    forbidden=('normalization-review.json','phase1-result.json','normalized.phase1.json','normalized.json','normalization_report.json','exclusions.json',f'normalize_{p["workbook"]}.py','source_profiles.json')
    require(not any((selected.run/n).exists()for n in forbidden),'normalization emission already has effects')


@contextmanager
def context(N,p,*,initial):
    o,job,D,env,producer,w,selected=load(N,p);base=env[0]
    tp=N.read(p['takeover_plan']);T=N.module('single_completed_takeover',tp['shared']['takeover']);T.life=N.module('single_completed_lifecycle',tp['shared']['lifecycle'])
    before,after,lease=T.validate_completion(p['completion']);done=N.read(p['completion']);stop=N.read(done['stopped_owner_proof']);research=N.read(o['research']);previous=base.decode(research['lease'])
    require(T._selection(tp['selection'])==T._selection(stop['selection']) and tp['selection']['checkpoint']==o['research'] and tp['selection']['worker_kind']=='environment' and tp['selection']['config']==job['config'] and tp['original_snapshot']==N.read(job['config'])['code'] and before['revision']>=previous.revision and (before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'])==(previous.owner,previous.token) and p['tracker']==dict(path=tp['selection']['tracker']['path'],sha256=done['after']['sha256']) and after['bindings']['original_raw_source']==p['source'] and lease.owner==p['owner'],'single review takeover detached from original custody')
    RT=N.module('single_review_runtime',p['runtime']);A=N.module('single_review_campaign_adapter',p['campaign_adapter'])
    with RT.installed(p['runtime_profile']):
        review=importlib.import_module('scripts.batch.harbor_review_context');old_review=producer.normalization_review;old_keep=producer.keep
        producer.normalization_review=review.normalization_review
        def retained_keep(path,value):
            raw=value if isinstance(value,bytes)else producer.executor.canonical(value)
            require(Path(path).read_bytes()==raw,'retained preparation would change')
            return N.binding(path)
        try:
            producer.keep=retained_keep
            expected=producer.prepared_call(selected,p['prepared']);observation=producer.executor.inspect(expected,w.campaign)
            require(observation.status=='completed' and asdict(observation.call)==N.read(o['completed_review'])['call'],'exact completed original review required')
            call=observation.call
            if initial:
                if 'prepared_refresh'in p:pass
                elif 'completed_emission'in p:
                    completed=N.module('single_completed_emission',p['completed_helper']);completed.validate_single(N,p,producer,selected,base.encode(lease))
                else:stopped(N,p,o,job,env,producer,selected)
                _,rows=producer.analyse(selected)
                producer.normalization_review(call,p['prepared'],source=selected.bindings['source_file'],baseline=selected.bindings['baseline_inputs'],draft=N.binding(selected.run/'draft.json'),rows=rows,bindings=selected.bindings,policy=selected.policy)
            producer.keep=old_keep
            facade=SimpleNamespace(selection=lambda prepared:selected if prepared==p['prepared']else (_ for _ in ()).throw(ValueError('prepared differs')),emit_phase1=lambda prepared:producer.emit_phase1(selected,prepared,call),call=call)
            values=p,dict(code_root=str(env[6]),research=o['research'],original_job=o['job']),None,D,env,facade,w,lease,A
            if 'prepared_refresh'in p:
                prepared=N.module('single_prepared_refresh',p['prepared_helper'])
                with prepared.installed(N,p,producer,selected,w,base.encode(lease),initial=initial):yield values
            else:yield values
        finally:
            producer.normalization_review=old_review;producer.keep=old_keep
