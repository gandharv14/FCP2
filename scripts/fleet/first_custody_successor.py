"""Once-only first fleet custody for reviewed historical continuations.

No prior fleet worker is invented. Original tracker custody is replaced only by
its owning continuation helper after a centrally fenced claim. A lost response
retains root/remote intent and requires reconciliation, never automatic replay.
"""
from __future__ import annotations
import argparse,ast,fcntl,json,os,shlex,subprocess,sys
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,safe
from scripts.fleet.pool import lock,persist
from scripts.fleet.coordinator import Client
from scripts.fleet import launch_job as L,fleet_control as F
SCHEMA='harbor-fleet-first-historical-custody/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def original_authority(p,job):
    a=checked(p['authority']);inspection=checked(a['inspection'])
    require(set(a)=={'schema_version','kind','workbook','job','source','tracker','checkpoint','descriptor','inspection','history'},'closed historical authority required')
    require(a['schema_version']=='harbor-fleet-retained-native-authority/v1' and a['job']==p['job']
        and a['workbook']==job['workbook'] and a['source']==job['source'],'native job/source identity differs')
    tracker=checked(a['tracker']);checkpoint=checked(a['checkpoint']);checked(a['descriptor'])
    require(tracker['workbook_id']==job['workbook'] and tracker['bindings']['original_raw_source']==a['source']
        and checkpoint['descriptor']==a['descriptor'],'native tracker/checkpoint/source differs')
    require(inspection['model_calls']==0 and inspection['publication_ready'] is False,'successful non-model native probe required')
    command=job['command'];require('--output' in command and command[command.index('--output')+1]==str(Path(job['result']).parent),'exact original output namespace required')
    h=checked(a['history'])
    if a['kind']=='source-only':
        require(job['workbook']=='0006' and inspection['status']=='SOURCE_ONLY_CONTINUATION_VALIDATED'
            and inspection['source']==a['source'] and inspection['new_admission'] is False,'source-only native probe differs')
        require(a['tracker']==inspection['predecessor']['trackers/workbooks/0006.json']
            and a['checkpoint']==inspection['predecessor']['dispatcher-v2/first/000/result.json']
            and a['descriptor']==inspection['predecessor']['admission-001/descriptor.json'],'source-only predecessor differs')
        require(h['schema_version']=='fleet-source-continuation-history/v1' and h['fresh_admission_allowed'] is False
            and h['tracker']==a['tracker'] and h['checkpoint']==a['checkpoint'] and h['descriptor']==a['descriptor']
            and h['source']==a['source'] and h['central_histories'].get(job['workbook'])
            and job.get('continuation_history')==a['history'],'retained source history differs')
        require('migrate' in command and '--continuation-history' in command
            and command[command.index('--continuation-history')+1]==a['history']['path']
            and command[command.index('--continuation-history-sha256')+1]==a['history']['sha256'],'migration history command differs')
    elif a['kind']=='existing-review':
        require(job['workbook']=='0190' and inspection['status']=='EXISTING_REVIEW_VALIDATED'
            and inspection['same_review_must_not_replay'] is True and inspection['source_sha256']==a['source']['sha256']
            and inspection['tracker']==a['tracker'] and inspection['takeover_selection']['checkpoint']==a['checkpoint'],'existing-call native probe differs')
        require(h['schema_version']=='0190-fleet-existing-review-refresh-plan/v1' and h['workbook']==job['workbook']
            and h['models'] is False and h['publication'] is False and h['automatic_retry'] is False
            and h['source']==a['source'] and h['original_tracker']==a['tracker'] and h['original_checkpoint']==a['checkpoint']
            and h['original_descriptor']==a['descriptor'] and h['command']==command and h['helper']==job['driver']
            and h['existing_review']==inspection['receipt'] and h['snapshot']==inspection['snapshot'],'reviewed existing-response history differs')
        for row in h['original_completed_call'].values():checked(row)
    else:raise ValueError('unsupported historical authority')
    return a


