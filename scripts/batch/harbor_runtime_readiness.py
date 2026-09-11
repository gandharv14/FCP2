"""Execute current runtime gates. Root coordinates capacity before calling.

This producer never accepts a task-generated success receipt or reuses a run.
Answer-key smoke tests grader wiring only; it is not a solve or reconstruction.
"""
from dataclasses import dataclass,replace
from pathlib import Path,PurePosixPath
import hashlib,json,os,re,shutil,sys,time,uuid
from concurrent.futures import ThreadPoolExecutor
from scripts.batch.harbor_admission import record,read,require
from scripts.batch.harbor_tracker_lifecycle import checked_record
from scripts.batch.harbor_readiness import tree
from scripts.batch.hardened_runner import _run_in_process_group

ROOT=Path(__file__).resolve().parents[2]
MEMORY=8*1024**3
CODE=('xl_mcp_oracle.py','plain_eligibility.py','xl_harbor_prep.py','mcp_env/server_assets.py','scripts/batch/harbor_runtime_readiness.py','scripts/batch/harbor_readiness.py','scripts/batch/harbor_admission.py','scripts/batch/harbor_tracker_lifecycle.py','scripts/batch/harbor_tracker.py','scripts/corpus.py','scripts/batch/hardened_runner.py','scripts/batch/process_guardian.py','grader/run_grader.py')


def _producer_tool_path(value):
    require(type(value) is str and bool(value),'producer tool path must be a string')
    require(not any(ord(c)<32 or ord(c)==127 or c in '\x85\u2028\u2029' for c in value) and '\\' not in value,
            'unsafe producer tool path')
    path=PurePosixPath(value)
    require(value.startswith('/') and not value.startswith('//') and len(path.parts)>1
            and path.as_posix()==value and all(part not in {'.','..',''} for part in value.split('/')[1:]),
            'producer tool path must be canonical and absolute')


@dataclass(frozen=True)
class RuntimeProducerIdentity:
    """Root-bound command identity for replay of an existing observation.

    This is not an execution capability or cleanup authorization. A private
    release verifier must bind it to its externally pinned bundle manifest only
    after native qualification; task-supplied command paths are not authority.
    """
    python_executable: str
    docker_executable: str

    def __post_init__(self):
        _producer_tool_path(self.python_executable)
        _producer_tool_path(self.docker_executable)


def capture_runtime_producer_identity():
    """Capture this producer host's real tool locators without executing them."""
    docker=shutil.which('docker');require(docker is not None,'Docker executable unavailable')
    return RuntimeProducerIdentity(sys.executable,docker)


@dataclass(frozen=True)
class RuntimeSelection:
    workbook: str
    stage: Path
    stage_tree: dict
    mode: str
    code: dict
    mcp: Path | None = None
    mcp_tree: dict | None = None
    allowlist: dict | None = None


def code_bindings(root=ROOT):
    paths=[root/p for p in CODE]+sorted((root/'grader/finance_grader').rglob('*.py'))
    return {str(p.relative_to(root)):record(p)['sha256'] for p in paths}


def _save(path,value):
    data=(json.dumps(value,indent=2,allow_nan=False)+'\n').encode();tmp=path.with_suffix('.tmp')
    with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def delivered_workbook(stage,workbook):
    paths=[stage/'environment'/(workbook+'-inputs'+suffix) for suffix in ('.xlsx','.xlsm')]
    found=[path for path in paths if path.is_file()]
    require(len(found)==1,'exactly one supported delivered workbook required')
    return found[0]


