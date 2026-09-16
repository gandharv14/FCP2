"""Scope original preparation checks to authenticated pre-import witnesses.

The current refresh validator and worker lease remain outside this projection.
Only the declared historical readers receive original path/hash identities.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'canonical refresh witness required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def witnesses(prepared,tracker,decode):
    value=json.loads(Path(prepared['path']).read_bytes());require(binding(prepared['path'])==prepared,'prepared refresh changed')
    before=value['before'];require(binding(before['path'])==before and before['sha256']==tracker['sha256'],'historical tracker is not original prepared before')
    root=Path(prepared['path']).parent
    require(Path(before['path'])==root/'before.json','foreign tracker witness')
    current=decode(value['selection']);normalization=Path(current.content.normalization_run)
    rows=[dict(original=tracker,copy=before)]
    names={'normalized_final':'normalized.json','normalization_report':'normalization_report.json','normalization_exclusions':'exclusions.json'}
    seen=set()
    for r in value['mutable_witnesses']:
        require(set(r)=={'key','original','copy'} and r['key']in names and r['key']not in seen,'invalid mutable refresh witness')
        seen.add(r['key']);name=names[r['key']]
        require(Path(r['original']['path'])==normalization/name and Path(r['copy']['path'])==root/'generated-before'/name,'foreign mutable output witness')
        rows.append(dict(original=r['original'],copy=r['copy']))
    return rows


@contextmanager
def readers(modules,rows):
    by_path={};by_binding={}
    for r in rows:
        original,copy=r['original'],r['copy'];require(set(original)==set(copy)=={'path','sha256'} and binding(copy['path'])==copy and copy['sha256']==original['sha256'],'refresh historical copy differs')
        require(original['path']not in by_path,'ambiguous historical path')
        by_path[original['path']]=original;by_binding[(original['path'],original['sha256'])]=copy
    saved=[]
    try:
        for m in modules:
            old_binding,old_read=m.binding,m.read
            def projected_binding(path,original=old_binding):
                row=by_path.get(str(Path(path)))
                if row is None:return original(path)
                copy=by_binding[(row['path'],row['sha256'])];require(binding(copy['path'])==copy,'historical witness changed during inspection');return row
            def projected_read(row,original=old_read):
                copy=by_binding.get((row.get('path'),row.get('sha256')))
                if copy is None:return original(row)
                require(binding(copy['path'])==copy,'historical witness changed during inspection');return json.loads(Path(copy['path']).read_bytes())
            saved.append((m,old_binding,old_read));m.binding,m.read=projected_binding,projected_read
        yield
    finally:
        for m,b,r in reversed(saved):m.binding,m.read=b,r


@contextmanager
def declared_readers(N,plan,selected_prepared,rows,committed):
    """Install only the two declared historical preparation validators."""
    old=N.module
    for r in (plan['prepared_helper'],plan['completed_helper'],selected_prepared):
        require(binding(r['path'])==r,'declared historical reader changed')
    require(Path(plan['prepared_helper']['path']).name==Path(selected_prepared['path']).name=='normalization_prepared_refresh.py'
        and Path(plan['completed_helper']['path']).name=='normalization_completed_emission.py',
        'unrecognized historical reader declaration')
    previous=N.read(N.read(plan['prepared_refresh'])['previous'])
    def factory(name,row):
        selected=selected_prepared if row==plan['prepared_helper']else row
        m=old(name,selected)
        if row==plan['prepared_helper']:
            validate=m.validate
            def inspected(reader,*args,**kwargs):
                require(args and args[0]==plan,'foreign prepared-reader plan')
                with readers([reader],rows):return validate(reader,*args,**kwargs,committed=committed)
            m.validate=inspected
        elif row==plan['completed_helper']:
            validate=m.validate_single
            def inspected(reader,*args,**kwargs):
                require(args and args[0]==previous,
                    'foreign completed-emission plan')
                with readers([m,reader],rows):return validate(reader,*args,**kwargs)
            m.validate_single=inspected
        return m
    N.module=factory
    try:yield
    finally:N.module=old


@contextmanager
def takeover_prior(C,worker,checkpoint,completion,T,*,previous_completion=None):
    """Use one actual replacement lease with its unchanged imported checkpoint."""
    original=C._prior
    def selected(bound,descriptor):
        value,previous=original(bound,descriptor)
        if bound!=checkpoint:return value,previous
        done=T.read(completion);stop=T.read(done['stopped_owner_proof'])
        before,after,current=T.validate_completion(completion)
        original_before=before
        if previous_completion is not None:
            prior=T.read(previous_completion);prior_stop=T.read(prior['stopped_owner_proof']);original_before,prior_after,_=T.validate_completion(previous_completion)
            require(stop['selection']['prior_completion']==previous_completion and prior_stop['selection']['checkpoint']==checkpoint
                and stop['before']['sha256']==prior['after']['sha256'] and prior_after==before,'replacement takeover chain detached')
        require(stop['selection']['checkpoint']==(previous_completion or checkpoint)
            and value['status']=='IMPORTED' and value['descriptor']==descriptor
            and before['workbook_id']==after['workbook_id']==worker.bootstrap.workbook
            and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']
            and (previous.owner,previous.token)==(original_before['leases']['task_agent']['owner'],original_before['leases']['task_agent']['token'])
            and previous.revision<=original_before['revision'] and current.revision==after['revision'],
            'replacement lease is detached from imported checkpoint')
        C.worker._active(worker,current)
        return value,current
    C._prior=selected
    try:yield
    finally:C._prior=original


@contextmanager
def phase1_projection(F,prepared,rows):
    """Revalidate one prepared phase-one selection against its actual archives.

    Only F.unchanged's original projection call sees the archived hash checks.
    F.validate's current phase-two checks and canonical lane module stay live.
    """
    from types import FunctionType,SimpleNamespace
    import ast,inspect,textwrap
    require(binding(prepared['path'])==prepared,'prepared refresh changed')
    value=json.loads(Path(prepared['path']).read_bytes())
    require(value['code']==binding(F.__file__),'refresh validator identity changed')
    expected=F.decode(value['selection']).content
    original=F.unchanged;projection=F.W.lanes.normalization_projection
    require(original.__globals__ is F.__dict__ and original.__closure__ is None
        and projection.__globals__ is F.W.lanes.__dict__ and projection.__closure__ is None,
        'plain original refresh projection required')
    tree=ast.parse(textwrap.dedent(inspect.getsource(original)))
    calls=[n for n in ast.walk(tree)if isinstance(n,ast.Call)and ast.unparse(n.func)=='W.lanes.normalization_projection']
    require(len(calls)==1 and ast.unparse(calls[0])=='W.lanes.normalization_projection(b, phase1=True)',
        'original single phase-one projection call differs')
    allowed={}
    for witness in value['mutable_witnesses']:
        pair=dict(original=witness['original'],copy=witness['copy'])
        require(pair in rows and binding(pair['copy']['path'])==pair['copy']
            and pair['original']['sha256']==pair['copy']['sha256'],
            'original mutable evidence archive differs')
        require(pair['original']==expected.bindings[witness['key']],
            'mutable archive detached from prepared content')
        allowed[(pair['original']['path'],pair['original']['sha256'])]=pair['copy']
    checked=projection.__globals__['checked_record']
    def checked_archive(row):
        copy=allowed.get((row.get('path'),row.get('sha256')))
        if copy is None:return checked(row)
        require(binding(copy['path'])==copy,'mutable archive changed during phase-one validation')
        checked(copy)
        return dict(row)
    selected=FunctionType(projection.__code__,{**projection.__globals__,'checked_record':checked_archive},projection.__name__,projection.__defaults__)
    selected.__kwdefaults__=projection.__kwdefaults__
    def projected(content,*,phase1=False):
        require(binding(prepared['path'])==prepared and binding(F.__file__)==value['code'],
            'historical projection identity changed')
        if phase1 and content==expected:return selected(content,phase1=True)
        return projection(content,phase1=phase1)
    lanes=SimpleNamespace(**{**F.W.lanes.__dict__,'normalization_projection':projected})
    worker=SimpleNamespace(**{**F.W.__dict__,'lanes':lanes})
    unchanged=FunctionType(original.__code__,{**original.__globals__,'W':worker},original.__name__,original.__defaults__)
    unchanged.__kwdefaults__=original.__kwdefaults__
    F.unchanged=unchanged
    try:yield
    finally:F.unchanged=original
