"""Adopt an actual refresh preparation after an exact pre-CAS identity refusal."""
from pathlib import Path
from contextlib import contextmanager
from copy import deepcopy

SCHEMA='fleet-normalization-prepared-refresh/v1'


def validate(N,p,producer,selected,worker,lease,*,full=True,committed=None):
    q=N.read(p['prepared_refresh']);N.require(set(q)=={'schema_version','previous','prepared','refresh_guard','outer','claim','stopped'} and q['schema_version']==SCHEMA,'closed prepared refresh custody required')
    old=N.read(q['previous']);changed={'helper','single_helper','home','fence','runtime_profile','prepared_refresh','prepared_helper'}
    N.require('single_author'in old and 'completed_emission'in old and 'prepared_refresh'not in old
        and {k:v for k,v in old.items()if k not in changed}=={k:v for k,v in p.items()if k not in changed}
        and p['fence']==old['fence']+1 and Path(p['home']).parent==Path(old['home']).parent
        and p['home']!=old['home'],'prepared refresh changed original authority')
    prior=N.read(old['runtime_profile']);current=N.read(p['runtime_profile']);expected=deepcopy(prior)
    N.require(len(prior['symbols'])==2 and not prior['dependencies'] and len(current['symbols'])==1
        and current['symbols']==prior['symbols'][:1] and len(current['dependencies'])==1,
        'prepared refresh must coherently select the existing module')
    selected_code={k:v for k,v in prior['symbols'][1].items()if k!='names'}
    N.require(prior['symbols'][1]['names']==['unchanged','prepare'] and current['dependencies']==[selected_code],
        'prepared refresh selected another implementation')
    expected.update(entrypoint=p['helper'],symbols=current['symbols'],dependencies=current['dependencies'])
    expected['read_call']['args'][0].update(home=p['home'],single_helper=p['single_helper'])
    N.require(current==expected,'prepared refresh changed unrelated runtime authority')
    previous=N.module('prepared_refresh_completed_emission',old['completed_helper'])
    previous.validate_single(N,old,producer,selected,lease)
    home=Path(old['home']);guard=N.read(q['refresh_guard']);intent=N.read(guard['intent']);log=Path(guard['log']['path']).read_text()
    N.require(q['refresh_guard']==N.binding(home/'refresh/resource.json') and guard['returncode']==1 and guard['failure']is None
        and guard['log']==N.binding(home/'refresh/step.log') and intent['action']=='refresh'
        and intent['arguments']==dict(legacy=N.binding(home/'legacy.json'),checkpoint=N.read(p['single_author'])['research'],current=N.binding(home/'current.json'),lease=N.binding(home/'lease.json'))
        and log.rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: refresh owner/worker changed')
        and not any((home/n).exists()for n in ('refresh/result.json','bridge-step','authoring-job.json','result.json'))
        and (committed is not None or not (home/'refresh/prefix/committed.json').exists()),
        'prepared refresh lacks exact pre-CAS refusal')
    value=N.read(q['prepared'])
    N.require(q['prepared']==N.binding(home/'refresh/prefix/prepared.json') and value['code']==selected_code['selected']
        and value['selection']==N.read(intent['arguments']['current']) and value['prior']==intent['arguments']['checkpoint']
        and N.read(intent['arguments']['lease'])==lease and value['before']['sha256']==p['tracker']['sha256']
        and N.binding(p['tracker']['path'])==p['tracker'],'prepared refresh content or tracker changed')
    if committed is not None:validate_committed(N,p,q,value,lease,committed)
    outer=N.read(q['outer']);wi=N.read(outer['intent']);job=N.read(wi['job'])
    N.require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1 and outer['failure_type']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False and outer['result']is None
        and wi['owner']==p['owner'] and job['driver']==old['helper']
        and job['source']==p['source'] and job['workbook']==p['workbook'] and job['inputs'][0]==q['previous']
        and job['command'][3:]==[old['helper']['path'],'run','--plan',q['previous']['path'],'--plan-sha256',q['previous']['sha256']],
        'prepared refresh stopped worker identity differs')
    claim=N.read(q['claim'])
    N.require(q['claim']==N.binding(Path(outer['intent']['path']).parent/'claim.json')
        and claim['fence']==old['fence'] and claim['owner']==p['owner'] and claim['workbook']==p['workbook']
        and claim['claim_id']==wi['claim_id'] and claim['version']==job['version']
        and claim['source_sha256']==p['source']['sha256'],'original claim receipt differs from stopped worker')
    stop=N.read(q['stopped']);N.require(set(stop)=={'at','claims','models','processes','files'} and stop['claims']==stop['models']==0 and stop['processes']==[], 'prepared refresh worker or model remains active')
    required=[q['previous'],q['prepared'],q['claim'],q['refresh_guard'],guard['intent'],guard['log'],q['outer'],outer['intent'],wi['job'],*intent['arguments'].values(),value['code'],value['before']]
    inventory={x['native']['path']:x['native']['sha256']for x in stop['files']}
    N.require(len(inventory)==len(stop['files']) and all(inventory.get(r['path'])==r['sha256']for r in required if r!=value['prior']), 'prepared refresh stopped custody incomplete')
    for x in stop['files']:
        N.require(N.binding(x['native']['path'])==x['native'] and N.binding(x['copy']['path'])==x['copy'] and x['native']['sha256']==x['copy']['sha256'],'prepared refresh custody bytes changed')
    from scripts.batch import harbor_environment_refresh as F
    N.require(N.binding(F.__file__)==value['code'] and all(getattr(F,n).__globals__ is F.__dict__ for n in ('prepare','validate','commit','_history_checkpoint')), 'refresh functions do not share selected identity')
    if full:F.validate(worker,q['prepared'],N.read(p['tracker']))
    return q,value,F


