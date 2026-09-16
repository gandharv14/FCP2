"""Closed retained-admission history projection; never fresh admission authority.

Both original survey/admission and complete current required/global surveys are
retained. A selected current history row is never removed to authorize a fresh
tracker. Read-only proof inspection is distinct from live fenced continuation.
"""
import argparse,json,os,time
from pathlib import Path
from scripts.fleet.migration import binding,checked,retain
SCHEMA='fleet-source-continuation-history/v1'


def capture(config,central,pause,destination):
    if os.environ.get('HARBOR_FLEET_HISTORY_PATH') or os.environ.get('HARBOR_FLEET_CONTINUATION_HISTORY_PATH'):raise ValueError('capture must observe actual local history')
    from scripts import harbor_campaign as C
    from scripts.batch import harbor_source_only_continuation as M
    cfg=checked(config);home=Path(cfg['run_root']);descriptor=binding(home/'admission-001/descriptor.json');checkpoint=binding(home/'dispatcher-v2/first/000/result.json');tracker=binding(home/'trackers/workbooks'/(cfg['workbook']+'.json'))
    _,old,saved,validated=M.inspect(config,descriptor,checkpoint,tracker)
    original_survey=checked(old.bootstrap.candidate_manifest)['survey'];survey=checked(original_survey)
    admission=binding(home/'bootstrap/admission.json');admitted=checked(admission)
    if admitted['tracker']['bindings']['original_raw_source']!=validated['original'] or saved['history']!=survey['histories'].get(cfg['workbook'],[]):raise ValueError('original admission/survey boundary differs')
    central_value=checked(central);pause_value=checked(pause)
    if pause_value.get('new_dispatch_allowed') is not False:raise ValueError('local dispatch custody not paused')
    roots=list(map(str,old.bootstrap.policy.history_roots));known_ids=sorted(r['workbook_id'] for r in C.admission.read_inventory(old.bootstrap.policy.original_inventory['path'])['sources'])
    if central_value['known_ids']!=known_ids or not set(roots)<=set(central_value['scan_roots']):raise ValueError('complete known history scope required')
    excluded,histories=C.admission.survey_namespaces(tuple(map(Path,roots)),set(known_ids))
    all_excluded,all_histories=C.admission.survey_namespaces(tuple(map(Path,central_value['scan_roots'])),set(known_ids))
    return retain(destination,dict(schema_version=SCHEMA,workbook=cfg['workbook'],source=validated['original'],config=config,descriptor=descriptor,checkpoint=checkpoint,tracker=tracker,prior_prepared=old.prepared,prior_admission=admission,prior_survey=original_survey,original_inventory=old.bootstrap.policy.original_inventory,roots=roots,known_ids=known_ids,excluded=excluded,histories=histories,central_predecessor=central,scan_roots=central_value['scan_roots'],central_excluded=all_excluded,central_histories=all_histories,pause=pause,captured_at_ns=time.time_ns(),fresh_admission_allowed=False))


def validate(bound,roots,known_ids,workbook):
    value=checked(bound)
    fields={'schema_version','workbook','source','config','descriptor','checkpoint','tracker','prior_prepared','prior_admission','prior_survey','original_inventory','roots','known_ids','excluded','histories','central_predecessor','scan_roots','central_excluded','central_histories','pause','captured_at_ns','fresh_admission_allowed'}
    if set(value)!=fields or value['schema_version']!=SCHEMA or value['fresh_admission_allowed'] is not False:raise ValueError('unsupported continuation history')
    if value['workbook']!=workbook or value['roots']!=list(map(str,roots)) or value['known_ids']!=sorted(known_ids) or not set(value['roots'])<=set(value['scan_roots']):raise ValueError('continued history scope differs')
    cfg=checked(value['config']);home=Path(cfg['run_root'])
    for key in ('descriptor','checkpoint','prior_prepared','prior_admission','prior_survey','original_inventory','central_predecessor','pause'):checked(value[key])
    if value['tracker']['path']!=str(home/'trackers/workbooks'/(workbook+'.json')) or cfg['workbook']!=workbook:raise ValueError('continued tracker identity differs')
    original=checked(value['prior_admission'])['tracker']['bindings']['original_raw_source']
    prepared=checked(value['prior_prepared']);prior=checked(value['prior_survey'])
    if original!=value['source'] or prepared['validated']['original']!=original or prepared['history']!=prior['histories'].get(workbook,[]):raise ValueError('retained admission history differs')
    if checked(value['pause']).get('new_dispatch_allowed') is not False:raise ValueError('local dispatch pause differs')
    # Full central state stays visible, including this task's retained tracker.
    if not value['central_histories'].get(workbook):raise ValueError('continued tracker omitted from full central history')
    return value


def projected_survey(roots,known_ids):
    bound={'path':os.environ['HARBOR_FLEET_CONTINUATION_HISTORY_PATH'],'sha256':os.environ['HARBOR_FLEET_CONTINUATION_HISTORY_SHA256']}
    workbook=os.environ['HARBOR_FLEET_WORKBOOK'];value=validate(bound,roots,known_ids,workbook)
    if os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1':
        from scripts.fleet.coordinator import Client
        client=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE'])
        claim=client.call('heartbeat',workbook=workbook,owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
        if claim['source_sha256']!=value['source']['sha256']:raise ValueError('continued source custody differs')
    return value['excluded'],value['histories']


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('config','central','pause','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();print(json.dumps(capture(binding(a.config.absolute()),binding(a.central.absolute()),binding(a.pause.absolute()),a.output.absolute())))

if __name__=='__main__':main()
