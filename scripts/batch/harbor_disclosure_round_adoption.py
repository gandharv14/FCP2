"""Owning instruction consumption of every actual bounded disclosure Call.

The receipt preserves the source build and spent reviews. It grants no model
transport and supplies no synthetic Call or pre-completed instruction step.
"""
from contextlib import contextmanager,ExitStack
from dataclasses import asdict,replace
from pathlib import Path
from types import ModuleType,SimpleNamespace
import fcntl,sys,shutil
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_transition_codec as codec
from scripts.batch import harbor_disclosure_round_history as history
from scripts.batch import harbor_disclosure_partition_review as review

SCHEMA='harbor-disclosure-round-adoption/v1'
CONTINUATIONS=('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery','code_repair','warning_requalification','disclosure_repair','lease_renewal','disclosure_revision','dialogue_pre_review_repair')
KEYS={'schema_version','implementation','history_reader','round_plan','round_result','review_plan','aggregate','build',
      'worker_run','initial','source','selection','lease','tracker','current_code','prior_continuations','blocked',
      'historical_records','historical_instruction','renewal','semantic_budget_reset','models_dispatched'}


def selected(worker):
    return worker.disclosure_repair is not None and read(worker.disclosure_repair).get('schema_version')==SCHEMA


def archive_records(plan,calls,checked,worker,selection,tracker,*,renewal_before=None):
    """Use genuine executor-retained bytes, preserving original binding identity."""
    evidence=checked[2];rows=[]
    for call in calls:
        submitted=read(call.submitted);snapshots=submitted['budget_evidence']
        require(len(snapshots)==len(evidence)+1,'actual Call omitted full reserved evidence')
        for original,archive in zip(evidence,snapshots[1:]):
            actual=renewal_before if renewal_before is not None and original==dict(path=tracker['path'],sha256=renewal_before['sha256'])else original
            require(E.checked_bytes(actual)==E.checked_bytes(archive),'actual Call snapshot differs from original evidence')
            row=dict(original=original,archive=archive)
            if not any(item['original']==original for item in rows):rows.append(row)
    p=checked[0];old=record(selection.content.stage/'instruction.md')
    archive=instruction_archive(p,old)
    rows.append(dict(original=old,archive=archive))
    return rows,old


def instruction_archive(plan,original):
    authority=read(plan['review_round'])if 'review_round'in plan else {}
    if 'source_derivative'not in authority:
        require(original['sha256']==plan['instruction']['sha256'],
                'historical staged instruction differs from reviewed snapshot')
        return plan['instruction']
    # The original reviewed input snapshot remains an independent exact archive.
    derivative=read(authority['source_derivative']);proof=read(derivative['proof'])
    require(original['path']==str(Path(proof['original_task'])/'instruction.md')
            and original['sha256']==proof['original_tree']['instruction.md'],
            'derivative lost original build instruction')
    E.checked_bytes(original)
    archived=derivative['original_instruction'];E.checked_bytes(archived)
    require(archived['path']!=original['path']and archived['sha256']==original['sha256'],
            'derivative original instruction archive differs')
    return archived


@contextmanager
def historical_round(receipt):
    """Scope archived reads to the original round reader, never live W custody."""
    from scripts.batch import harbor_disclosure_review_round as Q
    by_key={}
    for row in receipt['historical_records']:
        require(set(row)=={'original','archive'}and row['original']['sha256']==row['archive']['sha256'],
                'historical record must retain the exact original bytes')
        E.checked_bytes(row['archive']);key=(row['original']['path'],row['original']['sha256'])
        require(key not in by_key or E.checked_bytes(by_key[key])==E.checked_bytes(row['archive']),'conflicting historical record')
        by_key[key]=row['archive']
    original_read=Q.read;original_record=Q.record;original_life=Q.life
    def checked(binding):
        target=by_key.get((binding['path'],binding['sha256']))
        if target is None:return original_life.checked_record(binding)
        original_life.checked_record(target);return binding
    def historical_read(binding):
        target=by_key.get((binding['path'],binding['sha256']))
        return original_read(target or binding)
    def historical_record(path):
        old=receipt['historical_instruction']
        if str(path)==old['path']:
            checked(old);return old
        return original_record(path)
    proxy=SimpleNamespace(**vars(original_life));proxy.checked_record=checked
    try:
        Q.read=historical_read;Q.record=historical_record;Q.life=proxy
        yield
    finally:Q.read=original_read;Q.record=original_record;Q.life=original_life


