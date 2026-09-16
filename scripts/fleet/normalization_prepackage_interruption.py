"""Resume an interrupted phase-two tail before any packaging effects."""
from pathlib import Path
import ast

SCHEMA='fleet-normalization-prepackage-interruption/v1'


def validate(N,q,p):
    binding,read,require,module=N.binding,N.read,N.require,N.module
    proof=read(q['interruption'])
    require(set(proof)=={'schema_version','previous','release','evidence'} and proof['schema_version']==SCHEMA,
            'closed interrupted pre-package proof required')
    old=read(proof['previous']);changed={'helper','home','fence','interruption','interruption_helper','pair_cache','docker_profile','docker_helper','environment_helper'}
    require(old['schema_version']=='fleet-normalization-imported-continuation/v2'
        and set(q)==set(old)|{'interruption','interruption_helper','pair_cache','docker_profile','docker_helper'}
        and {k:v for k,v in q.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed}
        and q['fence']==old['fence']+1 and Path(q['home']).parent==Path(old['home']).parent
        and q['home']!=old['home'],'interrupted tail changed original source or authority')
    if q.get('environment_helper')!=old.get('environment_helper'):
        historical_clock_delta(N,old['environment_helper'],q['environment_helper'])
    original=module('prepackage_original_imported_owner',old['helper'])
    result=original.identity(old,p)
    release=read(proof['release']);e=read(proof['evidence'])
    require(release['status']=='STOPPED_AUTHOR_RECONCILED' and release['unknown_operations_released']is False
        and read(read(release['plan'])['arguments']['evidence'])==e
        and e['schema_version']=='fleet-stopped-author-no-operation-evidence/v2',
        'original author-only reconciliation required')
    args=read(release['plan'])['arguments'];w=read(e['workbook'])
    require(args['workbook']==old['workbook'] and args['owner']==old['owner'] and args['fence']==old['fence']
        and args['quarantines']=={} and w['state']=='hold','interrupted original claim differs')
    receipt=read(release['receipt'])
    require(receipt['after_reservation']['state']=='released'
        and receipt['after_reservation']['claim_id']==args['claim_id'],'author reservation not actually released')
    copies={r['native']['path']:r for r in e['copies']}
    require(len(copies)==len(e['copies']),'duplicate interrupted boundary evidence')
    for item in copies.values():
        require(binding(item['copy']['path'])==item['copy'] and item['copy']['sha256']==item['native']['sha256']
            and binding(item['native']['path'])==item['native'],'interrupted original files changed')
    def at(path):
        row=copies.get(str(path));require(row is not None,'missing interrupted original boundary');return read(row['native'])
    worker=Path(read(e['launch'])['remote']['job']['path']);job=at(worker);home=Path(old['home']);ops=Path(str(home)+'-authoring')
    require(job['driver']==old['helper'] and job['inputs'][0]==proof['previous'] and job['workbook']==old['workbook']
        and job['source']==old['source'] and job['result']==str(home/'result.json'),'different interrupted task job')
    worker_home=Path(job['run'])
    wi=at(worker_home/'intent.json');claim=at(worker_home/'claim.json');outer=at(worker_home/'outcome.json')
    require(wi['job']==binding(worker) and wi['owner']==claim['owner']==old['owner']
        and wi['claim_id']==claim['claim_id']==args['claim_id'] and claim['fence']==old['fence']
        and claim['version']==job['version'] and claim['source_sha256']==old['source']['sha256']
        and claim['workbook']==old['workbook'] and outer['intent']==binding(worker_home/'intent.json')
        and outer['status']=='RECONCILIATION_REQUIRED' and outer['automatic_retry']is False
        and outer['returncode']is None and outer['result']is None and outer['publication_performed']is False
        and outer['failure_type']in('RuntimeError','TimeoutError','URLError'),
        'interrupted original worker claim or outcome differs')
    intent=at(home/'intent.json')
    require(intent==dict(plan=proof['previous'],imported=old['checkpoint'],takeover=old['takeover_completion'],
                        emission_repeated=False,refresh_repeated=False,automatic_retry=False),'interrupted dispatcher intent changed')
    retained=read(read(old['remaining'])['completed']);last=read(retained['result'])
    child=at(ops/'000/intent.json')
    require(child['action']=='step' and child['arguments']==dict(descriptor=last['descriptor'],checkpoint=retained['result'],captures=None)
        and child['dispatch']is True,'interrupted child detached from completed phase two')
    expected={home/'intent.json',home/'authoring-job.json',ops/'job.json',*[ops/'000'/n for n in ('intent.json','declared-runtime-adapter.json','dependency.json','step.log')]}
    actual={f for root in (home,ops)for f in root.rglob('*')if f.is_file()}
    require(actual==expected and all(str(f)in copies for f in expected)
        and (ops/'000/step.log').read_bytes()==b'','interrupted dispatcher has later or partial effects')
    return result


def historical_clock_delta(N,original,selected):
    for row in (original,selected):
        N.require(N.binding(row['path'])==row,'historical environment reader changed')
    text=Path(original['path']).read_text()
    old_import='    from types import FunctionType\n'
    old_call="    selected=FunctionType(f.__code__,{**f.__globals__,'checked':historical},f.__name__,f.__defaults__)(p)"
    N.require(text.count(old_import)==text.count(old_call)==1,'original historical direct clock shape differs')
    expected=text.replace(old_import,'    from types import FunctionType,SimpleNamespace\n').replace(old_call,
        "    observed=stop['observed_at']\n"
        "    require(type(observed)is int and observed>0,'completed takeover observation time required')\n"
        "    clock=SimpleNamespace(time_ns=lambda:observed)\n"
        "    selected=FunctionType(f.__code__,{**f.__globals__,'checked':historical,'time':clock},f.__name__,f.__defaults__)(p)")
    N.require(ast.dump(ast.parse(expected))==ast.dump(ast.parse(Path(selected['path']).read_text())),
              'historical takeover reader changed outside exact observation clock')
