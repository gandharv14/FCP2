"""Publication-only reader for completed, retained normalization continuations."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import sys
import time

TAIL_SHA = '5d0074c174af53bbb8f83fd396c7ae94f547b5e90131541b8e6547e52270910a'


def history_binding(plan, *, checked, binding, require):
    require(not any(k in plan for k in ('history','continuation_history','source_continuation','source_authoring')),
            'mixed retained publication histories')
    selected=plan['normalization_authoring']
    require(set(selected)=={'plan','driver'},'closed completed normalization reader selection required')
    require(selected['driver']['sha256']==TAIL_SHA and binding(selected['driver']['path'])==selected['driver'],
            'unreviewed completed normalization reader')
    p=checked(selected['plan'])
    require(p['schema_version']=='fleet-undispatched-normalization-authoring/v1'
            and p['workbook']==plan['workbook'] and p['workbook'] in {'0173','0303','0077'}
            and p['source_sha256']==plan['source_sha256'] and p['version']==Path(plan['code_root']).name,
            'publication source/version/normalization identity differs')
    ready=checked(plan['ready'])
    require(set(ready)=={'status','workbook','plan','job','config','outcome','checkpoint','runtime_adapters',
                         'publication_performed','automatic_retry','semantic_budget_reset'}
            and ready['status']=='PUBLICATION_READY' and ready['workbook']==plan['workbook']
            and ready['plan']==selected['plan'] and ready['publication_performed']is False
            and ready['automatic_retry']is False and ready['semantic_budget_reset']is False,
            'actual completed owning authoring boundary required')
    require(plan['ready']['path']==str(Path(p['home'])/'result.json'),'noncanonical authoring completion')
    require(isinstance(ready['runtime_adapters'],list) and ready['runtime_adapters'],
            'declared owning child evidence missing')
    for row in ready['runtime_adapters']:
        adapter=checked(row)
        require(adapter['schema_version']=='fleet-declared-owning-child-adapter/v1'
                and adapter['reader_plan']==selected['plan'] and adapter['child_wrapper']==selected['driver']
                and adapter['original_snapshot_unchanged']is True and adapter['semantic_budget_reset']is False
                and adapter['automatic_retry']is False,'completed child reader lineage differs')
        for key in ('adapter','producer_code','base_code','child_wrapper','intent'):
            require(binding(adapter[key]['path'])==adapter[key], 'retained child dependency changed')
        for item in adapter['reader_evidence']:
            require(binding(item['path'])==item,'retained child evidence changed')
    job=checked(ready['job'])
    require(job['config']==ready['config'],'completed owning config differs')
    checked(ready['outcome']);checked(ready['checkpoint'])
    return selected['plan']


def load(name,row,*,binding,require):
    require(binding(row['path'])==row,'publication implementation changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m


def context(plan_binding,plan,*,checked,binding,require,status_validator):
    history_binding(plan,checked=checked,binding=binding,require=require)
    require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY')=='1'
            and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL')
            and not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),
            'live publication-only custody required')
    auth=dict(workbook=os.environ['HARBOR_FLEET_WORKBOOK'],owner=os.environ['HARBOR_FLEET_OWNER'],
              token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']))
    require(auth['workbook']==plan['workbook'],'publication workbook differs')
    from scripts.fleet.coordinator import Client
    client=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE'])
    status_validator(client.call('status'),auth,plan_binding,plan,plan['source_sha256'],now=time.time(),require=require)
    client.call('heartbeat',**auth,lease_seconds=180)
    selected=plan['normalization_authoring']
    tail=load('publication_completed_normalization',selected['driver'],binding=binding,require=require)
    # This owning reader revalidates full original snapshot, source/history,
    # original and recovered Calls, partition aggregate and immutable sidecars.
    # It does not invoke the tail's authoring run(), active() or lease operation.
    result=tail.load(selected['plan']);p,D,n,helper,env,worker,P,recovery,A,dispatcher,phase,emitted=result
    require(Path(env[6])==Path(plan['code_root']) and p['source_sha256']==plan['source_sha256'],
            'actual owning reader root/source differs')
    def forbidden(*args,**kwargs):raise RuntimeError('publication permits no authoring or model dispatch')
    # Install after the reader has inspected the exact original execute source;
    # there are no execute calls while constructing or validating that reader.
    import xl_transport
    from scripts.batch import harbor_model_executor as E
    for module,names in ((xl_transport,('model_slot','read_response','_read_response')),
                         (E,('execute','_open')),(P,('dispatch',)),(P.E,('execute','_open')),
                         (env[0],('run','_admit')),(env[1],('run',)),
                         (env[0].worker.bootstrap,('prepare','admit'))):
        for name in names:
            if hasattr(module,name):setattr(module,name,forbidden)
    recovery.execute=recovery._execute=forbidden
    return result


def evidence(plan):
    selected=plan['normalization_authoring']
    return [selected['plan'],selected['driver'],plan['ready']]


def publication_run(producer,request,journal,plan_binding,plan,*,child_adapter,wrapper):
    selected=child_adapter.select(producer,reader_plan=plan_binding,child_wrapper=wrapper,
                                  reader_evidence=evidence(plan))
    return selected(request,journal)


def publication_child(publisher,child_adapter,status_validator):
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['publication-child'])
    for key in ('plan','plan-sha256','intent','intent-sha256','adapter'):parser.add_argument('--'+key,required=True)
    a=parser.parse_args();bound=dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256)
    plan=publisher.checked(bound);sys.path.insert(0,plan['code_root'])
    result=context(bound,plan,checked=publisher.checked,binding=publisher.binding,
                   require=publisher.require,status_validator=status_validator)
    producer=result[4][2]
    intent=dict(path=a.intent,sha256=a.intent_sha256)
    child_adapter.validate(producer,reader_plan=bound,child_wrapper=publisher.binding(publisher.__file__),
                           reader_evidence=evidence(plan),intent=intent,sidecar=a.adapter)
    producer.execute(intent)


def patch(source):
    def replace(old,new,count=1):
        nonlocal source
        if source.count(old)!=count:raise ValueError('publication adapter anchor differs')
        source=source.replace(old,new)
    replace('    """Publication reuses the exact central survey selected for fresh authoring."""',
            '    """Publication reuses the exact central survey selected for fresh authoring."""\n'
            '    if "normalization_authoring" in plan:\n'
            '        return normalization_publication.history_binding(plan,checked=checked,binding=binding,require=require)')
    old="prefix = 'HARBOR_FLEET_CONTINUATION_HISTORY_' if 'continuation_history' in plan else 'HARBOR_FLEET_HISTORY_'"
    replace(old,"prefix = ('HARBOR_FLEET_UNDISPATCHED_HISTORY_' if 'normalization_authoring' in plan else 'HARBOR_FLEET_CONTINUATION_HISTORY_' if 'continuation_history' in plan else 'HARBOR_FLEET_HISTORY_')",2)
    old="""    if 'continuation_history' in plan:
        from scripts.fleet.continuation_history import projected_survey
    else:
        from scripts.fleet.history import projected_survey
    certificate = checked(history)
    projected_survey(tuple(map(Path, certificate['roots'])), set(certificate['known_ids']))
"""
    replace(old,"""    if 'normalization_authoring' in plan:
        normalization_publication.context(plan_binding,plan,checked=checked,binding=binding,require=require,status_validator=publication_status.validate_status)
    else:
"""+''.join('    '+line+'\n'for line in old.rstrip('\n').splitlines()))
    old='    publication = env[2].run(request, Path(plan["journal"]) / "owning-publication")'
    replace(old,"""    if 'normalization_authoring' in plan:
        publication = normalization_publication.publication_run(env[2],request,Path(plan['journal'])/'owning-publication',plan_binding,plan,child_adapter=publication_child_adapter,wrapper=binding(__file__))
    else:
"""+'    '+old)
    replace('if __name__ == "__main__": main()',
            'if __name__ == "__main__":\n'
            '    if len(sys.argv)>1 and sys.argv[1]=="publication-child":\n'
            '        normalization_publication.publication_child(sys.modules[__name__],publication_child_adapter,publication_status.validate_status)\n'
            '    else: main()')
    return source
