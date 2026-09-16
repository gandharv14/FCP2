"""Authenticate a sealed deterministic emission before idempotent completion.

Only a source-ID truncation failure after the original unchanged normalized
outputs is supported. The original producer validates all bytes and values.
"""
from pathlib import Path
import json
import hashlib

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),
        "canonical partial emission file required")
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={"path","sha256"} and binding(row["path"])==row,"partial emission bytes changed")
    return json.loads(Path(row["path"]).read_bytes())

SCHEMA='fleet-normalization-partial-emission/v1'


def names(workbook):
    return ('first_normalization.json',f'normalize_{workbook}.py','normalized.json',
            'normalization_report.json','exclusions.json')


def preexecution(p):
    """Retain an admitted worker that stopped before creating its task journal."""
    proof=read(p['preexecution'])
    require(set(proof)=={'schema_version','previous','outer','stopped','log'}
        and proof['schema_version']=='fleet-normalization-preexecution-stop/v1',
        'closed preexecution stop required')
    old=read(proof['previous'])
    changed={'helper','partial_helper','home','fence','preexecution','runtime_profile'}
    require('partial_emission'in old and 'preexecution'not in old
        and {k:v for k,v in old.items()if k not in changed}=={k:v for k,v in p.items()if k not in changed}
        and p['fence']==old['fence']+1 and Path(p['home']).parent==Path(old['home']).parent
        and p['home']!=old['home'], 'preexecution changed original continuation authority')
    from copy import deepcopy
    expected=deepcopy(read(old['runtime_profile']))
    expected['entrypoint']=p['helper']
    expected['read_call']['args'][0]['home']=p['home']
    require(read(p['runtime_profile'])==expected, 'preexecution changed selected runtime beyond its reader namespace')
    outer=read(proof['outer']);intent=read(outer['intent']);job=read(intent['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']is None
        and outer['failure_type']=='TimeoutError' and outer['result']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False
        and intent['owner']==old['owner'] and intent['automatic_retry']is False
        and job['workbook']==old['workbook'] and job['source']==old['source']
        and job['driver']==old['helper'] and job['inputs'][0]==proof['previous']
        and job['command'][3:]==[old['helper']['path'],'run','--plan',proof['previous']['path'],
            '--plan-sha256',proof['previous']['sha256']], 'different preexecution worker failure')
    require(proof['log']==binding(Path(proof['outer']['path']).parent/'worker.log')
        and not Path(proof['log']['path']).read_bytes() and not Path(old['home']).exists()
        and not Path(old['home']+'-authoring').exists(), 'preexecution task already began')
    stop=read(proof['stopped'])
    require(set(stop)=={'at','claims','models','processes','files'} and stop['claims']==stop['models']==0
        and stop['processes']==[], 'preexecution worker or model still active')
    required=[proof['previous'],proof['outer'],outer['intent'],intent['job'],proof['log']]
    inventory={x['native']['path']:x['native']['sha256']for x in stop['files']}
    require(len(inventory)==len(stop['files']) and all(inventory.get(r['path'])==r['sha256']for r in required),
        'preexecution custody omits owning evidence')
    for x in stop['files']:
        require(binding(x['native']['path'])==x['native'] and binding(x['copy']['path'])==x['copy']
            and x['native']['sha256']==x['copy']['sha256'], 'preexecution custody changed')
    return old


