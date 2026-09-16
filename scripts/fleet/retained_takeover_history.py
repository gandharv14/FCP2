"""Scope an already authenticated takeover-chain reader over historical renewal.

The complete original renewal validator still runs. Only its exact lease-chain
call uses the selected original takeover reader; current lease checks stay live.
"""
import ast, hashlib, importlib.util, json, sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import FunctionType


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in(p,*p.parents)), 'regular takeover history evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'takeover history bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def module(name,row):
    require(binding(row['path'])==row,'takeover history code changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def exact_validator(original,selected):
    before=next(n for n in ast.parse(Path(original).read_text()).body if isinstance(n,ast.FunctionDef)and n.name=='_validate')
    after=next(n for n in ast.parse(Path(selected).read_text()).body if isinstance(n,ast.FunctionDef)and n.name=='_continued_validate')
    after.name=before.name
    old=[n for n in ast.walk(before)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='_renewal_chain']
    new=[n for n in ast.walk(after)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='_takeover_chain']
    require(len(old)==len(new)==1 and ast.dump(new[0])==ast.dump(ast.parse('_takeover_chain(worker,permit,complete,after)',mode='eval').body), 'one original lease-chain substitution required')
    new[0].func=old[0].func;new[0].args=old[0].args;new[0].keywords=old[0].keywords
    require(ast.dump(before)==ast.dump(after),'takeover reader changed unrelated renewal validation')


@contextmanager
def installed(row,R,W,worker,old_snapshot):
    p=read(row)
    require(set(p)=={'schema_version','helper','adapter','profile','original_renewal','original_snapshot'}
        and p['schema_version']=='fleet-retained-takeover-history/v1'and p['helper']==binding(__file__)
        and p['original_snapshot']==old_snapshot,'closed selected takeover history required')
    old=read(old_snapshot);root=Path(old['root']);expected={r['relative']:r['sha256']for r in old['files']}
    require(len(expected)==len(old['files'])and {str(x.relative_to(root)):binding(x)['sha256']for x in root.rglob('*')if x.is_file()and '__pycache__'not in x.parts}==expected,'takeover historical snapshot changed')
    require(p['original_renewal']==binding(root/'scripts/batch/harbor_instruction_lease_renewal.py')
        and Path(R.__file__).read_bytes()==Path(p['original_renewal']['path']).read_bytes(), 'selected renewal implementation differs')
    profile=read(p['profile']);a=module('retained_selected_takeover_chain',p['adapter'])
    require(a.ORIGINAL_R==p['original_renewal']and profile['adapter']==p['adapter']and profile['continued_checkpoint']==worker.lease_renewal,'takeover reader lost original checkpoint')
    complete=read(worker.lease_renewal);permit=read(complete['permit'])
    require(complete['expired_takeover']==profile['takeover']and permit['takeover_read_authority']==profile['authority'],'takeover authority differs')
    authority=read(profile['authority']);require(authority['reader']==p['adapter'],'takeover authority selected another reader')
    exact_validator(R.__file__,a.__file__)
    t=profile['takeover_implementation'];require(t==binding(root/'scripts/batch/harbor_lease_takeover.py'),'historical takeover root differs')
    T=module('retained_original_takeover_validator',t)
    T.life=module('retained_original_takeover_lifecycle',binding(root/'scripts/batch/harbor_tracker_lifecycle.py'))
    # All reader helper bytes were authenticated above. Its original validator
    # explicitly checks source, original completed guard and the actual T CAS.
    g=dict(R.__dict__);g.update(PROFILE=p['profile'],checked=a.checked,asdict=asdict)
    chain=FunctionType(a._takeover_chain.__code__,g,a._takeover_chain.__name__)
    original=R._validate
    def selected_chain(w,perm,comp,after):
        import scripts.batch as package
        name='scripts.batch.harbor_lease_takeover';prior=sys.modules.get(name);attr=getattr(package,'harbor_lease_takeover',None)
        sys.modules[name]=T;package.harbor_lease_takeover=T
        try:return chain(w,perm,comp,after)
        finally:
            if prior is None:sys.modules.pop(name,None)
            else:sys.modules[name]=prior
            if attr is None:delattr(package,'harbor_lease_takeover')
            else:package.harbor_lease_takeover=attr
    g['_takeover_chain']=selected_chain
    continued=FunctionType(a._continued_validate.__code__,g,a._continued_validate.__name__,a._continued_validate.__defaults__)
    continued.__kwdefaults__=a._continued_validate.__kwdefaults__
    def route(selected,before,seen,*,historical_code_inventory=None):
        if selected.lease_renewal!=profile['continued_checkpoint']:
            return original(selected,before,seen,historical_code_inventory=historical_code_inventory)
        if selected==worker and selected.root!=root and selected.warning_requalification is not None and historical_code_inventory is None:
            # The original validator first authenticates the warning's retained
            # predecessor. Only that resulting historical call uses our chain.
            return original(selected,before,seen,historical_code_inventory=None)
        require(selected.run==worker.run and selected.root==root and selected.warning_requalification is None,
                'takeover history may validate only original historical worker')
        require(historical_code_inventory==R._historical_inventory(selected,old_snapshot), 'authenticated historical code inventory required')
        return continued(selected,before,seen,historical_code_inventory=historical_code_inventory)
    g['_validate']=route;R._validate=route
    try:yield
    finally:R._validate=original
