"""Archived custody for read-only validation of completed rejected reviews.

Only the retained context's final active check reads the original tracker at its
sealed admission time. The original source, stage, code, lifecycle and lease
checks run. This helper grants neither live custody nor model capability.
"""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import FunctionType,SimpleNamespace
import ast,hashlib,inspect,json,textwrap

SCHEMA='harbor-rejected-review-custody-history/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical historical input required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(binding(row['path'])==row,'historical custody bytes changed');return json.loads(Path(row['path']).read_bytes())

def function_sha(fn):
    return hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(fn))),include_attributes=False).encode()).hexdigest()

def archived_active(W,worker,selection,lease,p,selected,prepared):
    """Run the unchanged active checks against one real archived admission."""
    require(binding(W.__file__)==p['worker']and function_sha(W.active)==p['active_sha256'],
            'original active checker changed')
    archive=p['tracker_archive'];raw=Path(archive['path']).read_bytes();before=read(archive)
    admission=read(p['first_admission'])
    tracker=prepared['completed_custody']['tracker'];done=read(prepared['completed_custody']['takeover']['completion'])
    require(done['after']==archive and tracker['path']==str(worker.context.tracker_root/(selected['workbook']+'.json'))
        and tracker['sha256']==archive['sha256']==lease.sha256 and before['revision']==lease.revision,
        'historical tracker detached from completed custody')
    if 'reserved_continuation'in p:
        require('reserved_round'in selected and p['reserved_continuation']==binding(Path(selected['output'])/'continued.json')
            and read(p['reserved_continuation'])==selected['reserved_round'], 'actual reserved-round continuation required')
        receipt=None
    else:
        receipt=read(p['lease_receipt'])
        require(p['lease_receipt']==binding(Path(selected['output'])/'initial-lease/retained-live.json')
        and receipt['schema_version']=='fleet-retained-live-continuation-lease/v1'
        and receipt['authority']==p['run_plan']and receipt['lease']==asdict(lease)and receipt['tracker']==tracker
        and receipt['minimum_remaining_ns']==7200*10**9 and receipt['lease_operations']==0
        and receipt['task_guards_changed']is receipt['semantic_budget_reset']is receipt['automatic_retry']is False,
            'original no-CAS review admission differs')
    require(p['first_admission']==binding(Path(selected['output'])/'batches/0000/admission.json')
        and admission['tracker']==tracker and admission['transport_started']is False
        and admission['required_remaining_seconds']==660,
        'actual first review batch has different custody')
    at=admission['observed_at_ns'];native=before['leases']['task_agent']
    require(type(at)is int and native['heartbeat_at']<=at
        and native['expires_at']-at>=660*10**9
        and before['bindings']['original_raw_source']==selected['source'],
        'archived admission time or original source differs')
    if receipt is not None:
        require(type(receipt['observed_at_ns'])is int and native['heartbeat_at']<=receipt['observed_at_ns']<=at
            and native['expires_at']-receipt['observed_at_ns']>=7200*10**9,'original no-CAS admission time differs')
        for row in receipt['validated_evidence']:
            require(row==tracker or binding(row['path'])==row,'original admission evidence changed')
    fn=W.active;tree=ast.parse(textwrap.dedent(inspect.getsource(fn)));counts=[0,0,0,0]
    expressions=['path.read_bytes()',"facade._active(before, raw, lease.revision, lease.sha256, lease.owner, lease.token, 'workbook:'+wb)",'record(path)','facade.require_synchronized(wb)']
    replacements=['_historical_raw(path)',"_historical_lease(facade, before, raw, lease.revision, lease.sha256, lease.owner, lease.token, 'workbook:'+wb)",'_historical_record(path)','_historical_synchronized(facade,wb)']
    expected=[ast.dump(ast.parse(x,mode='eval').body,include_attributes=False)for x in expressions]
    class Select(ast.NodeTransformer):
        def visit_Call(self,node):
            for i,key in enumerate(expected):
                if ast.dump(node,include_attributes=False)==key:
                    counts[i]+=1;return ast.copy_location(ast.parse(replacements[i],mode='eval').body,node)
            return self.generic_visit(node)
    tree=Select().visit(tree);require(counts==[1,1,1,1],'original active boundary changed')
    def historical_raw(path):
        require(str(path)==tracker['path']and binding(archive['path'])==archive,'foreign historical tracker read');return raw
    def historical_lease(facade,*args):
        original=facade._active.__func__
        require(binding(inspect.getsourcefile(original))==p['facade']and function_sha(original)==p['facade_active_sha256'],
                'original lease validator changed')
        scope=dict(original.__globals__);scope['time']=SimpleNamespace(time_ns=lambda:at)
        exact=FunctionType(original.__code__,scope,original.__name__,original.__defaults__,original.__closure__)
        return exact(facade,*args)
    def historical_record(path):historical_raw(path);return archive
    def historical_synchronized(facade,wb):
        original=facade.require_synchronized.__func__
        require(wb==selected['workbook']and binding(inspect.getsourcefile(original))==p['facade'],
            'original synchronized notes validator changed')
        tree=ast.parse(textwrap.dedent(inspect.getsource(original)));count=0
        target=ast.dump(ast.parse('path.read_bytes()',mode='eval').body)
        class ArchiveTracker(ast.NodeTransformer):
            def visit_Call(self,node):
                nonlocal count
                if ast.dump(node)==target:
                    count+=1;return ast.copy_location(ast.parse('_historical_raw(path)',mode='eval').body,node)
                return self.generic_visit(node)
        tree=ArchiveTracker().visit(tree);require(count==1,'original synchronized tracker boundary changed')
        scope=dict(original.__globals__,_historical_raw=historical_raw)
        exec(compile(ast.fix_missing_locations(tree),original.__code__.co_filename,'exec'),scope)
        return scope[original.__name__](facade,wb)
    scope=dict(fn.__globals__);scope.update(_historical_raw=historical_raw,_historical_lease=historical_lease,_historical_record=historical_record,_historical_synchronized=historical_synchronized)
    exec(compile(ast.fix_missing_locations(tree),fn.__code__.co_filename,'exec'),scope)
    return scope[fn.__name__](worker,selection,lease)

