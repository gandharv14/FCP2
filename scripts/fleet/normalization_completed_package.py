"""Retain an actual completed package before the next original environment step."""
from contextlib import contextmanager
from pathlib import Path

SCHEMA='fleet-normalization-completed-package/v1'
FIELDS={'schema_version','previous','outer','completed','failed','stopped','package_tree','packet','tracker'}


def identity(N,q,p):
    read,binding,require=N.read,N.binding,N.require
    proof=read(q['completed_package']);require(set(proof)==FIELDS and proof['schema_version']==SCHEMA,'closed completed package custody required')
    old=read(proof['previous']);changes={'helper','home','fence','owner','takeover_profile','takeover_completion','docker_profile','completed_package','completed_package_helper'}
    require('interruption'in old and 'completed_package'not in old
        and set(q)==set(old)|{'completed_package','completed_package_helper'}
        and q['helper']==binding(N.__file__) and q['completed_package_helper']==binding(__file__)
        and {k:v for k,v in q.items()if k not in changes}=={k:v for k,v in old.items()if k not in changes}
        and q['fence']==old['fence']+1 and q['owner']!=old['owner']
        and q['home']!=old['home'] and Path(q['home']).parent==Path(old['home']).parent,
        'completed package changed original continuation authority')
    require(read(q['docker_profile'])==dict(read(old['docker_profile']),entrypoint=q['helper']),
        'completed package changed Docker scope beyond selected entrypoint')
    original=N.module('completed_package_original_imported',old['helper']);prior=original.identity(old,p)
    outer=read(proof['outer']);intent=read(outer['intent']);job=read(intent['job']);claimrow=binding(Path(outer['intent']['path']).parent/'claim.json');claim=read(claimrow)
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['result']is None and outer['automatic_retry']is False and outer['publication_performed']is False
        and job['driver']==old['helper'] and job['inputs'][0]==proof['previous'] and job['source']==q['source']and job['workbook']==q['workbook']
        and job['command'][1:]==['-I','-B',old['helper']['path'],'run','--plan',proof['previous']['path'],'--plan-sha256',proof['previous']['sha256']]
        and intent['owner']==claim['owner']==old['owner'] and intent['claim_id']==claim['claim_id']
        and claim['fence']==old['fence'] and claim['version']==job['version'] and claim['workbook']==q['workbook']
        and claim['source_sha256']==q['source']['sha256'],'completed package lost original stopped worker/claim')
    ops=Path(old['home']+'-authoring');done=read(proof['completed']);value=read(done['result']);phase2=read(read(old['remaining'])['completed'])
    require(proof['completed']==binding(ops/'000/outcome.json') and done['status']=='COMPLETED_OPERATION'
        and done['result']==binding(ops/'000/result.json') and value['status']=='STEP_COMPLETE'and value['step']=='environment'
        and value['descriptor']==prior[1]['descriptor'] and value['publication_ready']is False,
        'actual original completed environment guard required')
    g=read(done['resource']);i=read(g['intent'])
    require(g['returncode']==0 and g['failure']is None and g['log']==binding(ops/'000/step.log')
        and i['action']=='step' and i['arguments']==dict(descriptor=value['descriptor'],checkpoint=phase2['result'],captures=None)
        and i['dispatch']is True,'completed package guard lost original phase-two predecessor')
    failed=read(proof['failed']);fg=read(failed['resource']);fi=read(fg['intent'])
    require(proof['failed']==binding(ops/'001/outcome.json')and failed['status']=='RECONCILIATION_REQUIRED'
        and fg['returncode']==1 and fg['failure']is None and fg['log']==binding(ops/'001/step.log')
        and fi['action']=='step'and fi['arguments']==dict(descriptor=value['descriptor'],checkpoint=done['result'],captures=None)
        and same_guard_context(i,fi)
        and Path(fg['log']['path']).read_text().rstrip().endswith('ValueError: interrupted packaging effects must be retained separately')
        and not any((ops/'001'/n).exists()for n in ('result.json','dependency.json','lease-renewal.json')),
        'different failed post-package child')
    require(set(x.name for x in Path(old['home']).iterdir())=={'intent.json','authoring-job.json'}
        and set(x.name for x in ops.iterdir())=={'000','001','job.json','hold.json'}
        and read(binding(ops/'hold.json'))['outcome']==proof['failed']
        and read(binding(ops/'job.json'))==read(binding(Path(old['home'])/'authoring-job.json')),
        'post-package dispatcher advanced beyond stopped child')
    stop=read(proof['stopped']);require(set(stop)=={'at','processes','files'}and stop['processes']==[],'completed package predecessor process remains')
    rows={r['native']['path']:r for r in stop['files']};require(len(rows)==len(stop['files']),'duplicate completed package custody')
    required=[proof['previous'],proof['outer'],outer['intent'],intent['job'],claimrow,proof['completed'],done['result'],done['resource'],g['intent'],g['log'],proof['failed'],failed['resource'],fg['intent'],fg['log'],proof['packet'],proof['tracker']]
    for row in required:require(row['path']in rows and rows[row['path']]['native']==row,'completed package stopped evidence incomplete')
    for row in rows.values():
        require(binding(row['copy']['path'])==row['copy']and row['native']['sha256']==row['copy']['sha256'],'completed package stopped copy changed')
        if row['native']!=proof['tracker']:require(binding(row['native']['path'])==row['native'],'completed package historical file changed')
    return prior