# Exact immutable preparations are populated only after their native probe has
# passed. A future recipe requires a new reviewed launcher snapshot.
PROFILES = {'0029': {'driver': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/driver.py',
                     'sha256': '52e35506ca06909f04c51b3b45ec13ce8f246d1b9ba66c037b15984dcf8b0cdb'},
          'evidence': [{'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/gated-successor-004/source-proof.json',
                        'sha256': '5c8448deb7e3328cfd0a5eba646965d31c30e9aa1271a2d5b33e358db005d176'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/local-probe/result.json',
                        'sha256': 'bcb13e8f74580345aa2d15530b3627ccaef9d29bc56de86e057d8af5a5bca623'}],
          'history': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/plan.json',
                      'sha256': '835ce8237f0c8b1b19d487846b626924911ad004dd3f586799d7597c267e8843'},
          'host': 'hhu-server-3',
          'inspection': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/transfer-003/probe.json',
                         'sha256': '9b92788ab23f22bd222c58127e49bf4126cad4810fdfdcc3bdeb55e118184f7f'},
          'job': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0029/pre-review-001/worker-job-002.json',
                  'sha256': 'acc2541686f43134e91470c5dd11b48ff15c95b45ff5652862e3eabe7c52c098'},
          'kind': 'dialogue-pre-review-0029',
          'stage_memory': 8589934592,
          'stage_seconds': 3300,
          'version': '2d3e15a8e7b500635e6960eac14872beb98adcae75de8f1f53f174eeb42ec98c'},
 '0073': {'driver': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0073/gated-successor-003/normalization_0073-644059960bd74fd8dd56edf55925857856db4e247b61a9141a5c498850f47316.py',
                     'sha256': '644059960bd74fd8dd56edf55925857856db4e247b61a9141a5c498850f47316'},
          'evidence': [{'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0073/gated-successor-003/inspection-linux-001/inspection.json',
                        'sha256': 'af92515b112f93de6d96636da2ef6559a62edee9c16b9e59f8be1ec849129776'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0073/gated-successor-003/inspection-linux-001/dependency.json',
                        'sha256': '039ed384c7a28ba87beb5e5a1b13e54903ffed3ae7c30738c89e098e3e8d99cb'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0073/gated-successor-003/audit-inspection-linux-001/inspection.json',
                        'sha256': 'fd2fa7278de06e0915dab194072f8e3bc9154d43a2dc4dd3c765ac7d26712ac8'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0073/gated-successor-003/profile.json',
                        'sha256': '9e9e7fe5da1faa8f891ce57453189e3dfdfe49e4fdbdbffcd00c5765ac2bd203'}],
          'history': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0073/gated-successor-003/continuation-plan-draft.json',
                      'sha256': '6df5b4a0561fa4055972658e00f0193ddc637a90030704b74b260fbd59ca9085'},
          'host': 'hhu-server-2',
          'inspection': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0073/gated-successor-003/driver-probe-linux-001/probe.json',
                         'sha256': '8d2f70912e9923d6cb4d4a59eb9b4cf01a68bd452eac87db36eba15c9390c112'},
          'job': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0073/gated-successor-003/worker-job-draft-002.json',
                  'sha256': 'deae421dbf40ab3d1d7932b43f152d8976b98d4cc6a69c78d72ebf258f750070'},
          'kind': 'normalization-hold',
          'stage_memory': 8589934592,
          'stage_seconds': 3300,
          'version': '874a8f6940c553abeec97937e4b9124718b081fbf26580db7c40ebfaef22c3a8'},
 '0077': {'driver': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0077/gated-successor-001/jobs-001/normalization_0077-a42c1508ba2e87bdd52eff07111ed3290ff08cff828f193f4da30912247e3fa9.py',
                     'sha256': 'a42c1508ba2e87bdd52eff07111ed3290ff08cff828f193f4da30912247e3fa9'},
          'evidence': [{'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0077/gated-successor-001/inspection-linux-001/inspection.json',
                        'sha256': '9da18261b5c3c3748ef0c625107b02130b803219ae0070c81f152e0498dd7c77'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0077/gated-successor-001/inspection-linux-001/dependency.json',
                        'sha256': '7b1de0a98ca3e3649e32e90806dd7265001dfb15f1c17186dfda4d5486edba61'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0077/gated-successor-001/profile.json',
                        'sha256': '01f4520c1c510b6ec9dbc8083c7eaa2353b329e4cb6a53a1cce20e70c1089587'}],
          'history': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0077/gated-successor-001/jobs-001/continuation-plan-draft.json',
                      'sha256': 'fa6846e22f02f01a04201ed3dc1168be7bba28f94fbd7ca26f722ff79314de73'},
          'host': 'hhu-server-6',
          'inspection': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0077/gated-successor-001/jobs-001/probe-linux-001.json',
                         'sha256': 'f3b48ef57803d7bdddbf5b988798c48eb6d6a020365c57165e166876221c8759'},
          'job': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0077/gated-successor-001/jobs-001/worker-job-draft.json',
                  'sha256': 'f8b5fc6be5fb4d4564fd2eb3ebb1299d81b6ea7c51f890c22f3d76e84d1718c8'},
          'kind': 'normalization-hold',
          'stage_memory': 8589934592,
          'stage_seconds': 3300,
          'version': '94fa49411fd92b8ca1bf596d473c4fdfed894326d125fde498f94963c7b71779'},
 '0173': {'driver': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0173/gated-successor-001/jobs-001/normalization_0173-58faf6ec62db6177509600fc2e488089b4087046131d8bdaf8cd48adda0151c0.py',
                     'sha256': '58faf6ec62db6177509600fc2e488089b4087046131d8bdaf8cd48adda0151c0'},
          'evidence': [{'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0173/gated-successor-001/inspection-linux-001/inspection.json',
                        'sha256': '037492c0ac6137132d1956afcc03dbdc3c99feed5c2f47752ff60edae81555db'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0173/gated-successor-001/inspection-linux-001/dependency.json',
                        'sha256': '107d5e02b7bf110035099b1c0987187e9bb0b1cb7996cd75c4211a872a1ed897'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0173/gated-successor-001/profile.json',
                        'sha256': '11f5c48612e72e973ac0feb9efd9c3cf206b95ad5499530029afcbc4b32a4098'}],
          'history': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0173/gated-successor-001/jobs-001/continuation-plan-draft.json',
                      'sha256': '5463ed026837ed0bba6464f2bf52e7e7d3bb36ec5920ad32a872daf7b8b025bf'},
          'host': 'hhu-server-4',
          'inspection': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0173/gated-successor-001/jobs-001/probe-linux-001.json',
                         'sha256': '5eb357272855883be3c2c11edd215384190af5567d56a2ba68bf6b68b4a4d5ca'},
          'job': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0173/gated-successor-001/jobs-001/worker-job-draft.json',
                  'sha256': '65706a9ab9f4de6e0760df92a20df33e95a5e49e0c0616c1913c063f0ac4b8c9'},
          'kind': 'normalization-hold',
          'stage_memory': 8589934592,
          'stage_seconds': 3300,
          'version': '6bc05b90a9daf15dd66ccc96058f5366a12068f6a133765ec2cc451e13d99dc8'},
 '0303': {'driver': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0303/gated-successor-001/jobs-001/normalization_0303-c56abf97241de9c59ebc3f053e3785fde352a35eb6d54b6a294ddc3e51d901ff.py',
                     'sha256': 'c56abf97241de9c59ebc3f053e3785fde352a35eb6d54b6a294ddc3e51d901ff'},
          'evidence': [{'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0303/gated-successor-001/inspection-linux-001/inspection.json',
                        'sha256': 'e68c0e0a0e234c2b6bc2772d0da4c410721640c1873686786e90b21a15354c5e'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0303/gated-successor-001/inspection-linux-001/dependency.json',
                        'sha256': 'b7205b0de68f035f7bcd1cde6bb92719b513cc4f5e4efa3f679dd8b93a1efb49'},
                       {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0303/gated-successor-001/profile.json',
                        'sha256': '9c15048fe2d135b93c96bc3b5a9ac35d3f3577c1f9d07e4d1f31ef1192393b96'}],
          'history': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0303/gated-successor-001/jobs-001/continuation-plan-draft.json',
                      'sha256': '36b65e18a7f21b7569e3d6f927aefbdef85566a9d12bc65c89e4e7ac6f1d9cca'},
          'host': 'hhu-server-5',
          'inspection': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0303/gated-successor-001/jobs-001/probe-linux-001.json',
                         'sha256': '176eaf3517ca9f4b7a50edb350e08e27982f40ec452f2f04f3b7803ba00c3e83'},
          'job': {'path': '/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/holds-0303/gated-successor-001/jobs-001/worker-job-draft.json',
                  'sha256': '13b2d39e47cfe5b709f28875f577edebd33bfad935e52fcacffb58c331ba86ca'},
          'kind': 'normalization-hold',
          'stage_memory': 8589934592,
          'stage_seconds': 3300,
          'version': '89561d57d839234de37ddd8591c5e4e9f8493bb2a5e7f0d68a55ef20578bb229'}}


