"""Declared historical reader for a completed lane, retaining fresh live checks.

The owning commit is validated by its original CAS implementation. Only the
historical lease method receives the authenticated predecessor tracker.
"""
from pathlib import Path
import hashlib
import importlib.util
import inspect
import json
import sys
import types
from contextvars import ContextVar

SCHEMA = 'harbor-completed-lane-projection/v1'
REREAD = 'harbor-completed-lane-projection/v2'
ROUTED = 'harbor-completed-lane-projection/v3'

def require(ok, message):
    if not ok: raise ValueError(message)

def binding(path):
    p = Path(path)
    require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p, *p.parents)), 'regular completed lane evidence required')
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row) == {'path', 'sha256'} and binding(row['path']) == row, 'completed lane evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def validate(certificate):
    p = read(certificate)
    fields = {'schema_version', 'implementation', 'validator', 'reader', 'authority', 'method_sha256', 'worker_run', 'initial', 'before', 'after', 'completion', 'kind', 'schema', 'lifecycle'}
    reread = p.get('schema_version') in (REREAD, ROUTED)
    routed = p.get('schema_version') == ROUTED
    require(set(p) == (fields | {'live_tracker','routing'} if routed else fields | {'live_tracker'} if reread else fields) and
            p['schema_version'] in (SCHEMA, REREAD, ROUTED) and p['implementation'] == binding(__file__) and p['kind'] == 'dialogue', 'closed completed dialogue history required')
    for key in ('validator', 'reader', 'authority', 'initial', 'before', 'after', 'completion', 'schema', 'lifecycle'):
        require(binding(p[key]['path']) == p[key], 'completed lane dependency changed')
    require(Path(p['initial']['path']) == Path(p['worker_run'])/'initial.json', 'completed lane worker differs')
    if reread:require(isinstance(p['live_tracker'],str) and Path(p['live_tracker']).is_absolute() and '..' not in Path(p['live_tracker']).parts,'absolute selected live tracker required')
    if routed:
        r=p['routing'];require(set(r)=={'module','implementation','method_sha256'} and isinstance(r['module'],str) and r['module'].startswith('scripts.batch.') and binding(r['implementation']['path'])==r['implementation'],'closed selected historical route required')
    return p

def historical_method(original,p):
    if p['schema_version'] not in (REREAD,ROUTED):return original
    fn=original.__func__;namespace=fn.__globals__
    require(fn.__closure__ is None and callable(namespace.get('binding')) and callable(namespace.get('checked')),'plain historical tracker reader required')
    original_binding=namespace['binding']
    def historical_binding(path):
        if str(path)!=p['live_tracker']:return original_binding(path)
        require(read(binding(p['live_tracker']))==read(p['after']),'selected live tracker changed after completed commit')
        read(p['before']);return p['before']
    copied=types.FunctionType(fn.__code__,{**namespace,'binding':historical_binding},fn.__name__,fn.__defaults__,fn.__closure__)
    copied.__kwdefaults__=fn.__kwdefaults__
    return types.MethodType(copied,original.__self__)

def install_projection(worker_module, certificate, reader=None, router=None):
    p = validate(certificate)
    if reader is None:reader = worker_module.declared_namespace['_source_cards']
    else:require(p['schema_version']in(REREAD,ROUTED),'explicit reader requires declared reread certificate')
    original = reader.historical_lease
    require(reader.authority == p['authority'] and binding(inspect.getfile(type(reader))) == p['reader'] and
            hashlib.sha256(inspect.getsource(original).encode()).hexdigest() == p['method_sha256'], 'historical reader declaration differs')
    spec = importlib.util.spec_from_file_location('_selected_completed_lane_' + p['validator']['sha256'], p['validator']['path'])
    validator = importlib.util.module_from_spec(spec); sys.modules[spec.name] = validator; spec.loader.exec_module(validator)
    from scripts.batch import harbor_tracker as schema, harbor_tracker_lifecycle as lifecycle
    require(binding(schema.__file__) == p['schema'] and binding(lifecycle.__file__) == p['lifecycle'], 'declared lifecycle namespace differs')
    dependencies = types.SimpleNamespace(schema=schema, life=lifecycle)
    recomputing = ContextVar("completed_lane_" + certificate["sha256"], default=False)
    def historical(self, worker, current):
        value = validate(certificate)
        if str(worker.run) != value['worker_run']:
            return original(worker, current)
        after = read(value['after'])
        if value['schema_version']in(REREAD,ROUTED):require(str(worker.context.tracker_root/(after['workbook_id']+'.json'))==value['live_tracker'],'reader selected a foreign live tracker')
        require(current == after, 'unrecognized post-commit tracker history')
        if not recomputing.get():
            token = recomputing.set(True)
            try:
                validator.validate(W=worker_module, T=dependencies, worker=worker,
                    before_binding=value['before'], after_binding=value['after'],
                    completion=value['completion'], kind=value['kind'])
            finally:
                recomputing.reset(token)
        # The original gate itself reads this historical authority. During its
        # read-only CAS comparison, nested reads use the same exact predecessor;
        # they never assume the outer gate passed or alter current live state.
        return historical_method(original,value)(worker, read(value['before']))
    reader.historical_lease = types.MethodType(historical, reader)
    if p['schema_version']==ROUTED:
        r=p['routing'];require(router is not None and sys.modules.get(r['module'])is router,'selected historical route namespace differs')
        route=router.historical_lease
        require(isinstance(route,types.FunctionType) and binding(inspect.getfile(route))==r['implementation'] and hashlib.sha256(inspect.getsource(route).encode()).hexdigest()==r['method_sha256'],'historical route code differs')
        require(set(route.__kwdefaults__ or {})=={'_old','_new'} and route.__kwdefaults__['_new']==original,'historical route no longer captures exact original reader')
        copied=types.FunctionType(route.__code__,route.__globals__,route.__name__,route.__defaults__,route.__closure__)
        copied.__kwdefaults__={**route.__kwdefaults__,'_new':reader.historical_lease}
        router.historical_lease=copied
    else:require(router is None,'route projection requires explicit routed certificate')
    return worker_module
