from pathlib import Path
from types import SimpleNamespace
import ast,copy,json
import pytest
from scripts.fleet import pool_preclaim_refusal as P,source_pool_control as S,pool_v5


def fixture(tmp_path):
    put=lambda name,value:S.retain(tmp_path/name,value)
    host='hhu-server-7';wb='0293';root=tmp_path/'old-pool';worker=tmp_path/'workers/0293';home=tmp_path/'source';gen=root/host/'generations/generation'
    source=dict(path='/original.xlsx',sha256='s')
    sp=put('source-plan.json',dict(workbook=wb,source=dict(source,size_bytes=1),home=str(home),models=0,automatic_retry=False))
    job=dict(workbook=wb,source=source,inputs=[sp],driver=dict(path='/code/source_first.py',sha256='d'),run=str(worker),result=str(home/'result.json'),version='code')
    jb=put('job.json',job)
    before='def reserve(status,host,active):\n    return 1\n'
    room=next(n for n in ast.parse(Path(pool_v5.__file__).read_text()).body if isinstance(n,ast.FunctionDef)and n.name=='coordinator_room')
    (tmp_path/'old.py').write_text(before);(tmp_path/'new.py').write_text(before.replace('    return 1','    if not coordinator_room(status,host,active):return None\n    return 1')+'\n'+ast.unparse(room)+'\n')
    oldsuper=S.binding(tmp_path/'old.py');newsuper=S.binding(tmp_path/'new.py')
    cfg=dict(host=host,jobs=[jb],worker=dict(path='/code/worker.py',sha256='w'))
    cb=put('config.json',cfg);row=dict(workbook=wb,host=host,job=jb,source=source)
    prior=dict(rows=[row],assignments={host:[jb]},supervisor=oldsuper,runtime='unchanged',authorization='auth')
    old=dict(schema_version='fleet-source-pool-start/v5',prepared=put('prepared.json',prior),run_root=str(root),configs={host:cb},coordinator='coord',authorization='auth',admission_policy='policy')
    generation=dict(config=cb,supervisor=oldsuper,host=host,generation='generation',slots=[],resumed=False)
    launched=dict(generation,slots=[dict(slot=0,identity=dict(pid=1,start='1'))]);files=[]
    def native(path,value):
        c=put('copies/'+str(len(files))+'.json',value);n=dict(c,path=str(path));files.append(dict(native=n,copy=c));return n
    native(gen/'generation.json',generation);native(gen/'launched.json',launched);native(root/host/'current.json',launched)
    marker=native(root/host/'dispatches/0293.json',dict(slot=0,job=jb,host=host,automatic_retry=False,at=1))
    logpath=tmp_path/'copies/log';logpath.write_text('File "/code/worker.py"\nclaim = client.call("claim", x=1)\nraise Conflict(value[\'error\']) from None\n'+P.ERROR+'\n')
    log=dict(S.binding(logpath),path=str(gen/'slot-0.workbook-0293.log'));files.append(dict(native=log,copy=S.binding(logpath)))
    native(gen/'slot-0.workbook-0293.result.json',dict(job=jb,dispatch=marker,returncode=1,failure_type=None,forced=False,outcome=None,log=log,finished_at=3,automatic_retry=False,publication_performed=False))
    native(worker/'intent.json',dict(job=jb,owner=host+'/slot-0/generation',claim_id='unused-uuid',created_at_ns=2*10**9,automatic_retry=False))
    obs=dict(at=4*10**9,host=host,pool_status=dict(generation='generation',host=host,config=cb,supervisor=oldsuper),processes={},worker_inventory=['intent.json'],source_home_exists=False,files=files)
    record=dict(job=jb,observation=put('observation.json',obs))
    start=put('start.json',dict(status='START_RECORDED',automatic_retry=False,plan=put('old-plan.json',old),hosts=[dict(host=host,returncode=0,automatic_retry=False,stdout=put('start.stdout',dict(launched,resumed=False)))]))
    newjob=put('newjob.json',dict(job,run=str(worker.parent/'0293-preclaim-001')))
    prepared=dict(prior,rows=[dict(row,job=newjob)],assignments={host:[newjob]},supervisor=newsuper)
    proof=dict(schema_version=P.SCHEMA,previous_start=start,records={wb:record},coordinator='coord',authorization='auth')
    p=dict(run_root=str(tmp_path/'new-pool'),configs={host:put('newconfig.json',dict(cfg,jobs=[newjob]))},coordinator='coord',authorization='auth',admission_policy='policy',preclaim_recovery=dict(helper=S.binding(P.__file__),proof=put('proof.json',proof)))
    status=dict(workbooks=[dict(workbook=wb,state='ready',fence=0,owner=None,claim_id=None,version=None,checkpoint=None,expires=None,source_sha256='s')],operations=[],author_reservations=[])
    return SimpleNamespace(p=p,prepared=prepared,status=status,proof=proof,record=record,old=old,prior=prior,obs=obs,put=put,job=job)


