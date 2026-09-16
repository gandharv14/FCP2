"""Explicit zero-model 0190 retained-Call continuation through owning readers.

inspect revalidates original response, financial evidence, current prefix and
expired takeover recipe without changing a tracker. refresh requires a live
central fenced claim; it replaces the expired lease once and commits the
existing phase1 evidence. It never authors or publishes and never re-emits.
An interrupted transaction is retained and is not retried by this helper.
"""
from pathlib import Path
import argparse
import dataclasses
import hashlib
import json
import os
import sys
import urllib.request

BASE=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-queue-extension-20260911T185027Z')
HOME=BASE/'authoring/0190-generated-02'
SNAPSHOT={'path':str(BASE/'0190-existing-review-snapshot-v5.json'),'sha256':'e49370d9751610c3b9b9c4f5afa1d4898f36049e8a6175eae64ad8c5bf4d19f3'}
INPUTS={'path':str(BASE/'0190-indexed-review-repair/authority-inputs-v4.json'),'sha256':'4de4e3040b941072c9157df715b41e373690ebfb4d6b0bc55bf53916f7da1dfd'}
PRIOR={'path':str(HOME/'dispatcher-v2/first/002/result.json'),'sha256':'cbe539618dfbb1323089a662851ca051fbbc18f4c4c2d6a7ed80991c7b1ca436'}
DESCRIPTOR={'path':str(HOME/'admission/001/descriptor.json'),'sha256':'c334fd1efdcd1d57b36d486a159662c387f4d894c6149d08f545ef6ddcebf56b'}
TRACKER={'path':str(HOME/'trackers/workbooks/0190.json'),'sha256':'135544ec3a9714bc47e214fedf29048c8c9bc05ef7e1a8f017dc91eec7be4480'}
SOURCE_SHA='c6e9acb47959d5bcd2c6abac3f3111932a6e305025f59fe1881a03fe9f4292da'


