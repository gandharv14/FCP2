"""Original instruction dispatch after a complete independently reviewed round."""
from contextlib import contextmanager,nullcontext
from dataclasses import replace
from pathlib import Path
import argparse,fcntl,importlib.util,json,os,sys

SCHEMA='fleet-disclosure-aggregate-adoption/v1'
FIELDS={'schema_version','helper','context','adoption','history','owning_profile','original_job','round_result','utilities','capacity_policy','workbook','source','owner','fence','output','renewal_module'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    import hashlib
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical adoption binding required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):require(binding(row['path'])==row,'adoption input changed');return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'adoption implementation changed');spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def adoption_home(p):
    if 'interrupted_step'in p:
        proof=read(p['interrupted_step']['proof'])
        return adoption_home(read(proof['plan']))
    return Path(read(read(p['resume'])['plan'])['output'])if 'resume'in p else Path(p['output'])


def resume_state(p,*,continued=False):
    if 'interrupted_step'in p:
        K=module('aggregate_interrupted_consumer',p['interrupted_step']['helper'])
        return K.validate(sys.modules[__name__],p,continued=continued)[0]
    if 'completed_tail'in p:return resume_state(read(read(p['completed_tail'])['plan']),continued=True)
    if 'completed_step'in p:
        return resume_state(read(read(p['completed_step'])['plan']),continued=True)
    v=read(p['resume'])
    if v.get('schema_version')=='fleet-disclosure-adoption-prepared-unstarted/v1':return prepared_unstarted(p,v,continued=continued)
    require(set(v)=={'schema_version','plan','worker_outcome','permit','checkpoint','descriptor','tracker'}
        and v['schema_version']=='fleet-disclosure-adoption-unstarted-step/v1','closed unstarted adoption selection required')
    old=read(v['plan']);require('resume'not in old and old['schema_version']==SCHEMA and binding(old['helper']['path'])==old['helper']
        and {k:x for k,x in p.items()if k not in {'helper','owner','fence','output','checkpoint_reader','resume'}}=={k:x for k,x in old.items()if k not in {'helper','owner','fence','output'}}
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],'resume changes original adoption authority')
    home=Path(old['output']);out=read(v['worker_outcome']);intent=read(out['intent']);job=read(intent['job'])
    require(out['schema_version']=='harbor-fleet-worker-outcome/v1'and out['status']=='RECONCILIATION_REQUIRED'
        and out['returncode']==1 and out['failure_type']is None and out['result']is None and out['automatic_retry']is False
        and intent['owner']==old['owner']and job['driver']==old['helper']and job['source']==old['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'], 'unstarted adoption worker custody differs')
    log=Path(out['log']['path']);require(binding(log)==out['log'] and log.read_text().rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: disclosure repair cannot combine detached continuations'), 'unrecognized prior checkpoint decoder failure')
    require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=v['plan'],automatic_retry=False)
        and v['permit']==binding(home/'permit/permit.json')and v['checkpoint']==binding(home/'permit/result.json')
        and v['descriptor']==binding(home/'permit/descriptor.json'),'sealed adoption permit or original parent changed')
    result=read(v['checkpoint']);permit=read(v['permit'])
    require(result['status']=='RETRY_READY'and result['next_step']=='disclosure-review'and result['checkpoint']==v['permit']
        and result['descriptor']==v['descriptor']and permit['round_plan']==p['context']['plan']and permit['round_result']==p['round_result']
        and permit['tracker']==v['tracker']and permit['implementation']==p['adoption'],'sealed adoption handoff differs')
    config=read(read(old['original_job'])['config']);operations=Path(config['run_root'])/'dispatcher-v2'/home.name
    require(not(home/'first-step').exists()and not operations.exists(),'old adoption dispatched a child; reconcile its exact result')
    if not continued:require(not(home/'permit/steps').exists()and binding(v['tracker']['path'])==v['tracker'],'adoption already advanced beyond its unstarted checkpoint')
    return v


def prepared_unstarted(p,v,*,continued=False):
    require(set(v)=={'schema_version','plan','worker_outcome','permit','checkpoint','descriptor','tracker','failed_outcome'},
            'closed prepared unstarted adoption required')
    old=read(v['plan']);changes={'helper','owner','fence','output','resume','semantic_archive'}
    require('resume'not in old and 'prepared_context'in old
        and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
        'prepared adoption resume changes original authority')
    home=Path(old['output']);outer=read(v['worker_outcome']);wi=read(outer['intent']);job=read(wi['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['result']is None and outer['automatic_retry']is False and wi['owner']==old['owner']
        and job['driver']==old['helper']and job['source']==old['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'],'prepared stopped owner differs')
    require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=v['plan'],automatic_retry=False)
        and v['permit']==binding(home/'permit/permit.json')and v['checkpoint']==binding(home/'permit/result.json')
        and v['descriptor']==binding(home/'permit/descriptor.json'),'prepared permit parent differs')
    result=read(v['checkpoint']);permit=read(v['permit'])
    require(result['status']=='RETRY_READY'and result['next_step']=='disclosure-review'and result['checkpoint']==v['permit']
        and result['descriptor']==v['descriptor']and permit['round_plan']==p['context']['plan']and permit['round_result']==p['round_result']
        and permit['tracker']==v['tracker']and permit['implementation']==p['adoption'],'prepared sealed handoff differs')
    failed=read(v['failed_outcome']);resource=read(failed['resource']);intent=read(resource['intent'])
    require(v['failed_outcome']==binding(home/'first-step/outcome.json')and failed['status']=='RECONCILIATION_REQUIRED'
        and failed.get('result')is None and resource['returncode']==1 and resource.get('failure')is None
        and intent['action']=='step'and intent['arguments']==dict(descriptor=v['descriptor'],checkpoint=v['checkpoint']),
        'prepared first child differs or already completed')
    log=Path(resource['log']['path']);require(binding(log)==resource['log']and '.child(intent)'not in log.read_text(),
        'prepared failure log changed or child executed')
    if log.read_text().rstrip().endswith('ValueError: exact original semantic gate witness required'):
        require('owning_runtime' in log.read_text()and 'reader.installed' in log.read_text(),
            'semantic witness failure is outside the original pre-child loader')
        semantic_witness_correction(old,p)
    else:
        require(log.read_text().rstrip().endswith("ModuleNotFoundError: No module named 'scripts'")
            and 'selected_module' in log.read_text(),'not the exact pre-child prepared dependency failure')
    side=read(binding(home/'first-step/aggregate-bootstrap.json'))
    require(side['schema_version']==SCHEMA and side['plan']==v['plan']and side['helper']==old['helper']
        and side['kind']=='instruction'and side['intent']==resource['intent']and side['automatic_retry']is False,
        'prepared failed child detached from parent')
    config=read(read(old['original_job'])['config']);require(not(Path(config['run_root'])/'dispatcher-v2'/home.name).exists(),
        'prepared owner advanced beyond its first child')
    if not continued:require(not(home/'permit/steps').exists()and binding(v['tracker']['path'])==v['tracker'],
                             'prepared adoption already advanced; retain its checkpoint')
    return v