def same_guard_context(completed,failed):
    """The original environment guard has no separate base-code field."""
    return dict(failed,arguments=completed['arguments'])==completed


def prior_tracker_witness(N,q,T):
    """Retain the earlier takeover's exact before-image after a later T."""
    proof=N.read(q['completed_package']);old=N.read(proof['previous'])
    selected=N.read(q['takeover_profile']);prior=N.read(old['takeover_profile'])
    N.require(selected['selection']['prior_completion']==old['takeover_completion'],
        'completed package lost preceding takeover')
    before,after,lease=T.validate_completion(old['takeover_completion'])
    done=N.read(old['takeover_completion']);stop=N.read(done['stopped_owner_proof'])
    N.require(T._selection(stop['selection'])==T._selection(prior['selection'])
        and N.read(stop['before'])==before and done['after']['sha256']==lease.sha256
        and before['workbook_id']==after['workbook_id']==q['workbook']
        and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']==q['source'],
        'completed package preceding tracker archive changed')
    return (prior['selection']['tracker']['path'],stop['before']['sha256']),stop['before']


def packaging(N,q,w,C):
    proof=N.read(q['completed_package']);tree=N.read(proof['package_tree']);root=w.run/'packaging'
    N.require(set(tree)=={'root','files'}and tree['root']==str(root)and tree['files'],'closed original package tree required')
    actual=[N.binding(p)for p in sorted(root.rglob('*'))if p.is_file()]
    N.require(actual==tree['files'],'completed package files changed or were extended')
    value=N.read(N.read(proof['completed'])['result']);packet=N.read(proof['packet']);prepared=N.read(N.read(q['previous'])['prepared_refresh'])['prepared']
    N.require(proof['packet']==N.binding(Path(prepared['path']).parent/'continuation/environment.json')
        and packet['selection']==value['selection']and N.binding(root/'complete.json')in packet['evidence'],
        'completed package packet detached from original gate')
    lease=C.decode(value['lease']);N.require(lease.sha256==proof['tracker']['sha256'],'completed package tracker differs from actual gate lease')
    for row in packet['evidence']:N.require(N.binding(row['path'])==row,'completed package receipt changed')
    return value,lease


def takeover(N,q,T,lease):
    proof=N.read(q['completed_package']);old=N.read(proof['previous']);selected=N.read(q['takeover_profile']);done=N.read(q['takeover_completion']);stop=N.read(done['stopped_owner_proof']);before,after,current=T.validate_completion(q['takeover_completion']);completed=N.read(proof['completed']);value=N.read(completed['result'])
    original=N.read(old['takeover_profile'])
    expected=dict(original,allowance=selected['allowance'],selection=dict(original['selection'],allowance=selected['allowance'],tracker=proof['tracker'],checkpoint=completed['result'],guard=completed['resource'],prior_completion=old['takeover_completion'],owner=q['owner']))
    N.require(selected==expected and selected['selection']['checkpoint']==completed['result']and selected['selection']['prior_completion']==old['takeover_completion']
        and selected['selection']['tracker']==proof['tracker']and selected['selection']['owner']==q['owner']
        and stop['before']['sha256']==proof['tracker']['sha256']and done['after']['sha256']==current.sha256
        and before['workbook_id']==after['workbook_id']==q['workbook']
        and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']==q['source']
        and lease==current,'new takeover detached from completed package custody')
    return completed['result']


@contextmanager
def prior_scope(N,q,C,w,T):
    proof=N.read(q['completed_package']);completed=N.read(proof['completed']);target=completed['result'];original=C._prior
    def selected(checkpoint,descriptor):
        value,old=original(checkpoint,descriptor)
        if checkpoint!=target:return value,old
        before,after,current=T.validate_completion(q['takeover_completion']);takeover(N,q,T,current)
        N.require(old.sha256==proof['tracker']['sha256']and old.revision==before['revision']
            and (old.owner,old.token)==(before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'])
            and value['status']=='STEP_COMPLETE'and value['step']=='environment','package lease does not match original completed checkpoint')
        C.worker._active(w,current)
        return value,current
    C._prior=selected
    try:yield
    finally:C._prior=original
