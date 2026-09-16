"""Preserve a completed normalization emission across a pre-import refresh failure."""
from pathlib import Path
from copy import deepcopy
import json,hashlib

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),
        "canonical partial emission file required")
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={"path","sha256"} and binding(row["path"])==row,"partial emission bytes changed")
    return json.loads(Path(row["path"]).read_bytes())



SCHEMA='fleet-normalization-completed-emission/v1'

def result(p):
    q=read(p['completed_emission'])
    return read(read(q['emission_result'])['emitted'])

def validate(p,P,lease):
    q=read(p['completed_emission'])
    require(set(q)=={'schema_version','previous','emission_result','emission_guard','refresh_guard','outer','stopped'}
        and q['schema_version']==SCHEMA,'closed completed emission custody required')
    old=read(q['previous']);changed={'helper','home','fence','runtime_profile','completed_emission','completed_helper'}
    require('preexecution'in old and 'completed_emission'not in old
        and {k:v for k,v in old.items()if k not in changed}=={k:v for k,v in p.items()if k not in changed}
        and p['fence']==old['fence']+1 and p['home']!=old['home']
        and Path(p['home']).parent==Path(old['home']).parent,'completed emission changed original authority')
    prior=read(old['runtime_profile']);selected=read(p['runtime_profile']);expected=deepcopy(prior)
    require(len(prior['symbols'])==2 and len(selected['symbols'])==3
        and prior['symbols']==selected['symbols'][:2], 'completed emission changed selected generator')
    expected['entrypoint']=p['helper'];expected['read_call']['args'][0]['home']=p['home'];expected['symbols']=selected['symbols']
    require(selected==expected,'completed emission changed runtime beyond declared refresh function')
    home=Path(old['home']);guard=read(q['emission_guard']);intent=read(guard['intent']);done=read(q['emission_result'])
    require(q['emission_guard']==binding(home/'emit/resource.json') and q['emission_result']==binding(home/'emit/result.json')
        and guard['returncode']==0 and guard['failure_type']is None and guard['automatic_retry']is False
        and guard['log']==binding(home/'emit/step.log') and intent==dict(plan=q['previous'],helper=old['helper'],prepared=p['prepared'],lease=lease,seconds=3300,memory=8*1024**3)
        and done==dict(emitted=done['emitted'],lease=lease,runtime=old['runtime_profile'],models_dispatched=0),
        'original completed emission guard differs')
    refresh=read(q['refresh_guard']);ri=read(refresh['intent']);log=Path(refresh['log']['path']).read_text()
    require(q['refresh_guard']==binding(home/'refresh/resource.json') and refresh['returncode']==1 and refresh['failure']is None
        and refresh['log']==binding(home/'refresh/step.log') and ri['action']=='refresh'
        and ri['arguments']==dict(legacy=binding(home/'legacy.json'),checkpoint=read(p['ordinary_author'])['research'],current=binding(home/'current.json'),lease=binding(home/'lease.json'))
        and "manifest['previous']['prior_plan']"in log and log.rstrip().endswith("TypeError: 'NoneType' object is not subscriptable")
        and not any((home/n).exists()for n in ('refresh/result.json','bridge-step','authoring-job.json','result.json')),
        'refresh advanced or stopped for a different reason')
    outer=read(q['outer']);wi=read(outer['intent']);job=read(wi['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1 and outer['failure_type']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False and outer['result']is None
        and wi['owner']==p['owner'] and job['driver']==old['helper'] and job['source']==p['source']
        and job['workbook']==p['workbook'] and job['inputs'][0]==q['previous']
        and job['command'][3:]==[old['helper']['path'],'run','--plan',q['previous']['path'],'--plan-sha256',q['previous']['sha256']],
        'completed emission lacks original stopped worker')
    stop=read(q['stopped']);require(set(stop)=={'at','claims','models','processes','files'} and stop['claims']==stop['models']==0
        and stop['processes']==[], 'completed emission worker or model still active')
    required=[q['previous'],q['emission_result'],q['emission_guard'],guard['intent'],guard['log'],done['emitted'],
        q['refresh_guard'],refresh['intent'],refresh['log'],q['outer'],outer['intent'],wi['job']]
    inventory={x['native']['path']:x['native']['sha256']for x in stop['files']}
    require(len(inventory)==len(stop['files']) and all(inventory.get(r['path'])==r['sha256']for r in required),'completed emission custody incomplete')
    for x in stop['files']:
        require(binding(x['native']['path'])==x['native'] and binding(x['copy']['path'])==x['copy']
            and x['native']['sha256']==x['copy']['sha256'],'completed emission custody changed')
    emitted=read(done['emitted']);s=P.selection(p['prepared'])
    require(emitted['status']=='phase1_evidence_ready' and emitted['prepared']==p['prepared']
        and done['emitted']==binding(s.run/'phase1-result.json') and emitted['phase1']==binding(s.run/'normalized.phase1.json')
        and read(p['prepared'])['previous']is None and binding(p['tracker']['path'])==p['tracker'],
        'completed phase one or initial partition identity changed')
    P.aggregate(p['prepared'])
    require(not P.N.verify_against_golden(read(emitted['phase1']),Path(s.bindings['source_file']['path'])),
        'completed phase one differs from source')
    return emitted


def validate_single(N,p,producer,selected,lease):
    """Reuse an actual successful single emission after its pre-import refusal."""
    q=read(p['completed_emission'])
    require(set(q)=={'schema_version','previous','emission_result','emission_guard','refresh_guard','outer','stopped'}
        and q['schema_version']=='fleet-normalization-single-completed-emission/v1','closed single emission custody required')
    old=read(q['previous']);changed={'helper','single_helper','home','fence','runtime_profile','completed_emission','completed_helper'}
    require('single_author'in old and 'completed_emission'not in old
        and {k:v for k,v in old.items()if k not in changed}=={k:v for k,v in p.items()if k not in changed}
        and p['fence']==old['fence']+1 and p['home']!=old['home']
        and Path(p['home']).parent==Path(old['home']).parent,'single emission changed original authority')
    prior=read(old['runtime_profile']);current=read(p['runtime_profile']);expected=deepcopy(prior)
    require(len(prior['symbols'])==1 and len(current['symbols'])==2 and prior['symbols']==current['symbols'][:1],
        'single emission changed original completed review correction')
    expected['entrypoint']=p['helper'];expected['read_call']['args'][0]['home']=p['home']
    expected['read_call']['args'][0]['single_helper']=p['single_helper'];expected['symbols']=current['symbols']
    require(current==expected,'single emission runtime changed beyond declared refresh functions')
    home=Path(old['home']);guard=read(q['emission_guard']);intent=read(guard['intent']);done=read(q['emission_result'])
    require(q['emission_guard']==binding(home/'emit/resource.json') and q['emission_result']==binding(home/'emit/result.json')
        and guard['returncode']==0 and guard['failure_type']is None and guard['automatic_retry']is False
        and guard['log']==binding(home/'emit/step.log')
        and intent==dict(plan=q['previous'],helper=old['helper'],prepared=p['prepared'],lease=lease,seconds=3300,memory=12*1024**3)
        and done==dict(emitted=done['emitted'],lease=lease,runtime=old['runtime_profile'],models_dispatched=0),
        'single completed emission guard differs')
    refresh=read(q['refresh_guard']);ri=read(refresh['intent']);log=Path(refresh['log']['path']).read_text()
    require(q['refresh_guard']==binding(home/'refresh/resource.json') and refresh['returncode']==1 and refresh['failure']is None
        and refresh['log']==binding(home/'refresh/step.log') and ri['action']=='refresh'
        and ri['arguments']==dict(legacy=binding(home/'legacy.json'),checkpoint=read(p['single_author'])['research'],current=binding(home/'current.json'),lease=binding(home/'lease.json'))
        and log.rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: non-proof financial binding changed')
        and not any((home/n).exists()for n in ('refresh/result.json','bridge-step','authoring-job.json','result.json')),
        'single refresh advanced or stopped for a different reason')
    outer=read(q['outer']);wi=read(outer['intent']);job=read(wi['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1 and outer['failure_type']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False and outer['result']is None
        and wi['owner']==p['owner'] and job['driver']==old['helper'] and job['source']==p['source']
        and job['workbook']==p['workbook'] and job['inputs'][0]==q['previous']
        and job['command'][3:]==[old['helper']['path'],'run','--plan',q['previous']['path'],'--plan-sha256',q['previous']['sha256']],
        'single emission lacks original stopped worker')
    stop=read(q['stopped']);require(set(stop)=={'at','claims','models','processes','files'}and stop['claims']==stop['models']==0
        and stop['processes']==[],'single emission worker or model still active')
    required=[q['previous'],q['emission_result'],q['emission_guard'],guard['intent'],guard['log'],done['emitted'],q['refresh_guard'],refresh['intent'],refresh['log'],q['outer'],outer['intent'],wi['job']]
    inventory={x['native']['path']:x['native']['sha256']for x in stop['files']}
    require(len(inventory)==len(stop['files']) and all(inventory.get(r['path'])==r['sha256']for r in required),'single emission custody incomplete')
    for x in stop['files']:
        require(binding(x['native']['path'])==x['native']and binding(x['copy']['path'])==x['copy']
            and x['native']['sha256']==x['copy']['sha256'],'single emission custody changed')
    emitted=read(done['emitted'])
    require(emitted['status']=='phase1_evidence_ready'and emitted['prepared']==p['prepared']
        and done['emitted']==binding(selected.run/'phase1-result.json')
        and emitted['phase1']==binding(selected.run/'normalized.phase1.json')
        and binding(p['tracker']['path'])==p['tracker'],'single emitted phase one changed')
    for name,row in emitted['outputs'].items():
        require(name in {'normalized.json','normalization_report.json','exclusions.json'}
            and row==binding(selected.run/name),'single normalization output changed')
    require(set(emitted['outputs'])=={'normalized.json','normalization_report.json','exclusions.json'},'single output inventory differs')
    return emitted