def test_exact_refusal_accepts_only_new_worker_namespace(tmp_path):
    f=fixture(tmp_path);assert P.validate(S,f.p,f.prepared,f.status)[0]==f.old
    assert P.refusal(f.record,f.old,f.prior,S.read,S.binding)[1]==f.job


@pytest.mark.parametrize('fault',['source_effect','worker_effect','live','timeout','noncapacity','log_tamper','claim','owner','source','model','author','new_run_reused','changed_command','changed_runtime','changed_generation'])
def test_refusal_rejects_effects_uncertainty_and_changed_custody(tmp_path,fault):
    f=fixture(tmp_path)
    if fault in ('source_effect','worker_effect','live'):
        if fault=='source_effect':f.obs['source_home_exists']=True
        elif fault=='worker_effect':f.obs['worker_inventory'].append('claim.json')
        else:f.obs['processes']['0293']={'pid':1}
        f.record['observation']=f.put('bad-observation.json',f.obs);f.p['preclaim_recovery']['proof']=f.put('bad-proof.json',f.proof)
    elif fault in ('timeout','noncapacity','log_tamper'):
        r=next(r for r in f.obs['files']if r['native']['path'].endswith('.log'))
        Path(r['copy']['path']).write_text('TimeoutError'if fault=='timeout'else'Conflict: different'if fault=='noncapacity'else'changed')
    elif fault in ('claim','owner','source'):
        f.status['workbooks'][0][{'claim':'fence','owner':'owner','source':'source_sha256'}[fault]]=1 if fault=='claim'else'changed'
    elif fault in ('model','author'):f.status['operations'if fault=='model'else'author_reservations'].append(dict(workbook='0293'))
    elif fault in ('new_run_reused','changed_command'):
        job=S.read(f.prepared['rows'][0]['job']);job.update(run=f.job['run'])if fault=='new_run_reused'else job.update(command=['other'])
        f.prepared['rows'][0]['job']=f.put('bad-job.json',job)
    elif fault=='changed_runtime':f.prepared['runtime']='changed'
    else:
        s=S.read(f.proof['previous_start']);v=S.read(s['hosts'][0]['stdout']);v['generation']='foreign';s['hosts'][0]['stdout']=f.put('bad-start.stdout',v)
        f.proof['previous_start']=f.put('bad-start.json',s);f.p['preclaim_recovery']['proof']=f.put('bad-proof.json',f.proof)
    with pytest.raises(ValueError):P.validate(S,f.p,f.prepared,f.status)


def test_supervisor_rejects_unrelated_function_change(tmp_path):
    f=fixture(tmp_path);p=Path(f.prepared['supervisor']['path']);p.write_text(p.read_text().replace('return 1','return 2'));new=S.binding(p)
    with pytest.raises(ValueError,match='beyond'):P.supervisor_delta(S.read,S.binding,f.prior['supervisor'],new)


def test_native_requires_normal_stop_and_preserves_original_intent(tmp_path):
    f=fixture(tmp_path);host='hhu-server-7';root=Path(f.old['run_root'])/host
    for r in f.obs['files']:
        p=Path(r['native']['path']);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(Path(r['copy']['path']).read_bytes())
    S.retain(root/'stop.json',dict(force=False));state=dict(generation='generation',config=f.old['configs'][host],stop_requested=True,slots=[dict(supervisor_alive=False,worker_identity=None)])
    pool=SimpleNamespace(status=lambda r:state,native_census=lambda h:{})
    P.native(S,f.p,f.prepared,f.status,host,pool)
    state['slots'][0]['supervisor_alive']=True
    with pytest.raises(ValueError,match='gracefully stopped'):P.native(S,f.p,f.prepared,f.status,host,pool)