RENEWAL='harbor-disclosure-adoption-renewal/v1'
RENEWAL_KEYS={'schema_version','implementation','authority','round_plan','round_result','module','completion','before','after','historical_records','historical_instruction'}
TAKEOVER='harbor-disclosure-adoption-takeover/v1'
RETAINED='harbor-disclosure-adoption-retained-live/v1'

def validate_takeover(binding,worker,lease,round_plan,round_result,*,live):
    """Validate the actual replacement and archived source, without eligibility replay."""
    from scripts.batch import harbor_disclosure_review_round as Q
    value=read(binding)
    require(set(value)==RENEWAL_KEYS|{'takeover_plan','layout_runtime'} and value['schema_version']==TAKEOVER
        and value['implementation']==record(__file__) and value['round_plan']==round_plan and value['round_result']==round_result,
        'closed adoption takeover projection required')
    parent=read(value['authority']);selected=parent['completed_takeover'];tp=read(value['takeover_plan'])
    require(set(selected)=={'runtime','plan','completion'} and selected==dict(runtime=value['layout_runtime'],plan=value['takeover_plan'],completion=value['completion'])
        and parent['adoption']==record(__file__) and parent['context']['plan']==round_plan and parent['round_result']==round_result
        and parent['renewal_module'] is None and tp['schema_version']=='harbor-allowance-takeover-direct/v1'
        and value['module']==tp['shared']['takeover'],'takeover detached from selected adoption authority')
    require(record(Q.life.__file__)==tp['shared']['lifecycle'],'takeover lifecycle differs from retained owner')
    T=Q.bound_module(value['module']);runtime=Q.bound_module(value['layout_runtime'])
    if 'layout'in tp:runtime.select_layout(T,tp['layout'])
    before,after,actual=T.validate_completion(value['completion']);complete=read(value['completion']);stop=read(complete['stopped_owner_proof'])
    path=worker.context.tracker_root/(parent['workbook']+'.json')
    require(stop['selection']==T._selection_value(T._selection(tp['selection']))
        and value['before']==stop['before'] and value['after']==complete['after']
        and tp['selection']['tracker']==dict(path=str(path),sha256=value['before']['sha256'])
        and parent['source']==before['bindings']['original_raw_source']==after['bindings']['original_raw_source']
        and parent['workbook']==before['workbook_id']==after['workbook_id']
        and asdict(actual)==asdict(lease) and actual.owner==parent['owner'], 'completed adoption takeover source or custody differs')
    if live:require(record(path)==dict(path=str(path),sha256=value['after']['sha256']),'live custody drifted after completed takeover')
    return value

