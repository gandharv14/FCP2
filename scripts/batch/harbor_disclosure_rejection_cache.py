"""Reuse a fully checked historical rejection against exact unchanged inputs.

The cold proof runs the original outer and substantive rejection validators.
Only completed custody is read at its authenticated historical admission time.
Saved proofs grant neither current custody, review approval, nor a new attempt.
"""
from contextlib import contextmanager
from pathlib import Path
from types import CodeType,FunctionType,SimpleNamespace
import argparse,ast,hashlib,importlib.util,inspect,json,os,signal,subprocess,sys,textwrap

SCHEMA='harbor-completed-rejection-proof/v1'
REGISTRY='harbor-completed-rejection-cache/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path)
    require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)), 'canonical rejection proof input required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'rejection proof input changed')
    return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'rejection proof implementation changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m

def function_sha(fn):
    return hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(fn))),include_attributes=False).encode()).hexdigest()

def original_cold_seconds(reader):
    tree=ast.parse(textwrap.dedent(inspect.getsource(reader.closed_rejection)))
    calls=[n for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)
        and isinstance(n.func.value,ast.Name)and n.func.value.id=='subprocess'and n.func.attr=='run']
    require(len(calls)==1,'one original cold process required')
    values=[x.value for x in calls[0].keywords if x.arg=='timeout'];require(len(values)==1,'original cold duration required')
    node=values[0]
    value=node.value if isinstance(node,ast.Constant)else getattr(reader,node.id,None)if isinstance(node,ast.Name)else None
    require(type(value)is int and 1<=value<=1800,'unsupported original cold duration');return value

@contextmanager
def original_cold_deadline(seconds):
    previous=signal.getsignal(signal.SIGALRM)
    require(signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0),'unexpected nested historical deadline')
    def expired(*args):raise TimeoutError('original cold rejection deadline exceeded')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)

@contextmanager
def presence_witness():
    """Capture negative predicates too; a newly created attempt invalidates reuse."""
    names=('exists','is_file','is_dir','is_symlink');original={n:getattr(Path,n)for n in names};seen={}
    def wrap(name):
        def predicate(path,*args,**kwargs):
            value=original[name](path,*args,**kwargs)
            # These calls have no options in the pinned historical readers.
            require(not args and not kwargs,'unrecognized historical path predicate')
            key=(str(path.absolute()),name)
            require(key not in seen or seen[key]==value,'path topology changed during historical proof')
            seen[key]=value;return value
        return predicate
    try:
        for name in names:setattr(Path,name,wrap(name))
        yield lambda:[dict(path=p,predicate=n,value=v)for(p,n),v in sorted(seen.items())]
    finally:
        for name,fn in original.items():setattr(Path,name,fn)

def validate_witness(value):
    require(value['inputs']and value['input_directories']and value['path_predicates'],'complete original read witness required')
    for row in value['inputs']:require(binding(row['path'])==row,'cached rejection bytes changed')
    for row in value['input_directories']:
        require(set(row)=={'path','members'}and Path(row['path']).is_absolute()
            and not any(x.is_symlink()for x in(Path(row['path']),*Path(row['path']).parents))
            and sorted(os.listdir(row['path']))==row['members'],'cached rejection directory changed')
    for row in value['path_predicates']:
        require(set(row)=={'path','predicate','value'}and Path(row['path']).is_absolute()
            and row['predicate']in {'exists','is_file','is_dir','is_symlink'}and type(row['value'])is bool
            and getattr(Path(row['path']),row['predicate'])()==row['value'],'cached rejection topology changed')

def selected(row):
    p=read(row)
    require(set(p)=={'schema_version','helper','rejection','custody','parents','reader','functions','scope','ordinal','python','guard'}
        and p['schema_version']==SCHEMA and p['helper']==binding(__file__), 'closed original rejection proof required')
    old=read(p['rejection']);run=read(old['run_plan'])
    require(old['implementation']==p['reader']and old['round']==p['ordinal']
        and p['scope']==dict(workbook_id=run['workbook'],original_sha256=run['source']['sha256'],lane='harbor-disclosure-writer',budget_id='disclosure-review')
        and set(p['custody'])=={'helper','profile'}and read(p['custody']['profile'])['run_plan']==old['run_plan']
        and set(p['functions'])=={'closed_rejection','_cold_validate'},'historical rejection source or custody differs')
    for key in ('reader','guard'):require(binding(p[key]['path'])==p[key],'historical proof dependency changed')
    return p,old,run