def semantic_witness_correction(old,p):
    """Correct only a derived reader/checker name mix-up in an unstarted plan."""
    import ast,hashlib
    before=old['semantic_archive'];after=p['semantic_archive']
    require(before['helper']==after['helper']and binding(before['helper']['path'])==before['helper'],
        'semantic archive implementation changed')
    first=before['profile'];second=after['profile']
    require(set(first)==set(second)=={'schema_version','complete','lanes','functions'}
        and {k:v for k,v in first.items()if k!='functions'}=={k:v for k,v in second.items()if k!='functions'}
        and set(first['functions'])==set(second['functions'])=={'disclosure','evidence'}
        and first['functions']['evidence']==second['functions']['evidence']
        and binding(first['complete']['path'])==first['complete']and binding(first['lanes']['path'])==first['lanes'],
        'semantic witness repair changed original modules or evidence reader')
    tree=ast.parse(Path(first['complete']['path']).read_text())
    def digest(name):
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name==name]
        if not nodes and name=='disclosure_review':
            imports=[n for n in tree.body if isinstance(n,ast.ImportFrom)
                and n.module=='scripts.batch.harbor_semantic_readiness' and n.level==0
                and any(a.name==name and a.asname in (None,name) for a in n.names)]
            require(len(imports)==1,'exact original semantic reader import required')
            root=Path(first['complete']['path']).parents[2]
            manifest=root/'.pipeline-code.json'
            require(binding(manifest)['sha256']==root.name,'original semantic snapshot manifest changed')
            relative='scripts/batch/harbor_semantic_readiness.py';source=root/relative
            require(binding(source)['sha256']==json.loads(manifest.read_bytes())[relative],
                'original imported semantic reader changed')
            nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef)and n.name==name]
        require(len(nodes)==1,'one original semantic function required')
        return hashlib.sha256(ast.dump(nodes[0],include_attributes=False).encode()).hexdigest()
    require(first['functions']['disclosure']==digest('disclosure_review')
        and second['functions']['disclosure']==digest('check_disclosure'),
        'only exact original reader-to-checker witness correction is supported')


def completed_step(p,*,continued=False):
    """Retain an adopted round when the following cold loader never ran I.child."""
    if 'completed_tail'in p:return completed_step(read(read(p['completed_tail'])['plan']),continued=True)
    v=read(p['completed_step'])
    require(set(v)=={'schema_version','plan','worker_outcome','outcome','failed_outcome','tracker'}
        and v['schema_version']=='fleet-disclosure-adoption-completed-step/v1','closed completed adoption selection required')
    old=read(v['plan']);changes={'helper','owner','fence','output','namespace_module','completed_step'}
    require('completed_step'not in old and 'resume'in old
        and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
        'completed adoption changes original authority')
    resume_state(old,continued=True)
    home=Path(old['output']);outer=read(v['worker_outcome']);wi=read(outer['intent']);job=read(wi['job'])
    require(outer['schema_version']=='harbor-fleet-worker-outcome/v1'and outer['status']=='RECONCILIATION_REQUIRED'
        and outer['returncode']==1 and outer['failure_type']is None and outer['result']is None and outer['automatic_retry']is False
        and wi['owner']==old['owner']and job['driver']==old['helper']and job['source']==p['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'],'completed adoption worker custody differs')
    require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=v['plan'],automatic_retry=False)
        and v['outcome']==binding(home/'first-step/outcome.json'),'completed adoption parent differs')
    out=read(v['outcome']);result=read(out['result']);guard=read(out['resource'])
    require(out['status']=='COMPLETED_OPERATION'and result['status']=='STEP_COMPLETE'and result['next_step']=='disclosure-commit'
        and guard['returncode']==0 and guard.get('failure')is None,'original adoption did not complete')
    failed=read(v['failed_outcome']);resource=read(failed['resource']);intent=read(resource['intent'])
    operations=Path(read(read(old['original_job'])['config'])['run_root'])/'dispatcher-v2'/home.name
    require(v['failed_outcome']==binding(operations/'000/outcome.json')and failed['status']=='RECONCILIATION_REQUIRED'
        and failed.get('result')is None and resource['returncode']==1 and resource.get('failure')is None
        and intent['action']=='step'and intent['arguments']==dict(descriptor=result['descriptor'],checkpoint=out['result'])
        and read(binding(operations/'hold.json'))['outcome']==v['failed_outcome']
        and not(operations/'001').exists(),'failed successor differs or a later operation exists')
    log=Path(resource['log']['path']);require(binding(log)==resource['log']
        and log.read_text().rstrip().endswith('ValueError: dispatcher namespace exists; no replay')
        and 'install_commands' in log.read_text(),'not the known pre-child namespace failure')
    side=read(binding(operations/'000/aggregate-bootstrap.json'))
    require(side['schema_version']==SCHEMA and side['plan']==v['plan']and side['helper']==old['helper']
        and side['kind']=='instruction'and side['intent']==resource['intent']and side['automatic_retry']is False,
        'failed cold child detached from original parent')
    if not continued:
        require(binding(v['tracker']['path'])==v['tracker']
            and not(adoption_home(p)/'permit/steps/disclosure-commit').exists(),'completed adoption already advanced; use its exact checkpoint')
    return v


