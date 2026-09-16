"""Original snapshot/repair custody and scoped instruction continuation.

A retained failure stays visible throughout the remaining instruction phases.
Only the original complete-readiness transition can clear it before enqueue.
"""
import copy,types
from contextlib import contextmanager
from dataclasses import asdict,replace
from pathlib import Path

SCHEMA='harbor-preserved-owning-gate-repair/v1'
READY='PRESERVED_FAILURE_READY'
PHASES={'disclosure':'disclosure-commit','naturalization':'nat-commit','verification':'verification-commit','dialogue':'dialogue-commit'}
PROFILE_FIELDS={'implementation','source','workbook','failure_transaction','failure_completion','failure_proof','tracker','custody_before','custody_after','custody_transition','trusted_public_key','original_descriptor','failed_step','failed_outcome','failed_sidecar','worker_outcome','renewal_module','owning'}
KEYS={'schema_version','implementation','profile','worker_run','worker_root','source','phase','selection','lease','prior','failure_transaction','failure_completion','failure_result','before_snapshot','snapshot_transaction','snapshot_completion','after_snapshot','after_repair','snapshot','original_descriptor','renewal_module','renewal_completion','before_renewal','after_renewal','models_dispatched','semantic_budget_reset'}

def require(ok,message):
    if not ok:raise ValueError(message)

def function_sha(function):
    """Use the same AST witness without requiring a newer task-code module."""
    import ast,hashlib,inspect,textwrap
    return hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(function))).body[0],include_attributes=False).encode()).hexdigest()


def intended_gate(notes,p,result):
    """A source-context rejection retains its actual interrupted owning phase."""
    if result['gate']!='source':
        require('source_context'not in p and result['gate']in PHASES,'unsupported owning failure')
        return result['gate']
    selected=p.get('source_context',{})
    require(set(selected)=={'kind','prefix'}and selected['kind']in PHASES,'closed source-context owning phase required')
    phase=PHASES[selected['kind']];blocked=notes.read(p['failed_step']);task=notes.read(blocked['intent'])
    outcome=notes.read(p['failed_outcome']);guard=notes.read(outcome['resource']);intent=notes.read(guard['intent'])
    failed=notes.read(outcome['result']);prior=notes.read(intent['arguments']['checkpoint'])
    require(outcome['status']=='COMPLETED_OPERATION'and guard['returncode']==0 and guard['failure']is None
        and intent['action']=='step'and intent['models']is False
        and intent['code']==p['owning']['instruction']
        and intent['arguments']['descriptor']==p['original_descriptor']==failed['descriptor']==prior['descriptor']
        and failed['status']==blocked['status']=='OWNING_GATE_FAILED'
        and failed['checkpoint']==p['failed_step']and failed['next_step']==blocked['step']==task['step']==phase
        and prior['status']=='STEP_COMPLETE'and prior['next_step']==phase
        and task['previous']==prior['checkpoint']
        and failed['selection']==blocked['selection']==task['selection']==prior['selection'],
        'source rejection detached from original owning phase')
    return selected['kind']

def corrected_gate(W,notes,p,result,before,selection,worker,*,original=None):
    """Revalidate every original prefix gate before retrying a source-context commit."""
    kind=intended_gate(notes,p,result);check=original or W.check_completed_gate
    if result['gate']!='source':return check(before,W.CompletionEvidence(kind,selection),worker.context)
    from scripts.batch import harbor_completed_lanes as completed
    prefix=p['source_context']['prefix']
    require(prefix==W.record(Path(worker.root)/'scripts/batch/harbor_instruction_repair.py'),
        'source-context prefix differs from original worker code')
    R=_module(W,prefix,'preserved_source_context_prefix');R.check_completed_gate=check
    rows=R.prefix(before,selection,worker.context,stop=kind)
    expected=[x for x in completed.STEPS[:[x[0]for x in completed.STEPS].index(kind)]]
    require([x['kind']for x in rows]==[x[0]for x in expected], 'source-context prefix omitted an owning gate')
    view=copy.deepcopy(before)
    for row,(name,lane)in zip(rows,expected):
        gate=row['result'];require(gate['passed']is True and gate['lane']==lane,'source-context prefix still rejects')
        view['bindings'].update(gate['bindings'])
        view['lane_state'][lane].update(state='running'if name=='normalization_phase1'else'passed',phase=gate.get('phase'))
    return check(view,W.CompletionEvidence(kind,selection),worker.context)