def native_bindings(p,job):
    a=checked(p['authority'])
    require(set(a)=={'schema_version','kind','workbook','job','source','tracker','checkpoint','descriptor','inspection','history'},'closed historical authority required')
    require(a['schema_version']=='harbor-fleet-retained-native-authority/v1' and a['job']==p['job']
        and a['workbook']==job['workbook'] and a['source']==job['source'],'native job/source identity differs')
    tracker=checked(a['tracker']);checkpoint=checked(a['checkpoint']);checked(a['descriptor'])
    require(tracker['workbook_id']==job['workbook'] and tracker['bindings']['original_raw_source']==a['source']
        and checkpoint['descriptor']==a['descriptor'],'native tracker/checkpoint/source differs')
    profile=PROFILES.get(job['workbook'])
    require(profile is not None and profile['kind']==a['kind'],'unreviewed historical preparation')
    require(p['host']==profile['host'] and p['job']==profile['job'] and checked(p['job'])==job and a['inspection']==profile['inspection']
        and a['history']==profile['history'],'exact reviewed host/job/probe/recipe required')
    require(job['driver']==profile['driver'] and job['version']==profile['version'],'reviewed driver/code differs')
    for row in profile['evidence']:checked(row)
    require(binding(job['driver']['path'])==job['driver'],'reviewed helper changed')
    return a,tracker,checkpoint,checked(a['inspection']),checked(a['history']),profile


