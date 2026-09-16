"""Validate a sealed prefix of real disclosure calls without replay authority."""
from dataclasses import asdict
from pathlib import Path
import math
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_disclosure_partition_review as R


def sealed_batches(plan, directory, *, policy, root, campaign, validated=None, start_index=0):
    """An incomplete next directory remains a reconciliation barrier."""
    checked=validated or R.validate_plan(plan,policy=policy,root=root,current_inputs=True)
    p,projection,_=checked
    require(p==read(plan),'sealed calls use a changed review plan')
    parent=Path(directory);require(parent.is_absolute(),'absolute round journal required')
    batches=parent/'batches';names=sorted(x.name for x in batches.iterdir())if batches.exists()else[]
    require(type(start_index)is int and 0<=start_index<len(projection['batches'])
            and names==['%04d'%i for i in range(start_index,start_index+len(names))]
            and start_index+len(names)<=len(projection['batches']),
            'round batch prefix is noncontiguous or exceeds its reservation')
    calls=[];evidence=[]
    for index,name in enumerate(names,start_index):
        home=batches/name
        require(home.is_dir()and not any(x.is_symlink()for x in(home,*home.parents)),
                'canonical sealed batch directory required')
        expected={'request.json','admission.json','transport-complete.json','complete.json'}
        require({x.name for x in home.iterdir()}==expected and all(x.is_file()and not x.is_symlink()for x in home.iterdir()),
                'unsealed or foreign batch requires reconciliation, never replay')
        bindings={key:record(home/key)for key in expected};v={key:read(row)for key,row in bindings.items()}
        complete=v['complete.json'];transport=v['transport-complete.json'];request=v['request.json'];admitted=v['admission.json']
        require(set(complete)=={'index','plan','call','cached_input_recheck_seconds'}and complete['index']==index and complete['plan']==plan,
                'batch completion differs from its selected plan')
        elapsed=complete['cached_input_recheck_seconds']
        require(type(elapsed)in(int,float)and math.isfinite(elapsed)and elapsed>=0,'invalid retained input recheck duration')
        require(set(transport)=={'index','plan','outcome','call','semantic_approval','automatic_retry'}
                and transport['index']==index and transport['plan']==plan and transport['call']==complete['call']
                and transport['semantic_approval']is False and transport['automatic_retry']is False,
                'transport receipt differs or manufactures semantic approval')
        require(set(admitted)=={'tracker','observed_at_ns','required_remaining_seconds','physical_policy','transport_started'}
                and admitted['transport_started']is False and admitted['required_remaining_seconds']==660,
                'original pre-HTTP admission receipt differs')
        require(set(request)=={'prepared','context'},'closed retained prepared request required')
        raw=request['prepared'];args={**raw,'policy':R.E.Policy(**raw['policy']),
            'scope':R.E.SemanticScope(**raw['scope']),'budget_evidence':tuple(raw['budget_evidence'])}
        prepared=R.E.PreparedCall(**args)
        require(asdict(prepared.scope)==asdict(R.scope(p,index))and prepared.ordinal==prepared.limit==1,
                'sealed request changes its distinct shard allowance')
        context=read(request['context'])
        require(context==dict(schema_version=R.REQUEST,plan=plan,index=index,
                batch_id=projection['batches'][index]['batch_id'],request=prepared.request,
                budget_evidence=list(prepared.budget_evidence),scope=asdict(prepared.scope),ordinal=1,limit=1)
                and prepared.budget_evidence[0]['sha256']==admitted['tracker']['sha256'],
                'sealed request context or pre-HTTP tracker differs')
        observed=R.E.inspect(prepared,campaign)
        require(observed.status=='completed'and observed.call is not None and observed.outcome==transport['outcome']
                and asdict(observed.call)==complete['call'],'sealed transport does not match its actual executor journal')
        R.validate_batch(observed.call,plan,index,policy,root,_validated=checked)
        calls.append(observed.call);evidence.extend(bindings.values());evidence.append(request['context'])
    return calls,len(projection['batches']),evidence


def batch_completion(selected,plan,index):
    """Locate one exact sealed Call without copying it into another journal."""
    home=Path(selected['output']);expected=None
    if 'completed_prefix'in selected:
        proof=read(selected['completed_prefix']);n=proof['completed_count']
        require(proof['schema_version']=='fleet-disclosure-completed-batch-prefix/v1'
            and type(n)is int and 0<n<selected['max_calls'],'closed retained batch locator required')
        kept=read(record(home/'retained-calls.json'));old=read(proof['plan'])
        require(kept['proof']==selected['completed_prefix']and kept['plan']==plan
            and kept['calls_replayed']==0 and len(kept['calls'])==n
            and old['workbook']==selected['workbook']and old['source']==selected['source'],
            'retained batch locator changed source or calls')
        if index<n:home=Path(old['output']);expected=kept['calls'][index]
    done=read(record(home/'batches'/('%04d'%index)/'complete.json'))
    require(done['index']==index and done['plan']==plan and (expected is None or done['call']==expected),
        'sealed batch locator changed actual Call')
    return done