@contextmanager
def source_gate_scope(W,I,value,base):
    """Keep the recorded source failure live until original enqueue clears it."""
    if value.get('failure_result',{}).get('gate')!='source':
        yield;return
    from scripts.batch import harbor_completed_lanes as completed,harbor_lane_notes as notes
    p=W.read(value['profile']);kind=intended_gate(notes,p,value['failure_result'])
    worker=I.worker_decode(I.read(p['original_descriptor'])['worker'])
    original=completed.check_completed_gate;worker_original=W.check_completed_gate
    def checked(before,packet,context,**kwargs):
        if packet.kind!=kind or before['promotion_failure']is None:
            return original(before,packet,context,**kwargs)
        require(not kwargs and context==worker.context and before['workbook_id']==p['workbook']
            and before['promotion_failure']==base['promotion_failure']
            and packet.selection.content.bindings['original_raw_source']==p['source'],
            'source-context gate belongs to another preserved failure')
        return corrected_gate(W,notes,p,value['failure_result'],before,packet.selection,worker,original=original)
    completed.check_completed_gate=checked;W.check_completed_gate=checked
    try:yield
    finally:completed.check_completed_gate=original;W.check_completed_gate=worker_original

def validate_failure(notes,profile):
    """Retained original validator result must reproduce every failure byte."""
    require(set(profile)-{'source_context'}==PROFILE_FIELDS and profile['implementation']==notes.record(__file__),
            'closed selected preserved-failure profile required')
    d=notes.read(profile['failure_transaction']);proof=notes.read(profile['failure_proof'])
    require(proof['status']=='EXACT_ORIGINAL_FAILURE_MUTATION_PASS'and proof['models_dispatched']==proof['task_mutations']==0,
            'exact original failure proof required')
    saved=proof['result'];require(saved['transaction']==profile['failure_transaction']and saved['completion']==profile['failure_completion']
        and saved['before']==d['before']and saved['after']==d['after']and saved['models_dispatched']==saved['task_mutations']==0,
        'failure proof detached from original transaction')
    before=notes.read(d['before']);after=notes.read(d['after']);result=saved['result'];wb=before['workbook_id']
    require(result['passed']is False and result['terminal']is False and intended_gate(notes,profile,result)in PHASES,
            'unsupported or terminal owning failure')
    require(Path(profile['failure_completion']['path'])==Path(profile['failure_transaction']['path']).parent/'complete.json'
        and notes.read(profile['failure_completion'])==dict(tracker_sha256=d['after']['sha256'],notes_sha256=d['notes']['sha256']),
        'original failure completion changed')
    rebuilt,rendered=notes._failure_after(before,result,after['updated_at'],Path(after['agent_notes']['path']))
    require(rebuilt==after and Path(d['notes']['path']).read_bytes()==rendered,'failure no longer matches original mutation')
    notes.lifecycle.checked_record(d['notes']);notes.validate_notes_pair(after,rendered)
    old=before['leases']['task_agent'];require(old['heartbeat_at']<=after['updated_at']<old['expires_at'],'failure outside original custody')
    require(after['workbook_id']==profile['workbook']and after['bindings']['original_raw_source']==profile['source'],
            'failure belongs to another source')
    return d,before,after,result