def decoded_budget(value):
    if value['type']=='scalar':return value['value']
    if value['type']=='tuple':return [decoded_budget(x) for x in value['value']]
    if value['type']=='dict':return {decoded_budget(k):decoded_budget(v) for k,v in value['value']}
    if value['type']=='Budget':return {k:decoded_budget(v) for k,v in value['value'].items()}
    raise ValueError('unsupported retained budget encoding')


def normalization_authority(p,job):
    a,tracker,checkpoint,probe,h,profile=native_bindings(p,job)
    w=job['workbook']
    require(w in {'0073','0173','0303','0077'} and h['workbook']==w
        and h['schema_version']==f'harbor-fleet-{w}-normalization-continuation/v1','normalization recipe differs')
    require(h['tracker']==a['tracker'] and h['research']==a['checkpoint']
        and h['stopped_proof']==p['handoff'],'retained normalization predecessor differs')
    unresolved=checked(h['unresolved']);old=checked(h['original_config']);selection=checked(h['normalization_plan'])
    require(unresolved['status']=='UNRESOLVED' and unresolved['step']=='normalization_phase1'
        and unresolved['descriptor']==a['descriptor'] and checkpoint['status']=='STEP_COMPLETE'
        and checkpoint['step']=='research','exact completed research and unresolved normalization required')
    require(probe['status']=='SUPPORTED_EXPIRED_UNSENT_PARTITIONS' and probe['plan']==a['history']
        and probe['models_dispatched']==0 and probe['tracker_modified'] is False
        and probe['semantic_budget_reset'] is False and probe['retained_research']['passed'] is True,'successful unchanged non-model probe required')
    require(probe['snapshot']==h['snapshot'] and probe['original_budget']==selection['budget']
        and selection['budget']['ordinal']==1 and selection['budget']['limit']==2,'original normalization budget differs')
    descriptor=checked(a['descriptor']);worker=descriptor['worker']['value']
    require(decoded_budget(worker['normalization_budget'])==selection['budget'],'owning normalization attempt differs')
    require(old['workbook']==w and old['run_root']==str(Path(a['tracker']['path']).parents[2]),'original admitted config differs')
    snapshot=checked(h['snapshot']);require(snapshot['root']==h['code_root'] and Path(h['code_root']).name==job['version'],'immutable continuation code differs')
    for row in snapshot['files']:
        name=Path(row['relative']);require(not name.is_absolute() and '..' not in name.parts,'snapshot path escape')
        require(binding(Path(h['code_root'])/name)['sha256']==row['sha256'],'immutable owning snapshot changed')
    require(job['command']==[str(Path(job['cwd']).parent/'venv/bin/python'),'-I','-B',job['driver']['path'],
        'run','--plan',a['history']['path'],'--plan-sha256',a['history']['sha256']]
        and job['result']==str(Path(h['home'])/'result.json'),'exact continuation command/output required')
    # 24 hours bounds the multistage worker lifetime, including fleet model
    # waits. Each owning child still has its original 3300-second/8-GiB guard.
    require(job['timeout_seconds']==86400 and profile['stage_seconds']==3300
        and profile['stage_memory']==8*1024**3,'stage/worker lifetime limits differ')
    return a


