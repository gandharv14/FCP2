"""Explicit runtime compatibility for immutable retained history consumers.

A profile names every original file and exact function AST. The only supported
rewrite substitutes a completed-prefix reader for a historical eligibility call.
The original authorization functions and files are never changed. Cold children
carry a separate physical/logical argv sidecar bound to the same profile.
"""
from pathlib import Path
from contextlib import contextmanager
import argparse,ast,hashlib,importlib.machinery,importlib.util,inspect,json,os,runpy,sys,textwrap
from types import ModuleType,FunctionType
SCHEMA='fleet-retained-runtime/v1'
SCHEMA2='fleet-retained-runtime/v2'
SCHEMA3='fleet-retained-runtime/v3'
SIDE='fleet-retained-runtime-child/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    path=Path(path).absolute()
    require(path.is_file() and not any(p.is_symlink() for p in (path,*path.parents)),'regular retained runtime file required')
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'retained runtime binding changed')
    return json.loads(Path(row['path']).read_bytes())

def source_hash(function):
    node=ast.parse(textwrap.dedent(inspect.getsource(function)))
    return hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()

def load_module(name,row):
    require(binding(row['path'])==row,'retained module changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

def profile(row):
    value=checked(row)
    fields={'schema_version','runtime','entrypoint','read_call','patches','guard_modules','child_plans','journal_roots','dependencies','symbols'}
    require((set(value)==fields and value['schema_version']==SCHEMA) or (set(value)==fields|{'factories'} and value['schema_version']==SCHEMA2) or (set(value)==fields|{'factories','historical_imports'} and value['schema_version']==SCHEMA3),'closed retained runtime profile required')
    require(value['runtime']==binding(__file__),'unselected retained runtime implementation')
    for item in [value['entrypoint'],*value['guard_modules'],*value['child_plans']]:require(binding(item['path'])==item,'retained dependency changed')
    require(0<len(value['patches'])+len(value['dependencies'])+len(value['symbols'])+len(value.get('factories',[]))+len(value.get('historical_imports',[]))<=32,'bounded runtime compatibility declarations required')
    for patch in value['patches']:
        require(set(patch)=={'module','function','function_sha256','owner_name','owning_module','reader','invocation'} and patch['function'] in {'context','load'},'only declared historical context readers may change')
        for item in (patch['module'],patch['owning_module'],patch['reader']):require(binding(item['path'])==item,'historical patch dependency changed')
        require(set(patch['invocation'])=={'args','kwargs'} and isinstance(patch['invocation']['args'],list) and isinstance(patch['invocation']['kwargs'],dict),'closed historical invocation required')
    for dependency in value['dependencies']:
        require(set(dependency)=={'module_name','original','selected'} and dependency['module_name'].startswith('scripts.'),'closed dependency replacement required')
        for item in (dependency['original'],dependency['selected']):require(binding(item['path'])==item,'selected dependency changed')
    for symbol in value['symbols']:
        require(set(symbol)=={'module_name','original','selected','names'} and isinstance(symbol['module_name'],str) and all(part.isidentifier()for part in symbol['module_name'].split('.')),'closed symbol replacement required')
        require(isinstance(symbol['names'],list) and 0<len(symbol['names'])<=16 and len(set(symbol['names']))==len(symbol['names']) and all(name.isidentifier()for name in symbol['names']),'explicit unique function names required')
        for item in (symbol['original'],symbol['selected']):require(binding(item['path'])==item,'selected symbol dependency changed')
    for factory in value.get('factories',[]):
        require(set(factory)=={'module','function','function_sha256','reader','certificate'} and factory['function']=='install','closed namespace factory projection required')
        for item in (factory['module'],factory['reader'],factory['certificate']):require(binding(item['path'])==item,'factory projection dependency changed')
    for item in value.get('historical_imports',[]):
        require(set(item)=={'module','function','function_sha256','dependency'}
                and item['function']=='_takeover_chain','only completed takeover history imports may be pinned')
        dependency=item['dependency']
        require(set(dependency)=={'module_name','selected'}
                and dependency['module_name']=='scripts.batch.harbor_lease_takeover',
                'only the historical takeover dependency may be selected')
        for row in (item['module'],dependency['selected']):require(binding(row['path'])==row,'historical import dependency changed')
    require(set(value['read_call'])=={'function','args','kwargs'} and value['read_call']['function'] in {'context','load','cold_context','completed_context','tail_context'},'read-only context entry required')
    for root in value['journal_roots']:
        path=Path(root);require(path.is_absolute() and '..' not in path.parts,'closed journal roots required')
    return value


def historical_import_function(module,row,pinned):
    """Pin one historical import inside unchanged read-only function bytecode.

    A private import function avoids changing the process's canonical module or
    package attributes. Current custody callers continue seeing the new module.
    """
    import builtins
    require(binding(module.__file__)==row['module'],'historical consumer changed')
    original=getattr(module,row['function'])
    original=getattr(original,'_retained_import_original',original)
    require(source_hash(original)==row['function_sha256'] and not original.__closure__,
            'historical import function changed')
    tree=ast.parse(textwrap.dedent(inspect.getsource(original)))
    forbidden={'authorize','replace_expired','recover','lease_operation','renew','commit','dispatch','execute','admit','keep','retain','put','mkdir','cas_mutate'}
    require(not any(isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and n.func.attr in forbidden for n in ast.walk(tree)),
            'owning function cannot receive historical imports')
    name=row['dependency']['module_name'];parent,_,attribute=name.rpartition('.')
    def selected(*args,**kwargs):
        require(binding(module.__file__)==row['module'] and binding(pinned.__file__)==row['dependency']['selected'],
                'historical import bytes changed before invocation')
        namespace=dict(original.__globals__)
        base=namespace.get('__builtins__',builtins)
        scope=dict(base if isinstance(base,dict)else vars(base));normal=scope['__import__']
        def scoped_import(request,globals=None,locals=None,fromlist=(),level=0):
            if level==0 and request==name and fromlist:return pinned
            if level==0 and request==parent and tuple(fromlist)==(attribute,):
                package=normal(request,globals,locals,fromlist,level)
                proxy=ModuleType(package.__name__);proxy.__dict__.update(package.__dict__)
                setattr(proxy,attribute,pinned);return proxy
            return normal(request,globals,locals,fromlist,level)
        scope['__import__']=scoped_import;namespace['__builtins__']=scope
        exact=FunctionType(original.__code__,namespace,original.__name__,original.__defaults__)
        exact.__kwdefaults__=original.__kwdefaults__
        return exact(*args,**kwargs)
    selected._retained_import_original=original
    return selected

def history_function(module,patch):
    """Build a new function with one witnessed call change; no global R patch."""
    require(binding(module.__file__)==patch['module'],'historical consumer changed')
    original=getattr(module,patch['function']);require(not original.__closure__ and source_hash(original)==patch['function_sha256'],'historical function changed')
    tree=ast.parse(textwrap.dedent(inspect.getsource(original)));node=tree.body[0]
    require(isinstance(node,ast.FunctionDef) and not node.decorator_list,'plain historical function required')
    forbidden={'authorize','replace_expired','recover','lease_operation','renew','commit','dispatch','execute','admit','keep','retain'}
    require(not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in forbidden for n in ast.walk(node)),'owning function cannot be a historical reader')
    injected='_retained_completed_history_call'
    require(injected not in original.__code__.co_names,'historical helper name collision')
    changes=[]
    class Replace(ast.NodeTransformer):
        def visit_Call(self,n):
            n=self.generic_visit(n)
            if isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id==patch['owner_name'] and n.func.attr=='_boundary':
                changes.append(n.lineno)
                n.args.insert(0,ast.Name(id=patch['owner_name'],ctx=ast.Load()));n.func=ast.Name(id=injected,ctx=ast.Load())
            return n
    tree=Replace().visit(tree);require(len(changes)==1,'one exact eligibility callsite required');ast.fix_missing_locations(tree)
    require(binding(patch['reader']['path'])==patch['reader'],'shared history reader changed')
    readers=[]
    def read_prefix(owner,*args,**kwargs):
        require(binding(owner.__file__)==patch['owning_module'],'original history producer changed')
        if not readers:readers.append(load_module('_retained_shared_history_'+patch['reader']['sha256'],patch['reader']))
        reader=readers[0];require(binding(reader.__file__)==patch['reader'],'shared history reader changed')
        return reader.completed_history(*args,**kwargs)
    namespace={**original.__globals__,injected:read_prefix}
    exec(compile(tree,__file__+'#'+patch['function_sha256'],'exec'),namespace)
    selected=namespace[patch['function']]
    def routed(*args,**kwargs):
        if list(args)!=patch['invocation']['args'] or kwargs!=patch['invocation']['kwargs']:
            return original(*args,**kwargs)
        require(binding(module.__file__)==patch['module'],'historical source changed before read')
        return selected(*args,**kwargs)
    routed.original=original
    routed.witness=dict(original_function_sha256=patch['function_sha256'],changed_lines=changes,
        selected_ast_sha256=hashlib.sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest(),reader=patch['reader'])
    return routed

