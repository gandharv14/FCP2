"""Exact retained0029 namespace certificate; never fresh admission authority.

Historical pre-audit topology is read as captured history, while complete current
central histories and all eight subsequent Calls remain explicit. No alias or
absence is fabricated on the remote filesystem.
"""
import hashlib,json,os,time
from pathlib import Path
SCHEMA='fleet-0029-retained-namespace-history/v1'
WORKBOOK='0029'
SOURCE='55029f5834ed624c303b4d7f00adb9bf0ad88c1ff10856e174e44cc7ae1f9509'
ROOT_CODE='2d3e15a8e7b500635e6960eac14872beb98adcae75de8f1f53f174eeb42ec98c'
PROFILE=None

def require(v,message):
    if not v:raise ValueError(message)
def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)),'regular canonical namespace evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'0029 namespace evidence changed')
    return json.loads(Path(row['path']).read_bytes())
def retain(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
    with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(p)
def _context(config):
    from scripts import harbor_campaign as C
    cfg=checked(config);intake=C.decode(checked(cfg['intake']));policy=intake.bootstrap.policy
    inv=C.admission.read_inventory(policy.original_inventory['path']);known={r['workbook_id'] for r in inv['sources']}
    candidate=checked(intake.bootstrap.candidate_manifest);rows=[r for r in candidate['selected_candidates'] if r['original']['workbook_id']=='0029'];require(len(rows)==1,'0029 exact candidate required')
    proof=rows[0]['pre_audit_history'];require(proof in policy.campaign_evidence,'0029 original history authority detached')
    return cfg,intake,policy,known,proof

def capture(config,central,handoff,successor,destination):
    require(not any(k.startswith('HARBOR_FLEET_') for k in os.environ),'0029 capture must observe real central filesystem')
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_pre_audit_history as H
    cfg,intake,policy,known,proof=_context(config);home=Path(cfg['run_root']);source=checked(binding(home/'bootstrap/admission.json'))['tracker']['bindings']['original_raw_source']
    require(source['sha256']==SOURCE and cfg['workbook']=='0029','0029 retained source required')
    value=H.validate(proof,workbook='0029',original=source,history_roots=policy.history_roots,known_ids=known)
    base=checked(central);hand=checked(handoff);require(checked(hand['pause'])['new_dispatch_allowed'] is False and base['known_ids']==sorted(known),'0029 complete paused inventory required')
    roots=list(map(str,policy.history_roots));require(set(roots)<=set(base['scan_roots']),'0029 central namespace coverage incomplete')
    excluded,histories=C.admission.survey_namespaces(tuple(map(Path,roots)),known)
    full_excluded,full_histories=C.admission.survey_namespaces(tuple(map(Path,base['scan_roots'])),known)
    require(full_histories.get('0029'),'0029 current history omitted')
    profile=checked(checked(successor)['profile']);require(len(profile['legacy_calls'])==8,'0029 eight consumed Calls required')
    from scripts.fleet import dialogue_0029_proof as P
    P.verified_code(Path(checked(successor)['root']))
    for row in profile['legacy_calls'].values():
        for b in row.values():binding(b['path'])==b or require(False,'0029 retained Call changed')
        require(checked(row['outcome'])['status']=='completed','0029 unknown response cannot migrate')
    return retain(destination,dict(schema_version=SCHEMA,workbook='0029',source=source,original_config=config,original_history_proof=proof,
        original_admission=binding(home/'bootstrap/admission.json'),original_inventory=policy.original_inventory,successor=successor,
        original_tracker=binding(home/'trackers/workbooks/0029.json'),retained_legacy_calls=profile['legacy_calls'],
        history_roots=roots,known_ids=sorted(known),historical_aliases=value['historical_path_aliases'],central=central,handoff=handoff,
        excluded=excluded,histories=histories,scan_roots=base['scan_roots'],central_excluded=full_excluded,central_histories=full_histories,
        captured_at_ns=time.time_ns(),fresh_admission_allowed=False,model_history_route='eight_completed_calls_retained_no_reset',historical_replay_allowed=False))

def certificate():
    require(PROFILE is not None,'0029 namespace certificate not configured');h=checked(PROFILE)
    fields={'schema_version','workbook','source','original_config','original_history_proof','original_admission','original_inventory','successor','original_tracker','retained_legacy_calls','history_roots','known_ids','historical_aliases','central','handoff','excluded','histories','scan_roots','central_excluded','central_histories','captured_at_ns','fresh_admission_allowed','model_history_route','historical_replay_allowed'}
    require(set(h)==fields and h['schema_version']==SCHEMA and h['workbook']==WORKBOOK and h['source']['sha256']==SOURCE and h['fresh_admission_allowed'] is False and h['historical_replay_allowed'] is False and h['model_history_route']=='eight_completed_calls_retained_no_reset','0029 typed retained history differs')
    cfg,intake,policy,known,proof=_context(h['original_config'])
    require(proof==h['original_history_proof'] and policy.original_inventory==h['original_inventory'] and h['history_roots']==list(map(str,policy.history_roots)) and h['known_ids']==sorted(known),'0029 original history context differs')
    require(checked(h['original_admission'])['tracker']['bindings']['original_raw_source']==h['source'] and binding(h['source']['path'])==h['source'],'0029 original source/admission changed')
    base=checked(h['central']);hand=checked(h['handoff']);require(checked(hand['pause'])['new_dispatch_allowed'] is False,'0029 local handoff changed')
    require(h['known_ids']==base['known_ids'] and h['scan_roots']==base['scan_roots'] and set(h['history_roots'])<=set(h['scan_roots']) and h['central_histories'].get('0029'),'0029 complete central history missing')
    old=checked(proof);require(old['workbook']=='0029' and old['original']==h['source'] and old['history_roots']==h['history_roots'] and old['known_ids']==h['known_ids'] and old['historical_path_aliases']==h['historical_aliases'],'0029 historical proof changed')
    # Verify the exact canonical records named by the captured proof. Lexical
    # old aliases remain explicit metadata; no remote link or whole-tree claim.
    records=[old['original'],old['tracker'],old['driver'],*old['canonical_curations'],*old['diagnostics'],*old['related_history']]
    for b in records:require(binding(b['path'])==b,'0029 historical canonical closure changed')
    require(checked(old['tracker'])==old['historical_tracker_value'],'0029 original tracker value changed')
    require(old['model_history_route']=='pre_audit_not_entered' and old['local_attempt_history']=='retained_without_reset_or_new_retry_allowance','0029 original pre-audit boundary changed')
    s=checked(h['successor']);root=Path(s['root']);require(root.name==ROOT_CODE,'0029 selected successor differs')
    from scripts.fleet import dialogue_0029_proof as P
    P.verified_code(root);p=P.profile();require(h['retained_legacy_calls']==p['legacy_calls'] and len(p['legacy_calls'])==8,'0029 later consumed Calls omitted')
    for row in h['retained_legacy_calls'].values():
        for b in row.values():require(binding(b['path'])==b,'0029 completed Call journal changed')
        require(checked(row['outcome'])['status']=='completed','0029 Call uncertain')
    return h,old

def install_execution_guards(root):
    from scripts.batch import harbor_admission as A,harbor_model_executor as E
    def forbidden(*a,**k):raise ValueError('0029 retained namespace cannot authorize fresh admission')
    A.DispatcherAdmission._check=forbidden
    if os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1':
        def readonly(*a,**k):raise ValueError('0029 namespace read-only probe cannot execute')
        E.execute=E._open=readonly
        return
    from scripts.fleet import dialogue_0029_proof as P
    P.require_fleet_custody(root)

def history_authority(item,intake,inventory,root):
    h,old=certificate();require(Path(root).name==ROOT_CODE and item['workbook_id']=='0029' and intake.bootstrap.workbook=='0029','0029 retained reader scope differs')
    original={k:item['original_source'][k] for k in ('path','sha256')}
    cfg,original_intake,policy,known,proof=_context(h['original_config'])
    require(intake==original_intake and original==h['source'] and {r['workbook_id'] for r in inventory['sources']}==known,'0029 original intake/source/inventory changed')
    require(item['history_class']=='deterministic_rebuild_only' and old['curations']==[{k:r[k] for k in ('path','sha256')} for r in item['preserved_curations']],'0029 queue history/curations changed')
    install_execution_guards(Path(root));return proof

def require_instruction_outcome(job,env):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','0029 read-only setup cannot dispatch')
    require(job['publish'] is False,'0029 adapter cannot bypass serialized publication')
    h,_=certificate();root=env[-2];install_execution_guards(root)
    outcome=checked(job['outcome']);require(outcome['status']=='COMPLETED_OPERATION','0029 uncertain operation cannot continue')
    result=checked(outcome['result']);I=env[1]
    require(result.get('schema_version')==I.SCHEMA and result.get('status') in I.RESUMABLE,'0029 only retained instruction outcome may continue; fresh admission forbidden')
    worker,_,_=I.selected(result['descriptor']);require(worker.dialogue_pre_review_repair is not None and worker.root==root,'0029 exact source-proved permit lineage required')
    I._prior(outcome['result'],result['descriptor'])

def publication_history(plan):
    h,_=certificate()
    require(plan.get('workbook')=='0029' and plan.get('source_sha256')==SOURCE and Path(plan['code_root']).name==ROOT_CODE,'0029 publication identity differs')
    require(not any(k in plan for k in ('history','continuation_history','source_continuation','existing_review_history')) and plan.get('namespace_history')==PROFILE,'0029 historical and fresh authorities cannot mix')
    manifest=checked(plan['namespace_adapter'])
    require(manifest['schema_version']=='fleet-0029-retained-dispatcher/v1' and manifest['namespace_certificate']==PROFILE and manifest['namespace_module']==binding(__file__),'0029 publication adapter changed')
    for key in ('namespace_module','dispatcher','publication_driver','original_dispatcher','original_publication_driver','original_runner'):
        require(binding(manifest[key]['path'])==manifest[key],'0029 adapter code bytes changed')
    ready=checked(plan['ready']);require(ready['workbook']=='0029' and ready['status']=='PUBLICATION_READY' and ready['publication_performed'] is False,'0029 actual publication boundary required')
    job=checked(ready['job']);require(job['driver']==manifest['dispatcher'] and job['config']==ready['config'] and job['publish'] is False,'0029 selected dispatcher/publication config differs')
    config=checked(job['config']);snapshot=checked(config['code']);require(snapshot['root']==plan['code_root'],'0029 publication code differs')
    return PROFILE

def publication_live(plan):
    publication_history(plan)
    require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY')=='1' and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL') and not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'0029 live publication-only custody required')
    require(os.environ.get('HARBOR_FLEET_0029_NAMESPACE_PATH')==PROFILE['path'] and os.environ.get('HARBOR_FLEET_0029_NAMESPACE_SHA256')==PROFILE['sha256'],'0029 publication namespace environment differs')
    install_execution_guards(Path(plan['code_root']))