def dialogue_authority(p,job):
    a,tracker,checkpoint,probe,h,profile=native_bindings(p,job)
    require(job['workbook']==h['workbook']=='0029' and h['schema_version']=='harbor-fleet-0029-pre-review-continuation/v1','exact pre-review recipe required')
    require(all(h[k]==a[k] for k in ('tracker','checkpoint','descriptor')) and h['stopped_proof']==p['handoff']
        and h['linux_probe']==a['inspection'],'retained instruction predecessor differs')
    require(h['source']['sha256']==job['source']['sha256'] and binding(h['source']['path'])==h['source'],'byte-identical retained source required')
    proof=checked(probe['source_proof']);require(proof['bindings']['source']==h['source'],'full source proof detached')
    require(probe['status']=='SOURCE_PREFIX_MECHANICS_VERIFIED' and probe['model_calls']==probe['models_dispatched']==0
        and probe['publication_ready'] is False and probe['task_evidence_mutated'] is False
        and probe['snapshot']==h['snapshot'] and probe['tracker']==a['tracker'] and probe['blocked']==h['blocked']
        and probe['claims']==43 and probe['source_formula_instances']==153 and probe['passed_prefix_checks']==10
        and probe['custody']['semantic_budget_reset'] is False and len(probe['legacy_calls'])==8,'complete source/prefix non-model proof required')
    for row in probe['legacy_calls']:
        require(row['status']=='completed','uncertain legacy model response')
        checked(row['outcome']);checked(row['plan'])
    for k in ('blocked','guard','original_config'):checked(h[k])
    snapshot=checked(h['snapshot']);require(Path(snapshot['root']).name==job['version'],'owning code version differs')
    for row in snapshot['files']:
        name=Path(row['relative']);require(not name.is_absolute() and '..' not in name.parts,'snapshot path escape')
        require(binding(Path(snapshot['root'])/name)['sha256']==row['sha256'],'immutable owning snapshot changed')
    require(job['command']==[h['native_python'],'-I','-B',job['driver']['path'],'run','--plan',a['history']['path'],
        '--sha256',a['history']['sha256'],'--output',str(Path(job['result']).parent)]
        and job['timeout_seconds']==3300 and h['owner']==p['new_owner'],'exact pre-review command/owner/guard required')
    return a


def authority(p,job):
    kind=checked(p['authority'])['kind']
    if kind in {'source-only','existing-review'}:return original_authority(p,job)
    if kind=='normalization-hold':return normalization_authority(p,job)
    if kind=='dialogue-pre-review-0029':return dialogue_authority(p,job)
    raise ValueError('unsupported historical authority')


def load(row):
    p=checked(row)
    require(set(p)=={'schema_version','host','job','authority','handoff','coordinator_receipt','coordinator_process',
        'smoke_verification','smoke_collection','worker','new_owner','remote_receipt','expected_reason'},'closed first-custody plan required')
    require(p['schema_version']==SCHEMA and p['host'] in L.HOSTS,'authorized fleet host required')
    job=checked(p['job']);a=authority(p,job)
    require(job['command'][0]==str(Path(job['cwd']).parent/'venv/bin/python')
        and p['worker']['path']==str(Path(job['cwd'])/'scripts/fleet/worker.py'),'isolated interpreter/runtime worker required')
    maximum=86400 if a['kind'] in {'normalization-hold','dialogue-pre-review-0029'} else 3300
    require(job['memory_limit_bytes']==8*1024**3 and job['minimum_disk_bytes']>=12*1024**3
        and 0<job['timeout_seconds']<=maximum,'original resource guards required')
    require(p['new_owner'].startswith(p['host']+':') and len(p['new_owner'])<256,'explicit host owner required')
    path=Path(p['remote_receipt']);require(path.is_absolute() and '..' not in path.parts and path.is_relative_to(L.REMOTE),'isolated launch receipt required')
    h=checked(p['handoff']);pause=checked(h['pause']);require(h['schema_version']=='local-to-vm-custody-handoff/v1'
        and h['status']=='LOCAL_DISPATCH_PAUSED_HANDOFF_FOR_SUPPORTED_VM_ADMISSION' and pause['new_dispatch_allowed'] is False,'stopped local handoff required')
    item=next(x for x in h['workbooks'] if x['workbook_id']==job['workbook'])
    require({k:item['original_source'][k] for k in ('path','sha256')}==job['source'] and item['status']!='published','handoff source not eligible for continuation')
    for r in job['inputs']:checked(r)
    return p,job