def completed_tail(p,*,continued=False):
    """A sealed original dispatcher prefix followed by an unused owning step."""
    v=read(p['completed_tail'])
    direct=v.get('schema_version')=='fleet-disclosure-adoption-completed-tail/v2'
    require(set(v)=={'schema_version','plan','worker_outcome','outcomes','failed_outcome','tracker'}|({'first_outcome'}if direct else set())
        and v['schema_version']in{'fleet-disclosure-adoption-completed-tail/v1','fleet-disclosure-adoption-completed-tail/v2'},'closed completed tail required')
    old=read(v['plan']);changes={'helper','owner','fence','output','completed_tail','renewal_runtime','semantic_archive'}
    require('completed_tail'not in old and (('resume'in old and 'completed_step'not in old and old.get('archived_history')is True and 'semantic_archive'in old)if direct else 'completed_step'in old)
        and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
        'completed tail changes original adoption authority')
    require(p.get('semantic_archive',{}).get('profile')==old.get('semantic_archive',{}).get('profile'),'completed tail changed original semantic gate witness')
    saved=resume_state(old,continued=True)if direct else completed_step(old,continued=True)
    home=Path(old['output']);outer=read(v['worker_outcome']);wi=read(outer['intent']);job=read(wi['job'])
    require(outer['schema_version']=='harbor-fleet-worker-outcome/v1'and outer['status']=='RECONCILIATION_REQUIRED'
        and outer['returncode']==1 and outer['failure_type']is None and outer['result']is None and outer['automatic_retry']is False
        and wi['owner']==old['owner']and job['driver']==old['helper']and job['source']==p['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'], 'completed tail worker custody differs')
    require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=v['plan'],automatic_retry=False),'completed tail parent differs')
    config=read(read(old['original_job'])['config']);ops=Path(config['run_root'])/'dispatcher-v2'/home.name
    if direct:
        first=read(v['first_outcome']);fg=read(first['resource']);fi=read(fg['intent']);fr=read(first['result'])
        require(v['first_outcome']==binding(home/'first-step/outcome.json')and first['status']=='COMPLETED_OPERATION'
                and fg['returncode']==0 and fg.get('failure')is None and fr['status']=='STEP_COMPLETE'
                and fr['next_step']=='disclosure-commit'and fg['intent']==binding(home/'first-step/intent.json')
                and fi['action']=='step'and fi['arguments']==dict(descriptor=saved['descriptor'],checkpoint=saved['checkpoint']),
                'direct original aggregate consumer did not complete')
        prior=first['result']
    else:prior=read(read(old['completed_step'])['outcome'])['result']
    require(type(v['outcomes'])is list and 0<len(v['outcomes'])<=32,'bounded actual completed prefix required')
    for index,row in enumerate(v['outcomes']):
        out=read(row);res=read(out['result']);guard=read(out['resource']);intent=read(guard['intent']);previous=read(prior)
        require(row==binding(ops/('%03d'%index)/'outcome.json')and out['status']=='COMPLETED_OPERATION'
            and guard['returncode']==0 and guard.get('failure')is None and res['status']=='STEP_COMPLETE'
            and intent['action']=='step'and intent['arguments']==dict(descriptor=previous['descriptor'],checkpoint=prior),
            'completed tail original guarded chain differs')
        prior=out['result']
    failed=read(v['failed_outcome']);resource=read(failed['resource']);intent=read(resource['intent']);last=read(prior);index=len(v['outcomes'])
    require(v['failed_outcome']==binding(ops/('%03d'%index)/'outcome.json')and failed['status']=='RECONCILIATION_REQUIRED'
        and failed.get('result')is None and resource['returncode']==1 and resource.get('failure')is None
        and intent['action']=='step'and intent['arguments']==dict(descriptor=last['descriptor'],checkpoint=prior)
        and read(binding(ops/'hold.json'))['outcome']==v['failed_outcome']and not(ops/('%03d'%(index+1))).exists(),
        'failed tail successor differs or later operation exists')
    inputs=read(read(p['context']['plan'])['inputs']);log=Path(resource['log']['path'])
    require(binding(log)==resource['log'],'stopped tail log changed')
    if direct:
        require(last['next_step']=='nat-commit'and 'validate(worker,before)'in log.read_text()
                and 'history.completed_run'in log.read_text()and 'prefix_records'in log.read_text()
                and log.read_text().rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: typed prefix directory changed'),
                'not the exact pre-commit historical stage failure')
    else:
        require('resume_state' in log.read_text()
            and log.read_text().rstrip().endswith('scripts.batch.harbor_tracker.Conflict: changed evidence: '+inputs['instruction']['path'])
            and '.child(intent)'not in log.read_text(),'not the known historical instruction pre-child failure')
    side=binding(ops/('%03d'%index)/'aggregate-bootstrap.json');value=read(side)
    require({'schema_version','plan','helper','kind','intent','automatic_retry'}<=set(value)and value['schema_version']==SCHEMA and value['plan']==v['plan']and value['helper']==old['helper']
        and value['kind']=='instruction'and value['intent']==resource['intent']and value['automatic_retry']is False,
        'failed tail child detached from original parent')
    if not continued:require(binding(v['tracker']['path'])==v['tracker'],'tail current custody changed')
    return v,dict(old,_binding=v['plan']),(resource['intent'],side)


def submitted_instruction_archive(A,p,receipt):
    """Resolve only an old self-alias using its genuine submitted budget snapshot."""
    require(receipt['round_plan']==p['context']['plan']and receipt['round_result']==p['round_result'],
            'instruction archive belongs to another reviewed round')
    instruction=receipt['historical_instruction'];rows=receipt['historical_records']
    aliases=[r for r in rows if r==dict(original=instruction,archive=instruction)]
    if not aliases:return receipt,None
    candidates=[r['archive']for r in rows if r['original']==instruction and r['archive']!=instruction]
    require(candidates and all(r['sha256']==instruction['sha256']for r in candidates),'self-alias has no exact real archive')
    result=read(receipt['round_result']);selected=read(receipt['round_plan']);home=Path(selected['output'])
    submitted=[]
    for index in range(result['actual_review_calls']):
        complete=(A.history.batch_completion(selected,result['review_plan'],index)if 'completed_prefix'in selected
                  else read(binding(home/'batches'/('%04d'%index)/'complete.json')))
        require(complete['index']==index and complete['plan']==result['review_plan'],'actual shard completion differs')
        call=complete['call'];journal=read(call['submitted']);submitted.extend(journal['budget_evidence'])
    archive=candidates[0]
    require(archive in submitted and Path(archive['path']).name.startswith('budget-')
        and Path(archive['path']).suffix=='.snapshot','instruction archive is not an actual submitted budget snapshot')
    A.E.checked_bytes(archive)
    normalized={**receipt,'historical_records':[dict(original=instruction,archive=archive)if r in aliases else r for r in rows]}
    return normalized,archive


def archived_prefix_reader(A,p,receipt,Q,original,*,evidence_function=None):
    """Recompute a retained stage tree from every authenticated archived member."""
    import ast,inspect,textwrap
    from types import SimpleNamespace
    from scripts.batch import harbor_readiness as static
    result=read(p['round_result']);plan=read(result['review_plan']);authority=read(plan['review_round'])
    old_prefix=read(authority['prefix']);directories=authority['prefix_directories']
    archive_map={(r['original']['path'],r['original']['sha256']):r['archive']for r in receipt['historical_records']}
    selected=[d for d in directories if d['name']=='staged_bundle']
    require(len(selected)<=1,'ambiguous historical stage inventory')
    def tree(path):
        match=[d for d in selected if Path(d['binding']['path'])==path]
        if not match:return static.publication._tree_hash(path)
        d=match[0];files=d['files'];inventory={}
        for row in files:
            relative=Path(row['path']).relative_to(path)
            require(relative.parts and '..'not in relative.parts and str(relative)not in inventory,'invalid historical stage membership')
            archive=archive_map.get((row['path'],row['sha256']))
            require(archive is not None and archive['sha256']==row['sha256'],'historical stage member missing from actual Call archive')
            raw=A.E.checked_bytes(archive);inventory[str(relative)]=dict(algorithm='sha256',path=str(relative),sha256=row['sha256'],size_bytes=len(raw))
        digest=static.publication.canonical_hash(inventory)
        require(digest==d['tree_sha256']==d['binding']['sha256'],'historical stage inventory hash differs')
        return digest,inventory
    def original_record(path):
        for d in selected:
            for row in d['files']:
                if str(path)==row['path']:
                    A.E.checked_bytes(archive_map[(row['path'],row['sha256'])]);return row
        return Q.record(path)
    source=ast.parse(textwrap.dedent(inspect.getsource(original)));expected=ast.parse('from scripts.batch import harbor_readiness as static').body[0];expected_evidence=ast.parse('from scripts.batch.harbor_complete_readiness import evidence_records').body[0];count=0
    class Change(ast.NodeTransformer):
        def visit_ImportFrom(self,node):
            nonlocal count
            if any(ast.dump(node,include_attributes=False)==ast.dump(item,include_attributes=False)for item in (expected,expected_evidence)):count+=1;return None
            return node
    changed=Change().visit(source);require(count==2,'original typed inventory reader changed')
    scope=dict(original.__globals__);scope['static']=SimpleNamespace(publication=SimpleNamespace(_tree_hash=tree));scope['record']=original_record
    from scripts.batch.harbor_complete_readiness import evidence_records
    if evidence_function is not None:
        witness=p['semantic_archive']['profile']
        require(binding(evidence_function.__globals__['__file__'])==witness['complete']
                and A.function_sha(evidence_function)==witness['functions']['evidence'],
                'prefix factory original evidence function differs')
        evidence_records=evidence_function
    from types import FunctionType
    def checked_archive(row):
        archive=archive_map.get((row['path'],row['sha256']))
        if archive is not None:A.E.checked_bytes(archive);return row
        return evidence_records.__globals__['checked_record'](row)
    evidence_scope={**evidence_records.__globals__,'checked_record':checked_archive}
    scope['evidence_records']=FunctionType(evidence_records.__code__,evidence_scope,evidence_records.__name__,evidence_records.__defaults__,evidence_records.__closure__)
    exec(compile(ast.fix_missing_locations(changed),original.__code__.co_filename,'exec'),scope);fn=scope[original.__name__]
    def checked(prefix):
        require(prefix==old_prefix,'historical stage requested for a different prefix')
        return fn(prefix)
    return checked