def cached(profile,receipt,rejection,scope,ordinal):
    """Accept only an actual successful guarded cold proof, then recheck its inputs."""
    p,_,_=selected(profile)
    require((p['rejection'],p['scope'],p['ordinal'])==(rejection,scope,ordinal),'cached rejection belongs to another consumed round')
    require(set(receipt)=={'resource','inspection'},'closed native rejection proof receipt required')
    resource=read(receipt['resource']);value=read(receipt['inspection'])
    require(resource['inspection']==receipt['inspection']and resource['returncode']==0 and resource['failure_type']is None
        and resource['models']==resource['claims']==0 and resource['memory_limit_bytes']==8*1024**3
        and resource['timeout_seconds']==1800 and 0<resource['elapsed_seconds']<1800
        and 0<resource['peak_rss_bytes']<=8*1024**3
        and resource['command']==[p['python'],'-I','-B',p['helper']['path'],'probe','--plan',profile['path'],'--sha256',profile['sha256']],
        'rejection proof did not complete its exact native guard')
    home=Path(receipt['resource']['path']).parent
    require(Path(receipt['inspection']['path']).parent==home and Path(resource['log']['path'])==home/'probe.log'
        and read(resource['log'])==value,'rejection proof detached from actual guard output')
    require(value['schema_version']==SCHEMA and value['plan']==profile and value['rejection']==rejection
        and value['scope']==scope and value['ordinal']==ordinal and value['completed_rejection']is True
        and value['models_dispatched']==value['task_mutations']==0
        and value['semantic_approval']is value['current_custody']is False,'cache is not an original completed rejection proof')
    validate_witness(value);return True

@contextmanager
def installed(Q,row):
    p=read(row)
    require(set(p)=={'schema_version','helper','entries'}and p['schema_version']==REGISTRY
        and p['helper']==binding(__file__)and type(p['entries'])is list and p['entries'],'closed historical rejection registry required')
    entries={}
    for entry in p['entries']:
        require(set(entry)=={'rejection','profile','receipt'},'closed historical rejection entry required')
        key=json.dumps(entry['rejection'],sort_keys=True)
        require(key not in entries,'duplicate historical rejection cache entry');entries[key]=entry
    original=Q.closed_rejection
    def check(rejection,scope,ordinal):
        entry=entries.get(json.dumps(rejection,sort_keys=True))
        if entry is None:return original(rejection,scope,ordinal)
        return cached(entry['profile'],entry['receipt'],rejection,scope,ordinal)
    Q.closed_rejection=check
    try:yield
    finally:Q.closed_rejection=original

