"""Read an unchanged historical dialogue consumer in a private archive view.

Only the declared task and tracker are bound, read-only, inside a new Linux
mount namespace. The consumer runs as the original user with a write/network
guard. The live owning worker never enters this namespace.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,pwd,stat,subprocess,sys

SCHEMA='fleet-retained-dialogue-archive-reader/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path)
    require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical archive evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'archive evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def tree(root):
    root=Path(root);require(root.is_dir()and not any(x.is_symlink()for x in(root,*root.parents)),'canonical archive directory required')
    rows={}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(),'archive contains symlink')
        if path.is_file():rows[str(path.relative_to(root))]=binding(path)['sha256']
        else:require(path.is_dir(),'archive contains special file')
    return rows

def load(bound):
    p=read(bound)
    require(set(p)=={'schema_version','helper','archive','selection','context_helper','python','uid','gid','binaries'}
        and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed historical archive reader required')
    archive=read(p['archive']);selected=read(p['selection']);rejected=read(selected['rejection'])
    require(archive['schema_version']=='fleet-retained-dialogue-input-archive/v1'
        and archive['model_calls']==archive['task_mutations']==0 and archive['publication']is False
        and archive['original_intent']==rejected['original_intent']
        and archive['request']['rejection']==selected['rejection']and archive['request']['source']==selected['source'],
        'archive detached from exact original failed consumer')
    require(binding(selected['source']['path'])==selected['source']and binding(p['context_helper']['path'])==p['context_helper'],
        'historical source or consumer changed')
    source=Path(archive['stage']['path']);copy=Path(archive['stage']['archive']);tracker=archive['tracker']
    intent=read(rejected['original_intent'])
    require(intent['selection']['value']['content']['value']['stage']['value']==str(source)==archive['request']['stage'],
        'historical stage is not original failed consumer input')
    require(source.name==copy.name==selected['workbook']+'-outputs'and source.is_absolute()and copy.is_absolute()
        and source!=copy and not source.is_relative_to(copy)and not copy.is_relative_to(source)
        and copy.parent==Path(archive['request']['output'])
        and archive['stage']['tree']==tree(copy),'historical task archive differs')
    descriptor=read(rejected['descriptor']);original_tracker=Path(descriptor['worker']['context']['tracker_root'])/(selected['workbook']+'.json')
    require(tracker['original']['path']==str(original_tracker)==archive['request']['tracker']
        and Path(tracker['archive']['path'])==copy.parent/'tracker.json'
        and tracker['archive']['sha256']==tracker['original']['sha256']and binding(tracker['archive']['path'])==tracker['archive'],
        'historical tracker archive differs')
    before=read(tracker['archive']);require(before['workbook_id']==selected['workbook']and before['bindings']['original_raw_source']==selected['source'],'historical tracker source differs')
    for path in(source,original_tracker):
        require(path.exists()and not any(x.is_symlink()for x in(path,*path.parents)),'historical mount target changed topology')
    require(type(p['uid'])is int and p['uid']>0 and type(p['gid'])is int and p['gid']>0
        and copy.stat().st_uid==p['uid']and Path(tracker['archive']['path']).stat().st_uid==p['uid'],'original archive user required')
    require(set(p['binaries'])=={'sudo','unshare','mount'},'closed native namespace tools required')
    for name,row in p['binaries'].items():require(binding(row['path'])==row and Path(row['path']).name==name,'namespace binary changed')
    require(Path(p['python']).is_file()and Path(p['python']).name=='python'
        and Path(p['python']).resolve()==Path(sys.executable).resolve(),'selected Python interpreter required')
    return p,archive

def install_guard():
    """Reject writes; existing empty executor locks are opened read-only."""
    original_open=os.open;inputs={};directories={};busy=False
    def locked_open(path,flags,mode=0o777,*,dir_fd=None):
        if flags&os.O_ACCMODE==os.O_RDWR:
            p=Path(path)
            require(dir_fd is None and p.name=='scope.lock'and p.is_file()and p.stat().st_size==0
                and not(flags&(os.O_CREAT|os.O_TRUNC|os.O_APPEND)),'historical read requested writable descriptor')
            flags=(flags&~os.O_ACCMODE)|os.O_RDONLY
        return original_open(path,flags,mode,dir_fd=dir_fd)
    os.open=locked_open
    denied={'os.remove','os.rmdir','os.rename','os.link','os.symlink','os.chmod','os.chown','os.utime',
        'os.truncate','os.exec','os.posix_spawn','os.fork','os.system','subprocess.Popen','socket.connect','socket.bind'}
    def audit(event,args):
        nonlocal busy
        if busy:return
        if event in denied:raise ValueError('historical consumer requested forbidden effect')
        if event=='os.mkdir':
            require(Path(args[0]).is_dir(),'historical consumer requested new directory')
        if event in {'os.scandir','os.listdir'}and not isinstance(args[0],int):
            target=Path(os.fsdecode(args[0]or '.')).absolute()
            if target not in directories:
                busy=True
                try:directories[target]=sorted(os.listdir(target))
                finally:busy=False
        if event!='open':return
        path,mode,flags=args
        require(not(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)),'historical consumer requested write')
        if not isinstance(path,(str,bytes,os.PathLike)):return
        target=Path(os.fsdecode(path)).absolute()
        require(target.name not in {'.env','model.env','worker.token','admin.token'},'historical consumer requested credentials')
        if target in inputs or not target.is_file():return
        busy=True
        try:inputs[target]=hashlib.sha256(target.read_bytes()).hexdigest()
        finally:busy=False
    sys.addaudithook(audit)
    def verified_inputs():
        rows=[]
        for path,digest in list(inputs.items()):
            require(hashlib.sha256(path.read_bytes()).hexdigest()==digest,'historical input changed during validation')
            rows.append(dict(path=str(path),sha256=digest))
        return sorted(rows,key=lambda r:r['path'])
    def verified_directories():
        rows=[]
        for path,members in list(directories.items()):
            require(sorted(os.listdir(path))==members,'historical directory changed during validation')
            rows.append(dict(path=str(path),members=members))
        return sorted(rows,key=lambda r:r['path'])
    verified_inputs.directories=verified_directories
    return verified_inputs

def child(bound,parent_namespace):
    require(sys.platform=='linux'and os.geteuid()==0 and sys.flags.isolated and sys.dont_write_bytecode,'isolated native namespace setup required')
    p,a=load(bound);namespace=os.readlink('/proc/self/ns/mnt')
    require(namespace!=parent_namespace and os.readlink('/proc/%d/ns/mnt'%os.getppid())==parent_namespace,
        'historical reader must use a separate mount namespace from its actual parent')
    mount=p['binaries']['mount']['path'];pairs=[(a['stage']['archive'],a['stage']['path']),(a['tracker']['archive']['path'],a['tracker']['original']['path'])]
    for source,target in pairs:
        subprocess.run([mount,'--bind',source,target],check=True,capture_output=True)
        subprocess.run([mount,'-o','remount,bind,ro',target],check=True,capture_output=True)
        require(os.statvfs(target).f_flag&os.ST_RDONLY,'historical bind is not read-only')
    account=pwd.getpwuid(p['uid']);require(account.pw_gid==p['gid'],'original user group differs')
    os.setgroups([]);os.setgid(p['gid']);os.setuid(p['uid'])
    require(os.getuid()==os.geteuid()==p['uid']and os.getgid()==p['gid'],'historical user drop failed')
    # The unchanged consumer receives no fleet or model environment authority.
    os.environ.clear();os.environ.update(PATH='/usr/bin:/bin',HOME=account.pw_dir,PYTHONDONTWRITEBYTECODE='1',HARBOR_FLEET_CONTINUATION_READ_ONLY='1')
    import resource,signal
    resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
    signal.setitimer(signal.ITIMER_REAL,1740)
    verified=install_guard()
    spec=importlib.util.spec_from_file_location('_unchanged_historical_dialogue_consumer',p['context_helper']['path'])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    payload=module.cold_payload(p['selection'])
    require(tree(a['stage']['path'])==a['stage']['tree']and binding(a['tracker']['original']['path'])==a['tracker']['original'],'historical namespace bytes changed')
    return dict(schema_version=SCHEMA,plan=bound,payload=payload,inputs=verified(),input_directories=verified.directories(),mount_namespace=namespace,
        parent_namespace=parent_namespace,uid=os.getuid(),gid=os.getgid(),read_only_binds=pairs,
        models_dispatched=0,task_mutations=0,publication=False)

def run(bound):
    require(sys.platform=='linux'and os.geteuid()!=0,'original user must own historical archive execution')
    p,a=load(bound);require(os.getuid()==p['uid']and os.getgid()==p['gid'],'archive requested by different user')
    parent=os.readlink('/proc/self/ns/mnt');before=(tree(a['stage']['path']),binding(a['tracker']['original']['path']))
    command=[p['binaries']['sudo']['path'],'-n',p['binaries']['unshare']['path'],'--mount','--propagation','private','--',p['python'],'-I','-B',p['helper']['path'],'child','--plan',bound['path'],'--sha256',bound['sha256'],'--parent-namespace',parent]
    result=subprocess.run(command,capture_output=True,text=True,timeout=1800)
    require(os.readlink('/proc/self/ns/mnt')==parent and before==(tree(a['stage']['path']),binding(a['tracker']['original']['path'])),'live owning view changed during historical read')
    require(result.returncode==0,'historical archive consumer failed: '+result.stderr[-1800:])
    value=json.loads(result.stdout);require(value['plan']==bound and value['parent_namespace']==parent and value['mount_namespace']!=parent and value['uid']==p['uid'],'foreign historical namespace result')
    return value

def validate_cached(bound,value):
    """A process cache may reuse only unchanged bytes and directory membership."""
    p,a=load(bound)
    rejected=read(read(p['selection'])['rejection']);failed=Path(rejected['original_intent']['path']).parent
    require(not any((failed/name).exists()for name in ('complete.json','blocked.json')),'original failed consumer acquired a checkpoint')
    require(value['schema_version']==SCHEMA and value['plan']==bound and value['uid']==p['uid']and value['gid']==p['gid']
        and value['mount_namespace']!=value['parent_namespace']and value['models_dispatched']==value['task_mutations']==0
        and value['publication']is False and value['payload']['selection']==p['selection'], 'foreign historical archive cache')
    require(value['read_only_binds']==[[a['stage']['archive'],a['stage']['path']],[a['tracker']['archive']['path'],a['tracker']['original']['path']]],'historical cache archive view differs')
    def projected(path):
        path=Path(path);stage=Path(a['stage']['path'])
        if path==Path(a['tracker']['original']['path']):return Path(a['tracker']['archive']['path'])
        return Path(a['stage']['archive'])/path.relative_to(stage)if path.is_relative_to(stage)else path
    require(value['inputs']and value['input_directories'],'complete historical input witness required')
    for row in value['inputs']:
        require(set(row)=={'path','sha256'}and hashlib.sha256(projected(row['path']).read_bytes()).hexdigest()==row['sha256'],'cached historical bytes changed')
    for row in value['input_directories']:
        require(set(row)=={'path','members'}and sorted(os.listdir(projected(row['path'])))==row['members'],'cached historical directory changed')
    return value['payload']

def guarded_cache(bound,receipt):
    """Authenticate a completed Linux read before checking all recorded inputs."""
    p,_=load(bound)
    require(set(receipt)=={'resource','inspection'},'closed completed archive proof required')
    resource=read(receipt['resource']);value=read(receipt['inspection'])
    require(resource['inspection']==receipt['inspection']and resource['returncode']==0
        and resource['failure_type']is None and resource['models']==resource['claims']==0
        and resource['memory_limit_bytes']==8*1024**3 and resource['timeout_seconds']==1800
        and 0<resource['elapsed_seconds']<1800 and 0<resource['peak_rss_bytes']<=8*1024**3,
        'historical archive guard did not complete within its allowance')
    require(resource['command']==[p['python'],'-I','-B',p['helper']['path'],'inspect','--plan',bound['path'],'--sha256',bound['sha256']],
        'completed historical proof invoked another consumer')
    require(Path(receipt['inspection']['path']).parent==Path(receipt['resource']['path']).parent
        and Path(resource['log']['path'])==Path(receipt['resource']['path']).parent/'probe.log'
        and read(resource['log'])==value,'historical proof detached from actual guarded output')
    validate_cached(bound,value)
    return value


def context_consumer(original,bound,cache,receipt=None):
    """Replace only where the original cold payload runs, keeping its semantics."""
    from types import FunctionType,SimpleNamespace
    import ast
    p,_=load(bound);require(binding(original.__file__)==p['context_helper'],'unselected original historical context')
    tree=ast.parse(Path(original.__file__).read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='load')
    scripts=[n.value.value for n in ast.walk(fn)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='script'for t in n.targets)and isinstance(n.value,ast.Constant)and isinstance(n.value.value,str)]
    require(len(scripts)==1 and 'm.cold_payload(json.loads(sys.argv[2]))'in scripts[0],'original cold consumer invocation differs')
    def historical_run(argv,**kwargs):
        require(argv[:6]==[p['python'],'-I','-B','-c',scripts[0],original.__file__]and len(argv)==7
            and json.loads(argv[6])==p['selection']and set(kwargs)=={'capture_output','text','timeout','env'}
            and kwargs['capture_output']is True and kwargs['text']is True and kwargs['timeout']==1500
            and kwargs['env'].get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1'and 'HARBOR_FLEET_URL'not in kwargs['env'],
            'archive view requested for a different consumer or capability')
        key=json.dumps(bound,sort_keys=True)
        if key not in cache:cache[key]=guarded_cache(bound,receipt)if receipt is not None else run(bound)
        payload=validate_cached(bound,cache[key])
        return subprocess.CompletedProcess(argv,0,json.dumps(payload,default=str),'')
    values=dict(original.__dict__);values['subprocess']=SimpleNamespace(**{**vars(subprocess),'run':historical_run})
    old=original.load;selected=FunctionType(old.__code__,values,old.__name__,old.__defaults__,old.__closure__);selected.__kwdefaults__=old.__kwdefaults__;values['load']=selected
    return SimpleNamespace(**values)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['run','inspect','child']);parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--parent-namespace');args=parser.parse_args()
    bound=dict(path=args.plan,sha256=args.sha256)
    value=child(bound,args.parent_namespace)if args.action=='child'else run(bound)
    print(json.dumps(value,sort_keys=True,default=str))

if __name__=='__main__':main()
