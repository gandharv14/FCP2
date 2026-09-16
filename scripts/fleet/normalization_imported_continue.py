"""Continue one authentic imported normalization prefix without repeating it."""
from pathlib import Path
from contextlib import contextmanager,ExitStack
import argparse,hashlib,importlib.util,json,os,sys,ast

SCHEMA='fleet-normalization-imported-continuation/v1'
SCHEMA2='fleet-normalization-imported-continuation/v2'
FIELDS={'schema_version','helper','previous','history_helper','prepared_reader','analysis_cache','environment_helper','takeover_runtime','takeover_profile','takeover_completion','checkpoint','committed','refresh_outcome','bridge_guard','outer','stopped','workbook','source','owner','fence','home'}

def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical imported continuation evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
    require(binding(row['path'])==row,'imported continuation evidence changed');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
    require(binding(row['path'])==row,'imported continuation module changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m


def identity(q,p):
    if 'completed_package'in q:
        P=module('selected_completed_package',q['completed_package_helper'])
        return P.identity(sys.modules[__name__],q,p)
    if 'interruption' in q:
        require(q['helper']==binding(__file__),'selected interrupted helper differs')
        interrupted=module('selected_prepackage_interruption',q['interruption_helper'])
        return interrupted.validate(sys.modules[__name__],q,p)
    if q.get('schema_version')==SCHEMA2:return remaining_identity(q,p)
    require(set(q)in(FIELDS,FIELDS|{'preexecution'}) and q['schema_version']==SCHEMA and q['helper']==binding(__file__)
        and 'single_author'in p and 'prepared_refresh'in p and q['workbook']==p['workbook'] and q['source']==p['source'],
        'closed original imported normalization required')
    home,old=Path(q['home']),Path(p['home'])
    require(home.is_absolute()and home.parent==old.parent and home!=old and type(q['fence'])is int and q['fence']==p['fence']+(2 if 'preexecution'in q else 1),
        'distinct next imported continuation required')
    outer=read(q['outer']);intent=read(outer['intent']);job=read(intent['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['result']is None and outer['automatic_retry']is False and outer['publication_performed']is False
        and job['driver']==p['helper'] and job['inputs'][0]==q['previous'] and job['workbook']==q['workbook']and job['source']==q['source']
        and intent['owner']==p['owner']
        and len(job['command'])==9 and job['command'][1:]==['-I','-B',p['helper']['path'],'run','--plan',q['previous']['path'],'--plan-sha256',q['previous']['sha256']]
        and job['result']==str(old/'result.json'),'different stopped normalization worker')
    claim=read(binding(Path(outer['intent']['path']).parent/'claim.json'))
    require(claim['claim_id']==intent['claim_id'] and claim['owner']==p['owner'] and claim['fence']==p['fence']
        and claim['workbook']==q['workbook'] and claim['version']==job['version'] and claim['source_sha256']==q['source']['sha256'],'original claim custody changed')
    guard=read(q['bridge_guard']);gi=read(guard['intent'])
    cp=read(q['checkpoint']);refresh=read(q['refresh_outcome'])
    require(q['checkpoint']==binding(old/'refresh/result.json') and cp['status']=='IMPORTED'
        and q['refresh_outcome']==binding(old/'refresh/outcome.json') and refresh['status']=='COMPLETED_OPERATION'and refresh['result']==q['checkpoint']
        and read(refresh['resource'])['returncode']==0,'original completed import required')
    require(q['bridge_guard']==binding(old/'bridge-step/resource.json') and guard['returncode']==1 and guard['failure']is None
        and guard['log']==binding(old/'bridge-step/step.log') and gi['action']=='step'
        and gi['arguments']==dict(descriptor=cp['descriptor'],checkpoint=q['checkpoint'],captures=None)
        and Path(guard['log']['path']).read_text().rstrip().endswith('ValueError: single emitted phase one changed')
        and not any((old/n).exists()for n in ('bridge-step/result.json','authoring-job.json','result.json')),
        'different stopped post-import boundary')
    prepared=read(p['prepared_refresh'])['prepared'];descriptor=read(cp['descriptor']);value=read(prepared)
    require(descriptor['refresh']==prepared and cp['selection']==value['selection']and cp['step']==value['last']
        and q['committed']==binding(Path(prepared['path']).parent/'committed.json') and q['committed']in cp['evidence'],
        'imported prefix changed')
    if 'preexecution'in q:preexecution(q)
    return prepared,cp,outer



def remaining_identity(q,p):
    """Preserve a completed phase-two gate and its failed pre-packaging child."""
    proof=read(q['remaining']);require(set(proof)=={'schema_version','previous','outer','stopped','completed','failed'}
        and proof['schema_version']=='fleet-imported-phase2-remaining/v1','closed phase-two remaining custody required')
    old=read(proof['previous']);changed={'schema_version','helper','history_helper','fence','home','remaining'}
    require(old['schema_version']==SCHEMA and set(q)==set(old)|{'remaining'}
        and q['helper']==binding(__file__)
        and {k:v for k,v in q.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed}
        and q['fence']==old['fence']+1 and Path(q['home']).parent==Path(old['home']).parent and q['home']!=old['home'],
        'remaining phase-two continuation changed original authority')
    trees=[]
    for r in (old['history_helper'],q['history_helper']):
        require(binding(r['path'])==r,'remaining historical helper changed');tree=ast.parse(Path(r['path']).read_text())
        added=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='phase1_projection']
        require(len(added)==(0 if r==old['history_helper'] else 1),'only explicit phase-one archive scope may be added')
        tree.body=[n for n in tree.body if n not in added];trees.append(ast.dump(tree))
    require(trees[0]==trees[1],'unrelated historical reader changed')
    original=module('imported_completed_predecessor',old['helper']);prepared,cp,original_outer=original.identity(old,p)
    home=Path(old['home']);operations=Path(str(home)+'-authoring')
    outer=read(proof['outer']);wi=read(outer['intent']);job=read(wi['job']);claim_row=binding(Path(outer['intent']['path']).parent/'claim.json');claim=read(claim_row)
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1 and outer['failure_type']is None
        and outer['result']is None and outer['automatic_retry']is False and outer['publication_performed']is False
        and job['driver']==old['helper'] and job['inputs'][0]==proof['previous']
        and job['source']==q['source'] and job['workbook']==q['workbook']
        and job['command'][3:]==[old['helper']['path'],'run','--plan',proof['previous']['path'],'--plan-sha256',proof['previous']['sha256']]
        and claim['owner']==wi['owner']==old['owner'] and claim['fence']==old['fence']
        and claim['version']==job['version'] and claim['workbook']==q['workbook']
        and claim['source_sha256']==q['source']['sha256'] and claim['claim_id']==wi['claim_id'],
        'different stopped phase-two worker or claim')
    completed=read(proof['completed']);last=read(completed['result']);bridge_row=binding(home/'bridge-step/outcome.json');bridge=read(bridge_row)
    require(proof['completed']==binding(operations/'000/outcome.json') and completed['status']==bridge['status']=='COMPLETED_OPERATION'
        and completed['result']==binding(operations/'000/result.json')
        and last['status']=='STEP_COMPLETE' and last['step']=='normalization_phase2'
        and last['descriptor']==cp['descriptor'] and read(bridge['result'])['status']=='STEP_COMPLETE'
        and read(bridge['result'])['step']=='source_profile' and read(bridge['result'])['descriptor']==cp['descriptor'],'completed source-profile and phase-two prefix required')
    for outcome,prior in ((bridge,q['checkpoint']),(completed,bridge['result'])):
        guard=read(outcome['resource']);intent=read(guard['intent'])
        require(guard['returncode']==0 and guard['failure']is None and intent['action']=='step'
            and intent['arguments']==dict(descriptor=cp['descriptor'],checkpoint=prior,captures=None),
            'completed prefix guard detached from original predecessor')
    failed=read(proof['failed']);guard=read(failed['resource']);intent=read(guard['intent']);log=Path(guard['log']['path']).read_text()
    mutable=next(w['original']['path']for w in read(prepared)['mutable_witnesses']if w['key']=='normalized_final')
    require(proof['failed']==binding(operations/'001/outcome.json') and failed['status']=='RECONCILIATION_REQUIRED'
        and guard['returncode']==1 and guard['failure']is None and intent['action']=='step'
        and intent['arguments']==dict(descriptor=cp['descriptor'],checkpoint=completed['result'],captures=None)
        and guard['log']==binding(operations/'001/step.log')
        and log.rstrip().endswith('scripts.batch.harbor_tracker.Conflict: changed evidence: '+mutable)
        and 'normalization_projection' in log and 'F.validate(w,prepared,read(current))' in log
        and not (operations/'001/result.json').exists() and not (home/'result.json').exists(),
        'different stopped pre-packaging boundary')
    stop=read(proof['stopped']);require(set(stop)=={'at','processes','files'} and stop['processes']==[], 'phase-two predecessor process remains')
    by={r['native']['path']:r['native']['sha256']for r in stop['files']}
    required=[proof['previous'],proof['outer'],outer['intent'],claim_row,proof['completed'],completed['result'],bridge_row,bridge['result'],proof['failed'],failed['resource'],guard['intent'],guard['log']]
    require(len(by)==len(stop['files']) and all(by.get(r['path'])==r['sha256']for r in required),'phase-two stopped inventory incomplete')
    tracker_path=read(q['takeover_profile'])['selection']['tracker']['path']
    encoded=last['lease'];require(set(encoded)=={'type','value'} and encoded['type']=='Lease'
        and set(encoded['value'])=={'owner','token','revision','sha256'}
        and all(isinstance(v,dict) and set(v)=={'type','value'} and v['type']=='scalar' for v in encoded['value'].values())
        and by.get(tracker_path)==encoded['value']['sha256']['value'],
        'stopped tracker is not the actual completed phase-two lease')
    for r in stop['files']:
        require(binding(r['copy']['path'])==r['copy'] and r['native']['sha256']==r['copy']['sha256'],'phase-two stopped evidence copy changed')
        if r['native']['path']!=tracker_path:require(binding(r['native']['path'])==r['native'],'immutable phase-two boundary changed')
    return prepared,cp,original_outer