@contextmanager
def archived_round_validation(Q,run,worker,archive):
    """The old producer-location check also needs its original tracker input."""
    require(binding(Q.__file__)==run['modules']['rounds'],'original round validator changed')
    require(binding(archive['path'])==archive,'archived producer tracker changed')
    outer=Q.validate;fn=outer;cells=None
    unmodified_reserved='round_reader'in run and run['round_reader'].get('profile')is None
    if unmodified_reserved:
        chosen=run['round_reader'];stopped=read(run['reserved_round'])
        require(set(chosen)=={'runtime','profile'}and binding(chosen['runtime']['path'])==chosen['runtime']
            and stopped['schema_version']=='fleet-disclosure-interrupted-reservation/v1'
            and read(stopped['plan'])['modules']['rounds']==run['modules']['rounds'],
            'unmodified reserved reader detached from interrupted authority')
    if 'round_reader'in run and not unmodified_reserved:
        chosen=run['round_reader'];value=read(chosen['profile'])
        require(binding(outer.__code__.co_filename)==chosen['runtime']and outer.__name__=='checked',
            'original reserved producer wrapper changed')
        cells=dict(zip(outer.__code__.co_freevars,(c.cell_contents for c in outer.__closure__ or ())))
        require(set(cells)=={'actual','original','selected','value'}and cells['original']is Q
            and cells['value']==value and value['original']==binding(Q.__file__)
            and value['selected']==binding(cells['selected'].__file__), 'reserved producer closure detached')
        fn=cells['actual'];declared=cells['selected'].validate
        require(fn.__code__.co_filename==value['selected']['path']and fn.__globals__['__file__']==Q.__file__,
            'reserved producer function detached')
    else:declared=fn
    tree=ast.parse(textwrap.dedent(inspect.getsource(declared)));count=0
    def signature(code):
        return (code.co_code,code.co_names,code.co_varnames,code.co_argcount,code.co_posonlyargcount,
            code.co_kwonlyargcount,code.co_flags,tuple(signature(x)if isinstance(x,CodeType)else x for x in code.co_consts))
    compilation=tree
    if cells is None:
        require(fn.__code__.co_filename==Q.__file__,'original producer validator detached')
        compilation=ast.parse(Path(Q.__file__).read_text())
        nodes=[n for n in compilation.body if isinstance(n,ast.FunctionDef)and n.name==fn.__name__]
        require(len(nodes)==1 and ast.dump(nodes[0])==ast.dump(tree.body[0]),'original producer module differs')
    compiled=compile(compilation,fn.__code__.co_filename,'exec')
    functions=[x for x in compiled.co_consts if isinstance(x,CodeType)and x.co_name==fn.__name__]
    require(len(functions)==1 and signature(functions[0])==signature(fn.__code__), 'historical producer source differs from installed function')
    target=ast.dump(ast.parse("record(worker.context.tracker_root/(inputs['workbook']+'.json'))",mode='eval').body,include_attributes=False)
    class Select(ast.NodeTransformer):
        def visit_Call(self,node):
            nonlocal count
            if ast.dump(node,include_attributes=False)==target:
                count+=1;return ast.copy_location(ast.parse('_archived_tracker(worker,inputs)',mode='eval').body,node)
            return self.generic_visit(node)
    tree=Select().visit(tree)
    if unmodified_reserved and count==0:
        # This original reader derives the producer from immutable worker metadata.
        # It has no current tracker operand to project; keep every original check.
        imports=[n for n in ast.walk(tree)if isinstance(n,ast.ImportFrom)and n.module=='scripts.batch.harbor_disclosure_command_repair'and [a.name for a in n.names]==['producer_run']]
        calls=[n for n in ast.walk(tree)if isinstance(n,ast.Call)and ast.dump(n,include_attributes=False)==ast.dump(ast.parse('producer_run(worker)',mode='eval').body,include_attributes=False)]
        require(len(imports)==len(calls)==1,'original immutable producer lookup changed')
        yield
        return
    require(count==1,'original producer tracker boundary changed')
    def tracker(selected,inputs):
        require(selected==worker and inputs['workbook']==run['workbook']and binding(archive['path'])==archive,
            'foreign producer requested historical tracker')
        return archive
    scope=dict(fn.__globals__,_archived_tracker=tracker)
    if cells is None:compilation.body[compilation.body.index(nodes[0])]=tree.body[0]
    else:compilation=tree
    code=compile(ast.fix_missing_locations(compilation),fn.__code__.co_filename,'exec')
    functions=[x for x in code.co_consts if isinstance(x,CodeType)and x.co_name==fn.__name__]
    require(len(functions)==1,'one historical producer function required')
    projected=FunctionType(functions[0],scope,fn.__name__,fn.__defaults__,fn.__closure__)
    projected.__kwdefaults__=fn.__kwdefaults__
    if cells is None:Q.validate=projected
    else:
        def cell(value):return (lambda:value).__closure__[0]
        closure=tuple(cell(projected)if name=='actual'else old for name,old in zip(outer.__code__.co_freevars,outer.__closure__))
        Q.validate=FunctionType(outer.__code__,outer.__globals__,outer.__name__,outer.__defaults__,closure)
        Q.validate.__kwdefaults__=outer.__kwdefaults__
    try:yield
    finally:Q.validate=outer