def validate(p,P,lease):
    lineage=preexecution(p)if 'preexecution'in p else p
    q=read(p['partial_emission'])
    require(set(q)=={'schema_version','previous','guard','outer','stopped','outputs'}
        and q['schema_version']==SCHEMA,'closed partial emission custody required')
    old=read(q['previous'])
    require('ordinary_author'in old and 'partial_emission'not in old
        and {k:v for k,v in old.items()if k not in {'helper','runtime_profile','home','fence'}}
            =={k:v for k,v in p.items()if k not in {'helper','runtime_profile','home','fence','partial_emission','partial_helper','preexecution'}}
        and lineage['fence']==old['fence']+1 and Path(p['home']).parent==Path(old['home']).parent
        and p['home']!=old['home'],'partial emission changed original authority or lineage')
    previous_profile=read(old['runtime_profile']);profile=read(p['runtime_profile'])
    require(len(previous_profile['symbols'])==1 and len(profile['symbols'])==2
        and previous_profile['symbols']==profile['symbols'][:1],
        'partial emission must preserve original alias correction and add only source identity')
    d=Path(old['home']);g=read(q['guard']);i=read(g['intent'])
    require(q['guard']==binding(d/'emit/resource.json') and g['returncode']==1
        and g['failure_type']is None and g['automatic_retry']is False
        and g['log']==binding(d/'emit/step.log') and i['plan']==q['previous']
        and i['helper']==old['helper'] and i['prepared']==p['prepared']
        and i['seconds']==3300 and i['memory']==8*1024**3
        and set(i)=={'plan','helper','prepared','lease','seconds','memory'} and i['lease']==lease,
        'partial emission lacks exact completed deterministic failure')
    log=Path(g['log']['path']).read_text()
    tail=log.rstrip().splitlines()[-1]
    require(tail.startswith('ValueError: Duplicate explicit source id ')
        and tail.endswith(' collides with a different source definition')
        and 'N.validate_spec(spec)'in log,'different emission failure')
    outer=read(q['outer']);wi=read(outer['intent']);job=read(wi['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1
        and outer['failure_type']is None and outer['automatic_retry']is False
        and outer['publication_performed']is False and outer['result']is None
        and job['driver']==old['helper'] and job['source']==p['source']
        and job['workbook']==p['workbook'] and job['inputs'][0]==q['previous']
        and job['command'][3:]==[old['helper']['path'],'run','--plan',q['previous']['path'],
            '--plan-sha256',q['previous']['sha256']],
        'partial emission outer worker changed or is uncertain')
    stop=read(q['stopped'])
    require(set(stop)=={'at','claims','files','models','processes'} and stop['claims']==stop['models']==0
        and stop['processes']==[],'partial emission worker remains active')
    inventory={x['native']['path']:x['native']['sha256']for x in stop['files']}
    required=[q['previous'],q['guard'],g['intent'],g['log'],q['outer'],outer['intent'],wi['job']]
    require(len(inventory)==len(stop['files'])and all(inventory.get(x['path'])==x['sha256']for x in required),
        'partial emission stopped inventory omits owning evidence')
    for x in stop['files']:
        require(binding(x['native']['path'])==x['native']and binding(x['copy']['path'])==x['copy']
            and x['native']['sha256']==x['copy']['sha256'],'partial emission stopped evidence changed')
    run=P.selection(p['prepared']).run
    require(q['outputs']==[binding(run/n)for n in names(p['workbook'])]
        and all(inventory.get(x['path'])==x['sha256']for x in q['outputs']),
        'partial emission output custody changed')
    require(not any((d/n).exists()for n in ('emit/result.json','refresh','bridge-step','authoring-job.json','result.json'))
        and not any((run/n).exists()for n in ('phase1-result.json','normalized.phase1.json','plain-eligibility.json',
            'source_profiles.json','source-profile-mapping.json','source-display-review.json','oracle_forced_exclusions.json')),
        'partial emission or downstream work already advanced')
    m,s,c,draft,rows=P._current(p['prepared'],allow_emitted=True)
    plan=read(m['selection']);_,expected=P.N.expected_outputs(s.workbook,draft,rows,run,plan['pipeline_bindings'])
    for path,value in expected.items():
        require(path.read_bytes()==(json.dumps(value,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode(),
            'partial output differs from original reviewed generator')
    P.N.validate_emission(s.workbook,draft,rows,run,plan['pipeline_bindings'])
    spec=read(binding(run/'normalized.json'));P.N.validate_spec(spec)
    require(not P.N.verify_against_golden(spec,Path(s.bindings['source_file']['path'])),
        'partial normalization values differ from original source')
    return q