def validate_retained(binding,worker,lease,round_plan,round_result,*,live):
    """Read the original no-CAS retain-live receipt and its exact full proof."""
    from scripts.batch import harbor_disclosure_review_round as Q
    value=read(binding)
    require(set(value)==RENEWAL_KEYS and value['schema_version']==RETAINED and value['implementation']==record(__file__)
        and value['round_plan']==round_plan and value['round_result']==round_result,'closed retained adoption custody required')
    parent=read(value['authority']);before=read(value['before']);after=read(value['after']);complete=read(value['completion'])
    require(parent.get('retain_sufficient')is True and parent['adoption']==record(__file__)
        and parent['context']['plan']==round_plan and parent['round_result']==round_result
        and parent['renewal_module']==value['module']and parent['source']==before['bindings']['original_raw_source']
        and parent['workbook']==before['workbook_id'],'retained adoption custody detached from selected authority')
    L=Q.bound_module(value['module']);require(L.SCHEMA=='fleet-validated-continuation-lease/v1'and L.DURATION_NS==7200*10**9,'original bounded retained lease module required')
    path=worker.context.tracker_root/(parent['workbook']+'.json');tracker=dict(path=str(path),sha256=value['before']['sha256'])
    require(E.checked_bytes(value['before'])==E.checked_bytes(value['after'])and before==after
        and before['revision']==lease.revision and tracker['sha256']==lease.sha256,'retained custody changed task bytes or lease')
    native=before['leases']['task_agent'];at=complete['observed_at_ns']
    require(type(at)is int and native['heartbeat_at']<=at and native['expires_at']-at>=L.DURATION_NS
        and (native['owner'],native['token'],native['scope'])==(lease.owner,lease.token,'workbook:'+parent['workbook']),
        'retained receipt has insufficient original runway or foreign ownership')
    count=Q.life.validate_lifecycle(before);home=Path(parent['output']);proof_binding=record(home/'validated-round.json');proof=read(proof_binding)
    require(proof==dict(round_plan=round_plan,round_result=round_result,tracker=tracker,historical_records=value['historical_records'],
        historical_instruction=value['historical_instruction'],models_dispatched=0),'retained full review proof differs')
    require(Path(value['completion']['path'])==home/'lease/retained-live.json'
        and complete==dict(schema_version='fleet-retained-live-continuation-lease/v1',authority=value['authority'],lease=asdict(lease),tracker=tracker,
            validated_evidence=[round_plan,round_result,proof_binding],minimum_remaining_ns=L.DURATION_NS,observed_at_ns=at,
            original_expired_replacements=count,lease_operations=0,task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False),
        'retained receipt differs from original no-CAS authority')
    if live:require(record(path)==tracker,'live custody drifted after retaining lease')
    return value

def validate_renewal(binding,worker,lease,round_plan,round_result,*,live):
    """Authenticate the exact real renewal before using old round archives."""
    import importlib.util
    from scripts.batch import harbor_disclosure_review_round as Q
    from scripts.batch import harbor_instruction_worker as W
    value=read(binding)
    if value.get('schema_version')==RETAINED:return validate_retained(binding,worker,lease,round_plan,round_result,live=live)
    if value.get('schema_version')==TAKEOVER:return validate_takeover(binding,worker,lease,round_plan,round_result,live=live)
    require(set(value)==RENEWAL_KEYS and value['schema_version']==RENEWAL
        and value['implementation']==record(__file__)and value['round_plan']==round_plan and value['round_result']==round_result,
        'closed adoption renewal projection required')
    parent=read(value['authority'])
    require(parent['adoption']==record(__file__)and parent['context']['plan']==round_plan and parent['round_result']==round_result
        and parent['source']==read(value['before'])['bindings']['original_raw_source']
        and parent['workbook']==read(value['before'])['workbook_id']and parent['renewal_module']==value['module'],
        'renewal projection detached from selected owning authority')
    Q.life.checked_record(value['module']);spec=importlib.util.spec_from_file_location('actual_adoption_lease_reader',value['module']['path'])
    L=importlib.util.module_from_spec(spec);spec.loader.exec_module(L)
    require(L.SCHEMA=='fleet-validated-continuation-lease/v1'and L.DURATION_NS==7200*10**9,'only original bounded same-owner renewal is supported')
    complete=read(value['completion']);previous=W.Lease(**complete['previous']);path=worker.context.tracker_root/(parent['workbook']+'.json')
    actual=L.validate_completed(life=Q.life,read=read,record=record,path=path,previous=previous,completion=value['completion'],before_binding=value['before'],after_binding=value['after'],authority_record=value['authority'])
    require(actual==lease and complete['tracker']==dict(path=str(path),sha256=value['after']['sha256']),
        'adoption lease differs from actual completed renewal')
    if live:require(record(path)==complete['tracker'],'live custody drifted after completed renewal')
    return value


