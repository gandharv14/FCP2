import dataclasses,json,shutil
from pathlib import Path
import pytest
from scripts.batch import harbor_runtime_readiness as r
from scripts.batch.harbor_admission import record


def fixture(tmp_path,monkeypatch):
    root=tmp_path/'code';root.mkdir()
    for name in r.CODE:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixed current code\n')
    pkg=root/'grader/finance_grader';pkg.mkdir();(pkg/'__init__.py').write_text('# current fixture grader\n')
    stage=tmp_path/'0001-outputs';(stage/'environment/mcp-server/runtime').mkdir(parents=True);(stage/'tests').mkdir()
    from xl_harbor_prep import DOCKERFILE
    from mcp_env.server_assets import SIDECAR_DOCKERFILE,SERVER_PY
    (stage/'environment/Dockerfile').write_text(DOCKERFILE % ('0001-inputs.xlsx','0001-inputs.xlsx')+'COPY additional-assumptions.md /app/additional-assumptions.md\n')
    (stage/'environment/mcp-server/Dockerfile').write_text(SIDECAR_DOCKERFILE)
    (stage/'environment/mcp-server/server.py').write_text(SERVER_PY)
    (stage/'environment/0001-inputs.xlsx').write_bytes(b'unchanged synthetic workbook bytes')
    (stage/'environment/additional-assumptions.md').write_text('Current dialogue.')
    (stage/'tests/answer_key.json').write_text(json.dumps({'targets':{'Sheet!B1':3}}))
    shutil.copyfile(root/'grader/run_grader.py',stage/'tests/run_grader.py');shutil.copytree(pkg,stage/'tests/finance_grader')
    mcp=tmp_path/'mcp';(mcp/'eval').mkdir(parents=True);(mcp/'eval/tasks.jsonl').write_text('{"task_id":"one"}\n')
    s=r.RuntimeSelection('0001',stage,r.tree(stage),'mcp',r.code_bindings(root),mcp,r.tree(mcp))
    monkeypatch.setattr(r.shutil,'which',lambda n:'/usr/bin/docker')
    calls=[]
    def command(args,*,run,label,deadline,root):
        args=[str(x) for x in args];calls.append((label,args));out='';image='sha256:'+('a' if label.startswith('mcp') else 'b')*64;cid='c'*64
        if label in ('mcp-build','main-build'):Path(args[3]).write_text(image)
        elif label in ('mcp-build-inspect','main-build-inspect'):out=json.dumps([{'Id':args[-1]}])
        elif label=='mcp-start':out=cid
        elif label in ('mcp-inspect','mcp-inspect-after'):out=json.dumps([{'Id':cid,'Image':'sha256:'+'a'*64,'State':{'Running':True},'NetworkSettings':{'Ports':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'12345'}]}}}])
        elif label=='oracle':
            result={'valid':True,'bundle':args[args.index('--bundle')+1],'mcp_authoring_bundle':args[args.index('--mcp')+1],'workbook':str(Path(args[args.index('--bundle')+1])/'environment/0001-inputs.xlsx'),'tasks':1,'manifest_errors':[],**{k:{'valid':True} for k in ('environment','workbook_checks','static_profile_excerpts')},'mcp_checks':{'valid':True,'url':'http://127.0.0.1:12345/mcp','tasks':1,'failures':[],'readiness':{'ready':True},'exact_resolutions':1,'provenance_chains':1,'broad_queries_with_conflicts':1,'profile_excerpts':{'valid':True}}}
            (run/'oracle.json').write_text(json.dumps(result))
        elif label=='main-startup':out='HARBOR_STARTUP_JSON='+json.dumps({p.name:record(p)['sha256'] for p in (s.stage/'environment').iterdir() if p.suffix in ('.xlsx','.xlsm','.md')})
        elif label=='grader':
            (run/'reward/reward.json').write_text('{"score":1.0}')
            (run/'reward/reward-details.json').write_text(json.dumps({'score':1.0,'metadata':{'passed':True,'n_targets':1,'n_answered':1,'n_exact':1}}))
        log=run/(label+'.log');log.write_text(out)
        (run/(label+'.command.json')).write_text(json.dumps({'argv':args,'returncode':0,'metrics':{},'log':record(log)}))
        return out
    monkeypatch.setattr(r,'_command',command)
    return s,root,calls,command


def capture(tmp_path,monkeypatch):
    s,root,calls,command=fixture(tmp_path,monkeypatch);run=tmp_path/'runtime'
    result=r.run_runtime(s,run,root=root)
    return s,root,run,r.RuntimeObservation(record(run/'execution.json'),result['run_id']),calls,command


