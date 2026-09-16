"""Declared shared takeover runtime over an unchanged retained task context.

The selected context supplies its previously reviewed path layout and imports.
Only the hash-bound shared lifecycle/takeover/allowance implementation supplies
new allowance semantics. No old module, file, descriptor or tracker is edited.
This module has a read-only probe API; root separately owns any actual CAS.
"""
from pathlib import Path
from dataclasses import asdict
import hashlib
import importlib.util
import json
import sys
import time
import types


def require(ok, message):
    if not ok:raise ValueError(message)


def checked(row):
    require(set(row)=={'path','sha256'},'exact implementation binding required')
    p=Path(row['path']);require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)), 'regular bound file required')
    raw=p.read_bytes();require(hashlib.sha256(raw).hexdigest()==row['sha256'],'selected bytes changed')
    return raw


def module(name,row):
    checked(row);spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def select_layout(T,row):
    """Select only a retained layout function, with explicit full-file witness."""
    import inspect
    require(set(row)=={'module','function_sha256'},'closed retained layout witness required')
    original=module('_allowance_direct_retained_layout',row['module'])
    require(hashlib.sha256(inspect.getsource(original._layout).encode()).hexdigest()==row['function_sha256'],'retained layout function changed')
    T._layout=types.FunctionType(original._layout.__code__,T.__dict__,original._layout.__name__,original._layout.__defaults__,original._layout.__closure__)
    T.HISTORICAL_IMPLEMENTATIONS=frozenset(T.HISTORICAL_IMPLEMENTATIONS)|{row['module']['sha256']}


