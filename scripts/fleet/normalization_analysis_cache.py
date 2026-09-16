"""Process-scoped reuse of one authenticated, unchanged normalization analysis.

Every gate and actual Call validation still executes. Only the pure three-file
analysis result is memoized, after its first original execution. No cache or
success certificate survives the context or grants ownership of a checkpoint.
"""
from contextlib import contextmanager
from pathlib import Path
from types import FunctionType,ModuleType
import copy,hashlib,inspect,re,sys,time
from datetime import date,datetime,time as datetime_time,timedelta
from dataclasses import dataclass


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical analysis input required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def check_records(records):
    """Hash every file; check common ancestor paths once in this validation."""
    expected={};parents=set()
    for row in records:
        require(set(row)=={'path','sha256'},'closed cache dependency required')
        p=Path(row['path']);require(p.is_absolute(),'absolute cache dependency required')
        require(str(p)not in expected or expected[str(p)]==row['sha256'],'conflicting cache dependency')
        expected[str(p)]=row['sha256'];parents.update(p.parents)
    require(not any(p.is_symlink()for p in parents),'canonical cache dependency required')
    for name,sha in expected.items():
        path=Path(name)
        require(path.is_file()and not path.is_symlink()and hashlib.sha256(path.read_bytes()).hexdigest()==sha,
            'review body input or dependency changed')


def fingerprint(value):
    if value is None or type(value)in(bool,int,float,str,bytes):return(type(value).__name__,value)
    if isinstance(value,Path):return('path',str(value))
    if isinstance(value,FunctionType):
        require(value.__closure__ is None,'analysis functions with closures require separate authority')
        # Code objects are immutable. Their identity and bytecode are stable;
        # marshal output can vary with constants' reference counts after calls.
        return('function',id(value),id(value.__code__),value.__code__.co_code,fingerprint(value.__defaults__),fingerprint(value.__kwdefaults__))
    if isinstance(value,re.Pattern):return('regex',value.pattern,value.flags)
    if type(value)in(tuple,list):return(type(value).__name__,tuple(fingerprint(x)for x in value))
    if type(value)in(set,frozenset):return(type(value).__name__,tuple(sorted((fingerprint(x)for x in value),key=repr)))
    if type(value)is dict:return('dict',tuple(sorted(((fingerprint(k),fingerprint(v))for k,v in value.items()),key=repr)))
    return(type(value).__module__,type(value).__qualname__,id(value))


def body_key(value):
    """Typed digest, preserving values while visiting each shared object once."""
    memo={};visiting=set()
    def digest(v):
        if v is None or type(v)in(bool,int,str,bytes):data=(type(v).__name__,v)
        elif type(v)is float:data=('float',v.hex())
        elif type(v)in(date,datetime,datetime_time):data=(type(v).__name__,v.isoformat(),getattr(v,'fold',None))
        elif type(v)is timedelta:data=('timedelta',v.days,v.seconds,v.microseconds)
        elif type(v)in(tuple,list,dict):
            identity=id(v)
            if identity in memo:return memo[identity]
            if identity in visiting:raise TypeError('cyclic review body data')
            visiting.add(identity)
            if type(v)is dict:data=('dict',tuple(sorted((digest(k),digest(x))for k,x in v.items())))
            else:data=(type(v).__name__,tuple(digest(x)for x in v))
            result=hashlib.sha256(repr(data).encode()).digest();visiting.remove(identity);memo[identity]=result;return result
        else:raise TypeError('review body contains unsupported mutable data')
        return hashlib.sha256(repr(data).encode()).digest()
    return digest(value)