@contextmanager
def archived_instruction_runtime(A,p,receipt,original,*,evidence_function=None):
    """Historical functions read archived bytes; live I/W continue using the file."""
    import ast,inspect,textwrap
    value,archive=submitted_instruction_archive(A,p,receipt)
    if archive is None:
        # A prepared review can already use a distinct instruction archive.
        # Its complete staged tree still needs historical validation after NAT.
        Q=sys.modules['scripts.batch.harbor_disclosure_review_round'];old_prefix=Q.prefix_records
        prefix=archived_prefix_reader(A,p,value,Q,old_prefix,evidence_function=evidence_function)if 'review_round'in read(read(p['round_result'])['review_plan'])else old_prefix
        try:
            Q.prefix_records=prefix
            with original(value):yield
        finally:Q.prefix_records=old_prefix
        return
    instruction=receipt['historical_instruction'];R=A.review
    require(binding(R.__file__)==read(p['context']['plan'])['modules']['review'],'historical partition implementation differs')
    def text(row):
        require(row==instruction,'archive text requested for another input')
        A.E.checked_bytes(archive);return Path(archive['path']).read_text()
    saved={name:getattr(R,name)for name in ('validate_plan','full_body','checked_record')}
    Q=sys.modules['scripts.batch.harbor_disclosure_review_round'];old_prefix=Q.prefix_records
    prefix=archived_prefix_reader(A,p,value,Q,old_prefix,evidence_function=evidence_function)if 'review_round'in read(read(p['round_result'])['review_plan'])else old_prefix
    def compile_reader(fn,expression):
        tree=ast.parse(textwrap.dedent(inspect.getsource(fn)));expected=ast.parse(expression,mode='eval').body;count=0
        class Change(ast.NodeTransformer):
            def visit_Call(self,node):
                nonlocal count
                if ast.dump(node,include_attributes=False)==ast.dump(expected,include_attributes=False):
                    count+=1
                    arg="p['instruction']"if fn.__name__=='validate_plan'else 'instruction'
                    return ast.copy_location(ast.parse('_historical_instruction_text('+arg+')',mode='eval').body,node)
                return self.generic_visit(node)
        changed=Change().visit(tree);require(count==1,'original historical instruction read changed')
        scope=dict(fn.__globals__);scope['_historical_instruction_text']=text
        exec(compile(ast.fix_missing_locations(changed),fn.__code__.co_filename,'exec'),scope);return scope[fn.__name__]
    checked=lambda row: saved['checked_record'](archive if row==instruction else row)
    full=compile_reader(saved['full_body'],"Path(instruction['path']).read_text()")
    validate=compile_reader(saved['validate_plan'],"Path(p['instruction']['path']).read_text()")
    # The original manifest depends on exact input hashes, not their locators.
    from scripts.batch import harbor_semantic_readiness as semantic
    manifest=semantic.disclosure_manifest
    def archived_manifest(i,*args,**kwargs):return manifest(archive if i==instruction else i,*args,**kwargs)
    full.__globals__['checked_record']=checked;validate.__globals__['checked_record']=checked;validate.__globals__['full_body']=full
    try:
        R.checked_record=checked;R.full_body=full;R.validate_plan=validate;semantic.disclosure_manifest=archived_manifest;Q.prefix_records=prefix
        with original(value):yield
    finally:
        semantic.disclosure_manifest=manifest;Q.prefix_records=old_prefix
        for name,fn in saved.items():setattr(R,name,fn)


@contextmanager
def historical_runtime(A,p):
    if 'completed_tail'not in p and not p.get('archived_history'):
        yield;return
    original=A.historical_round
    evidence=None
    if 'semantic_archive'in p:
        from scripts.batch.harbor_complete_readiness import evidence_records
        evidence=getattr(original,'_historical_prefix_evidence',None)or evidence_records
        witness=p['semantic_archive']['profile']
        require(binding(evidence.__globals__['__file__'])==witness['complete']
                and A.function_sha(evidence)==witness['functions']['evidence'],
                'historical prefix requires the declared original evidence function')
    active=[]
    @contextmanager
    def selected(receipt):
        if active:
            require(receipt==active[-1],'nested historical round changed its receipt')
            for row in receipt['historical_records']:A.E.checked_bytes(row['archive'])
            yield;return
        active.append(receipt)
        try:
            with archived_instruction_runtime(A,p,receipt,original,evidence_function=evidence):yield
        finally:active.pop()
    selected._historical_prefix_evidence=evidence
    A.historical_round=selected
    try:yield
    finally:A.historical_round=original


@contextmanager
def owning_runtime(A,p,*,receipt=None):
    """Bind a declared relocated checkpoint reader's own history global too."""
    from scripts import harbor_instruction_campaign as I
    from contextlib import ExitStack
    with (historical_runtime(A,p)if receipt is not None else nullcontext()),A.installed(p['owning_profile'],receipt=receipt),ExitStack()as extra:
        if receipt is not None and 'semantic_archive'in p:
            selected=p['semantic_archive'];require(set(selected)=={'helper','profile'},'closed semantic archive reader required')
            reader=module('adopted_semantic_archive',selected['helper']);extra.enter_context(reader.installed(A,selected['profile'],receipt))
        if 'checkpoint_reader'not in p:
            yield;return
        witness=p['checkpoint_reader'];prior=I._prior;decode=prior.__globals__['worker_decode'];scope=decode.__globals__;old=scope['_disclosure_history']
        require(set(witness)=={'module','prior','decode','history'}and binding(scope['__file__'])==witness['module']
            and prior.__globals__ is scope and witness['prior']==A.function_sha(prior)
            and witness['decode']==A.function_sha(decode)and witness['history']==A.function_sha(old)
            and old is not I._disclosure_history,'exact relocated checkpoint reader witness required')
        scope['_disclosure_history']=I._disclosure_history
        try:yield
        finally:scope['_disclosure_history']=old