def _repair_after(F,notes,before_binding,after_binding,snapshot_notes,context):
    """Compare the unchanged original begin_repair mutation without CAS."""
    before=notes.read(before_binding);after=notes.read(after_binding);lease=before['leases']['task_agent'];seen=[]
    fn=F.FailureSnapshots.begin_repair;namespace=dict(fn.__globals__);schema=types.SimpleNamespace(**vars(namespace['schema']))
    def compare(path,**kw):
        require(Path(path)==Path(before_binding['path'])and kw['expected_revision']==before['revision']and kw['expected_sha256']==before_binding['sha256']
            and (kw['owner'],kw['token'],kw['scope'])==(lease['owner'],lease['token'],'workbook:'+before['workbook_id']),
            'original repair CAS custody differs')
        result=copy.deepcopy(before);kw['mutate'](result)
        require(all(result.get(k)==before.get(k)or k in kw['allowed_fields']for k in before),'repair changed unowned fields')
        now=after['updated_at'];require(lease['heartbeat_at']<=now<lease['expires_at'],'repair outside original live lease')
        result['revision']=before['revision']+1;result['updated_at']=now;result['leases']['task_agent']['heartbeat_at']=now
        require(result==after,'repair differs from exact original mutation');seen.append(True)
        return dict(revision=result['revision'],sha256=after_binding['sha256'],tracker=result)
    schema.cas_mutate=compare;namespace['schema']=schema
    original=types.FunctionType(fn.__code__,namespace,fn.__name__,fn.__defaults__,fn.__closure__)
    def synchronized(wb):
        require(wb==before['workbook_id'],'foreign repair notes')
        notes.lifecycle.checked_record(snapshot_notes);notes.validate_notes_pair(before,Path(snapshot_notes['path']).read_bytes())
    proxy=types.SimpleNamespace(context=context,_path=lambda wb:Path(before_binding['path']),require_synchronized=synchronized)
    original(proxy,before['workbook_id'],expected_revision=before['revision'],expected_sha256=before_binding['sha256'],owner=lease['owner'],token=lease['token'])
    require(seen==[True],'one original repair comparison required')
    return after


def validate(W,receipt):
    from scripts.batch import harbor_failure_snapshot as F,harbor_lane_notes as notes
    value=W.read(receipt);require(set(value)==KEYS and value['schema_version']==SCHEMA and value['implementation']==W.record(__file__),
                                  'closed preserved gate repair receipt required')
    p=W.read(value['profile']);require(p['implementation']==value['implementation']and p['source']==value['source']
        and value['models_dispatched']==0 and value['semantic_budget_reset']is False,'repair changed code, source or semantic budget')
    d,_,failure,result=validate_failure(notes,p)
    require(value['failure_transaction']==p['failure_transaction']and value['failure_completion']==p['failure_completion']
        and value['failure_result']==result and value['phase']==PHASES[intended_gate(notes,p,result)], 'repair failure authority changed')
    before=W.read(value['before_snapshot']);validate_renewal(W,value,failed=failure);first=W.read(value['after_snapshot']);second=W.read(value['after_repair'])
    require(before['promotion_failure']==failure['promotion_failure']and before['failure_history']==failure['failure_history']
        and before['bindings']==failure['bindings']and before['repairs']==failure['repairs'], 'repair changed original failed source or consumption')
    tx=W.read(value['snapshot_transaction'])
    require(tx['schema_version']==F.SCHEMA and tx['snapshot']==value['snapshot']and tx['before']==value['before_snapshot']and tx['after']==value['after_snapshot']
        and value['snapshot_completion']==W.record(Path(value['snapshot_transaction']['path']).parent/'complete.json')
        and W.read(value['snapshot_completion'])==dict(tracker_sha256=tx['after']['sha256'],notes_sha256=tx['notes']['sha256']),
        'snapshot lacks original complete pair')
    rebuilt,rendered=F.after_snapshot(before,value['snapshot'],first['updated_at'],Path(first['agent_notes']['path']))
    require(rebuilt==first and Path(tx['notes']['path']).read_bytes()==rendered,'snapshot differs from original byte-preserving mutation')
    context=notes.RootContext(Path(p['tracker']['path']).parent,p['trusted_public_key'])
    _repair_after(F,notes,value['after_snapshot'],value['after_repair'],tx['notes'],context)
    require(second['promotion_failure']==first['promotion_failure']and value['lease']==dict(owner=second['leases']['task_agent']['owner'],token=second['leases']['task_agent']['token'],revision=second['revision'],sha256=value['after_repair']['sha256']),
            'repair did not retain exact failure and resulting custody')
    return value,second