def claim_environment(q):
    # The outer worker maintains its heartbeat throughout code/context loading.
    # Client imports become available only after the original S.load.
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==q['workbook']
        and os.environ.get('HARBOR_FLEET_OWNER')==q['owner']
        and os.environ.get('HARBOR_FLEET_FENCE')==str(q['fence'])
        and all(os.environ.get(k)for k in ('HARBOR_FLEET_URL','HARBOR_FLEET_TOKEN_FILE','HARBOR_FLEET_CLAIM_TOKEN')),
        'live imported worker claim environment required')


def replacement_takeover(q,previous):
    if q['takeover_profile']==previous['takeover_profile']and q['takeover_completion']==previous['takeover_completion']:
        require(q['owner']==previous['owner'],'owner changed without completed takeover');return None
    original=read(previous['takeover_profile']);done=read(previous['takeover_completion']);selected=read(q['takeover_profile'])
    require('selection'in original and 'after'in done,'original completed takeover schema differs')
    require(selected==dict(original,selection=dict(original['selection'],tracker=dict(original['selection']['tracker'],sha256=done['after']['sha256']),prior_completion=previous['takeover_completion'],checkpoint=previous['takeover_completion'],owner=q['owner']))
        and q['takeover_completion']!=previous['takeover_completion'],'replacement takeover changed original checkpoint or authority')
    current=read(q['takeover_completion']);stop=read(current['stopped_owner_proof'])
    require(stop['before']['sha256']==done['after']['sha256']and read(stop['before'])==read(done['after']),'replacement takeover does not follow retained custody')
    return previous['takeover_completion']