def reseal(run,obs):
    p=run/'execution.json';j=json.loads(p.read_text());j['artifacts']={str(f.relative_to(run)):record(f)['sha256'] for f in run.rglob('*') if f.is_file() and f!=p};p.write_text(json.dumps(j));return r.RuntimeObservation(record(p),obs.run_id)


def test_executes_fixed_image_bound_gates_and_read_only_observation_recheck(tmp_path,monkeypatch):
    s,root,run,obs,calls,_=capture(tmp_path,monkeypatch)
    before=r.tree(run);assert r.validate_runtime(s,obs,root=root)['exact_grader_targets']==1
    assert r.tree(run)==before
    labels=[label for label,_ in calls]
    assert labels[:7]==['mcp-build','mcp-build-inspect','mcp-start','mcp-inspect','main-build','main-build-inspect','main-startup']
    assert set(labels[7:9])=={'oracle','grader'}
    assert labels[9:]==['mcp-inspect-after','mcp-cleanup']
    assert any('sha256:'+'b'*64 in args and '/tests/run_grader.py' in args for _,args in calls)


@pytest.mark.parametrize('change',['code','stage','mcp','image','container','port','retrieval','oracle_context','oracle_flag','grader_score','grader_missing_target','grader_bool','startup','command','raw_log','missing_log','run_id','cleanup','smoke_answers'])
def test_stale_or_incomplete_runtime_observations_never_qualify(tmp_path,monkeypatch,change):
    s,root,run,obs,_,_=capture(tmp_path,monkeypatch)
    if change=='code':(root/'xl_mcp_oracle.py').write_text('new validator')
    elif change=='stage':(s.stage/'environment/0001-inputs.xlsx').write_text('different')
    elif change=='mcp':(s.mcp/'eval/tasks.jsonl').write_text('{"task_id":"two"}\n')
    elif change in ('image','container','port'):
        p=run/'mcp-inspect.log';j=json.loads(p.read_text())
        if change=='image':j[0]['Image']='sha256:'+'d'*64
        elif change=='container':j[0]['Id']='d'*64
        else:j[0]['NetworkSettings']['Ports']['8000/tcp'][0]['HostIp']='0.0.0.0'
        p.write_text(json.dumps(j));q=run/'mcp-inspect.command.json';j=json.loads(q.read_text());j['log']=record(p);q.write_text(json.dumps(j))
    elif change.startswith('oracle') or change=='retrieval':
        p=run/'oracle.json';j=json.loads(p.read_text())
        if change=='retrieval':j['mcp_checks']['exact_resolutions']=0
        elif change=='oracle_context':j['bundle']='/other/task'
        else:j['workbook_checks']['valid']=False
        p.write_text(json.dumps(j))
    elif change.startswith('grader'):
        p=run/'reward/reward-details.json';j=json.loads(p.read_text())
        if change=='grader_score':j['score']=0.5
        elif change=='grader_missing_target':j['metadata']['n_exact']=0
        else:j['score']=True
        p.write_text(json.dumps(j))
    elif change=='startup':
        p=run/'main-startup.log';p.write_text('{}');q=run/'main-startup.command.json';j=json.loads(q.read_text());j['log']=record(p);q.write_text(json.dumps(j))
    elif change=='command':
        p=run/'grader.command.json';j=json.loads(p.read_text());j['argv']=['true'];p.write_text(json.dumps(j))
    elif change=='raw_log':(run/'mcp-start.log').write_text('different')
    elif change=='missing_log':(run/'oracle.log').unlink()
    elif change=='run_id':obs=dataclasses.replace(obs,run_id='d'*32)
    elif change=='cleanup':
        p=run/'mcp-cleanup.command.json';j=json.loads(p.read_text());j['returncode']=1;p.write_text(json.dumps(j))
    elif change=='smoke_answers':(run/'workspace/answers.json').write_text('{}')
    # Most adversaries also rehash their local inventories: semantic checks must
    # reject them independently, even before root's immutable binding comparison.
    if change not in ('run_id','raw_log','missing_log'):obs=reseal(run,obs)
    with pytest.raises((ValueError,KeyError,FileNotFoundError)):r.validate_runtime(s,obs,root=root)


def test_execution_failure_is_durable_and_owned_container_cleanup_runs(tmp_path,monkeypatch):
    s,root,calls,command=fixture(tmp_path,monkeypatch)
    def broken(args,**kw):
        if kw['label']=='oracle':raise RuntimeError('transport failure')
        return command(args,**kw)
    monkeypatch.setattr(r,'_command',broken)
    with pytest.raises(RuntimeError):r.run_runtime(s,tmp_path/'runtime',root=root)
    j=json.loads((tmp_path/'runtime/execution.json').read_text());assert j['valid'] is False and j['failure_type']=='RuntimeError';assert calls[-1][0]=='mcp-cleanup'