def direct(p):
    """Use ordinary configured custody without constructing a task-specific loader."""
    require(set(p)in ({'schema_version','original_snapshot','shared','allowance','selection'},{'schema_version','original_snapshot','shared','allowance','selection','layout'}),'closed direct takeover selection required')
    require(set(p['shared'])=={'lifecycle','takeover','allowance','stdlib'},'complete shared dependency selection required')
    for row in [p['allowance'],*p['shared'].values()]:checked(row)
    snapshot=json.loads(checked(p['original_snapshot']));root=Path(snapshot['root'])
    require(root.is_absolute() and root.is_dir() and not any(x.is_symlink() for x in (root,*root.parents)),'canonical original code root required')
    expected={x['relative']:x['sha256'] for x in snapshot['files']}
    require(len(expected)==len(snapshot['files']),'duplicate original code records')
    actual={}
    for path in root.rglob('*'):
        require(not path.is_symlink(),'original code contains symlink')
        if path.is_file() and '__pycache__' not in path.relative_to(root).parts:
            actual[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    require(actual==expected,'original complete code snapshot changed')
    module('_allowance_direct_stdlib',p['shared']['stdlib']).preload_inspect()
    sys.path.insert(0,str(root))
    # Direct profiles use the ordinary configured path layout. Historical alias
    # layouts continue through the original context branch below.
    for name in ('scripts','scripts.batch'):
        cached=sys.modules.get(name)
        if cached is not None:
            locations=list(getattr(cached,'__path__',[]))
            require(locations and all(Path(x).is_relative_to(root) for x in locations),'direct takeover requires isolated original imports')
    import scripts.batch as package
    life=module('scripts.batch.harbor_tracker_lifecycle',p['shared']['lifecycle']);package.harbor_tracker_lifecycle=life
    allowance=module('scripts.batch.harbor_continuation_allowance',p['shared']['allowance']);package.harbor_continuation_allowance=allowance
    T=module('_allowance_direct_takeover',p['shared']['takeover'])
    if 'layout'in p:select_layout(T,p['layout'])
    values=dict(p['selection']);values['campaign_root']=Path(values['campaign_root'])
    require(values.get('config') is not None and values.get('allowance')==p['allowance'],'direct profile requires configured allowance custody')
    s=T.Selection(**values);before=json.loads(checked(s.tracker))
    _,duration=allowance.lease_limits(p['allowance'],before,at=time.time_ns())
    require(s.duration_ns==duration,'direct lease duration differs from explicit allowance')
    # Do not infer eligibility here: probe/replace_expired still run the full
    # owning recipe, prior completion validation, expiry and stopped locks.
    return dict(p,workbook=s.workbook,tracker=s.tracker,prior_completion=s.prior_completion),T,s,dict(original_snapshot=p['original_snapshot'],shared=p['shared'])


def load(binding):
    p=json.loads(checked(binding))
    if p.get('schema_version')=='harbor-allowance-takeover-direct/v1':return direct(p)
    require(set(p)=={'schema_version','context','context_plan','shared','allowance','tracker','prior_completion','owner','workbook','checkpoint'},'closed shared takeover selection required')
    require(p['schema_version']=='harbor-allowance-takeover-runtime/v1','unknown runtime selection')
    require(set(p['shared'])=={'lifecycle','takeover','allowance','stdlib'},'complete shared dependency selection required')
    for row in [p['context'],p['context_plan'],p['allowance'],*p['shared'].values()]:checked(row)
    module('_allowance_stdlib',p['shared']['stdlib']).preload_inspect()
    # Existing load validates unchanged full source snapshot, historical config,
    # guard/checkpoint and declared original helper closure before returning T.
    context=module('_allowance_retained_context',p['context'])
    loaded=context.load(p['context_plan'])
    require(isinstance(loaded,tuple) and len(loaded)==4,'retained context must return plan, proof, takeover, selection')
    _,_,original,selected=loaded
    require(selected.workbook==p['workbook'] and selected.owner==p['owner'],'retained task owner/workbook changed')
    original_binding={'path':original.__file__,'sha256':hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()}
    # Keep the old module intact for any historical reader holding its reference.
    life=module('scripts.batch.harbor_tracker_lifecycle',p['shared']['lifecycle'])
    import scripts.batch as package
    package.harbor_tracker_lifecycle=life
    allowance=module('scripts.batch.harbor_continuation_allowance',p['shared']['allowance']);package.harbor_continuation_allowance=allowance
    T=module('_allowance_selected_takeover',p['shared']['takeover'])
    # A declared dependency substitution, not a relaxed config predicate: exact
    # original layout bytecode remains responsible for its historical aliases.
    T._layout=types.FunctionType(original._layout.__code__,T.__dict__,original._layout.__name__,original._layout.__defaults__,original._layout.__closure__)
    T.HISTORICAL_IMPLEMENTATIONS=frozenset(T.HISTORICAL_IMPLEMENTATIONS)|{original_binding['sha256']}
    values=asdict(selected);values.update(tracker=p['tracker'],prior_completion=p['prior_completion'],allowance=p['allowance'],checkpoint=p['checkpoint'])
    before=json.loads(checked(p['tracker']))
    count,duration=allowance.lease_limits(p['allowance'],before,at=time.time_ns())
    values['duration_ns']=duration
    selection=T.Selection(**values)
    require(selection.campaign_root==selected.campaign_root and selection.checkpoint in (selected.checkpoint,p['prior_completion']) and selection.guard==selected.guard,'only original checkpoint or exact prior takeover completion may carry custody')
    return p,T,selection,dict(retained_takeover=original_binding,layout_code_sha256=hashlib.sha256(original._layout.__code__.co_code).hexdigest(),shared=p['shared'])


def probe(binding):
    p,T,s,dependencies=load(binding)
    before,old,run,evidence=T._recipe(s)
    chain=T._prior_chain(s,before)
    count,duration=T._limits(s,before,at=time.time_ns())
    require(old['expires_at']<=time.time_ns(),'live lease is not eligible for expired takeover')
    with T._stopped_locks(s):
        require(T.record(s.tracker['path'])==s.tracker,'tracker changed during stopped-lock observation')
    return dict(status='ALLOWANCE_TAKEOVER_BOUNDARY_VALIDATED_NOT_STARTED',workbook=s.workbook,tracker=s.tracker,
                replacement_count=T.life.validate_lifecycle(before),next_replacement=T.life.validate_lifecycle(before)+1,
                maximum_replacements=count,duration_ns=duration,allowance=p['allowance'],prior_completion=p['prior_completion'],
                original_guard=s.guard,original_checkpoint=s.checkpoint,evidence_count=len(evidence),chain_records=len(chain),
                dependencies=dependencies,models=0,claims=0,lease_mutations=0)