def _clone(function,old,new):
    import ast,inspect,textwrap
    before=ast.parse(old).body[0];replacement=ast.parse(new).body[0];tree=ast.parse(textwrap.dedent(inspect.getsource(function)));count=0
    class Change(ast.NodeTransformer):
        def visit_Expr(self,node):
            nonlocal count
            if ast.dump(node,include_attributes=False)==ast.dump(before,include_attributes=False):count+=1;return ast.copy_location(replacement,node)
            return self.generic_visit(node)
    tree=Change().visit(tree);require(count==1,'original owning predicate changed')
    return tree


@contextmanager
def active_scope(W,*,receipt,profile):
    """All original live custody checks remain, with one exact failure predicate."""
    import ast
    value,base=validate(W,receipt);old=W.active
    require(profile['worker']==W.record(W.__file__)and profile['active']==function_sha(old),
            'original active function witness changed')
    def allowed(worker,before):
        require(str(worker.run)==value['worker_run']and str(worker.root)==value['worker_root'],'foreign preserved-failure worker')
        validate(W,receipt)
        require(before['bindings']['original_raw_source']==value['source']and before['failure_history'][:len(base['failure_history'])]==base['failure_history']
            and before['handoffs'][:len(base['handoffs'])]==base['handoffs'],'preserved failure lost original source or history')
        return before['promotion_failure']is None or before['promotion_failure']==base['promotion_failure']
    tree=_clone(old,"require(before['promotion_failure'] is None, 'existing failure requires root-owned snapshot/repair before continuing')",
                    "require(_preserved_failure(worker,before), 'different failure requires its own root repair')")
    scope=dict(old.__globals__);scope['_preserved_failure']=allowed;exec(compile(ast.fix_missing_locations(tree),old.__code__.co_filename,'exec'),scope)
    W.active=scope[old.__name__]
    try:yield
    finally:W.active=old