def test_existing_output_and_changed_inputs_reject_before_any_execution(tmp_path,monkeypatch):
    s,root,calls,_=fixture(tmp_path,monkeypatch);out=tmp_path/'existing';out.mkdir()
    with pytest.raises(ValueError):r.run_runtime(s,out,root=root)
    (s.stage/'environment/0001-inputs.xlsx').write_text('changed')
    with pytest.raises(ValueError):r.run_runtime(s,tmp_path/'fresh',root=root)
    assert calls==[]


def test_startup_observation_preserves_warnings_but_requires_one_exact_json_record():
    assert r._startup_files('optional workbook warning\nHARBOR_STARTUP_JSON={"file":"digest"}')=={'file':'digest'}
    for text in ('{}','HARBOR_STARTUP_JSON={}\nHARBOR_STARTUP_JSON={}'): 
        with pytest.raises(ValueError):r._startup_files(text)


@pytest.mark.parametrize('name',['environment/Dockerfile','environment/mcp-server/Dockerfile','environment/mcp-server/server.py'])
def test_task_cannot_substitute_executable_code_even_with_new_tree_digest(tmp_path,monkeypatch,name):
    s,root,calls,_=fixture(tmp_path,monkeypatch);(s.stage/name).write_text('arbitrary executable code')
    s=dataclasses.replace(s,stage_tree=r.tree(s.stage))
    with pytest.raises(ValueError):r.run_runtime(s,tmp_path/'fresh',root=root)
    assert calls==[]


def test_runtime_build_and_grader_read_only_frozen_exact_input_copy(tmp_path,monkeypatch):
    s,root,run,obs,calls,_=capture(tmp_path,monkeypatch)
    copied=run/'inputs'/s.stage.name;assert r.tree(copied)==s.stage_tree
    assert next(args for label,args in calls if label=='main-build')[-1]==str(copied/'environment')
    assert str(copied/'tests') in ' '.join(next(args for label,args in calls if label=='grader'))
    (copied/'environment/0001-inputs.xlsx').write_text('changed frozen copy');obs=reseal(run,obs)
    with pytest.raises(ValueError):r.validate_runtime(s,obs,root=root)


def test_plain_runtime_keeps_startup_and_grader_without_inventing_an_oracle(tmp_path,monkeypatch):
    s,root,calls,_=fixture(tmp_path,monkeypatch)
    shutil.rmtree(s.stage/'environment/mcp-server')
    (s.stage/'environment/additional-assumptions.md').unlink()
    dockerfile=s.stage/'environment/Dockerfile';dockerfile.write_text(dockerfile.read_text().replace('COPY additional-assumptions.md /app/additional-assumptions.md\n',''))
    s=dataclasses.replace(s,mode='plain',mcp=None,mcp_tree=None,stage_tree=r.tree(s.stage))
    run=tmp_path/'runtime';result=r.run_runtime(s,run,root=root)
    assert 'mcp' not in result and not any(label=='oracle' for label,_ in calls)
    assert r.validate_runtime(s,r.RuntimeObservation(record(run/'execution.json'),result['run_id']),root=root)['exact_grader_targets']==1


def test_supported_xlsm_artifact_uses_exact_current_filename_in_image_and_startup(tmp_path,monkeypatch):
    s,root,calls,_=fixture(tmp_path,monkeypatch);shutil.rmtree(s.stage/'environment/mcp-server')
    (s.stage/'environment/additional-assumptions.md').unlink()
    dockerfile=s.stage/'environment/Dockerfile';dockerfile.write_text(dockerfile.read_text().replace('COPY additional-assumptions.md /app/additional-assumptions.md\n',''))
    (s.stage/'environment/0001-inputs.xlsx').rename(s.stage/'environment/0001-inputs.xlsm')
    dockerfile=s.stage/'environment/Dockerfile';dockerfile.write_text(dockerfile.read_text().replace('0001-inputs.xlsx','0001-inputs.xlsm'))
    s=dataclasses.replace(s,mode='plain',mcp=None,mcp_tree=None,stage_tree=r.tree(s.stage));run=tmp_path/'runtime'
    result=r.run_runtime(s,run,root=root)
    assert '0001-inputs.xlsm' in result['startup_files']
    assert r.validate_runtime(s,r.RuntimeObservation(record(run/'execution.json'),result['run_id']),root=root)['valid']