def child_intent(command,p):
    if not isinstance(command,list) or len(command)<5 or command[3]!=p['entrypoint']['path'] or command[4]!='owning-child':return None
    require(len(command)==15 and command[1:3]==['-I','-B'] and command[5]=='--plan' and command[7]=='--plan-sha256' and command[9]=='--kind' and command[11]=='--intent' and command[13]=='--intent-sha256','closed retained child argv required')
    require(dict(path=command[6],sha256=command[8]) in p['child_plans'] and command[10] in {'environment','instruction','renewal'},'unselected retained child plan')
    intent=dict(path=command[12],sha256=command[14]);require(binding(intent['path'])==intent,'child intent changed')
    path=Path(intent['path']);require(any(path.is_relative_to(Path(root)) for root in p['journal_roots']),'child journal outside declared roots')
    return intent

def _retain(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w')as file:json.dump(value,file,sort_keys=True,indent=2);file.write('\n');file.flush();os.fsync(file.fileno())
    fd=os.open(Path(path).parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(path)

@contextmanager
def installed(row):
    p=profile(row);original_exec=importlib.machinery.SourceFileLoader.exec_module;seen={};restores=[]
    # Retain the original object before installing prospective dependencies.
    historical={}
    for item in p.get('historical_imports',[]):
        selected=item['dependency']['selected'];key=selected['sha256']
        matches=[m for m in list(sys.modules.values())if isinstance(m,ModuleType)and getattr(m,'__file__',None)==selected['path']]
        historical[key]=matches[0]if matches else load_module('_retained_historical_'+key,selected)
    replacing=set()
    def replace_references(original,selected):
        dictionaries=[];ids=set()
        for consumer in list(sys.modules.values()):
            if not isinstance(consumer,ModuleType):continue
            dictionaries.append(consumer.__dict__)
            dictionaries.extend(f.__globals__ for f in list(consumer.__dict__.values())if isinstance(f,FunctionType))
        for namespace in dictionaries:
            if id(namespace) in ids:continue
            ids.add(id(namespace))
            for key,value in list(namespace.items()):
                if value is original:
                    restores.append((namespace,key,original));namespace[key]=selected
    def rebind(original,selected,name):
        restores.append((sys.modules,name,original));sys.modules[name]=selected
        parent,_,attribute=name.rpartition('.')
        if parent in sys.modules:
            package=sys.modules[parent];restores.append((package,attribute,getattr(package,attribute,original)));setattr(package,attribute,selected)
        replace_references(original,selected)
    def apply(module):
        filename=getattr(module,'__file__',None)
        if filename is None:return
        path=str(Path(filename).absolute())
        for dependency in p['dependencies']:
            name=dependency['module_name']
            if path==dependency['original']['path'] and name not in replacing:
                require(binding(path)==dependency['original'],'original dependency changed')
                current=sys.modules.get(name)
                if current is not None and getattr(current,'__file__',None)==dependency['selected']['path']:
                    require(binding(current.__file__)==dependency['selected'],'already-selected dependency changed');selected=current
                else:
                    replacing.add(name)
                    try:selected=load_module(name,dependency['selected'])
                    finally:replacing.remove(name)
                rebind(module,selected,name);seen[(id(module),'dependency:'+name)]=dependency
        for symbol in p['symbols']:
            key=(id(module),'symbols:'+symbol['module_name'])
            if path==symbol['original']['path'] and key not in seen:
                require(binding(path)==symbol['original'],'original symbol module changed')
                selected=load_module('_retained_symbols_'+symbol['selected']['sha256'],symbol['selected'])
                for name in symbol['names']:
                    old=getattr(module,name);new=getattr(selected,name)
                    require(isinstance(old,FunctionType) and isinstance(new,FunctionType),'only explicit function symbols may be replaced')
                    replace_references(old,new)
                seen[key]=symbol
        for item in p.get('historical_imports',[]):
            key=(id(module),'historical-import:'+item['function'])
            if path==item['module']['path'] and key not in seen:
                previous=getattr(module,item['function'])
                selected=historical_import_function(module,item,historical[item['dependency']['selected']['sha256']])
                replace_references(previous,selected);seen[key]=item
        factories=[f for f in p.get('factories',[])if path==f['module']['path']]
        for function in dict.fromkeys(f['function']for f in factories):
            selected_rows=[f for f in factories if f['function']==function]
            key=(id(module),'factory:'+function)
            if key not in seen:
                previous=getattr(module,function)
                original=getattr(previous,'_retained_factory_original',previous)
                require(all(binding(path)==f['module']and source_hash(original)==f['function_sha256']for f in selected_rows),'namespace factory function changed')
                def make_factory(original,rows):
                    def projected(*args,**kwargs):
                        readers=[]
                        for row in rows:
                            require(binding(row['module']['path'])==row['module'],'factory changed before invocation')
                            reader=load_module('_retained_projection_'+row['reader']['sha256'],row['reader'])
                            reader.validate(row['certificate']);readers.append((reader,row['certificate']))
                        value=original(*args,**kwargs)
                        for reader,certificate in readers:value=reader.install_projection(value,certificate)
                        return value
                    return projected
                selected=make_factory(original,selected_rows);selected._retained_factory_original=original
                replace_references(previous,selected);seen[key]=selected_rows
        for patch in p['patches']:
            key=(id(module),patch['function'])
            if path==patch['module']['path'] and key not in seen:
                selected=history_function(module,patch);restores.append((module,patch['function'],getattr(module,patch['function'])));setattr(module,patch['function'],selected);seen[key]=selected.witness
        if any(path==r['path'] for r in p['guard_modules']) and (id(module),'guard')not in seen:
            require(binding(path)in p['guard_modules'],'original guard module changed');runner=module._run_in_process_group
            def wrapped(command,**options):
                intent=child_intent(command,p)
                if intent is None:return runner(command,**options)
                require(options['timeout_seconds']>0 and options['memory_limit_bytes']>0,'declared owning guard required')
                selected=[command[0],'-I','-B',__file__,'child','--profile',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
                _retain(Path(intent['path']).parent/'retained-runtime-bootstrap.json',dict(schema_version=SIDE,profile=row,intent=intent,logical_command=command,physical_command=selected,timeout_seconds=options['timeout_seconds'],memory_limit_bytes=options['memory_limit_bytes'],automatic_retry=False))
                return runner(selected,**options)
            restores.append((module,'_run_in_process_group',runner));module._run_in_process_group=wrapped;seen[(id(module),'guard')]=True
    def execute(loader,module):
        original_exec(loader,module);apply(module)
    importlib.machinery.SourceFileLoader.exec_module=execute
    try:
        for module in list(sys.modules.values()):
            if module is not None:apply(module)
        yield p,seen
    finally:
        importlib.machinery.SourceFileLoader.exec_module=original_exec
        for module,name,value in reversed(restores):
            if isinstance(module,dict):module[name]=value
            else:setattr(module,name,value)

def check(row):
    with installed(row)as(p,seen):
        entry=load_module('_retained_runtime_entry',p['entrypoint']);call=p['read_call'];value=getattr(entry,call['function'])(*call['args'],**call['kwargs'])
        require(all(any(isinstance(k,tuple)and k[1]==patch['function'] for k in seen)for patch in p['patches']),'declared history patch not reached')
        return dict(status='RETAINED_CONTEXT_VALIDATED',profile=row,patches=[v for v in seen.values()if isinstance(v,dict)],result_type=type(value).__name__)

def run_entrypoint(row):
    """Load definitions through the hook, then execute the exact original CLI block."""
    require(binding(row['path'])==row,'retained entrypoint changed')
    tree=ast.parse(Path(row['path']).read_text());blocks=[]
    for node in tree.body:
        if isinstance(node,ast.If) and isinstance(node.test,ast.Compare) and isinstance(node.test.left,ast.Name) and node.test.left.id=='__name__' and len(node.test.ops)==1 and isinstance(node.test.ops[0],ast.Eq) and len(node.test.comparators)==1 and isinstance(node.test.comparators[0],ast.Constant) and node.test.comparators[0].value=='__main__':blocks.append(node)
    require(len(blocks)==1 and not blocks[0].orelse,'one original main block required')
    module=load_module('_retained_runtime_main',row);module.__dict__['__name__']='__main__'
    exec(compile(ast.Module(body=blocks[0].body,type_ignores=[]),row['path'],'exec'),module.__dict__)


def child(row,intent):
    p=profile(row);side=checked(binding(Path(intent['path']).parent/'retained-runtime-bootstrap.json'))
    require(set(side)=={'schema_version','profile','intent','logical_command','physical_command','timeout_seconds','memory_limit_bytes','automatic_retry'}and side['schema_version']==SIDE and side['profile']==row and side['intent']==intent and side['automatic_retry']is False,'closed retained physical child required')
    require(child_intent(side['logical_command'],p)==intent and sys.argv==side['physical_command'][3:],'actual retained child command differs')
    require(side['physical_command'][0]==sys.executable and binding(side['logical_command'][3])==p['entrypoint'],'retained entrypoint or executable differs')
    with installed(row):
        previous=sys.argv;sys.argv=side['logical_command'][3:]
        try:run_entrypoint(p['entrypoint'])
        finally:sys.argv=previous

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['check','child']);a.add_argument('--profile',required=True);a.add_argument('--sha256',required=True);a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();row=dict(path=v.profile,sha256=v.sha256)
    if v.action=='check':print(json.dumps(check(row)))
    else:child(row,dict(path=v.intent,sha256=v.intent_sha256))