def takeover_custody(p,worker,previous,old_archive):
    """Read the actual sealed replacement; never reapply pre-T eligibility."""
    from dataclasses import asdict
    selected=p['completed_takeover'];require(set(selected)=={'runtime','plan','completion'},'closed completed adoption takeover required')
    tp=read(selected['plan']);T=module('adoption_completed_takeover',tp['shared']['takeover']);loader=module('adoption_layout',selected['runtime'])
    require(tp['schema_version']=='harbor-allowance-takeover-direct/v1'
        and binding(T.life.__file__)==tp['shared']['lifecycle'],'declared takeover lifecycle required')
    if 'layout'in tp:loader.select_layout(T,tp['layout'])
    before,after,lease=T.validate_completion(selected['completion']);done=read(selected['completion']);stop=read(done['stopped_owner_proof'])
    path=worker.context.tracker_root/(p['workbook']+'.json');old=before['leases']['task_agent']
    require(stop['selection']==T._selection_value(T._selection(tp['selection']))
        and stop['before']['sha256']==old_archive['sha256'] and binding(old_archive['path'])==old_archive
        and tp['selection']['tracker']==dict(path=str(path),sha256=old_archive['sha256'])
        and (old['owner'],old['token'],before['revision'],old_archive['sha256'])==(previous.owner,previous.token,previous.revision,previous.sha256)
        and before['workbook_id']==after['workbook_id']==p['workbook']
        and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']==p['source']
        and lease.owner==(read(read(p['resume'])['plan'])['owner']if 'resume'in p else p['owner']),'completed replacement detached from actual reviewed custody')
    return lease,stop['before'],done['after']


def current_checkpoint(p,env,worker,renewed,A,invocation,*,installed=False):
    """Original child checkpoint and live custody, distinct from old review bytes."""
    intent,sidecar=invocation;side=read(sidecar);U=module('adoption_checkpoint_utilities',p['utilities'])
    Q=sys.modules['scripts.batch.harbor_disclosure_review_round'];D=Q.bound_module(read(p['original_job'])['driver'])
    kind,actual=U.retained_command(side['original_command'],p,D,env);C,I,_,W=env[:4]
    seconds=I.guard_seconds('step',read(intent)['arguments'])if kind=='instruction'else read(intent)['seconds']
    require(actual==intent and side==dict(schema_version=SCHEMA,plan=p['_binding'],helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],seconds=seconds,memory_bytes=8589934592,automatic_retry=False)
        and Path(sidecar['path'])==Path(intent['path']).parent/'aggregate-bootstrap.json','cold checkpoint detached from original owning child')
    receipt=binding(adoption_home(p)/'permit/permit.json')
    with nullcontext()if installed else owning_runtime(A,p,receipt=receipt):
        if kind=='instruction':
            arguments=read(intent)['arguments']
            w,controls,_=I.selected(arguments['descriptor']);value,s,l=I._prior(arguments['checkpoint'],arguments['descriptor'])
        else:
            value,w,_,s,l=D.renewal_context(env,read(intent)['checkpoint'])
            _,controls,_=I.selected(value['descriptor'])
        require(controls==env[7] and w.run==worker.run and w.root==worker.root and w.context==worker.context
            and w.campaign==worker.campaign and w.disclosure_repair==receipt
            and s.content.bindings['original_raw_source']==p['source']
            and (l.owner,l.token)==(renewed.owner,renewed.token),'current checkpoint changed reviewed worker/source/custody')
        _,before,_=W.active(w,s,l)
        require(before['revision']>=renewed.revision,'current checkpoint precedes completed custody')
        certificate=read(binding(adoption_home(p)/'round-renewal.json'));anchor=read(certificate['after'])
        require(before['handoffs'][:len(anchor['handoffs'])]==anchor['handoffs'],
                'current checkpoint lost the completed custody history')
    return w,s,l


def completed_review_context(M,p,row,home,*,invocation=None):
    """Replace one current-only assertion with authenticated historical custody.

    No binding or read function is changed. Every file read still returns its
    actual bytes; the selected old review archive is explicitly named.
    """
    import ast,inspect,textwrap
    original=ast.parse(textwrap.dedent(inspect.getsource(M.context)))
    expected=ast.parse("require(binding(path)==dict(path=str(path),sha256=r['lease_after']['sha256']),'current tracker differs from completed renewal')").body[0]
    replacement=ast.parse("require(_adoption_completed_custody(worker,lease,r['lease_after'],env),'completed review custody differs')").body[0]
    count=0
    class Transform(ast.NodeTransformer):
        def visit_Expr(self,node):
            nonlocal count
            if ast.dump(node,include_attributes=False)==ast.dump(expected,include_attributes=False):
                count+=1;return ast.copy_location(replacement,node)
            return self.generic_visit(node)
    selected=Transform().visit(original);require(count==1,'original completed review assertion changed')
    def checked(worker,previous,old_archive,env=None):
        require(binding(old_archive['path'])==old_archive,'actual original review archive changed')
        history=M.module('scripts.batch.harbor_disclosure_round_history',p['history'])
        import scripts.batch as package
        package.harbor_disclosure_round_history=history
        A=M.module('scripts.batch.harbor_disclosure_round_adoption',p['adoption']);package.harbor_disclosure_round_adoption=A
        from scripts.batch.harbor_workbook_bootstrap import Lease
        if 'completed_takeover'in p:
            current,_,after=takeover_custody(p,worker,previous,old_archive)
        else:
            certificate=binding(home/'round-renewal.json');value=read(certificate)
            require(value['authority']==row and value['before']['sha256']==old_archive['sha256'],'new renewal is detached from original review archive')
            complete=read(value['completion'])
            require(complete['previous']==__import__('dataclasses').asdict(previous),'completed review original lease differs')
            current=Lease(**complete['lease']);after=value['after']
            A.validate_renewal(certificate,worker,current,p['context']['plan'],p['round_result'],live=invocation is None)
        if invocation is not None:current_checkpoint(dict(p,_binding=row),env,worker,current,A,invocation)
        elif 'completed_tail'in p:
            _,prior,actual=completed_tail(p)
            current_checkpoint(prior,env,worker,current,A,actual)
        elif 'completed_takeover'in p:require(binding(worker.context.tracker_root/(p['workbook']+'.json'))['sha256']==after['sha256'],'live custody drifted after completed replacement')
        return True
    def archived_resume(*args,**kwargs):
        A=sys.modules['scripts.batch.harbor_disclosure_round_adoption']
        certificate=read(binding(home/'round-renewal.json'))
        require(certificate['implementation']==p['adoption']and certificate['round_plan']==p['context']['plan']
            and certificate['round_result']==p['round_result'],'historical review certificate differs')
        with historical_runtime(A,p),A.historical_round(certificate):return M.resume_state(*args,**kwargs)
    scope=dict(M.__dict__);scope['_adoption_completed_custody']=checked;scope['resume_state']=archived_resume
    exec(compile(ast.fix_missing_locations(selected),M.__file__,'exec'),scope)
    return scope['context']