@pytest.mark.parametrize('change',[None,'image','name','alive','absent'])
def test_real_child_interrupt_retains_intent_and_exact_container_recovery(tmp_path,monkeypatch,change):
    import os,subprocess,sys
    state=tmp_path/'docker-state.json';harness=tmp_path/'docker-harness'
    harness.write_text('#!'+sys.executable+'\n'+'''import json,sys
from pathlib import Path
state=Path('''+repr(str(state))+''');args=sys.argv[1:]
if args[0]=='run':
    state.write_text(json.dumps({'ID':'c'*64,'Names':args[args.index('--name')+1],'Image':args[-1]}));print('c'*64)
elif args[:2]==['container','ls']:
    if state.exists():print(state.read_text())
elif args[0]=='inspect':
    v=json.loads(state.read_text());print(json.dumps([{'Id':v['ID'],'Name':'/'+v['Names'],'Image':v['Image']}]))
elif args[:2]==['rm','-f']:
    v=json.loads(state.read_text());assert args[2]==v['ID'];state.unlink()
else:raise SystemExit(2)
''');harness.chmod(0o700)
    script=tmp_path/'interrupted.py';run=tmp_path/'runtime';area=tmp_path/'fixture';area.mkdir()
    script.write_text('''import os,signal,subprocess,sys
from pathlib import Path
import pytest
sys.path.insert(0,'''+repr(str(Path(__file__).parent))+''')
sys.path.insert(0,'''+repr(str(Path(__file__).resolve().parents[1]))+''')
from test_harbor_runtime_readiness import fixture
from scripts.batch import harbor_runtime_readiness as r
p=pytest.MonkeyPatch();s,root,_,original=fixture(Path('''+repr(str(area))+'''),p)
r.shutil.which=lambda _:'''+repr(str(harness))+'''
def command(args,**kw):
    if kw['label']=='mcp-start':
        subprocess.run([str(a) for a in args],check=True,capture_output=True)
        os.kill(os.getpid(),signal.SIGKILL)
    return original(args,**kw)
r._command=command
r.run_runtime(s,Path('''+repr(str(run))+'''),root=root)
''')
    child=subprocess.run([sys.executable,str(script)],capture_output=True,timeout=15)
    assert child.returncode==-9,child.stderr.decode()
    assert not (run/'execution.json').exists() and not (run/'mcp-start.command.json').exists()
    intent=record(run/'launch-intent.json');run_id=json.loads(Path(intent['path']).read_text())['run_id']
    monkeypatch.setattr(r.shutil,'which',lambda _:str(harness));calls=[]
    def command(args,*,run,label,deadline,root):
        calls.append([str(a) for a in args]);completed=subprocess.run(calls[-1],capture_output=True,check=True,text=True,timeout=5)
        log=run/(label+'.log');log.write_text(completed.stdout)
        r._save(run/(label+'.command.json'),{'argv':calls[-1],'returncode':0,'log':record(log)})
        return completed.stdout.strip()
    monkeypatch.setattr(r,'_command',command)
    if change in ('image','name'):
        value=json.loads(state.read_text());value['Image' if change=='image' else 'Names']='wrong';state.write_text(json.dumps(value))
    elif change=='alive':
        value=json.loads(Path(intent['path']).read_text());value['producer_pid']=os.getpid();Path(intent['path']).write_text(json.dumps(value));intent=record(Path(intent['path']))
    elif change=='absent':state.unlink()
    if change in ('image','name','alive'):
        with pytest.raises(ValueError):r.reconcile_interrupted_runtime(intent,run_id,root=area/'code')
        assert state.exists() and not any(a[1:3]==['rm','-f'] for a in calls)
    else:
        result=r.reconcile_interrupted_runtime(intent,run_id,root=area/'code')
        assert result['reconciled'] is True and result['valid'] is False and not state.exists()
        assert not (run/'execution.json').exists()
        if change is None:assert calls[-1][1:]==['rm','-f','c'*64]


@pytest.mark.parametrize('extensions',[('.xlsm',),('.xlsx',),('.xlsx','.xlsm'),()])
def test_environment_and_runtime_agree_on_exact_delivered_workbook(tmp_path,monkeypatch,extensions):
    from scripts.batch import harbor_readiness as content
    s,root,_,_=fixture(tmp_path,monkeypatch)
    shutil.rmtree(s.stage/'environment/mcp-server');(s.stage/'environment/additional-assumptions.md').unlink()
    (s.stage/'environment/0001-inputs.xlsx').unlink()
    from openpyxl import Workbook
    for extension in extensions:
        book=Workbook();book.active['A1']='input';book.save(s.stage/'environment'/('0001-inputs'+extension));book.close()
    selected=content.Selection('0001',s.stage,'unused',{'source_file':record(root/'grader/run_grader.py')},root,root,{}, {})
    if len(extensions)==1:
        assert content.environment_content(selected,'plain')['valid']
        assert r.delivered_workbook(s.stage,'0001').suffix==extensions[0]
    else:
        with pytest.raises(ValueError):content.environment_content(selected,'plain')
        with pytest.raises(ValueError):r.delivered_workbook(s.stage,'0001')