def validate_committed(N,p,q,value,lease,committed):
    """Authenticate the completed CAS while validating its original preparation."""
    from scripts import harbor_campaign as C
    N.require(set(committed)=={'checkpoint','committed'},'closed committed refresh history required')
    cp=N.read(committed['checkpoint']);receipt=N.read(committed['committed']);before=N.read(value['before'])
    N.require(committed['checkpoint']==N.binding(Path(p['home'])/'refresh/result.json')
        and committed['committed']==N.binding(Path(q['prepared']['path']).parent/'committed.json')
        and cp['status']=='IMPORTED' and cp['publication_ready']is False
        and cp['selection']==value['selection'] and cp['step']==value['last']
        and receipt==dict(prepared=q['prepared'],revision=before['revision']+1,tracker_sha256=C.decode(cp['lease']).sha256),
        'completed refresh differs from original prepared CAS')
    initial,current=C.decode(lease),C.decode(cp['lease'])
    N.require((current.owner,current.token)==(initial.owner,initial.token)
        and current.revision==initial.revision+1==receipt['revision'], 'completed refresh lease lineage differs')
    descriptor=N.read(cp['descriptor'])
    N.require(descriptor['schema_version']=='harbor-environment-coordinator-refresh/v1'
        and descriptor['refresh']==q['prepared'] and descriptor['initial_lease']==cp['lease']
        and q['prepared']in cp['evidence'] and committed['committed']in cp['evidence'],
        'completed refresh descriptor detached from owning CAS')


@contextmanager
def installed(N,p,producer,selected,worker,lease,*,initial):
    q,value,F=validate(N,p,producer,selected,worker,lease,full=initial)
    from scripts import harbor_campaign as C
    original=F.prepare
    def retained(s,current_lease,prior,current,last,root):
        N.require(s==worker and C.encode(current_lease)==lease and prior==value['prior']
            and F.encode(current)==value['selection'] and last==value['last']
            and Path(root)==Path(p['home'])/'refresh/prefix' and not Path(root).exists(),
            'original prepare arguments differ from retained preparation')
        _,before=F.W._active(s,current_lease);F.validate(s,q['prepared'],before)
        N.require(N.binding(p['tracker']['path'])==p['tracker'],'retained refresh tracker changed')
        return q['prepared']
    F.prepare=retained
    try:yield q
    finally:F.prepare=original
