"""Completed aggregate authoring history for an original semantic retry.

This reader preserves the completed aggregate and every original guarded child.
It has no model, lease, claim, or authoring entry point.
"""
from pathlib import Path
import ast,hashlib,importlib.util,json,sys
from contextlib import ExitStack,contextmanager
SCHEMA='fleet-retained-completed-aggregate/v1'
FIELDS={'schema_version','helper','workbook','source','context','original_job','config',
        'original_snapshot','snapshot','outcome','result','descriptor','blocked'}
def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical aggregate evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'aggregate evidence changed')
    return json.loads(Path(row['path']).read_bytes())
def module(name,row):
    require(binding(row['path'])==row,'aggregate reader changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value

def validate_inventory(selected,env,p):
    q=read(selected['plan']);config=read(q['config']);old=Path(read(q['original_snapshot'])['root']);new=Path(read(q['snapshot'])['root'])
    C,I,P,W,R,actual,root,controls=env
    require(set(selected)=={'helper','plan','projection','config','snapshot','kind'}
        and selected['kind']=='completed_disclosure'and selected['projection']==dict(environment=3,dispatcher=2,job=1)
        and all(p[k]==q[k]for k in ('original_job','outcome','result','descriptor','blocked','source','workbook'))
        and selected['helper']==q['helper']==binding(__file__)and selected['config']==q['config']and selected['snapshot']==q['snapshot']
        and actual==config and config['code']==q['original_snapshot']and config['workbook']==q['workbook']and root==new
        and all(Path(m.__file__).is_relative_to(old)for m in(C,I,P))
        and Path(W.__file__).is_relative_to(new)and Path(R.__file__).is_relative_to(new)
        and C.SECONDS==3300 and C.MEMORY==8589934592,'aggregate original or selected inventory differs')
    return q

def completed_chain(M,U,D,env,p,row,q):
    home=Path(p['output']);first=binding(home/'first-step/outcome.json');ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name
    dirs=[x for x in sorted(ops.iterdir())if x.is_dir()]
    require(dirs and [x.name for x in dirs]==['%03d'%i for i in range(len(dirs))],'aggregate child sequence differs')
    outcomes=[first,*[binding(x/'outcome.json')for x in dirs]];require(outcomes[-1]==q['outcome'],'aggregate rejection is not the final original child')
    preceding=binding(M.adoption_home(p)/'permit/result.json')
    for index,outcome in enumerate(outcomes):
        v=read(outcome);resource=read(v['resource']);intent=read(resource['intent']);side=read(binding(Path(resource['intent']['path']).parent/'aggregate-bootstrap.json'))
        kind,bound=U.retained_command(side['original_command'],p,D,env)
        seconds=env[1].guard_seconds('step',intent['arguments'])if kind=='instruction'else intent['seconds']
        require(bound==resource['intent']and side==dict(schema_version=M.SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=bound,
            original_command=side['original_command'],seconds=seconds,memory_bytes=8589934592,automatic_retry=False),'aggregate original cold guard differs')
        require((intent['arguments']['checkpoint']if kind=='instruction'else intent['checkpoint'])==preceding,'aggregate predecessor differs')
        if outcome!=outcomes[-1]:
            _,result=D.successful(outcome,env)
            require(result['status'] in ('STEP_COMPLETE','LEASE_RENEWED'),'aggregate predecessor did not complete')
            if index==0:require(result['status']=='STEP_COMPLETE'and result['next_step']=='disclosure-commit'
                and all(intent[k]is False for k in('models','commands','containers')),'aggregate adoption was not zero-model completed')
        else:
            require(v['result']==q['result']and resource['returncode']==0 and resource['failure']is None
                and intent['action']=='step'and kind=='instruction','aggregate rejection guard did not complete')
        preceding=v['result']
    return outcomes

def history_runtime(p,env):
    profile=read(p['runtime_profile'])
    require(set(profile)=={'schema_version','runtime','entrypoint','read_call','patches','guard_modules','child_plans','journal_roots','dependencies','symbols'}
        and profile['schema_version']=='fleet-retained-runtime/v1'and profile['entrypoint']==p['helper']
        and profile['read_call']==dict(function='context',args=[],kwargs=dict(initial=False))
        and all(profile[k]==[]for k in('patches','guard_modules','child_plans','journal_roots','dependencies')),
        'only declared aggregate semantic history symbols permitted')
    expected=[('harbor_dialogue_semantic_retry','_history',Path(read(p['original_snapshot'])['root'])),
              ('harbor_instruction_repair','validate_historical_integrity',Path(read(p['snapshot'])['root']))]
    require(len(profile['symbols'])==len(expected),'exact two historical readers required')
    for row,(name,function,root)in zip(profile['symbols'],expected):
        require(set(row)=={'module_name','names','original','selected'}and row['module_name']=='scripts.batch.'+name
            and row['names']==[function]and row['original']==binding(root/'scripts/batch'/(name+'.py')),
            'foreign aggregate historical function')
        old=ast.parse(Path(row['original']['path']).read_text());require(binding(row['selected']['path'])==row['selected'],'selected historical reader changed')
        new=ast.parse(Path(row['selected']['path']).read_text())
        def others(tree):return ast.dump(ast.Module(body=[n for n in tree.body if not isinstance(n,ast.FunctionDef)or n.name!=function],type_ignores=[]),include_attributes=False)
        require(others(old)==others(new)and sum(isinstance(n,ast.FunctionDef)and n.name==function for n in new.body)==1,
            'historical selection changed unrelated code')
        __import__(row['module_name'])
    runtime=module('aggregate_semantic_history_runtime',profile['runtime'])
    return runtime.installed(p['runtime_profile'])

@contextmanager
def aggregate_history(p,env,worker,A,old):
    selected=p['aggregate_history']
    from scripts.batch import harbor_nat_retry as N,harbor_instruction_lease_renewal as R
    require(set(selected)=={'helper','archive','original','function_sha256'}
        and selected['archive']==old['semantic_archive']['helper']
        and selected['original']==binding(Path(read(p['original_snapshot'])['root'])/'scripts/batch/harbor_nat_retry.py')
        and binding(N.__file__)==selected['original']and A.function_sha(N._disclosure_history)==selected['function_sha256'],
        'exact original aggregate downstream reader required')
    H=module('completed_aggregate_history',selected['helper']);G=module('completed_aggregate_archive',selected['archive'])
    require(G.SCHEMA=='fleet-disclosure-semantic-archive/v1','unknown aggregate archive consumer')
    original=N._disclosure_history
    def checked(w,before):
        if not A.selected(w):return original(w,before)
        require(w.root==worker.root and w.run==worker.run and w.context==worker.context
            and w.disclosure_repair==worker.disclosure_repair,'foreign aggregate downstream worker')
        return H.adoption_history(A,R,w,before,historical_evidence=G.historical_evidence)[1]
    N._disclosure_history=checked
    try:yield
    finally:N._disclosure_history=original

def context(row,*,initial=False):
    require(initial is False,'completed aggregate is read-only history');p=read(row)
    require(set(p)-{'runtime_profile','aggregate_history'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed completed aggregate context required')
    c=p['context'];require(set(c)=={'helper','plan'},'closed original aggregate context required');old=read(c['plan'])
    require(old['schema_version']=='fleet-disclosure-aggregate-adoption/v1'and old['helper']==c['helper']
        and all(old[k]==p[k]for k in('workbook','source','original_job')),'original aggregate task differs')
    job=read(p['original_job']);config=read(p['config']);review=read(old['context']['plan'])
    require(job['config']==p['config']and config['code']==p['original_snapshot']and p['snapshot']in review['code_snapshots'],
        'aggregate code/source authority differs')
    M=module('retained_completed_aggregate',c['helper']);manager=ExitStack();loaded=manager.enter_context(M.context(c['plan'],continued=True))
    try:
        actual,reviewer,U,D,env,w,s,lease,A,raw,original=loaded
        require(actual==old and original==job and job['driver']==binding(D.__file__)and job['publish']is False
            and all(job[k]is True for k in('models','commands','containers'))and w.root==Path(read(p['snapshot'])['root']),
            'original aggregate job or selected root differs')
        completed_chain(M,U,D,env,old,c['plan'],p)
        _,chosen,current=U.completed_failure(p,env[1],env[7]);active,controls,_=env[1].selected(p['descriptor'])
        require(active.root==w.root and active.run==w.run and controls==env[7]and chosen.content.bindings['original_raw_source']==p['source']
            and active.disclosure_repair==binding(M.adoption_home(old)/'permit/permit.json')and active.dialogue_semantic_retry is None,
            'aggregate rejection lost its original permit or consumed semantic attempt')
        current_env=(*env[:6],w.root,env[7]);selection=dict(helper=p['helper'],plan=row,projection=dict(environment=3,dispatcher=2,job=1),config=p['config'],snapshot=p['snapshot'],kind='completed_disclosure')
        validate_inventory(selection,current_env,p)
        if 'runtime_profile'in p:manager.enter_context(history_runtime(p,env))
        if 'aggregate_history'in p:manager.enter_context(aggregate_history(p,env,active,A,old))
        original_selected=D.selected
        def dispatch(observed,result):
            require(observed==current_env,'aggregate current environment changed');return original_selected(env,result)
        D.selected=dispatch;D._retained_disclosure_manager=manager;D._retained_disclosure_original_env=env
        return p,job,D,current_env
    except BaseException:manager.__exit__(*sys.exc_info());raise