@contextmanager
def instruction_scope(W,I,*,receipt,profile):
    """Consume a named repair handoff; preserve every original later checkpoint."""
    import ast,inspect,textwrap
    value,base=validate(W,receipt);old_step=W._step;old_directory=W.step_directory;old_prior=I._prior;old_operation=W._operation
    require(set(profile)=={'worker','instruction','active','step','directory','prior'}
        and profile['worker']==W.record(W.__file__)and profile['instruction']==W.record(I.__file__)
        and profile['step']==function_sha(old_step)and profile['directory']==function_sha(old_directory)
        and profile['prior']==function_sha(old_prior),'original preserved-repair instruction witnesses changed')
    directory=Path(receipt['path']).parent
    def selected(worker):return str(worker.run)==value['worker_run']and str(worker.root)==value['worker_root']
    def step_directory(worker,phase):
        if selected(worker)and phase==value['phase']:return directory/'steps'/phase
        return old_directory(worker,phase)
    def prior(row,descriptor):
        if row!=W.record(directory/'result.json'):return old_prior(row,descriptor)
        v,_=validate(W,receipt);raw=I.read(row);d=I.read(descriptor);old=I.read(v['original_descriptor'])
        s=I.codec.decode(v['selection']);lease=W.Lease(**v['lease'])
        require(descriptor==W.record(directory/'descriptor.json')
            and d==dict(old,origin=[*old['origin'],receipt],initial=dict(selection=v['selection'],lease=I.base.encode(lease)))
            and raw==I._result(READY,descriptor,s,lease,checkpoint=receipt,next_step=v['phase'],evidence=[receipt,v['profile']]),
            'repair handoff detached from original descriptor or exact resulting lease')
        return raw,s,lease
    tree=ast.parse(textwrap.dedent(inspect.getsource(old_step)));counts=[0,0,0]
    target1=ast.parse("require(checkpoint==required, 'exact root-selected preceding checkpoint hash required')").body[0]
    target2=ast.parse("require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')").body[0]
    class Change(ast.NodeTransformer):
        def visit_If(self,node):
            if ast.dump(node.test,include_attributes=False)==ast.dump(ast.parse("'intent' in bound",mode='eval').body,include_attributes=False):
                counts[2]+=1
                replacement=ast.parse("if current==_repair_receipt:\n    current=_repair_value['prior']").body[0]
                replacement.orelse=[node];return ast.copy_location(replacement,node)
            return self.generic_visit(node)
        def visit_Expr(self,node):
            if ast.dump(node,include_attributes=False)==ast.dump(target1,include_attributes=False):
                counts[0]+=1
                return ast.parse("if checkpoint==_repair_receipt:\n    require(next_phase==_repair_value['phase'] and required==_repair_value['prior'],'repair did not retain exact original predecessor')\n    required=_repair_receipt").body+[node]
            if ast.dump(node,include_attributes=False)==ast.dump(target2,include_attributes=False):
                counts[1]+=1
                return ast.parse("if checkpoint==_repair_receipt:\n    predecessor=_repair_value").body+[node]
            return self.generic_visit(node)
    tree=Change().visit(tree);require(counts==[1,1,1],'original first repair checkpoint predicates changed')
    scope=dict(old_step.__globals__);scope.update(_repair_receipt=receipt,_repair_value=value)
    # These references must follow the selected owning module after installation.
    def active(*args,**kwargs):return W.active(*args,**kwargs)
    scope.update(active=active,step_directory=step_directory)
    exec(compile(ast.fix_missing_locations(tree),old_step.__code__.co_filename,'exec'),scope)
    def step(*args,**kwargs):validate(W,receipt);return scope[old_step.__name__](*args,**kwargs)
    # A later explicitly selected lease handoff can authenticate and compose
    # this exact transformed function without discarding the failure checks.
    step._retained_step_ast=(scope,old_step.__name__,ast.unparse(ast.fix_missing_locations(tree)))
    def operation(worker,selection,lease,phase,path,**options):
        if selected(worker)and phase=='enqueue':
            facade,before,_=W.active(worker,selection,lease)
            if before['promotion_failure']is not None:
                validate(W,receipt)
                clear=facade.transition(selection.content.workbook,'clear',selection,expected_revision=lease.revision,
                    expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)
                lease=replace(lease,revision=clear['revision'],sha256=clear['sha256'])
                W.keep(path/'preserved-failure-cleared.json',dict(repair=receipt,result=clear,original_complete_readiness=True))
        return old_operation(worker,selection,lease,phase,path,**options)
    # The cloned original step resolves its operation in this exact scope.
    scope['_operation']=operation
    with active_scope(W,receipt=receipt,profile=profile),source_gate_scope(W,I,value,base):
        W.step_directory=step_directory;W._step=step;I._prior=prior;W._operation=operation
        try:yield
        finally:W.step_directory=old_directory;W._step=old_step;I._prior=old_prior;W._operation=old_operation


@contextmanager
def initial_active_scope(W,before,worker):
    """Preserve original live checks for the exact failed tracker before repair."""
    import ast
    old=W.active
    def allowed(actual,value):return actual==worker and value==before and value['promotion_failure']is not None
    tree=_clone(old,"require(before['promotion_failure'] is None, 'existing failure requires root-owned snapshot/repair before continuing')",
                    "require(_exact_failed_tracker(worker,before), 'different failure requires its own root repair')")
    scope=dict(old.__globals__);scope['_exact_failed_tracker']=allowed;exec(compile(ast.fix_missing_locations(tree),old.__code__.co_filename,'exec'),scope)
    W.active=scope[old.__name__]
    try:yield
    finally:W.active=old