@contextmanager
def prepared_dependencies(M,selected,p):
    from contextlib import ExitStack
    require(set(selected['modules'])==set(M.MODULES),'complete original review dependency set required')
    saved=[]
    with ExitStack()as stack:
        try:
            for key,suffix in M.MODULES.items():
                package='scripts.fleet'if key=='gate'else'scripts.batch';parent=__import__(package,fromlist=[suffix]);name=package+'.'+suffix
                saved.append((name,parent,suffix,sys.modules.get(name),getattr(parent,suffix,None)))
                module=M.module(name,selected['modules'][key]);setattr(parent,suffix,module)
            if 'reserved_round'in selected:
                reader=M.module('adoption_reserved_dependency',selected['round_reader']['runtime'])
                stack.enter_context(reader.reader(sys.modules['scripts.batch.harbor_disclosure_review_round'],selected['round_reader']['profile']))
            parent=__import__('scripts.batch',fromlist=['harbor_disclosure_round_history'])
            for suffix,key in [('harbor_disclosure_round_history','history'),('harbor_disclosure_round_adoption','adoption')]:
                name='scripts.batch.'+suffix;saved.append((name,parent,suffix,sys.modules.get(name),getattr(parent,suffix,None)))
                module=M.module(name,p[key]);setattr(parent,suffix,module)
            yield module
        finally:
            stack.close()
            for name,parent,suffix,old,attribute in reversed(saved):
                if old is None:sys.modules.pop(name,None)
                else:sys.modules[name]=old
                if attribute is None:parent.__dict__.pop(suffix,None)
                else:setattr(parent,suffix,attribute)


def prepared_review_context(M,p,row,home,*,invocation=None):
    """Reopen the exact prepared build using historical custody and current I/W."""
    import ast,inspect,textwrap
    witness=p['prepared_context'];selected=read(p['context']['plan'])
    require(set(witness)=={'helper','completed_custody'}and witness['helper']==selected['retained']['helper'],
            'exact original prepared context witness required')
    original_module=M.module
    changed=[]
    def selected_module(name,bound):
        value=original_module(name,bound)
        if name!='retained_disclosure_review_context' or bound!=witness['helper']:return value
        import hashlib
        fn=value.completed_custody
        digest=hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0],include_attributes=False).encode()).hexdigest()
        require(digest==witness['completed_custody'],'original prepared custody function changed')
        tree=ast.parse(textwrap.dedent(inspect.getsource(fn)));expected=ast.parse('env[3].active(worker,selection,current)').body[0];count=0
        class Change(ast.NodeTransformer):
            def visit_Expr(self,node):
                nonlocal count
                if ast.dump(node,include_attributes=False)==ast.dump(expected,include_attributes=False):
                    count+=1;return ast.copy_location(ast.parse('_adoption_current(worker,selection,current,env)').body[0],node)
                return self.generic_visit(node)
        adapted=Change().visit(tree);require(count==1,'original prepared owning custody boundary differs')
        def current_checked(A,worker,selection,previous,env):
            from scripts.batch.harbor_workbook_bootstrap import Lease
            certificate=binding(home/'round-renewal.json');v=read(certificate);complete=read(v['completion'])
            require(v['authority']==(read(p['resume'])['plan']if 'resume'in p else row) and v['before']['sha256']==previous.sha256
                and read(v['before'])['revision']==previous.revision,'adoption detached from original prepared custody')
            lease=Lease(**complete['lease'])
            require((lease.owner,lease.token)==(previous.owner,previous.token),'prepared adoption changed custody identity')
            A.validate_renewal(certificate,worker,lease,p['context']['plan'],p['round_result'],live=invocation is None)
            if invocation is not None:current_checkpoint(dict(p,_binding=row),env,worker,lease,A,invocation)
            else:env[3].active(worker,selection,lease)
        def current(worker,selection,previous,env):
            # The original retained loader has installed its exact source root
            # before this callback. Reuse the declared review dependency order.
            with prepared_dependencies(M,selected,p)as A:
                return current_checked(A,worker,selection,previous,env)
        original_fn=getattr(fn,'__wrapped__',fn)
        scope=dict(original_fn.__globals__);scope['_adoption_current']=current
        exec(compile(ast.fix_missing_locations(adapted),original_fn.__code__.co_filename,'exec'),scope)
        changed.append((value,fn));value.completed_custody=scope['completed_custody'];return value
    original_context=M.context
    # The function's own globals must select the declared original loader.
    scope=dict(original_context.__wrapped__.__globals__);scope['module']=selected_module
    from types import FunctionType
    cold=FunctionType(original_context.__wrapped__.__code__,scope,original_context.__wrapped__.__name__,original_context.__wrapped__.__defaults__,original_context.__wrapped__.__closure__)
    @contextmanager
    def opened(binding):
        try:
            with contextmanager(cold)(binding)as value:yield value
        finally:
            for module,fn in reversed(changed):module.completed_custody=fn
    return opened


def initial_review_lease(p,selected,worker,previous,Q):
    """Authenticate the original review's completed initial renewal, historically."""
    from dataclasses import asdict
    c=p['initial_review_lease'];home=Path(selected['output'])
    require(set(c)=={'module','completion','before','after'} and c['module']==selected['renewal_module']
        and 'resume'not in selected and 'reserved_round'not in selected
        and 'completed_custody'not in read(selected['retained']['plan']),
        'closed original first review renewal required')
    result=read(p['round_result'])
    require(p['round_result']==binding(home/'result.json') and result['status']=='DISCLOSURE_REVIEW_COMPLETE_NOT_ADOPTED'
        and result['plan']==p['context']['plan'] and result['workbook']==p['workbook']
        and result['automatic_retry']is False and result['publication_performed']is False,
        'initial renewal detached from completed review')
    before=read(c['before']);after=read(c['after']);done=read(c['completion'])
    path=(worker.context.tracker_root/(p['workbook']+'.json')).resolve()
    require(before['workbook_id']==after['workbook_id']==p['workbook']
        and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']==p['source'],
        'review renewal source differs')
    if done['schema_version']=='fleet-retained-live-continuation-lease/v1':
        require(c['completion']==binding(home/'initial-lease/retained-live.json')
            and done['authority']==p['context']['plan'] and done['lease']==asdict(previous)
            and done['tracker']==dict(path=str(path),sha256=c['before']['sha256'])
            and c['before']['sha256']==c['after']['sha256']==previous.sha256
            and before==after and before['revision']==previous.revision
            and done['minimum_remaining_ns']==7200*10**9 and done['lease_operations']==0
            and done['task_guards_changed']is False and done['semantic_budget_reset']is False
            and done['automatic_retry']is False and before['leases']['task_agent']['heartbeat_at']<=done['observed_at_ns']
            and before['leases']['task_agent']['expires_at']-done['observed_at_ns']>=7200*10**9
            and Q.life.validate_lifecycle(before)==done['original_expired_replacements'],
            'retained original review lease differs')
        for row in done['validated_evidence']:require(binding(row['path'])==row,'retained initial review evidence changed')
        current=previous
    else:
        require(c['completion']==binding(home/'initial-lease/complete.json'),'original initial renewal namespace differs')
        L=module('completed_initial_review_lease',c['module'])
        current=L.validate_completed(life=Q.life,read=read,record=binding,path=path,previous=previous,
            completion=c['completion'],before_binding=c['before'],after_binding=c['after'],authority_record=p['context']['plan'])
    reserved=read(binding(home/'reserved.json'));plan=read(result['review_plan']);authority=read(plan['review_round'])
    require(reserved==dict(plan=result['review_plan'],automatic_retry=False)
        and authority['build']==selected['build'] and authority['inputs']==read(selected['inputs'])
        and authority['round']==selected['round'] and authority['limit']==selected['limit']
        and authority['created_at_ns']>=after['updated_at'],
        'actual reserved review is detached from its completed initial renewal')
    return current