def _inputs(s,root):
    require(re.fullmatch(r'[0-9]{4}',s.workbook) is not None,'invalid workbook identity')
    require(s.stage.name==s.workbook+'-outputs','stage workbook identity differs')
    require(tree(s.stage)==s.stage_tree,'staged inputs changed')
    require(s.code==code_bindings(root),'runtime validator code changed')
    require(s.mode in ('plain','mcp'),'unknown mode')
    if s.mode=='mcp':
        require(s.mcp is not None and tree(s.mcp)==s.mcp_tree,'MCP authoring context changed')
    else:
        from plain_eligibility import check_plain_environment
        require(s.mcp is None and s.mcp_tree is None and s.allowlist is None,'plain task has MCP evidence')
        require(check_plain_environment(s.stage,s.workbook)['valid'] is True,'plain hygiene failure')
    if s.allowlist:checked_record(s.allowlist)
    from xl_harbor_prep import DOCKERFILE
    name=delivered_workbook(s.stage,s.workbook).name;dockerfile=DOCKERFILE % (name,name)
    if (s.stage/'environment/additional-assumptions.md').exists():
        dockerfile+='COPY additional-assumptions.md /app/additional-assumptions.md\n'
    require((s.stage/'environment/Dockerfile').read_text()==dockerfile,'main Dockerfile differs from current builder/application')
    if s.mode=='mcp':
        from mcp_env.server_assets import SIDECAR_DOCKERFILE,SERVER_PY
        require((s.stage/'environment/mcp-server/Dockerfile').read_text()==SIDECAR_DOCKERFILE,'sidecar Dockerfile differs from current generator')
        require((s.stage/'environment/mcp-server/server.py').read_text()==SERVER_PY,'sidecar server differs from current generator')
    expected={k:v for k,v in tree(root/'grader/finance_grader').items() if '__pycache__' not in k.split('/') and not k.endswith('.pyc')}
    require(tree(s.stage/'tests/finance_grader')==expected,'staged grader package changed')
    require((s.stage/'tests/run_grader.py').read_bytes()==(root/'grader/run_grader.py').read_bytes(),'staged grader runner changed')


def _snapshot_inputs(s,run):
    frozen=run/'inputs';frozen.mkdir()
    stage=frozen/s.stage.name;shutil.copytree(s.stage,stage)
    require(tree(stage)==s.stage_tree,'runtime copy changed task bytes')
    mcp=None
    if s.mcp is not None:
        mcp=frozen/'mcp';shutil.copytree(s.mcp,mcp)
        require(tree(mcp)==s.mcp_tree,'runtime copy changed MCP bytes')
    allowlist=None
    if s.allowlist:
        path=frozen/'allowlist.json';shutil.copyfile(s.allowlist['path'],path);allowlist=record(path)
        require(allowlist['sha256']==s.allowlist['sha256'],'runtime copy changed allowlist')
    return replace(s,stage=stage,mcp=mcp,allowlist=allowlist)


def _snapshot_selection(s,run):
    frozen=run/'inputs';allowlist=record(frozen/'allowlist.json') if s.allowlist else None
    if allowlist:require(allowlist['sha256']==s.allowlist['sha256'],'runtime frozen allowlist changed')
    return replace(s,stage=frozen/s.stage.name,mcp=frozen/'mcp' if s.mcp is not None else None,allowlist=allowlist)