def prepare(W,I,worker,selection,lease,*,profile,directory,renewal_module,custody):
    """Original live renewal, exact failure snapshot and fixed repair-start CAS."""
    import fcntl
    from scripts.batch import harbor_failure_snapshot as F,harbor_lane_notes as notes
    p=W.read(profile);d,_,failed,result=validate_failure(notes,p);directory=Path(directory)
    require(p['implementation']==W.record(__file__)and p['source']==selection.content.bindings['original_raw_source']
        and p['workbook']==selection.content.workbook and p['original_descriptor']is not None,
        'root repair profile belongs to another source')
    require(not directory.exists(),'repair already started; reconcile its exact original transactions')
    blocked=W.read(p['failed_step']);previous=W.read(blocked['intent'])
    require(blocked['status']=='OWNING_GATE_FAILED'and blocked['step']==PHASES[intended_gate(notes,p,result)]
        and blocked['selection']==I.codec.encode(selection)and previous['selection']==blocked['selection']
        and previous['code']==W.code(worker)and previous['initial']==W.record(worker.run/'initial.json'),
        'root repair is not the exact original failed worker step')
    original_descriptor=I.read(p['original_descriptor']);require(original_descriptor['worker']==I.worker_encode(worker),
                                                               'repair changed original typed worker')
    custody();path=worker.context.tracker_root/(p['workbook']+'.json');before=W.read(W.record(path))
    validate_custody(W,p,failed,lease)
    require(W.record(path)==p['tracker']and before['promotion_failure']==failed['promotion_failure']
        and before['failure_history']==failed['failure_history']and before['bindings']==failed['bindings']
        and before['lane_state']==failed['lane_state']and before['repairs']==failed['repairs'],
        'repair current tracker changed beyond declared custody')
    require(p['custody_after']['sha256']==p['tracker']['sha256']and W.read(p['custody_after'])==before,'exact verified current custody selection required')
    require(p['custody_before']==d['after'],'custody did not start at the actual recorded failure')
    directory.mkdir(parents=True);W.keep(directory/'intent.json',dict(schema_version=SCHEMA,profile=profile,tracker=p['tracker'],automatic_retry=False))
    with (worker.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with initial_active_scope(W,before,worker):
            W.active(worker,selection,lease)
            def full(observed,current):
                require(observed==before,'failure changed during repair validation')
                W.active(worker,selection,current)
                gate=corrected_gate(W,notes,p,result,observed,selection,worker)
                require(gate['passed']is True,'corrected original owning gate still rejects')
                proof=W.keep(directory/'corrected-gate.json',dict(profile=profile,tracker=p['tracker'],gate=gate,models_dispatched=0))
                return [profile,p['failure_proof'],proof]
            W.snapshot(directory/'lease-before.json',W.record(path))
            current,renewal=renewal_module.renew(life=notes.lifecycle,read=W.read,record=W.record,keep=W.keep,path=path,lease=lease,
                authority_record=profile,validation=full,custody=custody,directory=directory/'lease',retain_sufficient=True)
            W.snapshot(directory/'lease-after.json',W.record(path))
        facade=W.HarborLaneLifecycle(worker.context);custody()
        saved=facade.snapshot_failure(p['workbook'],expected_revision=current.revision,expected_sha256=current.sha256,owner=current.owner,token=current.token)
        tx=W.read(saved['journal']);snapshot_completion=W.record(Path(saved['journal']['path']).parent/'complete.json')
        current=replace(current,revision=saved['revision'],sha256=saved['sha256']);custody()
        repaired=facade.begin_repair(p['workbook'],expected_revision=current.revision,expected_sha256=current.sha256,owner=current.owner,token=current.token)
        current=replace(current,revision=repaired['revision'],sha256=repaired['sha256']);after=W.snapshot(directory/'after-repair.json',W.record(path))
    value=dict(schema_version=SCHEMA,implementation=W.record(__file__),profile=profile,worker_run=str(worker.run),worker_root=str(worker.root),source=p['source'],renewal_module=W.record(renewal_module.__file__),renewal_completion=renewal,before_renewal=W.record(directory/'lease-before.json'),after_renewal=W.record(directory/'lease-after.json'),phase=blocked['step'],selection=I.codec.encode(selection),lease=asdict(current),prior=previous['previous'],failure_transaction=p['failure_transaction'],failure_completion=p['failure_completion'],failure_result=result,before_snapshot=tx['before'],snapshot_transaction=saved['journal'],snapshot_completion=snapshot_completion,after_snapshot=tx['after'],after_repair=after,snapshot=tx['snapshot'],original_descriptor=p['original_descriptor'],models_dispatched=0,semantic_budget_reset=False)
    receipt=W.keep(directory/'permit.json',value);validate(W,receipt)
    descriptor=W.keep(directory/'descriptor.json',dict(original_descriptor,origin=[*original_descriptor['origin'],receipt],initial=dict(selection=value['selection'],lease=I.base.encode(current))))
    checkpoint=W.keep(directory/'result.json',I._result(READY,descriptor,selection,current,checkpoint=receipt,next_step=blocked['step'],evidence=[receipt,profile]))
    return receipt,descriptor,checkpoint,current


def _module(W,row,name):
    import importlib.util,sys
    require(W.record(row['path'])==row,'preserved repair module changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module


def validate_custody(W,p,failed,lease):
    current=W.read(p['custody_after']);transition=p['custody_transition']
    if transition is None:
        require(current==failed and p['tracker']['sha256']==p['custody_before']['sha256'],
                'unexplained failure custody transition')
        old=failed['leases']['task_agent'];require(asdict(lease)==dict(owner=old['owner'],token=old['token'],revision=failed['revision'],sha256=p['tracker']['sha256']),
                                                'failure lease differs from unchanged tracker')
        return
    require(set(transition)=={'runtime','plan','completion'},'closed completed failure takeover required')
    tp=W.read(transition['plan']);T=_module(W,tp['shared']['takeover'],'preserved_failure_takeover')
    runtime=_module(W,transition['runtime'],'preserved_failure_takeover_layout')
    require(W.record(T.life.__file__)==tp['shared']['lifecycle'],'failure takeover lifecycle differs')
    if 'layout'in tp:runtime.select_layout(T,tp['layout'])
    before,after,selected=T.validate_completion(transition['completion']);complete=W.read(transition['completion']);stop=W.read(complete['stopped_owner_proof'])
    require(T._selection(stop['selection'])==T._selection(tp['selection'])and before==failed and after==current
        and p['tracker']==dict(path=tp['selection']['tracker']['path'],sha256=complete['after']['sha256'])
        and asdict(lease)==asdict(selected)and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']==p['source'],
        'failure takeover is not the exact completed original transition')


def validate_renewal(W,value,*,failed):
    from scripts.batch import harbor_lane_notes as notes
    p=W.read(value['profile']);before=W.read(value['before_renewal']);after=W.read(value['after_renewal'])
    old=before['leases']['task_agent'];lease=W.Lease(old['owner'],old['token'],before['revision'],value['before_renewal']['sha256'])
    require(before==W.read(p['custody_after'])and value['before_snapshot']['sha256']==value['after_renewal']['sha256'],
            'snapshot omitted original completed renewal')
    validate_custody(W,p,failed,lease)
    L=_module(W,value['renewal_module'],'preserved_failure_lease_history');done=W.read(value['renewal_completion'])
    require(value['renewal_module']==p['renewal_module'],'repair renewal implementation changed')
    if done['schema_version']=='fleet-retained-live-continuation-lease/v1':
        require(before==after and done['authority']==value['profile']and done['lease']==asdict(lease)
            and done['tracker']==p['tracker']and done['minimum_remaining_ns']==L.DURATION_NS
            and done['lease_operations']==0 and done['semantic_budget_reset']is False and done['task_guards_changed']is False
            and done['automatic_retry']is False and old['heartbeat_at']<=done['observed_at_ns']
            and old['expires_at']-done['observed_at_ns']>=L.DURATION_NS,'retained live lease differs')
        for row in done['validated_evidence']:W.record(row['path'])==row or require(False,'retained repair evidence changed')
    else:
        L.validate_completed(life=notes.lifecycle,read=W.read,record=W.record,path=p['tracker']['path'],previous=lease,
            completion=value['renewal_completion'],before_binding=value['before_renewal'],after_binding=value['after_renewal'],authority_record=value['profile'])