@contextmanager
def context(row,*,continued=False,invocation=None):
    p=read(row);require(set(p)-{'completed_takeover','round_history','checkpoint_reader','resume','completed_step','namespace_module','completed_tail','prepared_context','retain_sufficient','archived_history','semantic_archive','interrupted_step','initial_review_lease','renewal_runtime','lifecycle_scope'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed aggregate continuation required')
    require('retain_sufficient'not in p or p['retain_sufficient']is True,'explicit retain-live policy required')
    require('archived_history'not in p or p['archived_history']is True,'explicit completed archive reader required')
    require(('completed_takeover'in p)==('round_history'in p) and ('completed_takeover'not in p or p['renewal_module']is None),'replacement requires exact completed round history and cannot renew')
    require(set(p['context'])=={'helper','plan'}and read(p['context']['plan'])['helper']==p['context']['helper'],'exact completed review context required')
    M=module('completed_independent_review_context',p['context']['helper']);selected=read(p['context']['plan'])
    if 'resume'in p:resume_state(p,continued=continued)
    if 'completed_step'in p:completed_step(p,continued=continued)
    if 'completed_tail'in p:completed_tail(p,continued=continued)
    home=Path(p['output']);require(home.is_absolute()and not home.is_relative_to(Path(selected['output'])),'distinct owning adoption namespace required')
    original_context=M.context
    if continued:
        require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=row,automatic_retry=False),'exact owning parent intent required')
        require('resume'in selected or 'prepared_context'in p or 'initial_review_lease'in p,'completed historical review requires its declared custody archive')
    if 'initial_review_lease'in p:
        require('prepared_context'not in p and 'completed_takeover'not in p,'initial review cannot combine alternate custody readers')
    elif continued and 'prepared_context'in p:
        M.context=prepared_review_context(M,p,row,adoption_home(p),invocation=invocation)
    elif continued or 'completed_takeover'in p:
        M.context=completed_review_context(M,p,row,adoption_home(p),invocation=invocation)
    try:
        with M.context(p['context']['plan'])as(old,U,env,w,s,lease,inputs,modules):
            require((old['workbook'],old['source'])==(p['workbook'],p['source'])and binding(p['source']['path'])==p['source'],'review adoption source changed')
            C,I=env[:2];Q=modules['rounds'];D=Q.bound_module(old['build']['dispatcher']);job=read(p['original_job'])
            if 'initial_review_lease'in p:lease=initial_review_lease(p,old,w,lease,Q)
            require(job['driver']==binding(D.__file__)and read(job['config'])==env[5]and job['publish']is False,'original authoring dispatcher changed')
            raw=C._run_in_process_group
            history=module('scripts.batch.harbor_disclosure_round_history',p['history'])
            import scripts.batch as package
            package.harbor_disclosure_round_history=history
            A=module('scripts.batch.harbor_disclosure_round_adoption',p['adoption']);package.harbor_disclosure_round_adoption=A
            require(binding(history.R.__file__)==old['modules']['review'],'adoption uses a different completed review reader')
            historical_home=adoption_home(p)
            receipt=binding(historical_home/'permit/permit.json')if (historical_home/'permit/permit.json').exists()else None
            if 'completed_takeover'in p:
                lease,_,_=takeover_custody(p,w,lease,selected['resume']['lease_after'])
            if (home/'lease/complete.json').exists():
                L=module('aggregate_completed_lease',p['renewal_module'])
                lease=L.validate_completed(life=Q.life,read=read,record=binding,path=w.context.tracker_root/(p['workbook']+'.json'),previous=lease,
                    completion=binding(home/'lease/complete.json'),before_binding=binding(home/'lease-before.json'),after_binding=binding(home/'lease-after.json'),authority_record=row)
            with owning_runtime(A,p,receipt=receipt), interrupted_runtime(p,env[3],A,continued=continued):
                if receipt is not None:
                    actual=read(receipt);require(actual['round_plan']==p['context']['plan']and actual['round_result']==p['round_result'],'foreign adoption receipt')
                    w=replace(w,disclosure_repair=receipt)
                selected_runtime=nullcontext(env)
                if 'renewal_runtime'in p:
                    require('completed_tail'in p and read(p['completed_tail'])['schema_version']=='fleet-disclosure-adoption-completed-tail/v2',
                        'renewal runtime requires exact completed direct adoption')
                    profile=read(p['renewal_runtime']);selected_runtime=module('selected_adoption_renewal',profile['helper']).installed(sys.modules[__name__],p['renewal_runtime'],env,w,A,D)
                with selected_runtime as env:
                    with lifecycle_runtime(p,env,job,w):
                        if invocation is not None:w,s,lease=current_checkpoint(dict(p,_binding=row),env,w,lease,A,invocation,installed=True)
                        elif 'completed_tail'in p:
                            tail,prior,actual=completed_tail(p,continued=continued)
                            w,s,lease=current_checkpoint(prior,env,w,lease,A,actual,installed=True)
                            if not continued:require(not env[3].step_directory(w,read(read(tail['outcomes'][-1])['result'])['next_step']).exists(),'next tail operation already started')
                        yield p,M,U,D,env,w,s,lease,A,raw,job
    finally:M.context=original_context


@contextmanager
def lifecycle_runtime(p,env,job,worker):
    """Bind only the current facade to the authenticated selected code root."""
    if 'lifecycle_scope'not in p:
        yield;return
    config=read(job['config'])
    require(config==env[5]and config['workbook']==p['workbook'],
            'current facade requires the original authenticated configuration')
    snapshot=read(config['code']);original=Path(snapshot['root'])
    require(original==Path(env[6])and binding(env[3].__file__)==p['owning_profile']['worker'],
            'current facade is detached from the declared worker or source snapshot')
    helper=module('aggregate_current_lifecycle',p['lifecycle_scope'])
    with helper.installed(env[3],original=original,selected=worker.root):yield


@contextmanager
def interrupted_runtime(p,W,A,*,continued=False):
    if 'interrupted_step'not in p:
        yield;return
    K=module('aggregate_interrupted_consumer',p['interrupted_step']['helper'])
    with K.installed(sys.modules[__name__],p,W,A,continued=continued):yield


def operations_for(p,U,D,config):
    if 'namespace_module'not in p:return U.operations_for(D,config,Path(p['output']))
    selected=module('aggregate_operation_namespace',p['namespace_module'])
    return selected.selected_operation_namespace(config,str(Path(config['run_root'])/'dispatcher-v2'/Path(p['output']).name))