@contextmanager
def installed(G,*,workbook,source,baseline,draft,normalizer,dependencies=()):
    """Use exactly the admitted source, baseline and completed audit draft."""
    require(binding(G.__file__)==normalizer,'analysis module differs from original snapshot')
    original=G.analyse
    require(isinstance(original,FunctionType)and original.__name__=='analyse'
        and Path(inspect.getsourcefile(original)).resolve()==Path(G.__file__).resolve()
        and original.__globals__ is G.__dict__,'only the original normalizer analysis may be cached')
    require(Path(source['path']).name==workbook+'.xlsx'
        and Path(draft['path']).name=='draft.json'and Path(draft['path']).parent.name==workbook+'-variable-sources',
        'analysis source/draft scope changed')
    records=[normalizer,source,baseline,draft,*dependencies]
    require(len({r['path']for r in records})==len({(r['path'],r['sha256'])for r in records}),'conflicting analysis dependencies')
    signature=inspect.signature(original);initial={k:fingerprint(v)for k,v in G.__dict__.items()if not k.startswith('__')and k!='analyse'}
    original_state=fingerprint(original);loader_state=fingerprint(G.openpyxl.load_workbook)
    entries={};restores=[];metrics=dict(original_calls=0,cache_hits=0,original_seconds=0.0,validation_seconds=0.0)
    def validate():
        started=time.monotonic()
        require(all(binding(r['path'])==r for r in records),'analysis source, baseline, draft or dependency changed')
        require(fingerprint(original)==original_state and fingerprint(G.openpyxl.load_workbook)==loader_state
            and {k:fingerprint(v)for k,v in G.__dict__.items()if not k.startswith('__')and k!='analyse'}==initial,
            'analysis function or loaded dependency changed')
        require(all(ns.get(key)is cached for ns,key,_ in restores),'analysis cache alias changed')
        metrics['validation_seconds']+=time.monotonic()-started
    def cached(*args,**kwargs):
        validate();a=signature.bind(*args,**kwargs);a.apply_defaults();v=a.arguments
        expected=(workbook,Path(source['path']),source['sha256'],Path(draft['path']).parent.parent,Path(baseline['path']).parent)
        observed=(v['wb'],Path(v['source_file']).absolute(),v['expected_source_sha256'],Path(v['runs_root']).absolute(),Path(v['inputs_root']).absolute())
        if observed!=expected:
            # Another admitted historical selection retains the original behavior.
            return original(*args,**kwargs)
        resolved=G.resolve_workbook_artifact(v['inputs_root'],workbook,'%s-inputs')
        require(binding(Path(resolved).absolute())==baseline,'baseline resolver changed its selected artifact')
        if 'result'not in entries:
            started=time.monotonic();value=original(*args,**kwargs);metrics['original_seconds']+=time.monotonic()-started;metrics['original_calls']+=1
            validate();entries['result']=copy.deepcopy(value)
        else:metrics['cache_hits']+=1
        return copy.deepcopy(entries['result'])
    for m in tuple(sys.modules.values()):
        if not isinstance(m,ModuleType):continue
        for key,value in tuple(m.__dict__.items()):
            if value is original:restores.append((m.__dict__,key,value));m.__dict__[key]=cached
    require(any(ns is G.__dict__ and key=='analyse'for ns,key,_ in restores),'original analysis alias not installed')
    try:
        validate();yield metrics
    finally:
        for ns,key,value in reversed(restores):ns[key]=value
        entries.clear()


@contextmanager
def body_installed(context,*,code,dependencies):
    """Reuse only an identical pure body check; Call validation stays outside.

    Every referenced input and selected code dependency is authenticated on
    every call. Changed arguments execute the original checker; failures never
    enter the bounded process-local cache.
    """
    require(binding(context.__file__)==code,'review context code changed')
    original=context.normalization_body
    require(isinstance(original,FunctionType)and original.__name__=='normalization_body'
        and original.__globals__ is context.__dict__
        and Path(inspect.getsourcefile(original)).resolve()==Path(context.__file__).resolve(),
        'only original normalization body checker may be cached')
    records=[code,*dependencies];state=fingerprint(original)
    initial={k:fingerprint(v)for k,v in context.__dict__.items()if not k.startswith('__')and k!='normalization_body'}
    entries={};restores=[];metrics=dict(original_calls=0,cache_hits=0,original_seconds=0.0,validation_seconds=0.0,key_seconds=0.0)
    signature=inspect.signature(original)
    def references(value):
        if type(value)is dict:
            if set(value)=={'path','sha256'}:yield value
            else:
                for child in value.values():yield from references(child)
        elif type(value)in(tuple,list):
            for child in value:yield from references(child)
    def validate(arguments):
        started=time.monotonic()
        check_records([*records,*references(arguments)])
        require(fingerprint(original)==state and {k:fingerprint(v)for k,v in context.__dict__.items()if not k.startswith('__')and k!='normalization_body'}==initial,
            'review body checker or loaded dependency changed')
        require(all(ns.get(key)is cached for ns,key,_ in restores),'review body cache alias changed')
        metrics['validation_seconds']+=time.monotonic()-started
    def cached(*args,**kwargs):
        arguments=dict(signature.bind(*args,**kwargs).arguments);validate(arguments)
        started=time.monotonic()
        try:key=body_key(arguments)
        except TypeError:return original(*args,**kwargs)
        metrics['key_seconds']+=time.monotonic()-started
        if key in entries:metrics['cache_hits']+=1;return copy.deepcopy(entries[key])
        started=time.monotonic();value=original(*args,**kwargs);metrics['original_calls']+=1;metrics['original_seconds']+=time.monotonic()-started
        validate(arguments)
        if len(entries)<8:entries[key]=copy.deepcopy(value)
        return copy.deepcopy(value)
    for module in tuple(sys.modules.values()):
        if not isinstance(module,ModuleType):continue
        for key,value in tuple(module.__dict__.items()):
            if value is original:restores.append((module.__dict__,key,value));module.__dict__[key]=cached
    try:
        require(any(ns is context.__dict__ and key=='normalization_body'for ns,key,_ in restores),'original body alias not installed')
        validate({});yield metrics
    finally:
        for ns,key,value in reversed(restores):ns[key]=value
        entries.clear()