def retained_prefix(selected,plan,*,policy,root,campaign,validated):
    """Read original completed Calls across a recorded interruption boundary."""
    proof=read(selected['completed_prefix']);old=read(proof['plan'])
    require(set(proof)=={'schema_version','plan','worker_outcome','process','tracker','completed_count'}
        and proof['schema_version']=='fleet-disclosure-completed-batch-prefix/v1',
        'closed completed prefix history required')
    changes={'helper','owner','fence','output','completed_prefix','progress_reader','reserved_round','round_reader'}
    require('reserved_round'in old and 'completed_prefix'not in old and 'resume'not in old
        and {k:v for k,v in selected.items()if k not in changes}=={k:v for k,v in old.items()if k not in changes}
        and selected['fence']==old['fence']+1 and selected['owner']!=old['owner']
        and selected['output']!=old['output'],'completed prefix changed original authority')
    for bound in (proof['plan'],old['helper'],selected['progress_reader']):
        require(record(bound['path'])==bound,'completed prefix implementation changed')
    before=Path(old['output']);after=Path(selected['output'])
    require({p.name for p in before.iterdir()}=={'intent.json','continued.json','reserved.json','validation-metrics.json','batches'}
        and read(record(before/'intent.json'))==dict(plan=proof['plan'],automatic_retry=False)
        and read(record(before/'continued.json'))==old['reserved_round']
        and read(record(before/'reserved.json'))==read(record(after/'reserved.json'))==dict(plan=plan,automatic_retry=False)
        and read(record(after/'continued.json'))==selected['completed_prefix'],
        'completed prefix journals changed')
    outer=read(proof['worker_outcome']);intent=read(outer['intent']);job=read(intent['job']);process=read(proof['process'])
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['result']is None and outer['automatic_retry']is False and outer['publication_performed']is False
        and intent['owner']==old['owner']and job['driver']==old['helper']and job['source']==old['source']
        and job['workbook']==old['workbook']and job['result']==str(before/'result.json')
        and job['run']==str(Path(proof['worker_outcome']['path']).parent)
        and job['inputs'][0]==proof['plan']and process['job']==intent['job']and process['automatic_retry']is False,
        'original completed prefix stopped custody differs')
    calls,total,evidence=sealed_batches(plan,before,policy=policy,root=root,campaign=campaign,validated=validated)
    n=proof['completed_count'];require(type(n)is int and 0<n<total and len(calls)==n,
        'completed retained prefix count differs')
    kept=record(after/'retained-calls.json')
    require(read(kept)==dict(proof=selected['completed_prefix'],plan=plan,calls=[asdict(call)for call in calls],calls_replayed=0),
        'retained Calls differ from original sealed transports')
    return calls,total,[selected['completed_prefix'],proof['plan'],proof['worker_outcome'],proof['process'],kept,*evidence]


def completed_run(run_plan, result_binding, *, policy, root, campaign):
    """Recompute the complete aggregate; the runner's status is not a verdict."""
    from scripts.batch import harbor_disclosure_review_round as Q
    selected=read(run_plan);result=read(result_binding)
    require(selected['schema_version']=='fleet-disclosure-review-continuation/v1'
            and result_binding==record(Path(selected['output'])/'result.json'),
            'review result detached from selected owning namespace')
    require(set(result)=={'status','workbook','plan','review_plan','aggregate','actual_review_calls','publication_performed','automatic_retry'}
            and result['status']=='DISCLOSURE_REVIEW_COMPLETE_NOT_ADOPTED'and result['plan']==run_plan
            and result['workbook']==selected['workbook']and result['publication_performed']is False
            and result['automatic_retry']is False,'closed unadopted round completion required')
    for item in [selected['helper'],*selected['modules'].values()]:
        require(record(item['path'])==item,'selected review implementation changed')
    require(record(R.__file__)==selected['modules']['review']and record(Q.__file__)==selected['modules']['rounds'],
            'completed round requires its exact selected review readers')
    checked=R.validate_plan(result['review_plan'],policy=policy,root=root,current_inputs=True)
    p,projection,_=checked
    require({k:v for k,v in p.items()if k!='review_round'}==read(selected['inputs'])
            and (Q.read(p['review_round'])['round'],Q.read(p['review_round'])['limit'])==(selected['round'],selected['limit']),
            'completed round changed inputs or cumulative consumption')
    prefix=[];prefix_evidence=[]
    if 'completed_prefix'in selected:
        prefix,_,prefix_evidence=retained_prefix(selected,result['review_plan'],policy=policy,root=root,campaign=campaign,validated=checked)
    calls,total,evidence=sealed_batches(result['review_plan'],Path(selected['output']),policy=policy,
                                      root=root,campaign=campaign,validated=checked,start_index=len(prefix))
    calls=prefix+calls;evidence=prefix_evidence+evidence
    require(len(calls)==total==result['actual_review_calls']==selected['max_calls']and total>0,
            'completed round lacks every actual sealed review')
    require(result['aggregate']==record(Path(selected['output'])/'aggregate.json'),
            'completed aggregate moved from owning round')
    require(read(result['aggregate'])==R.aggregate(result['review_plan'],calls,policy,root,_validated=checked),
            'actual aggregate differs from complete independent call coverage')
    return p,calls,[run_plan,result_binding,result['aggregate'],*evidence]
