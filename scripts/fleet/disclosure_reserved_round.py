"""Finish a sealed round reservation after an exact pre-request reader failure.

The original authority/ledger are never recreated. The sole code projection
replaces an optional producer helper with the original owning W.task contract.
"""
from contextlib import contextmanager
from pathlib import Path
import ast,hashlib,inspect,textwrap
from scripts.batch.harbor_admission import read,record,require

SCHEMA='fleet-disclosure-reserved-round/v1'

@contextmanager
def prefix_callback(original,runtime):
    """Keep the original reader's local prefix callback tied to its module.

    The reserved producer projection copies globals before historical archives
    are installed. Follow only that one callback; the authenticated archive
    reader still recomputes the complete original inventory on every call.
    """
    fn=original.validate
    require(record(fn.__code__.co_filename)==runtime and fn.__name__=='checked',
            'exact reserved reader callback required')
    cells=dict(zip(fn.__code__.co_freevars,(c.cell_contents for c in fn.__closure__ or ())))
    require(set(cells)=={'actual','original','selected','value'}and cells['original']is original,
            'reserved reader closure differs')
    value=cells['value'];actual=cells['actual'];scope=actual.__globals__
    require(record(original.__file__)==value['original']
            and record(cells['selected'].__file__)==value['selected']
            and scope['__file__']==original.__file__
            and all(scope[name]is getattr(original,name)for name in ('prefix_records','read','record','life')),
            'reserved reader prefix is not its original module callback')
    saved={name:scope[name]for name in ('prefix_records','read','record','life')}
    from types import SimpleNamespace
    for name in ('prefix_records','read','record'):
        scope[name]=lambda *args,_name=name,**kwargs:getattr(original,_name)(*args,**kwargs)
    scope['life']=SimpleNamespace(checked_record=lambda row:original.life.checked_record(row))
    try:yield
    finally:scope.update(saved)

@contextmanager
def reader(original,binding):
    value=read(binding)
    require(set(value)=={'original','selected'}and value['original']==record(original.__file__),
            'exact reserved round reader dependency required')
    from scripts.batch.harbor_disclosure_review_round import bound_module
    selected=bound_module(value['selected']);before=ast.parse(textwrap.dedent(inspect.getsource(original.validate)))
    after=ast.parse(textwrap.dedent(inspect.getsource(selected.validate)))
    expected_import=ast.parse('from scripts.batch.harbor_disclosure_command_repair import producer_run').body[0]
    new_import=ast.parse('from scripts.batch import harbor_instruction_worker as W').body[0]
    expected_call=ast.parse('producer_run(worker)',mode='eval').body
    new_call=ast.parse("W.task(worker,selection,record(worker.context.tracker_root/(inputs['workbook']+'.json'))).run",mode='eval').body
    counts=[0,0]
    class Change(ast.NodeTransformer):
        def visit_ImportFrom(self,node):
            if ast.dump(node,include_attributes=False)==ast.dump(expected_import,include_attributes=False):
                counts[0]+=1;return new_import
            return self.generic_visit(node)
        def visit_Call(self,node):
            if ast.dump(node,include_attributes=False)==ast.dump(expected_call,include_attributes=False):
                counts[1]+=1;return new_call
            return self.generic_visit(node)
    altered=Change().visit(before)
    require(counts==[1,1]and ast.dump(altered,include_attributes=False)==ast.dump(after,include_attributes=False),
            'round reader differs beyond exact owning producer lookup')
    scope=dict(original.__dict__);exec(compile(ast.fix_missing_locations(altered),selected.__file__,'exec'),scope)
    actual=scope['validate'];old=original.validate
    def checked(*args,**kwargs):
        require(record(original.__file__)==value['original']and record(selected.__file__)==value['selected'],
                'reserved reader bytes changed')
        return actual(*args,**kwargs)
    original.validate=checked
    try:yield
    finally:original.validate=old