@contextmanager
def workbooks_installed(context,*,code,source,baseline,dependencies=()):
    """Cache source cell observations, while the original body checker runs.

    Each view exposes only the three cell fields read by normalization_body.
    Unseen coordinates are loaded through the original parser. No workbook or
    cached approval escapes this bounded context.
    """
    require(binding(context.__file__)==code,'review context code changed')
    body=context.normalization_body;loader=context.load_workbook
    require(isinstance(body,FunctionType)and body.__name__=='normalization_body'
        and body.__globals__ is context.__dict__ and Path(inspect.getsourcefile(body)).resolve()==Path(context.__file__).resolve(),
        'original normalization body checker required')
    require(isinstance(loader,FunctionType)and loader.__name__=='load_workbook'
        and loader is sys.modules['openpyxl'].load_workbook,'original workbook parser required')
    code_state=fingerprint(body);loader_state=fingerprint(loader)
    records=[code,source,baseline,binding(inspect.getsourcefile(loader)),*dependencies]
    allowed={source['path'],baseline['path']};cells={name:{}for name in allowed};views=[];parsed=set()
    metrics=dict(workbook_loads=0,cache_hits=0,cells=0,parse_seconds=0.0,validation_seconds=0.0)
    @dataclass(frozen=True)
    class Cell:
        value:object
        data_type:str
        number_format:str
    def validate():
        started=time.monotonic();check_records(records)
        require(fingerprint(body)==code_state and context.normalization_body is body and fingerprint(loader)==loader_state
            and context.load_workbook is cached,'workbook cache implementation changed')
        metrics['validation_seconds']+=time.monotonic()-started
    class Sheet:
        def __init__(self,view,name):self.view=view;self.name=name
        def __getitem__(self,address):return self.view.cell(self.name,address)
    class View:
        def __init__(self,path):
            self.path=path;self.book=None;self.closed=False
            # Even a body with no observed cells must parse each workbook once.
            if path not in parsed:self.load()
        def load(self):
            started=time.monotonic();self.book=loader(self.path,data_only=False);metrics['parse_seconds']+=time.monotonic()-started;metrics['workbook_loads']+=1;parsed.add(self.path)
        def __getitem__(self,sheet):
            require(not self.closed,'workbook view already closed');return Sheet(self,sheet)
        def cell(self,sheet,address):
            require(not self.closed,'workbook view already closed');key=(sheet,address);saved=cells[self.path]
            if key in saved:metrics['cache_hits']+=1;return copy.deepcopy(saved[key])
            if self.book is None:self.load()
            original=self.book[sheet][address]
            value=Cell(copy.deepcopy(original.value),original.data_type,original.number_format);saved[key]=value;metrics['cells']+=1
            return copy.deepcopy(value)
        def close(self):
            if self.book is not None:self.book.close();self.book=None
            self.closed=True;validate()
    def cached(filename,*args,**kwargs):
        validate();path=str(Path(filename).absolute())
        if inspect.currentframe().f_back.f_code is not body.__code__ or path not in allowed or args or kwargs!={'data_only':False}:
            return loader(filename,*args,**kwargs)
        view=View(path);views.append(view);return view
    context.load_workbook=cached
    try:
        validate();yield metrics
    finally:
        for view in views:
            if view.book is not None:view.book.close()
        context.load_workbook=loader;cells.clear()
