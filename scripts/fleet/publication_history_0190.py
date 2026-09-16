"""0190 retained admission/history and exact reviewed refresh publication lineage.

The certificate is custody evidence for an already admitted task, never a fresh
admission. This module does not call models, take leases, or publish anything.
It is loaded by a separately selected publication wrapper, not historical code.
"""
from dataclasses import replace
import json,os
from pathlib import Path

SCHEMA='fleet-0190-retained-admission-history/v1'
LINEAGE='fleet-0190-refreshed-publication-lineage/v1'
SOURCE='c6e9acb47959d5bcd2c6abac3f3111932a6e305025f59fe1881a03fe9f4292da'
CODE='c7ad14b03f7c0ec3c6364efbb5e388affd9036ac15d745131ea306602700808f'
HOME=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-queue-extension-20260911T185027Z/authoring/0190-generated-02')
CONFIG_SHA='7abd123e0c2869234dbc76255e514ef632d08edfcd8569b714349899c7dcecf4'


def capture(P,central,handoff,destination):
    from scripts import harbor_campaign as C
    P.require(not any(os.environ.get('HARBOR_FLEET_'+k) for k in ('HISTORY_PATH','CONTINUATION_HISTORY_PATH','EXISTING_REVIEW_HISTORY_PATH')),'capture must observe actual central history')
    config=P.binding(HOME/'config.json');P.require(config['sha256']==CONFIG_SHA,'original0190 config changed')
    old=P.checked(config);worker=C.decode(P.checked(P.binding(HOME/'admission/001/descriptor.json'))['worker'])
    prepared=P.checked(worker.prepared);survey=P.checked(worker.bootstrap.candidate_manifest)['survey'];prior=P.checked(survey)
    admission=P.binding(HOME/'bootstrap/admission.json');admitted=P.checked(admission)
    source=admitted['tracker']['bindings']['original_raw_source'];P.require(source==prepared['validated']['original'] and source['sha256']==SOURCE,'retained source/admission differs')
    P.require(prepared['history']==prior['histories'].get('0190',[]),'retained original survey differs')
    original=worker.bootstrap.policy.original_inventory;known=sorted(r['workbook_id'] for r in C.admission.read_inventory(original['path'])['sources'])
    roots=list(map(str,worker.bootstrap.policy.history_roots));base=P.checked(central);hand=P.checked(handoff);pause=P.checked(hand['pause'])
    P.require(pause['new_dispatch_allowed'] is False and base['known_ids']==known and set(roots)<=set(base['scan_roots']),'complete paused central history required')
    excluded,histories=C.admission.survey_namespaces(tuple(map(Path,roots)),set(known))
    full_excluded,full_histories=C.admission.survey_namespaces(tuple(map(Path,base['scan_roots'])),set(known))
    P.require(full_histories.get('0190'),'current original0190 tracker must remain visible')
    return P.retain(destination,dict(schema_version=SCHEMA,workbook='0190',source=source,original_config=config,original_descriptor=P.binding(HOME/'admission/001/descriptor.json'),
        original_checkpoint=P.binding(HOME/'dispatcher-v2/first/002/result.json'),original_tracker=P.binding(HOME/'trackers/workbooks/0190.json'),prior_prepared=worker.prepared,prior_admission=admission,prior_survey=survey,original_inventory=original,
        central=central,handoff=handoff,roots=roots,known_ids=known,excluded=excluded,histories=histories,scan_roots=base['scan_roots'],central_excluded=full_excluded,central_histories=full_histories,fresh_admission_allowed=False))