def preexecution(q):
    proof=read(q['preexecution'])
    require(set(proof)=={'schema_version','previous','outer','stopped'} and proof['schema_version']=='fleet-imported-preexecution-stop/v1','closed imported preexecution stop required')
    previous=read(proof['previous']);changed={'helper','home','fence','preexecution','takeover_profile','takeover_completion','owner','history_helper'}
    require(set(previous)==FIELDS and {k:v for k,v in previous.items()if k not in changed}=={k:v for k,v in q.items()if k not in changed}
        and q['fence']==previous['fence']+1 and Path(q['home']).parent==Path(previous['home']).parent and q['home']!=previous['home'],
        'preexecution changed imported authority')
    replacement_takeover(q,previous)
    if q['history_helper']!=previous['history_helper']:
        trees=[]
        for r in(previous['history_helper'],q['history_helper']):
            require(binding(r['path'])==r,'historical reader code changed');tree=ast.parse(Path(r['path']).read_text());functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='takeover_prior'];require(len(functions)==1,'exact historical lease callback required');tree.body.remove(functions[0]);trees.append(ast.dump(tree))
        require(trees[0]==trees[1],'historical reader changed beyond completed takeover callback')
    outcome=read(proof['outer']);intent=read(outcome['intent']);job=read(intent['job']);claim=read(binding(Path(outcome['intent']['path']).parent/'claim.json'))
    require(outcome['status']=='RECONCILIATION_REQUIRED' and outcome['returncode']==1 and outcome['failure_type']is None
        and outcome['result']is None and outcome['automatic_retry']is False and outcome['publication_performed']is False
        and intent['owner']==previous['owner'] and claim['owner']==previous['owner'] and claim['fence']==previous['fence']
        and claim['claim_id']==intent['claim_id'] and claim['version']==job['version'] and claim['workbook']==q['workbook'] and claim['source_sha256']==q['source']['sha256']
        and job['driver']==previous['helper'] and job['inputs'][0]==proof['previous'] and job['source']==q['source'] and job['workbook']==q['workbook']
        and job['command'][3:]==[previous['helper']['path'],'run','--plan',proof['previous']['path'],'--plan-sha256',proof['previous']['sha256']],
        'different imported preexecution failure')
    log=binding(Path(proof['outer']['path']).parent/'worker.log');text=Path(log['path']).read_text()
    require(outcome['log']==log and text.rstrip().endswith("ModuleNotFoundError: No module named 'scripts'") and 'if live:N.active(q)'in text
        and not Path(previous['home']).exists() and not Path(previous['home']+'-authoring').exists(),
        'imported task effects already exist or failure differs')
    stop=read(proof['stopped']);require(set(stop)=={'at','processes','files'}and stop['processes']==[],'preexecution process remains')
    rows=stop['files'];inventory={r['native']['path']:r['native']['sha256']for r in rows}
    needed=[proof['outer'],outcome['intent'],intent['job'],proof['previous'],log,binding(Path(outcome['intent']['path']).parent/'claim.json')]
    require(len(rows)==len(inventory)and all(inventory.get(r['path'])==r['sha256']for r in needed),'preexecution inventory incomplete')
    for r in rows:require(binding(r['native']['path'])==r['native']and binding(r['copy']['path'])==r['copy']and r['native']['sha256']==r['copy']['sha256'],'preexecution evidence changed')