def authorize(worker,selection,lease,*,round_plan,round_result,directory,renewal=None):
    from scripts.batch import harbor_instruction_worker as W
    from scripts import harbor_instruction_campaign as I
    with ExitStack()as stack:
        lock=stack.enter_context((worker.run/'worker.lock').open('a+b'))
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(worker.disclosure_repair is None and worker.disclosure_revision is None,'a disclosure adoption already exists')
        marker=worker.run/'disclosure-round-adoption.json'
        require(not marker.exists(),'adoption already reserved; do not repeat')
        _,before,tracker=W.active(worker,selection,lease)
        renewed=None
        if renewal is not None:
            renewed=validate_renewal(renewal,worker,lease,round_plan,round_result,live=True)
            stack.enter_context(historical_round(renewed))
        p,calls,evidence=history.completed_run(round_plan,round_result,policy=selection.content.model_policy,root=worker.root,campaign=worker.campaign)
        run=read(round_plan);Q=sys.modules['scripts.batch.harbor_disclosure_review_round']
        old,s,_=Q.completed_build(run['build'],worker=worker,selection=selection,campaign=worker.campaign)
        require(p['original_source']==before['bindings']['original_raw_source']and p['workbook']==before['workbook_id'],
                'completed review belongs to another workbook source')
        require(not any(W.step_directory(worker,name).exists()for name in W.STEPS[2:]),'later instruction effects already exist')
        require(not (W.step_directory(worker,'disclosure-review')/'complete.json').exists(),'disclosure review already adopted')
        checked=review.validate_plan(read(round_result)['review_plan'],policy=selection.content.model_policy,root=worker.root,current_inputs=True)
        archives,instruction=archive_records(checked[0],calls,checked,worker,selection,tracker,renewal_before=renewed['before']if renewed is not None else None)
        build=read(run['build']['result']);predecessor=build['checkpoint']
        require(read(predecessor)['step']=='disclosure-build'and read(predecessor)['status']=='completed','actual completed build predecessor required')
        directory=E.safe(directory);require(not directory.exists(),'fresh adoption receipt namespace required')
        require(W.active(worker,selection,lease)[1]==before,'custody changed during complete review verification')
        prior={name:getattr(worker,name)for name in CONTINUATIONS if getattr(worker,name)is not None}
        authority=read(checked[0]['review_round'])if 'review_round'in checked[0]else {}
        derivative=authority.get('source_derivative')
        if derivative is not None:
            require(run.get('source_derivative')==derivative,'owning runner lost reviewed derivative')
        receipt=dict(schema_version=SCHEMA,implementation=record(__file__),history_reader=record(history.__file__),round_plan=round_plan,round_result=round_result,
            review_plan=read(round_result)['review_plan'],aggregate=read(round_result)['aggregate'],build=run['build'],worker_run=str(worker.run),
            initial=record(worker.run/'initial.json'),source=before['bindings']['original_raw_source'],selection=codec.encode(selection),lease=asdict(lease),
            tracker=tracker,current_code=W.code(worker),prior_continuations=prior,blocked=predecessor,historical_records=archives,historical_instruction=instruction,
            renewal=renewal,semantic_budget_reset=False,models_dispatched=0,
            **({'source_derivative':derivative}if derivative is not None else {}))
        binding=W.keep(directory/'permit.json',receipt);W.keep(marker,binding);return binding