def history(P,bound):
    h=P.checked(bound)
    fields={'schema_version','workbook','source','original_config','original_descriptor','original_checkpoint','original_tracker','prior_prepared','prior_admission','prior_survey','original_inventory','central','handoff','roots','known_ids','excluded','histories','scan_roots','central_excluded','central_histories','fresh_admission_allowed'}
    P.require(set(h)==fields and h['schema_version']==SCHEMA and h['workbook']=='0190' and h['fresh_admission_allowed'] is False,'typed0190 continued admission history required')
    P.require(h['source']['sha256']==SOURCE and h['original_config']==dict(path=str(HOME/'config.json'),sha256=CONFIG_SHA)
        and h['original_tracker']['path']==str(HOME/'trackers/workbooks/0190.json'),'original0190 identity changed')
    for key in ('original_config','original_descriptor','original_checkpoint','prior_prepared','prior_admission','prior_survey','original_inventory','central','handoff'):P.checked(h[key])
    prior=P.checked(h['prior_survey']);prepared=P.checked(h['prior_prepared']);admitted=P.checked(h['prior_admission'])
    P.require(prepared['history']==prior['histories'].get('0190',[]) and prepared['validated']['original']==h['source']==admitted['tracker']['bindings']['original_raw_source'],'original admission/survey/source differs')
    from scripts import harbor_campaign as C
    worker=C.decode(P.checked(h['original_descriptor'])['worker'])
    P.require(h['roots']==list(map(str,worker.bootstrap.policy.history_roots)) and h['original_inventory']==worker.bootstrap.policy.original_inventory and h['prior_prepared']==worker.prepared and P.binding(h['source']['path'])==h['source'],'original full source/history policy differs')
    base=P.checked(h['central']);P.require(h['known_ids']==base['known_ids'] and h['scan_roots']==base['scan_roots'] and set(h['roots'])<=set(h['scan_roots']) and h['central_histories'].get('0190'),'full central continued history required')
    P.require(P.checked(P.checked(h['handoff'])['pause'])['new_dispatch_allowed'] is False,'local dispatch handoff changed')
    return h


def validate(P,plan):
    P.require(plan.get('workbook')=='0190' and not any(k in plan for k in ('history','continuation_history','source_continuation')),'0190 legacy history cannot use fresh/source-only authority')
    h=history(P,plan['existing_review_history']);line=P.checked(plan['existing_review_lineage'])
    P.require(set(line)=={'schema_version','history','config','refresh_descriptor','refresh_checkpoint','refresh_plan','code_snapshot','model_calls','fresh_admission'}
        and line['schema_version']==LINEAGE and line['history']==plan['existing_review_history'] and line['model_calls']==0 and line['fresh_admission'] is False,'closed0190 refresh lineage required')
    old=P.checked(h['original_config']);cfg=P.checked(line['config']);snapshot=P.checked(line['code_snapshot']);root=Path(plan['code_root'])
    P.require(root.name==CODE and snapshot['root']==str(root) and cfg['code']==line['code_snapshot'] and plan['source_sha256']==SOURCE,'reviewed0190 code/source differs')
    from scripts import corpus,harbor_campaign as C
    from scripts.batch import harbor_environment_refresh as F
    P.require(C.ROOT==root and corpus.code_hash()==CODE,'clean reviewed0190 publication interpreter required')
    inventory=corpus._frozen_code_files(root)
    P.require(set(snapshot)=={'root','files'} and {r['relative']:r['sha256'] for r in snapshot['files']}=={**inventory,'.pipeline-code.json':P.sha(root/'.pipeline-code.json')},'full reviewed code snapshot differs')
    native=C.decode(cfg['controls']);previous=C.decode(old['controls'])
    P.require(native==replace(previous,env_file=Path('/home/gmahajan_labelbox_com/.config/harbor-fleet/model.env')) and cfg=={**old,'code':line['code_snapshot'],'controls':C.encode(native)},'0190 config changed beyond code/native environment')
    refresh=P.checked(line['refresh_plan']);P.require(refresh['schema_version']=='0190-fleet-existing-review-refresh-plan/v1' and refresh['source']==h['source'] and refresh['original_checkpoint']==h['original_checkpoint'] and refresh['original_tracker']==h['original_tracker'] and refresh['original_descriptor']==h['original_descriptor'] and refresh['models'] is False,'reviewed refresh plan changed')
    for row in refresh['original_completed_call'].values():P.checked(row)
    descriptor=P.checked(line['refresh_descriptor']);result=P.checked(line['refresh_checkpoint'])
    P.require(line['refresh_descriptor']['path']==str(Path(refresh['home'])/'descriptor.json') and line['refresh_checkpoint']['path']==str(Path(refresh['home'])/'result.json'),'exact refresh operation namespace required')
    worker,lease,controls=C.selected(line['refresh_descriptor']);original=C.decode(P.checked(h['original_descriptor'])['worker'])
    P.require(worker==original and controls==native and descriptor['schema_version']=='harbor-environment-coordinator-refresh/v1','refreshed worker/budget/control identity changed')
    proof,selection=F.validate(worker,descriptor['refresh']);committed=P.checked(P.binding(Path(descriptor['refresh']['path']).parent/'committed.json'))
    P.require(committed==dict(prepared=descriptor['refresh'],tracker_sha256=lease.sha256,revision=lease.revision) and lease.revision==P.checked(proof['before'])['revision']+1,'owning refresh commit/lease differs')
    P.require(result['status']=='IMPORTED' and result['step']=='normalization_phase1' and result['descriptor']==line['refresh_descriptor'] and result['lease']==descriptor['initial_lease'] and result['selection']==C.content_codec.encode(C.content_codec.decode(proof['selection']))
        and result['model_calls']==0 and result['semantic_budget_reset'] is False and result['emission_replayed'] is False and result['publication_ready'] is False,'retained refreshed checkpoint changed')
    P.require(h['original_descriptor'] in descriptor['origin'] and h['original_checkpoint'] in descriptor['origin'] and descriptor['refresh'] in result['evidence'] and P.binding(Path(descriptor['refresh']['path']).parent/'committed.json') in result['evidence'],'refresh lineage evidence omitted')
    return h,line