@contextmanager
def context(runner,profile):
    p=read(profile);base={'schema_version','helper','run_plan','prepared_helper','prepared_function_sha256','worker','active_sha256','facade','facade_active_sha256','tracker_archive','first_admission'}
    require(set(p)in (base|{'lease_receipt'},base|{'reserved_continuation'})
        and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed historical custody profile required')
    selected=read(p['run_plan']);prepared=read(selected['retained']['plan'])
    require(binding(runner.__file__)==selected['helper']and selected['retained']['helper']==p['prepared_helper']
        and prepared['helper']==p['prepared_helper']and 'completed_custody'in prepared,
        'historical context detached from original review')
    loader=runner.module
    def module(name,row):
        m=loader(name,row)
        if row!=p['prepared_helper']:return m
        original=m.completed_custody
        require(function_sha(original)==p['prepared_function_sha256'],'original completed custody changed')
        @contextmanager
        def historical(row,plan,env,worker,selection,previous):
            require(plan==prepared,'another prepared context requested historical custody')
            W=env[3];proxy=SimpleNamespace(**W.__dict__)
            proxy.active=lambda w,s,l:archived_active(W,w,s,l,p,selected,prepared)
            with original(row,plan,(*env[:3],proxy,*env[4:]),worker,selection,previous)as lease:yield lease
        m.completed_custody=historical;return m
    fn=runner.context.__wrapped__;scope=dict(fn.__globals__);scope['module']=module
    cold=FunctionType(fn.__code__,scope,fn.__name__,fn.__defaults__,fn.__closure__)
    with contextmanager(cold)(p['run_plan'])as value:yield value


def main():
    import argparse,importlib.util,sys
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['probe']);parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True);a=parser.parse_args();row=dict(path=a.plan,sha256=a.sha256)
    p=read(row);selected=read(p['run_plan']);helper=selected['helper'];require(binding(helper['path'])==helper,'original runner changed')
    spec=importlib.util.spec_from_file_location('historical_disclosure_runner',helper['path']);runner=importlib.util.module_from_spec(spec);sys.modules[spec.name]=runner;spec.loader.exec_module(runner)
    with context(runner,row)as loaded:
        value,_,_,worker,selection,lease,_,_=loaded
        require(value==selected and selection.content.workbook==selected['workbook'],'historical context changed source')
        print(json.dumps(dict(status='HISTORICAL_REVIEW_CONTEXT_VALIDATED',plan=row,run_plan=p['run_plan'],workbook=selected['workbook'],round=selected['round'],archived_tracker=p['tracker_archive'],models_dispatched=0,task_mutations=0,current_custody=False,semantic_approval=False)))

if __name__=='__main__':main()