def _command(args,*,run,label,deadline,root):
    remaining=deadline-time.monotonic();require(remaining>0,'runtime deadline exhausted')
    log=run/(label+'.log');metrics={}
    with log.open('xb') as out:
        rc=_run_in_process_group([str(x) for x in args],stdout=out,timeout_seconds=remaining,grace_seconds=2,memory_limit_bytes=MEMORY,metrics=metrics,cwd=root,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    _save(run/(label+'.command.json'),{'argv':[str(x) for x in args],'returncode':rc,'metrics':metrics,'log':record(log)})
    require(rc==0,'runtime command failed: '+label)
    return log.read_text().strip()


def _image(text):
    require(re.fullmatch('sha256:[0-9a-f]{64}',text) is not None,'invalid built image ID');return text


def _container(raw,cid,image):
    entries=json.loads(raw);require(len(entries)==1,'ambiguous container inspection');v=entries[0]
    require(v['Id']==cid and v['Image']==image,'container/image identity changed')
    require(v['State']['Running'] is True and not v['State'].get('Restarting') and not v['State'].get('OOMKilled'),'container not running cleanly')
    return v


def _oracle(report,s,url):
    require(report.get('valid') is True,'oracle rejected runtime')
    require(report.get('bundle')==str(s.stage.resolve()) and report.get('mcp_authoring_bundle')==str(s.mcp.resolve()),'oracle context differs')
    require(report.get('workbook')==str(delivered_workbook(s.stage,s.workbook).resolve()),'oracle workbook differs')
    tasks=[json.loads(x) for x in (s.mcp/'eval/tasks.jsonl').read_text().splitlines() if x.strip()]
    n=len(tasks);require(n>0 and report.get('tasks')==n and report.get('manifest_errors')==[],'oracle task coverage incomplete')
    for name in ('environment','workbook_checks','static_profile_excerpts'):
        require(report.get(name,{}).get('valid') is True,'oracle component rejected: '+name)
    m=report.get('mcp_checks',{})
    require(m.get('valid') is True and m.get('url')==url and m.get('tasks')==n and m.get('failures')==[],'live MCP endpoint/evidence rejected')
    require(m.get('readiness',{}).get('ready') is True,'live MCP never ready')
    for key in ('exact_resolutions','provenance_chains','broad_queries_with_conflicts'):
        require(type(m.get(key)) is int and m[key]==n,'incomplete live retrieval: '+key)
    require(m.get('profile_excerpts',{}).get('valid') is True,'live profile evidence rejected')
    return n


def _startup_files(text):
    rows=[line.removeprefix('HARBOR_STARTUP_JSON=') for line in text.splitlines() if line.startswith('HARBOR_STARTUP_JSON=')]
    require(len(rows)==1,'missing or ambiguous startup observation')
    return json.loads(rows[0])


def run_runtime(s,output,*,root=ROOT,timeout_seconds=900):
    """Fresh bounded execution. No external command/callback or receipt is accepted."""
    require(isinstance(s,RuntimeSelection),'root-selected runtime inputs required')
    require(0<timeout_seconds<=900,'runtime cap must not exceed 900 seconds')
    _inputs(s,root);output=Path(output).resolve()
    require(not output.exists() and not output.is_relative_to(s.stage.resolve()) and (s.mcp is None or not output.is_relative_to(s.mcp.resolve())), 'runtime output must be fresh and outside inputs')
    output.mkdir(parents=True);original=s;s=_snapshot_inputs(s,output);_inputs(s,root);deadline=time.monotonic()+timeout_seconds
    docker=shutil.which('docker');require(docker is not None,'Docker executable unavailable')
    command=lambda label,args:_command(args,run=output,label=label,deadline=deadline,root=root)
    container=None;image_ids=[];result={'schema_version':'harbor-runtime-execution/v1','valid':False,'workbook':s.workbook,'stage_tree':s.stage_tree,'code':s.code,'run_id':uuid.uuid4().hex,'started_at_ns':time.time_ns(),'answer_key_smoke_only':True,'solve_rollouts':0}
    try:
        def build(label,context):
            iid=output/(label+'.iid');command(label,[docker,'build','--iidfile',iid,context]);image=_image(iid.read_text().strip());image_ids.append(image)
            inspected=json.loads(command(label+'-inspect',[docker,'image','inspect',image]));require(len(inspected)==1 and inspected[0]['Id']==image,'built image changed');return image
        if s.mode=='mcp':
            sidecar=build('mcp-build',s.stage/'environment/mcp-server')
            name='harbor-readiness-'+result['run_id']
            # Durable intent precedes the detached launch, even if its CLI dies.
            _save(output/'launch-intent.json',{'schema_version':'harbor-runtime-launch/v1','run_id':result['run_id'],'container_name':name,'image':sidecar,'docker':docker,'producer_pid':os.getpid(),'created_at_ns':time.time_ns()})
            container=name
            cid=command('mcp-start',[docker,'run','-d','--name',name,'--memory',str(MEMORY),'--pids-limit','512','-p','127.0.0.1::8000',sidecar])
            require(re.fullmatch('[0-9a-f]{64}',cid) is not None,'invalid launched container ID')
            inspected=_container(command('mcp-inspect',[docker,'inspect',cid]),cid,sidecar)
            ports=inspected['NetworkSettings']['Ports']['8000/tcp'];require(len(ports)==1 and ports[0]['HostIp']=='127.0.0.1','MCP port is not exclusively loopback')
            port=int(ports[0]['HostPort']);require(1<=port<=65535,'invalid port');url=f'http://127.0.0.1:{port}/mcp'
        main=build('main-build',s.stage/'environment')
        app_files={delivered_workbook(s.stage,s.workbook).name:record(delivered_workbook(s.stage,s.workbook))['sha256']}
        notes=s.stage/'environment/additional-assumptions.md'
        if notes.exists():app_files[notes.name]=record(notes)['sha256']
        probe="import hashlib,json;from pathlib import Path;import openpyxl;expected=json.loads("+repr(json.dumps(app_files))+");actual={n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in expected};assert actual==expected;b=openpyxl.load_workbook('/app/"+delivered_workbook(s.stage,s.workbook).name+"',data_only=False);assert b.sheetnames;assert not any(c.data_type=='f' for w in b for row in w for c in row);b.close();print('HARBOR_STARTUP_JSON='+json.dumps(actual,sort_keys=True))"
        startup=command('main-startup',[docker,'run','--rm','--network','none','--memory',str(MEMORY),'--pids-limit','512',main,'python','-B','-c',probe])
        require(_startup_files(startup)==app_files,'startup file identity mismatch')
        workspace=output/'workspace';workspace.mkdir();reward=output/'reward';reward.mkdir()
        key=read(record(s.stage/'tests/answer_key.json'));require(isinstance(key.get('targets'),dict) and bool(key['targets']),'nonempty grader targets required')
        _save(workspace/'answers.json',key['targets'])
        args=[docker,'run','--rm','--network','none','--memory',str(MEMORY),'--pids-limit','512','--mount',f'type=bind,src={s.stage / "tests"},dst=/tests,readonly','--mount',f'type=bind,src={workspace},dst=/workspace,readonly','--mount',f'type=bind,src={reward},dst=/reward',main,'python','-B','/tests/run_grader.py','--workspace','/workspace','--answer-key','/tests/answer_key.json','--output-dir','/reward','--mode','discrete']
        def grader_job():
            command('grader',args)
            score=read(record(reward/'reward.json'));details=read(record(reward/'reward-details.json'));n=len(key['targets']);meta=details.get('metadata',{})
            require(type(score.get('score')) in (int,float) and score['score']==1.0 and type(details.get('score')) in (int,float) and details['score']==1.0,'grader score is not 1.0')
            require(meta.get('passed') is True and all(type(meta.get(k)) is int and meta[k]==n for k in ('n_targets','n_answered','n_exact')),'grader exact target coverage incomplete')
        def oracle_job():
            oracle_args=[sys.executable,'-B',root/'xl_mcp_oracle.py','--bundle',s.stage,'--mcp',s.mcp,'--url',url,'--report',output/'oracle.json']
            if s.allowlist:oracle_args+=['--allowlist',s.allowlist['path']]
            command('oracle',oracle_args)
            return _oracle(read(record(output/'oracle.json')),s,url)
        if s.mode=='mcp':
            with ThreadPoolExecutor(max_workers=2) as pool:
                oracle_future=pool.submit(oracle_job);grader_future=pool.submit(grader_job)
                count=oracle_future.result();grader_future.result()
            _container(command('mcp-inspect-after',[docker,'inspect',cid]),cid,sidecar)
            result['mcp']={'image':sidecar,'container':cid,'url':url,'exact_retrievals':count,'oracle':record(output/'oracle.json')}
        else:grader_job()
        _inputs(s,root);_inputs(original,root)
        result.update(valid=True,main_image=main,startup_files=app_files,grader=record(reward/'reward.json'),grader_details=record(reward/'reward-details.json'))
    except BaseException as exc:
        result['failure_type']=type(exc).__name__;raise
    finally:
        if container:
            try:
                # Cleanup gets a separate small bound even after the work deadline.
                _command([docker,'rm','-f',container],run=output,label='mcp-cleanup',deadline=time.monotonic()+30,root=root)
            except BaseException as exc:
                result.update(valid=False,cleanup_failure=type(exc).__name__)
        result['completed_at_ns']=time.time_ns();result['artifacts']={str(p.relative_to(output)):record(p)['sha256'] for p in output.rglob('*') if p.is_file()}
        _save(output/'execution.json',result)
    require(result['valid'],'runtime cleanup or validation failed')
    return result


@dataclass(frozen=True)
class RuntimeObservation:
    """Exact producer output captured by root, never nominated by a task agent."""
    execution: dict
    run_id: str


def validate_runtime(s,observation,*,root=ROOT,producer_identity=None):
    """Read-only recheck; explicit root-bound identity replays producer argv.

    The default still requires this host's Python and Docker locators. Explicit
    identity never probes or executes old tools and changes no evidence checks.
    """
    require(isinstance(observation,RuntimeObservation),'root-captured observation required')
    _inputs(s,root);report=read(observation.execution);run=Path(observation.execution['path']).parent
    require(Path(observation.execution['path']).name=='execution.json','unexpected runtime observation path')
    original=s;s=_snapshot_selection(s,run);_inputs(s,root)
    require(report.get('schema_version')=='harbor-runtime-execution/v1' and report.get('run_id')==observation.run_id and re.fullmatch('[0-9a-f]{32}',observation.run_id),'runtime observation identity differs')
    require(report.get('valid') is True and not report.get('cleanup_failure') and not report.get('failure_type'),'runtime execution did not complete')
    require(report.get('stage_tree')==s.stage_tree and report.get('code')==s.code and report.get('workbook')==s.workbook,'runtime context changed')
    actual=tree(run);actual.pop('execution.json',None);require(actual==report.get('artifacts'),'runtime evidence changed or incomplete')
    require(type(report.get('started_at_ns')) is int and type(report.get('completed_at_ns')) is int and 0<report['started_at_ns']<=report['completed_at_ns'],'runtime chronology invalid')
    identity=capture_runtime_producer_identity() if producer_identity is None else producer_identity
    require(type(identity) is RuntimeProducerIdentity,'typed runtime producer identity required')
    RuntimeProducerIdentity.__post_init__(identity)
    docker=identity.docker_executable
    evidence=[observation.execution]
    def command(label,prefix):
        binding=record(run/(label+'.command.json'));c=read(binding);evidence.append(binding)
        require(type(c.get('returncode')) is int and c['returncode']==0 and c.get('argv')==[str(x) for x in prefix],'runtime command differs: '+label)
        require(c.get('log')==record(run/(label+'.log')),'runtime command log binding differs');evidence.append(c['log'])
        return Path(c['log']['path']).read_text().strip()
    def image(label,context):
        iid=_image((run/(label+'.iid')).read_text().strip())
        command(label,[docker,'build','--iidfile',run/(label+'.iid'),context])
        inspect=json.loads(command(label+'-inspect',[docker,'image','inspect',iid]));require(len(inspect)==1 and inspect[0]['Id']==iid,'runtime build image drift');return iid
    images={}
    if s.mode=='mcp':
        sidecar=image('mcp-build',s.stage/'environment/mcp-server');images['mcp']=sidecar
        start=read(record(run/'mcp-start.command.json'))['argv'];require(len(start)==12 and start[:5]==[docker,'run','-d','--name',start[4]],'invalid sidecar launch')
        name=start[4];require(name=='harbor-readiness-'+observation.run_id,'invalid sidecar identity')
        intent=read(record(run/'launch-intent.json'))
        require(intent.get('schema_version')=='harbor-runtime-launch/v1' and intent.get('run_id')==observation.run_id and intent.get('container_name')==name and intent.get('image')==sidecar and intent.get('docker')==docker,'detached launch intent differs')
        cid=command('mcp-start',[docker,'run','-d','--name',name,'--memory',str(MEMORY),'--pids-limit','512','-p','127.0.0.1::8000',sidecar])
        require(re.fullmatch('[0-9a-f]{64}',cid),'invalid sidecar container ID')
        current=_container(command('mcp-inspect',[docker,'inspect',cid]),cid,sidecar)
        ports=current['NetworkSettings']['Ports']['8000/tcp'];require(len(ports)==1 and ports[0]['HostIp']=='127.0.0.1','nonloopback live retrieval')
        port=int(ports[0]['HostPort']);require(1<=port<=65535,'invalid live port');url=f'http://127.0.0.1:{port}/mcp'
        args=[identity.python_executable,'-B',root/'xl_mcp_oracle.py','--bundle',s.stage,'--mcp',s.mcp,'--url',url,'--report',run/'oracle.json']
        if s.allowlist:args+=['--allowlist',s.allowlist['path']]
        command('oracle',args);n=_oracle(read(record(run/'oracle.json')),s,url)
        _container(command('mcp-inspect-after',[docker,'inspect',cid]),cid,sidecar)
        command('mcp-cleanup',[docker,'rm','-f',name]);require(report['mcp']['exact_retrievals']==n and report['mcp']['image']==sidecar and report['mcp']['container']==cid,'runtime MCP summary inconsistent')
    main=image('main-build',s.stage/'environment');images['main']=main
    files={delivered_workbook(s.stage,s.workbook).name:record(delivered_workbook(s.stage,s.workbook))['sha256']}
    notes=s.stage/'environment/additional-assumptions.md'
    if notes.exists():files[notes.name]=record(notes)['sha256']
    probe="import hashlib,json;from pathlib import Path;import openpyxl;expected=json.loads("+repr(json.dumps(files))+");actual={n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in expected};assert actual==expected;b=openpyxl.load_workbook('/app/"+delivered_workbook(s.stage,s.workbook).name+"',data_only=False);assert b.sheetnames;assert not any(c.data_type=='f' for w in b for row in w for c in row);b.close();print('HARBOR_STARTUP_JSON='+json.dumps(actual,sort_keys=True))"
    startup=command('main-startup',[docker,'run','--rm','--network','none','--memory',str(MEMORY),'--pids-limit','512',main,'python','-B','-c',probe]);require(_startup_files(startup)==files,'startup observed different files')
    args=[docker,'run','--rm','--network','none','--memory',str(MEMORY),'--pids-limit','512','--mount',f'type=bind,src={s.stage / "tests"},dst=/tests,readonly','--mount',f'type=bind,src={run / "workspace"},dst=/workspace,readonly','--mount',f'type=bind,src={run / "reward"},dst=/reward',main,'python','-B','/tests/run_grader.py','--workspace','/workspace','--answer-key','/tests/answer_key.json','--output-dir','/reward','--mode','discrete']
    command('grader',args)
    key=read(record(s.stage/'tests/answer_key.json'));targets=key.get('targets');require(isinstance(targets,dict) and targets and read(record(run/'workspace/answers.json'))==targets,'grader smoke inputs differ')
    score=read(record(run/'reward/reward.json'));details=read(record(run/'reward/reward-details.json'));n=len(targets);meta=details.get('metadata',{})
    require(all(type(v) in (int,float) and v==1.0 for v in (score.get('score'),details.get('score'))),'grader score differs from 1.0')
    require(meta.get('passed') is True and all(type(meta.get(k)) is int and meta[k]==n for k in ('n_targets','n_answered','n_exact')),'grader target coverage incomplete')
    require(report.get('main_image')==main and report.get('startup_files')==files,'startup image summary inconsistent')
    _inputs(s,root);_inputs(original,root)
    return {'valid':True,'images':images,'exact_grader_targets':n,'answer_key_smoke_only':True,'evidence':evidence+[record(run/p) for p in actual]}


def reconcile_interrupted_runtime(intent_binding,run_id,*,root=ROOT):
    """Coordinator recovery after producer exit, under its original runtime lease.

    The ordinary-user coordinator selects the retained fresh run directory. This
    performs exact-name/image cleanup only and can never qualify the interrupted
    run. Unknown/alive producer or mismatched identity fails without deletion.
    """
    intent=read(intent_binding);run=Path(intent_binding['path']).parent
    require(Path(intent_binding['path']).name=='launch-intent.json','unexpected launch intent path')
    require(re.fullmatch('[0-9a-f]{32}',run_id) and intent.get('schema_version')=='harbor-runtime-launch/v1' and intent.get('run_id')==run_id,'invalid interrupted run identity')
    name='harbor-readiness-'+run_id
    require(intent.get('container_name')==name,'interrupted container name changed')
    image=_image(intent.get('image',''));docker=shutil.which('docker')
    require(docker is not None and intent.get('docker')==docker,'interrupted Docker executable changed')
    pid=intent.get('producer_pid');require(type(pid) is int and pid>0,'invalid producer PID')
    try:os.kill(pid,0)
    except ProcessLookupError:pass
    else:raise ValueError('producer alive or its PID reused; coordinator must resolve before cleanup')
    attempt=run/('recovery-'+uuid.uuid4().hex);attempt.mkdir();deadline=time.monotonic()+30
    result={'schema_version':'harbor-runtime-interruption-recovery/v1','run_id':run_id,'launch_intent':intent_binding,'valid':False,'reconciled':False}
    try:
        raw=_command([docker,'container','ls','--all','--no-trunc','--filter','name=^/'+name+'$','--format','{{json .}}'],run=attempt,label='list',deadline=deadline,root=root)
        entries=[json.loads(line) for line in raw.splitlines() if line.strip()]
        require(len(entries)<=1,'ambiguous interrupted container')
        if entries:
            entry=entries[0];cid=entry.get('ID','')
            require(entry.get('Names')==name and re.fullmatch('[0-9a-f]{64}',cid),'interrupted container identity differs')
            inspected=json.loads(_command([docker,'inspect',cid],run=attempt,label='inspect',deadline=deadline,root=root))
            require(len(inspected)==1 and inspected[0].get('Id')==cid and inspected[0].get('Name')=='/'+name and inspected[0].get('Image')==image,'interrupted container image/name differs')
            _command([docker,'rm','-f',cid],run=attempt,label='remove',deadline=deadline,root=root)
            result['removed_container']=cid
        result['reconciled']=True
    except BaseException as exc:
        result['failure_type']=type(exc).__name__;raise
    finally:
        result['completed_at_ns']=time.time_ns();result['artifacts']=tree(attempt)
        _save(attempt/'recovery.json',result)
    return result