def live(P,plan):
    h,line=validate(P,plan)
    P.require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY')=='1' and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL') and not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'live publication-only custody required')
    P.require(os.environ.get('HARBOR_FLEET_WORKBOOK')=='0190' and os.environ.get('HARBOR_FLEET_EXISTING_REVIEW_HISTORY_PATH')==plan['existing_review_history']['path'] and os.environ.get('HARBOR_FLEET_EXISTING_REVIEW_HISTORY_SHA256')==plan['existing_review_history']['sha256'],'0190 typed publication environment differs')
    from scripts.fleet.coordinator import Client
    c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE'])
    state=c.call('heartbeat',workbook='0190',owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
    P.require(state['state']=='active' and state['source_sha256']==SOURCE,'0190 source custody changed')
    # An existing task's publication must never exercise a new admission path.
    from scripts.batch import harbor_admission as A
    def forbidden(*args,**kwargs):raise ValueError('fresh admission forbidden during0190 publication')
    A.DispatcherAdmission._check=forbidden
    return line


def prepare_lineage(P, history_binding, refresh_plan, destination):
    """Retain prospective config only from a real completed owning refresh."""
    h=history(P,history_binding);plan=P.checked(refresh_plan)
    descriptor=P.binding(Path(plan['home'])/'descriptor.json');checkpoint=P.binding(Path(plan['home'])/'result.json')
    result=P.checked(checkpoint)
    P.require(result['status']=='IMPORTED' and result['model_calls']==0 and result['semantic_budget_reset'] is False and result['emission_replayed'] is False,'completed original-response refresh required')
    meta=P.checked(plan['snapshot']);root=Path(meta['root']);P.require(root.name==CODE,'reviewed corrected0190 successor required')
    from scripts import corpus,harbor_campaign as C
    P.require(C.ROOT==root and corpus.code_hash()==CODE,'clean corrected0190 interpreter required')
    worker,lease,controls=C.selected(descriptor);old=P.checked(h['original_config'])
    destination=P.safe(destination);P.require(not destination.exists(),'lineage preparation already attempted; reconcile')
    destination.mkdir(parents=True)
    code=P.retain(destination/'code.json',dict(root=str(root),files=[dict(relative=str(p.relative_to(root)),sha256=P.sha(p)) for p in sorted(root.rglob('*')) if p.is_file() and not {'.git','__pycache__','.pytest_cache'} & set(p.relative_to(root).parts)]))
    config=P.retain(destination/'config.json',{**old,'code':code,'controls':C.encode(controls)})
    lineage=P.retain(destination/'lineage.json',dict(schema_version=LINEAGE,history=history_binding,config=config,refresh_descriptor=descriptor,refresh_checkpoint=checkpoint,refresh_plan=refresh_plan,code_snapshot=code,model_calls=0,fresh_admission=False))
    validate(P,dict(workbook='0190',code_root=str(root),source_sha256=SOURCE,existing_review_history=history_binding,existing_review_lineage=lineage))
    return lineage