def retained_custody(q,rows,T):
    stop=read(q['stopped']);require(set(stop)=={'at','processes','files'}and stop['processes']==[],'stopped imported worker required')
    witnesses={(r['original']['path'],r['original']['sha256']):r['copy']for r in rows}
    completion=read(q['takeover_completion']);takeover=read(completion['stopped_owner_proof']);profile=read(q['takeover_profile'])
    witnesses[(profile['selection']['tracker']['path'],takeover['before']['sha256'])]=takeover['before']
    if 'completed_package'in q:
        P=module('completed_package_prior_tracker',q['completed_package_helper'])
        key,archive=P.prior_tracker_witness(sys.modules[__name__],q,T);witnesses[key]=archive
    seen=set()
    for item in stop['files']:
        native,copy=item['native'],item['copy'];require(native['path']not in seen,'duplicate stopped evidence');seen.add(native['path'])
        require(binding(copy['path'])==copy and copy['sha256']==native['sha256'],'stopped evidence copy changed')
        replacement=witnesses.get((native['path'],native['sha256']))
        require(binding((replacement or native)['path'])==(replacement or native),'stopped native evidence changed outside authenticated refresh history')
    required=[q['outer'],q['checkpoint'],q['committed'],q['bridge_guard'],q['refresh_outcome']]
    require(all(r['path']in seen for r in required),'stopped inventory lacks owning import boundary')


