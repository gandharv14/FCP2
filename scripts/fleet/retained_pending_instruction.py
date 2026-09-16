"""Read an actual prepared instruction through its original historical loader.

The preparation is a completed zero-effect coordinator command, not a completed
review. Only the historical unused-directory assertion is projected; current
worker custody and all original request/model validation remain with I/W/E.
"""
import ast
import hashlib
import importlib.util
import inspect
import json
import sys
import textwrap
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import CodeType, FunctionType

SCHEMA='fleet-retained-pending-instruction/v1'
FIELDS={'schema_version','helper','original','historical_helper','renewal','worker',
        'outcome','result','descriptor','pending','directory','tree','predecessor',
        'failed','source','workbook','aliases'}


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path)
    require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),
            'regular pending evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'pending evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def module(name,row):
    require(binding(row['path'])==row,'pending implementation changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m


def changed_function(original,old,new,extra):
    """One exact expression in an authenticated module; preserve compiler context."""
    path=Path(original.__code__.co_filename)
    require(path==Path(original.__globals__['__file__'])and not original.__closure__,
            'plain declared pending function required')
    binding(path);tree=ast.parse(path.read_text());nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name==original.__name__]
    require(len(nodes)==1 and ast.dump(nodes[0])==ast.dump(ast.parse(textwrap.dedent(inspect.getsource(original))).body[0]),
            'declared pending function changed')
    def sig(c):
        return(c.co_code,c.co_names,c.co_varnames,c.co_argcount,c.co_posonlyargcount,c.co_kwonlyargcount,c.co_flags,
               tuple(sig(x)if isinstance(x,CodeType)else x for x in c.co_consts))
    before=[x for x in compile(tree,str(path),'exec').co_consts if isinstance(x,CodeType)and x.co_name==original.__name__]
    require(len(before)==1 and sig(before[0])==sig(original.__code__),'loaded pending function changed')
    old=[old]if isinstance(old,str)else old;new=[new]if isinstance(new,str)else new
    require(len(old)==len(new)and 1<=len(old)<=2,'bounded exact expression changes required')
    targets={ast.dump(ast.parse(a,mode='eval').body):ast.parse(b,mode='eval').body for a,b in zip(old,new)}
    require(len(targets)==len(old),'duplicate expression change');counts={k:0 for k in targets}
    class Change(ast.NodeTransformer):
        def visit_Call(self,node):
            key=ast.dump(node)
            if key in targets:counts[key]+=1;return targets[key]
            return self.generic_visit(node)
    Change().visit(nodes[0]);require(all(n==1 for n in counts.values()),'one exact historical pending boundary required')
    after=[x for x in compile(ast.fix_missing_locations(tree),str(path),'exec').co_consts if isinstance(x,CodeType)and x.co_name==original.__name__]
    f=FunctionType(after[0],dict(original.__globals__,**extra),original.__name__,original.__defaults__)
    f.__kwdefaults__=original.__kwdefaults__;return f


def retained(p,*,closed=True):
    require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),
            'closed prepared instruction history required')
    value=read(p['result']);pending=read(p['pending']);out=read(p['outcome']);guard=read(out['resource']);intent=read(guard['intent'])
    require(value['status']=='MODEL_REQUIRED'and value['next_step']=='disclosure-review'
        and value['descriptor']==p['descriptor']and value['checkpoint']==p['pending']
        and value['publication_performed']is False and out['status']=='COMPLETED_OPERATION'
        and out['result']==p['result']and guard['returncode']==0 and guard['failure']is None
        and intent['action']=='step'and intent['models']is False and intent['commands']is False
        and intent['containers']is False and intent['arguments']['descriptor']==p['descriptor'],
        'actual zero-effect model preparation required')
    require(pending['status']=='MODEL_REQUIRED'and pending['step']==value['next_step']
        and pending['selection']==value['selection'], 'prepared worker checkpoint differs')
    wi=read(pending['intent']);prior=read(p['predecessor'])
    require(wi['previous']==p['predecessor']and wi['step']==pending['step']
        and wi['selection']==pending['selection']and wi['lease']==pending['lease']
        and prior['selection']==pending['selection']and prior['lease']==pending['lease'],
        'prepared request lost its historical lease predecessor')
    d=Path(p['directory']);require(d==Path(p['pending']['path']).parent
        and p['pending']==binding(d/'pending-0000.json')and pending['intent']==binding(d/'intent.json')
        and set(p['tree'])=={'intent.json','pending-0000.json','request.json','manifest.json','instruction.snapshot.md'},
        'unsupported prepared step inventory')
    actual={}
    for x in d.rglob('*'):
        require(not x.is_symlink(),'pending tree symlink')
        if x.is_file():actual[str(x.relative_to(d))]=binding(x)['sha256']
    require(all(actual.get(k)==v for k,v in p['tree'].items())and(not closed or actual==p['tree']),
            'prepared step tree changed')
    fail=read(p['failed']);fg=read(fail['resource']);fi=read(fg['intent'])
    require(fail['status']=='RECONCILIATION_REQUIRED'and fail.get('result')is None
        and fg['returncode']==1 and fg['failure']is None and fi['action']=='step'
        and fi['arguments']==dict(descriptor=p['descriptor'],checkpoint=p['result']),
        'stopped next child detached from actual preparation')
    return value,pending,wi,guard,intent