def central_gate(status,p,job):
    require(status['model_limit']==status['publication_limit']==1,'global limits must remain one')
    require(not any(o['state'] in {'active','hold'} for o in status['operations']),'global operation requires reconciliation')
    rows=[w for w in status['workbooks'] if w['workbook']==job['workbook']]
    require(len(rows)==1,'exact central workbook required');w=rows[0]
    require(w['state']=='hold' and w['fence']==0 and w['owner'] is None and w['version'] is None
        and w['source_sha256']==job['source']['sha256'] and w['reason']==p['expected_reason'],'first custody changed; do not retry')
    return w


def local_custody(p):
    receipt=checked(p['coordinator_receipt']);process=checked(p['coordinator_process'])
    require(process['command']==receipt['command'],'coordinator process command differs')
    require(binding(receipt['command'][2])['sha256']==receipt['code_sha256'],'frozen coordinator changed')
    native=checked(p['authority']);original_home=str(Path(native['tracker']['path']).parents[2])
    processes=subprocess.run(['ps','-axo','pid=,command='],capture_output=True,text=True,check=True).stdout.splitlines()
    require(not any(line.strip().split(None,1)[0]!=str(os.getpid()) and original_home in line for line in processes if line.strip()),'local historical task process still active')
    pid=process['pid'];require(type(pid) is int and pid>0,'live coordinator PID required')
    command=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True,check=True).stdout.strip()
    require(shlex.split(command)==receipt['command'],'coordinator process identity changed')
    observed=subprocess.run(['lsof','-a','-p',str(pid),'-Fn','--',*receipt['legacy_locks']],capture_output=True,text=True,check=True).stdout.splitlines()
    require(set(x[1:] for x in observed if x.startswith('n'))==set(receipt['legacy_locks']),'coordinator must retain every legacy lock descriptor')
    inodes=[]
    for name in receipt['legacy_locks']:
        path=safe(name);fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:pass
            else:raise ValueError('legacy lock not held by coordinator')
            stat=os.fstat(fd);inodes.append(dict(path=name,device=stat.st_dev,inode=stat.st_ino))
        finally:os.close(fd)
    return dict(pid=pid,locks=inodes,local_dispatch_paused=True)