def validate(worker,before):
    from scripts.batch import harbor_instruction_worker as W
    receipt=read(worker.disclosure_repair)
    require(set(receipt)-{'source_derivative'}==KEYS and receipt['schema_version']==SCHEMA and receipt['implementation']==record(__file__)
            and receipt['history_reader']==record(history.__file__),'closed selected aggregate adoption required')
    require(read(record(worker.run/'disclosure-round-adoption.json'))==worker.disclosure_repair
            and receipt['worker_run']==str(worker.run)and receipt['initial']==record(worker.run/'initial.json')
            and receipt['source']==before['bindings']['original_raw_source'],'foreign aggregate adoption')
    require(receipt['semantic_budget_reset']is False and receipt['models_dispatched']==0 and receipt['current_code']==W.code(worker),
            'aggregate adoption changed code or model authority')
    prior={name:getattr(worker,name)for name in CONTINUATIONS if name!='disclosure_repair'and getattr(worker,name)is not None}
    require(prior==receipt['prior_continuations'],'prior owning continuations differ')
    if receipt['renewal']is not None:
        validate_renewal(receipt['renewal'],worker,W.Lease(**receipt['lease']),receipt['round_plan'],receipt['round_result'],live=False)
    policy=codec.decode(receipt['selection']).content.model_policy
    with historical_round(receipt):
        p,calls,evidence=history.completed_run(receipt['round_plan'],receipt['round_result'],policy=policy,root=worker.root,campaign=worker.campaign)
        checked=review.validate_plan(receipt['review_plan'],policy=policy,root=worker.root,current_inputs=True)
        expected=[]
        for call in calls:
            snapshots=read(call.submitted)['budget_evidence']
            require(len(snapshots)==len(checked[2])+1,'completed Call lost original evidence snapshots')
            for original,archive in zip(checked[2],snapshots[1:]):
                require(original['sha256']==archive['sha256'],'completed Call snapshot hash differs')
                E.checked_bytes(archive)
                if not any(item['original']==original for item in expected):expected.append(dict(original=original,archive=archive))
        authority=read(p['review_round'])if 'review_round'in p else {}
        require(receipt.get('source_derivative')==authority.get('source_derivative'),
                'adoption detached from reviewed derivative')
        expected.append(dict(original=receipt['historical_instruction'],archive=instruction_archive(p,receipt['historical_instruction'])))
        require(receipt['historical_records']==expected,'historical reader is not the exact actual Call archive projection')
    require(read(receipt['round_result'])['aggregate']==receipt['aggregate']and read(receipt['round_result'])['review_plan']==receipt['review_plan'],
            'selected completed aggregate changed')
    return receipt


def operation(worker,selection,lease,directory):
    from scripts.batch import harbor_instruction_worker as W
    _,before,_=W.active(worker,selection,lease);receipt=validate(worker,before);p=read(receipt['review_plan'])
    require(codec.encode(selection)==receipt['selection']and asdict(lease)==receipt['lease'],'adoption changed the selected build or lease')
    with historical_round(receipt):
        proof=review.validate_aggregate(receipt['aggregate'],p['manifest'],instruction=p['instruction'],disclosure=p['disclosure'],golden=p['golden'],delivered=p['delivered'],policy=selection.content.model_policy,root=worker.root)
    if 'source_derivative'in receipt:
        selection=adopted_derivative(worker,selection,receipt,p,directory)
    W.keep(directory/'aggregate-consumption.json',dict(adoption=worker.disclosure_repair,aggregate=receipt['aggregate'],proof=proof,models_dispatched=0,ordinary_call=None))
    semantic=W.SemanticSelection(None,p['manifest'],p['instruction'],None,None,(),disclosure_partition_aggregate=receipt['aggregate'])
    return replace(selection,semantic=semantic),lease,None


