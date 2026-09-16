"""Nonowning Linux publication setup regression with explicitly simulated auth.

Only coordinator responses are test fixtures. Real retained dispatcher/setup,
source, immutable code, history and dependency validation execute. No claim,
publication, runtime task operation, model or native tracker mutation is run.
"""
import argparse,hashlib,importlib.util,json,os,sys,time
from pathlib import Path
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
JOB={'path':str(ROOT/'diagnostics/vm-fleet-20260912/holds-0029/namespace-001/authoring-job-template.json'),'sha256':'5dbe01ae3bc36cb05e98b9a738627fdfeecc271ed5d9e1ff923b8a3ab9110475'}
def checked(row):
    raw=Path(row['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==row['sha256'];return json.loads(raw)
def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output);assert not out.exists()
    m=checked(dict(path=a.manifest,sha256=a.sha256));job=checked(JOB);config=checked(job['config']);snapshot=checked(config['code']);root=Path(snapshot['root']);sys.path.insert(0,str(root))
    row=m['publication_driver'];assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256'];s=importlib.util.spec_from_file_location('native_publication_setup',row['path']);pub=importlib.util.module_from_spec(s);s.loader.exec_module(pub);G=pub.publication_guard
    # Explicitly simulated plan/auth. This fixture is never a launch artifact.
    fixture=dict(schema_version='harbor-fleet-remote-publication/v1',workbook='0029',source_sha256=G.SOURCE,code_root=str(root),namespace_adapter=G.ADAPTER)
    selected={'path':str(out.with_suffix('.fixture.json')),'sha256':'0'*64};raw=(json.dumps(fixture,sort_keys=True)+'\n').encode();selected['sha256']=hashlib.sha256(raw).hexdigest()
    with Path(selected['path']).open('xb') as f:f.write(raw)
    G.select(selected,fixture)
    for key in list(os.environ):
        if key.startswith('HARBOR_FLEET_') or key in ('OPENAI_API_KEY','LITELLM_API_KEY','ANTHROPIC_API_KEY'):os.environ.pop(key,None)
    os.environ.update(HARBOR_FLEET_PUBLICATION_ONLY='1',HARBOR_FLEET_URL='http://127.0.0.1:1',HARBOR_FLEET_TOKEN_FILE='/NONEXISTENT-PUBLICATION-TEST-FIXTURE',HARBOR_FLEET_WORKBOOK='0029',HARBOR_FLEET_OWNER='SIMULATED-PUBLICATION-AUTH-ONLY',HARBOR_FLEET_CLAIM_TOKEN='NONSECRET-FIXTURE',HARBOR_FLEET_FENCE='999')
    events=[]
    from scripts.fleet import coordinator
    class FixtureClient:
        def __init__(self,*args):pass
        def call(self,action,**kwargs):
            events.append(action);now=time.time();w=dict(workbook='0029',owner=os.environ['HARBOR_FLEET_OWNER'],fence=999,source_sha256=G.SOURCE,version=G.CODE,state='active',expires=now+180)
            if action=='heartbeat':
                assert kwargs==dict(workbook='0029',owner=w['owner'],fence=999,token='NONSECRET-FIXTURE');return w
            assert action=='status','probe attempted an owning coordinator action'
            op=dict(kind='publication',workbook='0029',owner=w['owner'],fence=999,state='active',expires=now+180,identity=coordinator.digest(dict(workbook='0029',request_sha256=selected['sha256'])))
            return dict(model_limit=1,publication_limit=1,at=now,workbooks=[w],operations=[op])
    coordinator.Client=FixtureClient
    started=time.monotonic();driver=pub.load('fleet_publication_dispatcher',job['driver']);env=driver.setup(job)
    assert env[6]==root and driver.namespace is not pub.namespace
    assert driver.namespace.install_execution_guards is pub.namespace.install_execution_guards is G.install
    from scripts.batch import harbor_model_executor as E,harbor_admission as A
    import xl_transport
    blocked=[]
    for name,fn in [('execute',E.execute),('open',E._open),('transport',xl_transport.read_response),('raw_transport',xl_transport._read_response),('model_slot',xl_transport.model_slot),('fresh_admission',A.DispatcherAdmission._check)]:
        try:fn()
        except RuntimeError:blocked.append(name)
        else:raise AssertionError('publication model/fresh gate bypassed')
    assert events and all(x in {'status','heartbeat'} for x in events)
    result=dict(status='PASS',schema_version='0029-publication-setup-regression/v1',manifest=dict(path=a.manifest,sha256=a.sha256),job=JOB,source_sha256=G.SOURCE,code=G.CODE,elapsed_seconds=time.monotonic()-started,real_owning_setup=True,authentication='EXPLICIT_TEST_FIXTURE_NO_LIVE_CLAIM',auth_fixture=selected,fixture_calls=events,blocked_interfaces=blocked,models_dispatched=0,claims_changed=0,publication_performed=False,native_task_operations=0,qualification_claimed=False)
    with out.open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps(dict(status='PASS',elapsed_seconds=result['elapsed_seconds'],result=str(out),authentication=result['authentication'])))
if __name__=='__main__':main()