def remote_preflight(payload):
    import os,sys,shutil,json
    from pathlib import Path
    p=payload['plan'];job=payload['job'];sys.path.insert(0,job['cwd'])
    from scripts.fleet.migration import checked,binding,safe
    from scripts.fleet.worker import validate
    if checked(p['job'])!=job:raise ValueError('remote job changed')
    validate(job)
    if binding(p['worker']['path'])!=p['worker']:raise ValueError('runtime worker changed')
    a=checked(p['authority'])
    for key in ('tracker','checkpoint','descriptor','history','inspection'):checked(a[key])
    if a['job']!=p['job'] or a['source']!=job['source']:raise ValueError('native remote identity changed')
    if shutil.disk_usage(job['cwd']).free<job['minimum_disk_bytes']:raise ValueError('disk reserve unavailable')
    receipt=safe(p['remote_receipt'])
    unused=[safe(job['run']),safe(job['result']).parent,receipt,receipt.with_suffix('.log'),receipt.with_suffix('.intent.json')]
    if a['kind'] in {'normalization-hold','dialogue-pre-review-0029'}:
        history=checked(a['history']);unused.append(safe(history['home']))
        if a['kind']=='dialogue-pre-review-0029':unused.append(safe(history['takeover_directory']))
        else:
            original=checked(history['original_config'])
            unused.append(safe(Path(original['run_root'])/'lease-takeovers'/('fleet-normalization-'+job['workbook']+'-001')))
    for path in unused:
        if path.exists():raise ValueError('attempt already exists; reconcile')
    # Original tracker/config directory catches historical children as well as
    # the selected output paths; /proc identity is read immediately pre-Popen.
    original_home=Path(a['tracker']['path']).parents[2]
    needles=[p['job']['path'],job['run'],job['result'],str(original_home)]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            if (entry/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':continue
            cmd=(entry/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            if any(n in cmd for n in needles):raise ValueError('original or selected task process active')
        except (FileNotFoundError,ProcessLookupError):continue
    return dict(status='VALIDATED_NOT_STARTED',job=p['job'],authority=p['authority'],active_pids=[],model_calls=0)


def function_source(name):
    source=Path(__file__).read_text();lines=source.splitlines(keepends=True)
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    return ''.join(lines[node.lineno-1:node.end_lineno])


def ssh(p,job,action,payload):
    code=function_source('remote_preflight')+'\n'+L.function_source('remote_spawn')+'\nimport json,sys\np=json.load(sys.stdin)\n'
    code+="remote_preflight(p)\nprint(json.dumps(remote_spawn(p['launch'])))\n" if action=='launch' else 'print(json.dumps(remote_preflight(p)))\n'
    command=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([job['command'][0],'-I','-B','-c',code])]
    result=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError('remote first-custody check/launch failed; reconcile before retry')
    return json.loads(result.stdout)


def launch(row,journal=None,*,execute=False):
    p,job=load(row);receipt=checked(p['coordinator_receipt']);client=Client(receipt['url'],Path(receipt['private_directory'])/'admin.token')
    with lock(Path(p['coordinator_receipt']['path']).parent/'job-launch.lock',blocking=False):
        status=client.call('status');before=central_gate(status,p,job);F.smoke_gate(p,status)
        local=local_custody(p);remote=ssh(p,job,'check',dict(plan=p,job=job))
        if not execute:return dict(status='VALIDATED_NOT_STARTED',central=before,local=local,remote=remote,claims_changed=0,workers_started=0)
        directory=safe(Path(journal).absolute());require(not directory.exists(),'launch journal exists; reconcile without replay');directory.mkdir(parents=True,mode=0o700)
        intent=persist(directory/'intent.json',dict(plan=row,implementation=binding(__file__),before=before,local=local,remote=remote,automatic_retry=False),immutable=True);phase='reconcile'
        try:
            status=client.call('status');central_gate(status,p,job);F.smoke_gate(p,status)
            require(local_custody(p)==local,'local custody changed during preflight')
            ready=client.call('reconcile',workbook=job['workbook'],disposition='ready',evidence=json.dumps(intent,sort_keys=True),checkpoint={'historical_first_custody':p['authority']})
            require(ready['state']=='ready' and ready['fence']==0 and ready['owner'] is None,'uncertain ready result')
            persist(directory/'ready.json',ready,immutable=True);phase='launch'
            command=[job['command'][0],'-I','-B',p['worker']['path'],'--job',p['job']['path'],'--job-sha256',p['job']['sha256'],'--url','http://127.0.0.1:8765','--token-file','/home/gmahajan_labelbox_com/.config/harbor-fleet/worker.token','--owner',p['new_owner']]
            result=ssh(p,job,'launch',dict(plan=p,job=job,launch=dict(receipt=p['remote_receipt'],command=command,job=p['job'],root_intent=intent,cwd=job['cwd'])))
            return persist(directory/'launched.json',dict(intent=intent,remote=result,automatic_retry=False),immutable=True)
        except Exception as exc:
            persist(directory/'uncertain.json',dict(intent=intent,phase=phase,status='RECONCILIATION_REQUIRED',failure_type=type(exc).__name__,automatic_retry=False),immutable=True);raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);p.add_argument('--journal');p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute and not a.journal:p.error('--execute requires fresh journal')
    print(json.dumps(launch(dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256),a.journal,execute=a.execute),indent=2))
if __name__=='__main__':main()