def record(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('symlink evidence forbidden')
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def checked(bound):
    if record(bound['path'])!=bound:raise ValueError('exact retained evidence changed')
    return json.loads(Path(bound['path']).read_text())


def gate():
    """No retry; lost coordinator response stops all dependent mutations."""
    import urllib.parse
    prefix='HARBOR_FLEET_'
    values={k:os.environ[prefix+k] for k in ('URL','TOKEN_FILE','WORKBOOK','OWNER','CLAIM_TOKEN','FENCE')}
    url=urllib.parse.urlparse(values['URL'])
    if values['WORKBOOK']!='0190' or url.scheme!='http' or url.hostname not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('0190 fenced loopback claim required')
    args={'workbook':'0190','owner':values['OWNER'],'token':values['CLAIM_TOKEN'],'fence':int(values['FENCE']),'lease_seconds':180}
    req=urllib.request.Request(values['URL'].rstrip('/')+'/v1',data=json.dumps({'action':'heartbeat','arguments':args}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+Path(values['TOKEN_FILE']).read_text().strip()})
    with urllib.request.urlopen(req,timeout=30) as response:state=json.load(response)
    if state['state']!='active' or state['source_sha256']!=SOURCE_SHA:raise ValueError('live original-source custody required')
    return values['OWNER']


def load():
    snapshot=checked(SNAPSHOT);root=Path(snapshot['root'])
    # Validate every file before importing the reviewed successor. No old file
    # is patched and the original source/model proof is checked again below.
    inventory=checked(record(root/'.pipeline-code.json'))
    if hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()!=root.name:
        raise ValueError('reviewed snapshot identity changed')
    for name,digest in inventory.items():checked_file=record(root/name);assert checked_file['sha256']==digest,'reviewed code bytes changed'
    sys.path.insert(0,str(root))
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_normalization_existing_review as M,harbor_environment_refresh as F,harbor_lease_takeover as T
    if Path(C.__file__).resolve()!=root/'scripts/harbor_campaign.py' or M.ROOT!=root:raise ValueError('clean interpreter required')
    def forbidden(*args,**kwargs):raise RuntimeError('model dispatch forbidden in existing-call reconciliation')
    C.executor.execute=forbidden
    M.E.execute=forbidden
    inputs=checked(INPUTS);proof=M.validate(record(HOME/'environment/normalization-existing-review-v1/0190-variable-sources/existing-review.json'))
    if proof['original'].budget.ordinal!=1 or proof['original'].budget.limit!=2:raise ValueError('original review cap changed')
    s=C.decode(checked(DESCRIPTOR)['worker']);controls=C.decode(checked(DESCRIPTOR)['controls'])
    original=C.content_codec.decode(checked(PRIOR)['selection'])
    run=proof['run'];value=checked(record(run/'phase1-result.json'))
    if value['status']!='phase1_evidence_ready':raise ValueError('existing normalization has not qualified')
    bindings={**original.content.bindings,'normalization_existing_review':record(run/'existing-review.json'),'normalization_draft':record(run/'draft.json'),'normalizer':record(run/'normalize_0190.py'),'normalization_prepared_manifest':inputs['prepared'],'normalization_prepared_request':checked(inputs['prepared'])['request'],'normalized_phase1':value['phase1'],'normalized_final':value['outputs']['normalized.json'],'normalization_report':value['outputs']['normalization_report.json'],'normalization_exclusions':value['outputs']['exclusions.json']}
    current=dataclasses.replace(original,content=dataclasses.replace(original.content,bindings=bindings,normalization_run=run,normalization_prepared=inputs['prepared'],normalization_call=M.N.Call(**inputs['call'])))
    if not F.unchanged(original,current):raise ValueError('existing-review delegation missing')
    prior_selection,prior_kind,prior_checkpoint=F.predecessor(s,PRIOR,current=current)
    if proof['original'].budget!=s.normalization_budget:raise ValueError('worker original budget differs')
    if C.read(record(s.run/'selection.json'))['selection']!=C.worker.bootstrap.serial(dataclasses.asdict(s)):raise ValueError('retained worker recipe changed')
    return root,C,M,F,T,s,controls,proof,current


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['inspect','refresh']);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--env-file',type=Path)
    p.add_argument('--check-runtime',action='store_true')
    a=p.parse_args();out=a.output.absolute()
    if out.exists():raise ValueError('operation exists; reconcile without replay')
    owner=gate() if a.action=='refresh' else 'fleet-0190-recovery'
    root,C,M,F,T,s,controls,proof,current=load()
    takeover=T.Selection(campaign_root=HOME,workbook='0190',tracker=TRACKER,worker_kind='environment',guard=record(HOME/'dispatcher-v2/first/003/resource.json'),checkpoint=PRIOR,owner=owner,config=record(HOME/'config.json'))
    before,old,run,evidence=T._recipe(takeover)
    checks=F.checks(s,before,current,'normalization_phase1')
    if any(not row['passed'] for row in checks):raise ValueError('owning prefix rejected')
    out.mkdir(parents=True)
    inspection=C.keep(out/'inspection.json',{'status':'EXISTING_REVIEW_VALIDATED','snapshot':SNAPSHOT,'inputs':INPUTS,'tracker':TRACKER,'source_sha256':SOURCE_SHA,'prepared':proof['prepared'],'receipt':record(proof['run']/'existing-review.json'),'phase1_result':record(proof['run']/'phase1-result.json'),'prefix_checks':checks,'takeover_selection':T._selection_value(takeover),'original_review_ordinal':1,'original_review_limit':2,'unused_correction_rounds':1,'same_review_must_not_replay':True,'model_calls':0,'next_owning_step':'source_profile','publication_ready':False})
    if a.action=='inspect':
        if a.check_runtime:C._dependency(s,out)
        print(json.dumps(inspection));return
    if a.env_file is None:raise ValueError('native runtime environment file required before mutation')
    controls=dataclasses.replace(controls,env_file=a.env_file.absolute());C._controls(controls)
    dependency=C._dependency(s,out)
    gate()
    lease,receipt=T.replace_expired(takeover,HOME/'lease-takeovers/fleet-existing-review-001')
    # The owning refresh validates quality then performs its own exact CAS.
    prepared=F.prepare(s,lease,PRIOR,current,'normalization_phase1',out/'refresh')
    gate()
    lease,current,committed=F.commit(s,lease,prepared)
    descriptor=C._descriptor(s,lease,controls,[DESCRIPTOR,PRIOR,INPUTS,inspection,dependency,receipt,prepared,committed]);descriptor.update(schema_version='harbor-environment-coordinator-refresh/v1',refresh=prepared)
    bound=C.keep(out/'descriptor.json',descriptor)
    result=C.keep(out/'result.json',{'status':'IMPORTED','step':'normalization_phase1','descriptor':bound,'selection':C.content_codec.encode(current),'lease':C.encode(lease),'evidence':[inspection,dependency,receipt,prepared,committed],'model_calls':0,'semantic_budget_reset':False,'emission_replayed':False,'publication_ready':False,'next_step_requires_fleet_model_gate':True})
    print(json.dumps(result))

if __name__=='__main__':main()