def cold(row):
    """Select the original context expression; every substantive check remains."""
    p,old,run=selected(row)
    require(sys.platform=='linux'and sys.flags.isolated and sys.dont_write_bytecode
        and Path(sys.executable).resolve()==Path(p['python']).resolve(),'isolated original Linux interpreter required')
    sys.path.insert(0,read(run['code_snapshots'][0])['root'])
    guard=module('rejection_read_only_guard',p['guard']);verified=guard.install_guard()
    custody=read(p['custody']['profile']);tracker_path=read(custody['first_admission'])['tracker']['path']
    def no_live_tracker(event,args):
        if event=='open'and isinstance(args[0],(str,bytes,os.PathLike)):
            require(os.fsdecode(args[0])!=tracker_path,'historical proof attempted a live tracker read')
    sys.addaudithook(no_live_tracker)
    with presence_witness()as predicates:
        p,old,run=selected(row)
        reader=module('original_complete_rejection',p['reader'])
        for name,sha in p['functions'].items():require(function_sha(getattr(reader,name))==sha,'original rejection function changed')
        H=module('original_archived_review_custody',p['custody']['helper'])
        @contextmanager
        def archived(runner):
            with H.context(runner,p['custody']['profile'])as value:
                custody=H.read(p['custody']['profile']);Q=value[-1]['rounds']
                with archived_round_validation(Q,run,value[3],custody['tracker_archive']):
                    if p['parents'] is None:yield value
                    else:
                        with installed(Q,p['parents']):yield value
        fn=reader._cold_validate;tree=ast.parse(textwrap.dedent(inspect.getsource(fn)));count=0
        target=ast.dump(ast.parse("runner.context(value['run_plan'])",mode='eval').body,include_attributes=False)
        class Select(ast.NodeTransformer):
            def visit_Call(self,node):
                nonlocal count
                if ast.dump(node,include_attributes=False)==target:
                    count+=1;return ast.copy_location(ast.parse('_archived(runner)',mode='eval').body,node)
                return self.generic_visit(node)
        tree=Select().visit(tree);require(count==1,'original cold context boundary changed')
        scope=dict(fn.__globals__,_archived=archived)
        exec(compile(ast.fix_missing_locations(tree),fn.__code__.co_filename,'exec'),scope)
        cold_validator=scope[fn.__name__];invocations=[]
        def dispatch(argv,**kwargs):
            require(argv[:4]==[sys.executable,'-I','-B','-c']and len(argv)==5
                and set(kwargs)=={'input','text','capture_output','timeout'}and kwargs['text']is kwargs['capture_output']is True
                and kwargs['timeout']==original_cold_seconds(reader),'original cold dispatcher changed')
            payload=json.loads(kwargs['input'])
            require(payload==dict(root=read(run['code_snapshots'][0])['root'],reader=p['reader'],binding=p['rejection'])
                and not invocations,'foreign or repeated cold rejection requested')
            invocations.append(True)
            with original_cold_deadline(kwargs['timeout']):
                require(cold_validator(p['rejection'])is True,'original substantive rejection failed')
            return SimpleNamespace(returncode=0,stderr='',stdout=json.dumps(dict(binding=p['rejection'],completed_rejection=True,models_dispatched=0)))
        fn=reader.closed_rejection;scope=dict(fn.__globals__,subprocess=SimpleNamespace(run=dispatch))
        original=FunctionType(fn.__code__,scope,fn.__name__,fn.__defaults__,fn.__closure__)
        require(original(p['rejection'],p['scope'],p['ordinal'])is True and invocations==[True], 'full original rejected history did not complete')
        inputs=verified();directories=verified.directories();presence=predicates()
    value=dict(schema_version=SCHEMA,plan=row,rejection=p['rejection'],scope=p['scope'],ordinal=p['ordinal'],
        completed_rejection=True,semantic_approval=False,current_custody=False,models_dispatched=0,task_mutations=0,
        inputs=inputs,input_directories=directories,path_predicates=presence)
    validate_witness(value);return value

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['probe']);parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True)
    a=parser.parse_args();print(json.dumps(cold(dict(path=a.plan,sha256=a.sha256))))