def evidence(q,p):
    return ([q['completed_package'],q['completed_package_helper']]if 'completed_package'in q else [])+([q['interruption'],q['interruption_helper'],q['pair_cache'],q['docker_profile'],q['docker_helper']]if 'interruption'in q else [])+([q['remaining']]if 'remaining'in q else [])+([q['preexecution']]if 'preexecution'in q else [])+[q[k]for k in ('previous','history_helper','prepared_reader','analysis_cache','environment_helper','takeover_runtime','takeover_profile','takeover_completion','checkpoint','committed','refresh_outcome','bridge_guard','outer','stopped')]+[p[k]for k in ('runtime','runtime_profile','campaign_adapter','single_author','single_helper','prepared_refresh','prepared_helper','completed_emission','completed_helper')]


@contextmanager
def context(row,*,initial=False,live=False):
    q=read(row);p=read(q['previous']);prepared,cp,outer=identity(q,p)
    N=module('imported_original_normalization',p['helper'])
    if live:claim_environment(q)
    H=module('imported_historical_preparation',q['history_helper'])
    E=module('imported_environment_takeover',q['environment_helper']);R=module('imported_takeover_runtime',q['takeover_runtime'])
    _,T,s,_=E.takeover_context(R,q['takeover_profile'],q['takeover_completion'],current=initial and 'remaining'not in q)
    tp=read(q['takeover_profile']);done=read(q['takeover_completion']);before,after,lease=T.validate_completion(q['takeover_completion'])
    prior_completion=replacement_takeover(q,read(read(q['preexecution'])['previous']))if 'preexecution'in q and 'completed_package'not in q else None
    package=module('selected_completed_package_context',q['completed_package_helper'])if 'completed_package'in q else None
    if package is not None:package.takeover(sys.modules[__name__],q,T,lease)
    else:
        require(s.workbook==q['workbook']and s.owner==q['owner']==lease.owner and s.checkpoint==(prior_completion or q['checkpoint'])
            and s.prior_completion==(prior_completion or p['completion']) and after['bindings']['original_raw_source']==q['source'],
            'replacement custody detached from original import')
    S=N.module('imported_original_single',p['single_helper']);values=S.load(N,p)
    if live:N.active(q)
    producer,w,selected=values[4:7];base=values[3][0]
    rows=H.witnesses(prepared,p['tracker'],base.content_codec.decode);retained_custody(q,rows,T)
    cache=module('imported_original_analysis_cache',q['analysis_cache']);runtime=read(p['runtime_profile'])
    dependencies=[q['previous'],p['helper'],p['runtime'],p['runtime_profile']]
    for item in [*runtime['symbols'],*runtime['dependencies']]:dependencies.extend(item[k]for k in ('original','selected'))
    with ExitStack()as stack:
        if 'interruption'in q:
            docker=module('selected_prepackage_docker_command',q['docker_helper'])
            stack.enter_context(docker.installed(q['docker_profile'],entrypoint=q['helper'],workbook=q['workbook'],source=q['source']))
        stack.enter_context(cache.installed(producer.gen,workbook=q['workbook'],source=selected.bindings['source_file'],baseline=selected.bindings['baseline_inputs'],draft=binding(selected.run/'draft.json'),normalizer=binding(Path(values[3][6])/'gen_normalizer.py'),dependencies=dependencies))
        stack.enter_context(H.declared_readers(N,p,q['prepared_reader'],rows,dict(checkpoint=q['checkpoint'],committed=q['committed'])))
        oldp,old,M,D,env,P,w,oldlease,A=stack.enter_context(N.context(q['previous'],initial=False))
        from scripts.batch import harbor_environment_refresh as F
        if 'remaining'in q:stack.enter_context(H.phase1_projection(F,prepared,rows))
        if 'interruption'in q:
            if package is not None:package.packaging(sys.modules[__name__],q,w,env[0])
            else:require(not (w.run/'packaging').exists(),'interrupted packaging effects must be retained separately')
            pair=module('selected_imported_pair_cache',q['pair_cache'])
            stack.enter_context(pair.installed(F,refresh=binding(F.__file__),lanes=binding(F.W.lanes.__file__)))
        current=binding(s.tracker['path']);F.validate(w,prepared,read(current));env[0]._prior(q['checkpoint'],cp['descriptor'])
        if 'remaining'in q and package is None:
            completed=read(read(q['remaining'])['completed']);last=read(completed['result']);_,actual_lease=env[0]._prior(completed['result'],last['descriptor'])
            require((actual_lease.owner,actual_lease.token)==(lease.owner,lease.token) and actual_lease.revision>=lease.revision,'completed phase-two lease left original takeover custody')
            lease=actual_lease
        if initial:
            require(not Path(q['home']).exists(),'imported continuation consumed')
            N.dispatcher_directory(env[5],q['home']);env[0].worker._active(w,lease)
        if package is not None:stack.enter_context(package.prior_scope(sys.modules[__name__],q,env[0],w,T))
        else:stack.enter_context(H.takeover_prior(env[0],w,q['checkpoint'],q['takeover_completion'],T,previous_completion=prior_completion))
        yield q,p,N,D,env,w,lease,A