def install_commands(row,p,U,D,env,raw,job):
    C,I=env[:2];operations=operations_for(p,U,D,env[5])
    def setup(current):
        require(current==dict(job,outcome=current['outcome'],operations=str(operations)),'original authoring job changed beyond declared adoption')
        return env
    D.setup=setup
    def guarded(command,*args,**options):
        kind,intent=U.retained_command(command,p,D,env)
        seconds=I.guard_seconds('step',read(intent)['arguments'])if kind=='instruction'else read(intent)['seconds']
        require(options['timeout_seconds']==seconds and options['memory_limit_bytes']==8589934592,'original owning guard changed')
        side=U.keep(Path(intent['path']).parent/'aggregate-bootstrap.json',dict(schema_version=SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=command,seconds=seconds,memory_bytes=8589934592,automatic_retry=False))
        argv=[command[0],'-I','-B',p['helper']['path'],'child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],'--bootstrap',side['path'],'--bootstrap-sha256',side['sha256']]
        return raw(argv,*args,**options)
    C._run_in_process_group=guarded


def child(row,intent,sidecar):
    require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated original owning child required')
    with context(row,continued=True,invocation=(intent,sidecar))as(p,M,U,D,env,w,s,l,A,raw,job):
        U.custody(p,w.root);side=read(sidecar);kind,actual=U.retained_command(side['original_command'],p,D,env)
        seconds=env[1].guard_seconds('step',read(intent)['arguments'])if kind=='instruction'else read(intent)['seconds']
        require(actual==intent and side==dict(schema_version=SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],seconds=seconds,memory_bytes=8589934592,automatic_retry=False)
                and Path(sidecar['path'])==Path(intent['path']).parent/'aggregate-bootstrap.json','original child detached from declared parent')
        install_commands(row,p,U,D,env,raw,job)
        return env[1].child(intent)if kind=='instruction'else D.renewal_child(intent)


def run(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot adopt')
    with context(row)as(p,M,U,D,env,w,s,lease,A,raw,job):
        C,I,_,W=env[:4];U.custody(p,w.root);home=Path(p['output']);require(not home.exists(),'adoption already started; retain outcome')
        home.mkdir(parents=True);U.keep(home/'intent.json',dict(schema_version=SCHEMA,plan=row,automatic_retry=False))
        if 'completed_tail'in p:
            saved,_,_=completed_tail(p);outcome=saved['outcomes'][-1]
            for previous in saved['outcomes']:D.successful(previous,env)
        elif 'completed_step'in p:
            saved=completed_step(p);outcome=saved['outcome']
            _,value=D.successful(outcome,env)
        elif 'resume'in p or 'interrupted_step'in p:
            saved=resume_state(p);receipt=saved['permit'];descriptor=saved['descriptor'];checkpoint=saved['checkpoint']
            _,s,lease=I._prior(checkpoint,descriptor)
        else:
            if 'completed_takeover'in p:
                selected=p['completed_takeover'];tp=read(selected['plan']);done=read(selected['completion']);stop=read(done['stopped_owner_proof'])
                proof=read(p['round_history'])
                require(proof['status']=='COMPLETED_ROUND_HISTORY_PASS_CUSTODY_NOT_GRANTED'and proof['plan']==p['context']['plan']
                    and proof['result']==p['round_result']and proof['tracker']==tp['selection']['tracker']
                    and proof['models_dispatched']==proof['lease_mutations']==proof['task_mutations']==0 and proof['aggregate_adopted']is False,
                    'actual completed round archive selection changed')
                renewed=U.keep(home/'round-renewal.json',dict(schema_version=A.TAKEOVER,implementation=p['adoption'],authority=row,
                    round_plan=p['context']['plan'],round_result=p['round_result'],module=tp['shared']['takeover'],completion=selected['completion'],
                    before=stop['before'],after=done['after'],historical_records=proof['historical_records'],historical_instruction=proof['historical_instruction'],
                    takeover_plan=selected['plan'],layout_runtime=selected['runtime']))
            else:
                L=module('aggregate_live_lease',p['renewal_module']);Q=sys.modules['scripts.batch.harbor_disclosure_review_round']
                with (w.run/'worker.lock').open('a+b')as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    tracker=W.active(w,s,lease)[2];W.snapshot(home/'lease-before.json',tracker)
                    def full(before,current):
                        _,actual,_=W.active(w,s,current);require(actual==before,'review adoption custody changed')
                        checked,calls,_=A.history.completed_run(p['context']['plan'],p['round_result'],policy=s.content.model_policy,root=w.root,campaign=w.campaign)
                        verified=A.review.validate_plan(read(p['round_result'])['review_plan'],policy=s.content.model_policy,root=w.root,current_inputs=True)
                        archives,instruction=A.archive_records(checked,calls,verified,w,s,tracker)
                        proof=U.keep(home/'validated-round.json',dict(round_plan=p['context']['plan'],round_result=p['round_result'],tracker=tracker,historical_records=archives,historical_instruction=instruction,models_dispatched=0))
                        return [p['context']['plan'],p['round_result'],proof]
                    lease,completion=L.renew(life=Q.life,read=read,record=binding,keep=U.keep,path=tracker['path'],lease=lease,authority_record=row,validation=full,custody=lambda:U.custody(p,w.root),directory=home/'lease',retain_sufficient=p.get('retain_sufficient',False))
                    W.snapshot(home/'lease-after.json',binding(tracker['path']))
                proof=read(binding(home/'validated-round.json'))
                renewed=U.keep(home/'round-renewal.json',dict(schema_version=A.RETAINED if read(completion)['schema_version']=='fleet-retained-live-continuation-lease/v1'else A.RENEWAL,implementation=p['adoption'],authority=row,round_plan=p['context']['plan'],round_result=p['round_result'],module=p['renewal_module'],completion=completion,before=binding(home/'lease-before.json'),after=binding(home/'lease-after.json'),historical_records=proof['historical_records'],historical_instruction=proof['historical_instruction']))
            receipt=A.authorize(w,s,lease,round_plan=p['context']['plan'],round_result=p['round_result'],directory=home/'permit',renewal=renewed)
            new=replace(w,disclosure_repair=receipt);old=read(read(p['context']['plan'])['build']['descriptor'])
            descriptor=U.keep(home/'permit/descriptor.json',{**old,'worker':I.worker_encode(new),'initial':dict(selection=I.codec.encode(s),lease=C.encode(lease)),
                'origin':[*old['origin'],p['context']['plan'],p['round_result'],receipt]})
            checkpoint=U.keep(home/'permit/result.json',I._result('RETRY_READY',descriptor,s,lease,checkpoint=receipt,next_step='disclosure-review',evidence=[p['round_result'],receipt]))
        install_commands(row,p,U,D,env,raw,job);U.custody(p,w.root)
        if 'completed_step'not in p and 'completed_tail'not in p:
            I.selected(descriptor);I._prior(checkpoint,descriptor)
            outcome=I.run('step',dict(descriptor=descriptor,checkpoint=checkpoint),env[7],home/'first-step',models=False,commands=False,containers=False)
        _,value=D.successful(outcome,env);require(value['status']=='STEP_COMPLETE'and ('completed_tail'in p or value['next_step']=='disclosure-commit'),'original aggregate consumption did not complete')
        current={**job,'outcome':outcome,'operations':str(operations_for(p,U,D,env[5]))};bound=U.keep(home/'authoring-job.json',current)
        D.run(bound);held=read(binding(Path(current['operations'])/'hold.json'));last,result=D.successful(held['outcome'],env)
        return U.keep(home/'result.json',dict(status=result['status'],workbook=p['workbook'],continuation=row,job=bound,config=job['config'],outcome=held['outcome'],checkpoint=last,aggregate=p['round_result'],publication_performed=False,automatic_retry=False))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('run','child'));parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True)
    for name in ('intent','intent-sha256','bootstrap','bootstrap-sha256'):parser.add_argument('--'+name)
    args=parser.parse_args();row=dict(path=args.plan,sha256=args.sha256)
    result=run(row)if args.action=='run'else child(row,dict(path=args.intent,sha256=args.intent_sha256),dict(path=args.bootstrap,sha256=args.bootstrap_sha256))
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