def adopted_derivative(worker,selection,receipt,plan,directory):
    """Copy only already-reviewed candidate bytes into a fresh owning stage."""
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_disclosure_review_round as Q
    authority=read(plan['review_round'])if 'review_round'in plan else {}
    _,proof,candidate=Q.reviewed_content(receipt['source_derivative'],
        {k:v for k,v in plan.items()if k!='review_round'},worker,selection,
        authority['build'],authority['prior_rejections'])
    target=directory/'source-derivative-stage'
    require(not target.exists(),'derivative stage already exists; reconcile prior owning step')
    shutil.copytree(candidate,target)
    require(W.static.tree(target)==proof['candidate_tree'],'reviewed derivative copy differs')
    W.keep(directory/'source-derivative.json',dict(authority=receipt['source_derivative'],
        aggregate=receipt['aggregate'],stage=str(target),tree=proof['candidate_tree'],models_dispatched=0))
    return W.refreshed(replace(selection,content=replace(selection.content,stage=target)))


def function_sha(function):
    import ast,hashlib,inspect,textwrap
    return hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(function))).body[0],include_attributes=False).encode()).hexdigest()


@contextmanager
def installed(profile,*,receipt=None):
    """Reinstall only declared consuming functions in warm and cold owners."""
    from scripts import harbor_instruction_campaign as I
    from scripts.batch import harbor_instruction_worker as W,harbor_disclosure_review_resume as D
    import scripts.batch as package
    require(set(profile)=={'implementation','instruction','worker','resume','functions'}
            and profile['implementation']==record(__file__)and profile['instruction']==record(I.__file__)
            and profile['worker']==record(W.__file__)and profile['resume']==record(D.__file__),
            'closed original owning module witness required')
    require(profile['functions']==dict(operation=function_sha(W._operation),disclosure_history=function_sha(I._disclosure_history),aggregate=function_sha(review.validate_aggregate)),
            'original consuming function changed')
    old_operation=W._operation;old_history=I._disclosure_history;old_aggregate=review.validate_aggregate
    def owning_operation(worker,selection,lease,phase,directory,**options):
        if phase=='disclosure-review'and selected(worker):return operation(worker,selection,lease,directory)
        return old_operation(worker,selection,lease,phase,directory,**options)
    def disclosure_history(binding,others):
        if binding is None or read(binding).get('schema_version')!=SCHEMA:return old_history(binding,others)
        saved=read(binding);require(set(saved)-{'source_derivative'}==KEYS and saved['implementation']==record(__file__),'closed cumulative disclosure receipt required')
        I._continuations(None,None,None,binding)
        for item in others:
            if item is None:continue
            if item in saved['prior_continuations'].values():continue
            value=read(item);permit=read(value['permit'])if 'permit'in value else value
            require(permit.get('prior_continuations',{}).get('disclosure_repair')==binding,
                    'disclosure aggregate cannot combine detached owning histories')
    def consuming_aggregate(binding,*args,**kwargs):
        if receipt is None:return old_aggregate(binding,*args,**kwargs)
        value=read(receipt)
        if binding!=value['aggregate']:return old_aggregate(binding,*args,**kwargs)
        require(set(value)-{'source_derivative'}==KEYS and value['implementation']==record(__file__),'closed historical aggregate receipt required')
        with historical_round(value):return old_aggregate(binding,*args,**kwargs)
    proxy=ModuleType(D.__name__);proxy.__dict__.update(vars(D));proxy.SCHEMA=SCHEMA
    def validate_selected(worker,before):
        return validate(worker,before)if selected(worker)else D.validate(worker,before)
    proxy.validate=validate_selected
    old_module=sys.modules[D.__name__];old_attribute=package.harbor_disclosure_review_resume
    try:
        W._operation=owning_operation;I._disclosure_history=disclosure_history;review.validate_aggregate=consuming_aggregate
        sys.modules[D.__name__]=proxy;package.harbor_disclosure_review_resume=proxy
        yield
    finally:
        W._operation=old_operation;I._disclosure_history=old_history;review.validate_aggregate=old_aggregate
        sys.modules[D.__name__]=old_module;package.harbor_disclosure_review_resume=old_attribute
