"""Continue a sealed second profile rejection from its retained import."""
from pathlib import Path
SCHEMA='fleet-profile-derived-remaining/v1'

def validate(E,p):
    proof=E.checked(p['profile_derived_remaining'])
    E.require(set(proof)=={'schema_version','helper','previous','outer','failed','stopped'} and proof['schema_version']==SCHEMA
        and proof['helper']==E.binding(__file__),'closed derived-profile remaining proof required')
    old=E.checked(proof['previous']);changes={'entry','home','operations','profile_recovery','profile_derived_remaining'}
    E.require('profile_derived_remaining'not in old and set(p)==set(old)|{'profile_derived_remaining'}
        and {k:v for k,v in p.items()if k not in changes}=={k:v for k,v in old.items()if k not in changes}
        and Path(p['home']).parent==Path(old['home']).parent and p['home']!=old['home']
        and Path(p['operations']).parent==Path(old['operations']).parent and p['operations']!=old['operations'],
        'derived continuation changed original source or import')
    original=E.module('derived_profile_original_remaining',old['entry']);original.profile_remaining_identity(old)
    policy=E.checked(p['profile_recovery']);prior_policy=E.checked(old['profile_recovery']);derivation=E.checked(policy['producer_derivation'])
    E.require(policy['producer_correction']==prior_policy['producer_correction']
        and all(policy[k]==prior_policy[k]==derivation[k]for k in ('source','phase1','inventory','captures','authorization')),
        'derived continuation changed prior correction or observed sources')
    outer=E.checked(proof['outer']);intent=E.checked(outer['intent']);job=E.checked(intent['job']);claim=E.checked(E.binding(Path(outer['intent']['path']).parent/'claim.json'))
    E.require(job['driver']==old['entry'] and job['inputs'][0]==proof['previous'] and job['source']==p['source'] and job['workbook']==p['workbook']
        and intent['owner']==claim['owner']==p['owner']and intent['claim_id']==claim['claim_id']
        and claim['version']==job['version']and claim['source_sha256']==p['source']['sha256']and claim['workbook']==p['workbook'],
        'derived continuation stopped claim differs')
    E.require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==0 and outer['failure_type']is None
        and outer['automatic_retry']is False and outer['result']==E.binding(Path(old['home'])/'result.json')
        and E.checked(outer['result'])['status']=='UNRESOLVED','sealed successful outer profile guard required')
    E.require(proof['failed']==E.binding(Path(old['home'])/'bridge/outcome.json'),'derived failed guard namespace differs')
    guard=E.checked(proof['failed']);resource=E.checked(guard['resource']);result=E.checked(guard['result']);gi=E.checked(resource['intent'])
    retained=E.checked(E.checked(old['profile_remaining'])['refresh']);imported=E.checked(retained['result'])
    E.require(guard['status']=='COMPLETED_OPERATION'and resource['returncode']==0 and resource['failure']is None
        and result['status']=='UNRESOLVED'and result['step']=='source_profile'and result['descriptor']==imported['descriptor']
        and result['lease']==imported['lease']and derivation['stop']in result['evidence']
        and gi['action']=='step'and gi['arguments']==dict(descriptor=imported['descriptor'],checkpoint=retained['result'],captures=policy['captures']),
        'derived profile rejection detached from retained import')
    stop=E.checked(proof['stopped']);E.require(stop['processes']==[],'derived profile worker remains live')
    copies={r['native']['path']:r for r in stop['files']};E.require(len(copies)==len(stop['files']),'duplicate stopped profile evidence')
    for r in copies.values():
        E.require(E.binding(r['copy']['path'])==r['copy']and r['copy']['sha256']==r['native']['sha256']and E.binding(r['native']['path'])==r['native'],'stopped derived profile custody changed')
    for r in (proof['previous'],proof['outer'],proof['failed'],outer['intent'],intent['job'],E.binding(Path(outer['intent']['path']).parent/'claim.json')):
        E.require(r['path']in copies and copies[r['path']]['native']==r,'missing derived profile stopped identity')
    operations=Path(old['operations']);expected={operations/'job.json',operations/'hold.json'}
    E.require({f for f in operations.rglob('*')if f.is_file()}==expected,'derived producer has later author steps')
    E.require(E.checked(E.binding(operations/'hold.json'))==dict(automatic_retry=False,outcome=proof['failed'],reason='owning status or bounded limit requires continuation')
        and E.checked(E.binding(operations/'job.json'))==dict(E.checked(old['original_job']),outcome=proof['failed'],operations=old['operations']),
        'derived dispatcher hold changed original job or failed guard')
    E.require(all(str(f)in copies and copies[str(f)]['native']==E.binding(f)for f in expected),'derived dispatcher hold not in stopped census')
    return proof