def selected_dispatcher(q,p,N,D,env,A,row):
    actual=dict(p,helper=q['helper'],home=q['home'],owner=q['owner'],fence=q['fence']);original=N.evidence
    N.evidence=lambda _:evidence(q,p)
    try:return N.selected_dispatcher(D,env,A,row,actual)
    finally:N.evidence=original


def owning_child(row,kind,intent):
    with context(row,live=True)as(q,p,N,D,env,w,lease,A):
        N.active(q);producer={'environment':env[0],'instruction':env[1],'renewal':D}[kind]
        A.validate_child(producer,base=env[0],kind=kind,reader_plan=row,child_wrapper=q['helper'],intent=intent,reader_evidence=evidence(q,p))
        return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)


def run(row):
    with context(row,initial=True,live=True)as(q,p,N,D,env,w,lease,A):
        N.active(q);D,selected=selected_dispatcher(q,p,N,D,env,A,row);C=selected[0];home=Path(q['home']);home.mkdir(mode=0o700)
        N.keep(home/'intent.json',dict(plan=row,imported=q['checkpoint'],takeover=q['takeover_completion'],emission_repeated=False,refresh_repeated=False,automatic_retry=False))
        if 'completed_package'in q:outcome=read(q['completed_package'])['completed']
        elif 'remaining'in q:outcome=read(q['remaining'])['completed']
        else:
            cp=read(q['checkpoint']);outcome=C.run('step',dict(descriptor=cp['descriptor'],checkpoint=q['checkpoint'],captures=None),env[-1],home/'bridge-step',dispatch=True)
        D.successful(outcome,selected);old=read(read(p['single_author'])['job']);job=dict(old,outcome=outcome,operations=str(N.dispatcher_directory(env[5],q['home'])))
        jb=N.keep(home/'authoring-job.json',job);D.run(jb);hold=read(binding(Path(job['operations'])/'hold.json'));checkpoint,result=D.successful(hold['outcome'],selected)
        return N.keep(home/'result.json',dict(status=result['status'],workbook=q['workbook'],plan=row,job=jb,config=job['config'],outcome=hold['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','owning-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);a.add_argument('--kind',choices=['environment','instruction','renewal']);a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();row=dict(path=v.plan,sha256=v.plan_sha256)
    if v.action=='run':result=run(row)
    elif v.action=='owning-child':result=owning_child(row,v.kind,dict(path=v.intent,sha256=v.intent_sha256))
    else:
        with context(row,initial=True)as(q,p,N,D,env,w,lease,A):result=dict(status='PASS',models_dispatched=0,result=dict(plan=row,checkpoint=q['checkpoint']),refresh_repeated=False,emission_repeated=False)
    print(json.dumps(result))