def validate(binding,p,worker,selection,lease,Q,*,continued=False):
    from dataclasses import asdict
    v=read(binding)
    if v.get('schema_version') in ('fleet-disclosure-interrupted-reservation/v1','fleet-disclosure-initial-cache-failure/v1','fleet-disclosure-derivative-request-failure/v1'):
        return interrupted_reservation(binding,p,worker,selection,lease,Q)
    if v.get('schema_version')=='fleet-disclosure-reserved-cache-failure/v1':
        return cache_failure(binding,p,worker,selection,lease,Q)
    require(set(v)=={'schema_version','plan','worker_outcome','authority','retained_lease'}and v['schema_version']==SCHEMA,
            'closed unsubmitted reserved round required')
    old=read(v['plan']);changes={'helper','owner','fence','output','reserved_round','round_reader'}
    require('reserved_round'not in old and 'resume'not in old
        and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
            'reserved continuation changed source, review budget or original work')
    home=Path(old['output']);outer=read(v['worker_outcome']);wi=read(outer['intent']);job=read(wi['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['result']is None and outer['automatic_retry']is False and wi['owner']==old['owner']
        and job['driver']==old['helper']and job['source']==p['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'], 'prior reservation worker custody differs')
    log=Path(outer['log']['path']);require(record(log)==outer['log']
        and log.read_text().rstrip().endswith("ModuleNotFoundError: No module named 'scripts.batch.harbor_disclosure_command_repair'")
        and 'validate(authority, inputs)'in log.read_text(),'not the closed original pre-request producer reader failure')
    require({x.name for x in home.iterdir()}=={'intent.json','initial-lease'}
        and read(record(home/'intent.json'))==dict(plan=v['plan'],automatic_retry=False)
        and {x.name for x in(home/'initial-lease').iterdir()}=={'retained-live.json'}
        and v['retained_lease']==record(home/'initial-lease/retained-live.json'), 'prior reservation has another task or model effect')
    retained=read(v['retained_lease']);tracker=record(worker.context.tracker_root/(p['workbook']+'.json'))
    require(retained['schema_version']=='fleet-retained-live-continuation-lease/v1'and retained['authority']==v['plan']
        and retained['lease']==asdict(lease)and retained['tracker']==tracker and retained['lease_operations']==0
        and retained['automatic_retry']is False and retained['semantic_budget_reset']is False,
        'exact unchanged retained native custody required')
    for row in retained['validated_evidence']:Q.life.checked_record(row)
    inputs=read(p['inputs']);authority=Q.validate(v['authority'],inputs)
    require(authority['implementation']==p['modules']['rounds']and authority['allowance']==p['allowance']
        and authority['build']==p['build']and authority['prior_rejections']==p['prior_rejections']
        and (authority['round'],authority['limit'])==(p['round'],p['limit']), 'original reservation changed')
    directory=Path(v['authority']['path']).parent
    names={x.name for x in directory.iterdir()}
    require(names in ({'authority.json','prefix.json'},{'authority.json','prefix.json','review-plan.json'}),
            'original reservation has additional effects')
    value={**inputs,'review_round':v['authority']}
    if (directory/'review-plan.json').exists():require(read(record(directory/'review-plan.json'))==value,'completed reserved plan differs')
    return directory/'review-plan.json',value


def equivalent_snapshots(previous,selected,cache):
    """Select the native complete inventory for the identical original code."""
    require(isinstance(previous,list) and isinstance(selected,list)
            and len(previous)==len(selected)>0,'complete inventory correspondence required')
    for old_binding,new_binding in zip(previous,selected):
        old=read(old_binding);new=read(new_binding)
        require(set(old) in ({'root','fingerprint','bindings'},{'root','fingerprint','bindings','parent'}) and set(new)=={'root','files'}
                and old['root']==new['root'] and Path(old['root']).name==old['fingerprint'],
                'only deployment-to-native inventory selection permitted')
        expected=old['bindings'];observed={x['relative']:x['sha256']for x in new['files']}
        require(len(observed)==len(new['files']) and bool(expected)
                and set(expected)==set(observed) and all(value==dict(path=str(Path(old['root'])/name),sha256=observed[name])for name,value in expected.items()),
                'equivalent code inventories required')
        require(cache.inventory(new_binding)==Path(old['root']),'actual original code inventory differs')


def cache_failure(binding,p,worker,selection,lease,Q):
    """Continue an already-written plan after a pre-model cache schema failure."""
    from scripts.batch import harbor_disclosure_partition_cache as cache
    v=read(binding)
    require(set(v)=={'schema_version','plan','worker_outcome','prior_reserved_round'},'closed pre-cache failure required')
    old=read(v['plan']);changes={'helper','owner','fence','output','reserved_round','round_reader','code_snapshots'}
    require('reserved_round'in old and 'resume'not in old and v['prior_reserved_round']==old['reserved_round']
            and read(v['prior_reserved_round'])['schema_version']==SCHEMA
            and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
            and p['reserved_round']==binding and p['fence']==old['fence']+1
            and p['owner']!=old['owner'] and p['output']!=old['output'],
            'cache continuation changed original review authority')
    outer=read(v['worker_outcome']);intent=read(outer['intent']);job=read(intent['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1 and outer['failure_type']is None
            and outer['result']is None and outer['automatic_retry']is False and intent['owner']==old['owner']
            and job['driver']==old['helper'] and job['source']==p['source']
            and job['result']==str(Path(old['output'])/'result.json')
            and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
            and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'],
            'pre-cache stopped worker identity differs')
    log=Path(outer['log']['path']);require(record(log)==outer['log']
            and log.read_text().rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: closed complete code snapshot required')
            and "cache=m['cache'].ValidationCache("in log.read_text(),
            'not the exact pre-cache inventory failure')
    home=Path(old['output'])
    require({x.name for x in home.iterdir()}=={'intent.json','reserved.json','continued.json'}
            and read(record(home/'intent.json'))==dict(plan=v['plan'],automatic_retry=False)
            and read(record(home/'continued.json'))==old['reserved_round'],
            'pre-cache failure has later task or model effects')
    path,value=validate(v['prior_reserved_round'],old,worker,selection,lease,Q)
    require(path.is_file() and read(record(path))==value
            and read(record(home/'reserved.json'))==dict(plan=record(path),automatic_retry=False),
            'actual original reserved plan differs')
    equivalent_snapshots(old['code_snapshots'],p['code_snapshots'],cache)
    return path,value


def completed_initial_lease(binding,p,worker,previous,Q):
    """Authenticate the original completed renewal before consuming its round."""
    v=read(binding)
    require(v.get('schema_version')=='fleet-disclosure-initial-cache-failure/v1'
        and set(v)=={'schema_version','plan','worker_outcome','process','authority','retained_lease','lease_before','lease_after'},
        'complete renewal requires closed initial cache failure custody')
    old=read(v['plan']);home=Path(old['output']);path=worker.context.tracker_root/(p['workbook']+'.json')
    require(v['retained_lease']==record(home/'initial-lease/complete.json')
        and set(x.name for x in(home/'initial-lease').iterdir())=={'intent.json','complete.json'}
        and old['renewal_module']==p['renewal_module']and old['workbook']==p['workbook']and old['source']==p['source']
        and v['lease_after']==record(path), 'original completed renewal namespace or current tracker changed')
    renewal=Q.bound_module(old['renewal_module'])
    current=renewal.validate_completed(life=Q.life,read=read,record=record,path=path,previous=previous,
        completion=v['retained_lease'],before_binding=v['lease_before'],after_binding=v['lease_after'],authority_record=v['plan'])
    require(record(path)==v['lease_after'],'tracker changed while validating original completed renewal')
    return current


def interrupted_reservation(binding,p,worker,selection,lease,Q):
    """Reuse a complete reservation sealed before the first request was prepared.

    An exception name is not evidence that a model was unsubmitted. The original
    runner's durable ordering, all shard executor namespaces, current native
    process absence and the exact retained authority are checked independently.
    """
    from dataclasses import asdict
    from scripts.batch import harbor_disclosure_partition_review as R
    v=read(binding)
    derivative_failure=v.get('schema_version')=='fleet-disclosure-derivative-request-failure/v1'
    cache_failure=v.get('schema_version')=='fleet-disclosure-initial-cache-failure/v1'
    completed_renewal=cache_failure and set(v)=={'schema_version','plan','worker_outcome','process','authority','retained_lease','lease_before','lease_after'}
    require((completed_renewal or set(v)=={'schema_version','plan','worker_outcome','process','authority','retained_lease'})
        and v['schema_version'] in ('fleet-disclosure-interrupted-reservation/v1','fleet-disclosure-initial-cache-failure/v1','fleet-disclosure-derivative-request-failure/v1'),
        'closed interrupted reservation required')
    old=read(v['plan']);changes={'helper','owner','fence','output','reserved_round','round_reader'}
    if cache_failure:changes.add('code_snapshots')
    if derivative_failure:
        changes.add('modules')
        require('source_derivative'in old and p.get('source_derivative')==old['source_derivative']
                and set(p['modules'])==set(old['modules'])
                and {k:x for k,x in p['modules'].items()if k!='review'}=={k:x for k,x in old['modules'].items()if k!='review'},
                'derivative request recovery changed selected authority modules')
        derivative_request_repair(old['modules']['review'],p['modules']['review'])
    require('resume'not in old and 'reserved_round'not in old
        and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
        and p['reserved_round']==binding and p['fence']==old['fence']+1
        and p['owner']!=old['owner']and p['output']!=old['output']
        and p['round_reader']==dict(runtime=record(__file__),profile=None),
        'interrupted continuation changed review, code, source or budget')
    # The sealed supervisor could not record its final HTTP disposition. It
    # remains a failed run; current fleet custody is acquired separately by U.
    outer=read(v['worker_outcome']);intent=read(outer['intent']);job=read(intent['job'])
    require(outer['schema_version']=='harbor-fleet-worker-outcome/v1'
        and outer['status']=='RECONCILIATION_REQUIRED'
        and ((outer['returncode']==1 and outer['failure_type']is None) if cache_failure or derivative_failure
             else (outer['returncode']is None and outer['failure_type']=='RuntimeError'))
        and outer['result']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False
        and intent['owner']==old['owner']and job['driver']==old['helper']
        and job['source']==old['source']and job['workbook']==old['workbook']
        and job['result']==str(Path(old['output'])/'result.json')
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'],
        'interrupted original worker identity differs')
    stopped=Path(v['worker_outcome']['path']).parent
    require(record(stopped/'outcome.json')==v['worker_outcome']
        and record(stopped/'intent.json')==outer['intent']
        and record(stopped/'worker.log')==outer['log'],'sealed original worker evidence differs')
    if derivative_failure:
        log=(stopped/'worker.log').read_text()
        require(log.rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: partition task content changed')
                and 'R.prepare(task,plan,index,_validated=validated)'in log
                and "require(record(task.stage/'instruction.md')"in log
                and not (stopped/'finish-uncertain.json').exists(),
                'not the sealed original derivative request boundary failure')
    elif cache_failure:
        log=(stopped/'worker.log').read_text()
        require(log.rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: closed complete code snapshot required')
            and "cache=m['cache'].ValidationCache("in log
            and not (stopped/'finish-uncertain.json').exists(), 'not a sealed pre-model cache inventory failure')
        from scripts.batch import harbor_disclosure_partition_cache as cache
        equivalent_snapshots(old['code_snapshots'],p['code_snapshots'],cache)
    else:
        require(not (stopped/'worker.log').read_bytes()
            and read(record(stopped/'finish-uncertain.json'))==dict(automatic_retry=False,failure_type='RuntimeError',
                outcome=v['worker_outcome'],reconciliation_required=True), 'sealed interrupted supervisor evidence differs')
    process=read(v['process'])
    # Refuse both the original supervisor and any surviving original child.
    argv=process['command']
    require(process['job']==intent['job']and process['automatic_retry']is False
        and argv[argv.index('--owner')+1]==old['owner']
        and argv[argv.index('--job')+1]==intent['job']['path']
        and argv[argv.index('--job-sha256')+1]==intent['job']['sha256'],
        'native process receipt detached from original job')
    stopped_process(process,stopped,v['plan'])
    home=Path(old['output'])
    require({x.name for x in home.iterdir()}=={'intent.json','initial-lease','reserved.json'}|({'validation-metrics.json'}if derivative_failure else set())
        and read(record(home/'intent.json'))==dict(plan=v['plan'],automatic_retry=False),
        'interrupted run crossed the first-request preparation boundary')
    if derivative_failure:
        metrics=read(record(home/'validation-metrics.json'))
        require(set(metrics)=={'full_constructor_seconds','source_rederivations'}
                and type(metrics['full_constructor_seconds'])in (int,float) and 0<=metrics['full_constructor_seconds']<86400
                and metrics['source_rederivations']==1,'original complete cache construction evidence differs')
    if completed_renewal:
        _,_,previous=Q.completed_build(old['build'])
        require(completed_initial_lease(binding,p,worker,previous,Q)==lease,
            'completed reservation renewal differs from authenticated build lease')
    else:
        require({x.name for x in(home/'initial-lease').iterdir()}=={'retained-live.json'}
            and v['retained_lease']==record(home/'initial-lease/retained-live.json'),
            'interrupted retained-live namespace changed')
        retained=read(v['retained_lease']);tracker=record(worker.context.tracker_root/(p['workbook']+'.json'))
        require(retained['schema_version']=='fleet-retained-live-continuation-lease/v1'
            and retained['authority']==v['plan']and retained['lease']==asdict(lease)
            and retained['tracker']==tracker and retained['lease_operations']==0
            and retained['automatic_retry']is False and retained['semantic_budget_reset']is False,
            'original retained native lease changed')
        for evidence in retained['validated_evidence']:Q.life.checked_record(evidence)
    inputs=read(p['inputs']);authority=Q.validate(v['authority'],inputs)
    if derivative_failure:require(authority.get('source_derivative')==p['source_derivative'],'original derivative reservation changed')
    require(authority['implementation']==p['modules']['rounds']and authority['allowance']==p['allowance']
        and authority['build']==p['build']and authority['prior_rejections']==p['prior_rejections']
        and (authority['round'],authority['limit'])==(p['round'],p['limit']),
        'original consumed reservation changed')
    directory=Path(v['authority']['path']).parent;path=directory/'review-plan.json'
    require({x.name for x in directory.iterdir()}=={'authority.json','prefix.json','review-plan.json'},
        'reserved authority contains another effect')
    value={**inputs,'review_round':v['authority']}
    require(read(record(path))==value and read(record(home/'reserved.json'))==dict(plan=record(path),automatic_retry=False),
        'actual original reserved review plan differs')
    Q._primary_history(worker.campaign,Q.semantic_scope(inputs),p['round'],p['prior_rejections'])
    projection=read(inputs['projection'])
    for index in range(len(projection['batches'])):
        scope=R.scope(value,index)
        if derivative_failure:
            require(not (Path(inputs['producer_run'])/'requests'/scope.budget_id).exists(),
                    'a derivative shard request was already prepared')
        require(not (Path(worker.campaign.root)/R.E.sha(R.E.canonical(asdict(scope)))).exists(),
            'a shard executor was prepared or submitted; reconciliation is required')
    return path,value


def derivative_request_repair(previous,selected):
    """Only bind original live bytes to the already authenticated candidate."""
    for row in (previous,selected):
        require(record(row['path'])==row,'partition reader bytes changed')
    before=ast.parse(Path(previous['path']).read_text());after=ast.parse(Path(selected['path']).read_text())
    helpers=[n for n in after.body if isinstance(n,ast.FunctionDef)and n.name=='task_content']
    require(len(helpers)==1 and hashlib.sha256(ast.dump(helpers[0],include_attributes=False).encode()).hexdigest()
            =='82f13e43bfcc016b78607203ad6d15847390ed2a974c6237d02246819083b9e3',
            'unreviewed derivative request content validator')
    expected=ast.parse("require(record(task.stage/'instruction.md')['sha256']==p['instruction']['sha256'] and record(task.stage/'tests/disclosure.json')['sha256']==p['disclosure']['sha256'],'partition task content changed')").body[0]
    replacement=ast.parse('task_content(task,p,before)').body[0];count=0
    for fn in before.body:
        if isinstance(fn,ast.FunctionDef)and fn.name=='prepare':
            for i,node in enumerate(fn.body):
                if ast.dump(node,include_attributes=False)==ast.dump(expected,include_attributes=False):fn.body[i]=replacement;count+=1
    after.body=[n for n in after.body if n not in helpers]
    require(count==1 and ast.dump(before,include_attributes=False)==ast.dump(after,include_attributes=False),
            'partition reader changed beyond exact derivative content binding')


def stopped_process(process,worker_directory,plan,*,proc=Path('/proc')):
    require(proc.is_dir(),'native process inventory is required')
    for item in proc.glob('[0-9]*/cmdline'):
        try:argv=[x.decode()for x in item.read_bytes().split(b'\0')if x]
        except FileNotFoundError:continue
        require(not(str(worker_directory)in argv or plan['path']in argv),
            'original supervisor or review child remains alive')
    require(not (proc/str(process['pid'])).exists(),
        'original process identity must be stopped before reserved continuation')