def completed_record(p,outcome):
    """A completed original review closes the former preparation namespace."""
    retained(p,closed=False)
    raw=read(outcome);guard=read(raw['resource']);intent=read(guard['intent']);value=read(raw['result'])
    complete=binding(Path(p['directory'])/'complete.json');saved=read(complete)
    require(raw['status']=='COMPLETED_OPERATION'and guard['returncode']==0 and guard['failure']is None
        and intent['action']=='step'and intent['models']is True
        and value['status']=='STEP_COMPLETE'and value['next_step']=='disclosure-commit'
        and value['checkpoint']==complete and value['publication_performed']is False
        and saved['status']=='completed'and saved['step']=='disclosure-review'
        and saved['intent']==read(p['pending'])['intent']and saved['selection']==value['selection'],
        'actual completed original prepared review required')
    actual={str(x.relative_to(Path(p['directory']))):binding(x)['sha256']for x in Path(p['directory']).rglob('*')if x.is_file()}
    require(actual==dict(p['tree'],**{'complete.json':complete['sha256']}),'completed review namespace changed')
    return value


def boundary(R,p,worker,selection,lease,checkpoint,completed=None):
    require(binding(R.__file__)==p['renewal'],'historical renewal implementation changed')
    value,pending,wi,_,_=retained(p,closed=completed is None)
    if completed is not None:completed_record(p,completed)
    require(str(worker.run)==str(Path(p['directory']).parents[1])
        and selection.content.workbook==p['workbook']and selection.content.bindings['original_raw_source']==p['source']
        and read(read(p['predecessor'])['permit'])['checkpoint']==checkpoint,
        'pending history is foreign to completed predecessor')
    def allowed(w,phase):
        require(w==worker and phase==value['next_step'],'historical pending phase changed')
        if completed is None:retained(p)
        else:completed_record(p,completed)
        return True
    old=["require(not w.step_directory(worker,phase).exists(),'pending or uncertain next step cannot renew')"]
    new=["require(not w.step_directory(worker,phase).exists() or _prepared_directory(worker,phase),'pending or uncertain next step cannot renew')"]
    def prefix(items,selected):
        completed_record(p,completed)
        indices=[i for i,(_,bound)in enumerate(items)if bound==selected]
        require(len(indices)==1 and selected==checkpoint,'historical checkpoint absent or duplicated')
        del items[indices[0]+1:]
        return True
    if completed is not None:
        old.append("require(completed and completed[-1][1]==checkpoint,'renewal requires exact latest successful checkpoint')")
        new.append("require(_prepared_completed_prefix(completed,checkpoint),'renewal requires exact latest successful checkpoint')")
    f=changed_function(R._boundary,old,new,{'_prepared_directory':allowed,'_prepared_completed_prefix':prefix})
    return f(worker,selection,lease,checkpoint)


@contextmanager
def context(row,*,completed=None):
    p=read(row);retained(p,closed=completed is None)
    if completed is not None:completed_record(p,completed)
    old=read(p['original']['plan'])
    require(set(p['original'])=={'helper','plan'}and old['helper']==p['original']['helper']
        and old['takeover_helper']==p['historical_helper'],'original loader identity differs')
    M=module('retained_prepared_original',p['original']['helper']);original_module=M.module
    aliases={a['original']['path']:a for a in p['aliases']}
    require(len(aliases)==len(p['aliases']),'duplicate archived code path')
    for a in p['aliases']:
        require(set(a)=={'original','archived'}and a['original']['sha256']==a['archived']['sha256']
            and binding(a['archived']['path'])==a['archived']and a['original']==old['capacity_policy'],
            'archive is not exact original capacity code')
    def load(name,selected):
        if selected['path']in aliases:
            a=aliases[selected['path']];require(selected==a['original'],'different archived binding')
            return original_module(name,a['archived'])
        got=original_module(name,selected)
        if selected==p['historical_helper']:
            got.context=changed_function(got.context,"R._boundary(worker,selection,lease,previous['checkpoint'])",
                "_prepared_boundary(R,worker,selection,lease,previous['checkpoint'])",
                {'_prepared_boundary':lambda R,w,s,l,cp:boundary(R,p,w,s,l,cp,completed)})
        return got
    M.module=load
    try:
        result=M.context(p['original']['plan'],initial=False)
        _,_,_,env,A,D,job,_,_,_,_,_=result;I,W=env[1],env[3]
        require(binding(W.__file__)==p['worker'],'selected prepared worker changed')
        cp,value=D.successful(p['outcome'],env)
        require(cp==p['result']and value==read(cp),'original successful preparation differs')
        w,controls,_=I.selected(p['descriptor']);observed,s,l=I._prior(p['result'],p['descriptor'])
        require(observed==value and value['selection']==I.codec.encode(s)
            and asdict(l)==read(p['pending'])['lease']and w.root.name==old['version'],
            'original pending checkpoint decoder differs')
        req=W.request_saved(Path(p['directory'])/'request.json');E=W.executor
        E.prepared_plan(req.prepared,w.campaign)
        if completed is None:
            require(not E.location(req.prepared,w.campaign).exists(), 'prepared model already has execution journal')
            identity=Path(w.campaign.root)/'attempt-identities'/(req.prepared.attempt_id+'.json')
            require(not identity.exists(),'prepared model identity already registered')
        else:
            _,done=D.successful(completed,env);preliminary=completed_record(p,completed)
            require(done==preliminary,'original completed review decoder differs')
            observed=E.inspect(req.prepared,w.campaign)
            require(observed.status=='completed'and observed.call is not None
                and I.codec.decode(done['selection']).semantic.disclosure_call==observed.call,
                'completed review lost its actual original Call')
        yield p,M,result,w,s,l,req
    finally:M.module=original_module
