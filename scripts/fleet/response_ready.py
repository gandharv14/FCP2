"""External READY custody adapter. Preserves current history and consumed budgets."""
from __future__ import annotations
import argparse, importlib.util, json, os, sys, time
from pathlib import Path


def helper(bound):
    import hashlib
    p=Path(bound['path'])
    if hashlib.sha256(p.read_bytes()).hexdigest()!=bound['sha256']: raise ValueError('response helper changed')
    spec=importlib.util.spec_from_file_location('ready_selected_response',p); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def validate(bound, *, ready=None, live=False):
    value=json.loads(Path(bound['path']).read_text()); R=helper(value['response_helper']); R.checked(bound)
    fields={'schema_version','helper','response_helper','code_root','workbook','source','ready','config','tracker','descriptor','checkpoint','budgets','central_predecessor','roots','known_ids','excluded','histories','scan_roots','central_excluded','central_histories','author_outcome','captured_at_ns','fresh_admission_allowed'}
    R.require(set(value)==fields and value['schema_version']=='harbor-ready-response-custody/v1'
      and value['helper']==R.binding(__file__) and value['fresh_admission_allowed'] is False,'closed current READY custody required')
    actual,job,D,env,checkpoint,request=R.owning_ready(value['ready'],value['code_root'])
    wb=actual['workbook']; cfg=env[5]; home=Path(cfg['run_root'])
    R.require(ready is None or ready==value['ready'],'selected READY differs')
    R.require(value['workbook']==wb and value['config']==actual['config'] and value['checkpoint']==actual['checkpoint']
      and value['descriptor']==checkpoint['descriptor'] and value['source']==request.selection.content.bindings['original_raw_source'],'current READY source/selection differs')
    R.require(value['tracker']==R.binding(home/'trackers/workbooks'/(wb+'.json')),'current owning tracker changed')
    R.checked(value['tracker']); R.checked(value['descriptor']); predecessor=R.checked(value['central_predecessor'])
    R.require(predecessor.get('schema_version')=='fleet-central-history/v1' and predecessor['roots']==value['roots'] and predecessor['scan_roots']==value['scan_roots'] and predecessor['known_ids']==value['known_ids'],'original complete history scope differs')
    outcome=R.checked(value['author_outcome']); R.require(outcome['status']=='PUBLICATION_READY' and outcome['returncode']==0 and outcome['result']==value['ready'] and outcome['failure_type'] is None and outcome['automatic_retry'] is False,'author did not finish successfully')
    R.require(value['budgets']==budget_files(R,home),'retained author budget/attempt evidence changed')
    R.require(set(value['roots'])<=set(value['scan_roots']) and wb in value['known_ids'] and value['histories'].get(wb)
      and value['tracker'] in value['histories'][wb] and value['tracker'] in value['central_histories'].get(wb,[]),'current tracker omitted from full history')
    if live:
        R.require(not os.environ.get('HARBOR_FLEET_HISTORY_PATH') and os.environ.get('HARBOR_FLEET_CONTINUATION_HISTORY_PATH')==bound['path']
          and os.environ.get('HARBOR_FLEET_CONTINUATION_HISTORY_SHA256')==bound['sha256'] and os.environ.get('HARBOR_FLEET_WORKBOOK')==wb,'exclusive current continuation history required')
        from scripts.fleet.coordinator import Client
        claim=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=wb,owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
        R.require(claim['source_sha256']==value['source']['sha256'] and claim['version']==Path(value['code_root']).name,'current READY claim differs')
    return value


def budget_files(R,home):
    return [R.binding(p) for p in sorted(home.rglob('*')) if p.is_file() and not p.is_relative_to(home/'response-review-final') and any('budget' in part or part=='attempts' for part in p.relative_to(home).parts)]


def capture(plan_binding):
    p=json.loads(Path(plan_binding['path']).read_text());R=helper(p['response_helper']);R.checked(plan_binding)
    R.require(set(p)=={'schema_version','helper','response_helper','code_root','ready','central_predecessor','author_outcome','output'}
      and p['schema_version']=='harbor-ready-response-capture/v1' and p['helper']==R.binding(__file__),'closed capture plan required')
    R.require(not any(os.environ.get(k) for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH')),'capture requires unprojected actual history')
    ready,job,D,env,checkpoint,request=R.owning_ready(p['ready'],p['code_root']);wb=ready['workbook'];home=Path(env[5]['run_root'])
    original=R.checked(p['central_predecessor']);roots=original['roots'];known=original['known_ids'];scans=original['scan_roots']
    from scripts import harbor_campaign as C
    excluded,histories=C.admission.survey_namespaces(tuple(map(Path,roots)),set(known))
    all_excluded,all_histories=C.admission.survey_namespaces(tuple(map(Path,scans)),set(known))
    value=dict(schema_version='harbor-ready-response-custody/v1',helper=p['helper'],response_helper=p['response_helper'],code_root=p['code_root'],workbook=wb,
      source=request.selection.content.bindings['original_raw_source'],ready=p['ready'],config=ready['config'],tracker=R.binding(home/'trackers/workbooks'/(wb+'.json')),
      descriptor=checkpoint['descriptor'],checkpoint=ready['checkpoint'],budgets=budget_files(R,home),central_predecessor=p['central_predecessor'],roots=roots,known_ids=known,
      excluded=excluded,histories=histories,scan_roots=scans,central_excluded=all_excluded,central_histories=all_histories,author_outcome=p['author_outcome'],captured_at_ns=time.time_ns(),fresh_admission_allowed=False)
    bound=R.retain(p['output'],value);validate(bound);return bound


def review(plan_binding):
    p=json.loads(Path(plan_binding['path']).read_text());R=helper(p['response_helper']);R.checked(plan_binding)
    R.require(set(p)=={'schema_version','helper','response_helper','review_plan','continuation_history'} and p['schema_version']=='harbor-current-response-review/v1' and p['helper']==R.binding(__file__),'closed current response review plan required')
    selected=R.checked(p['review_plan']);validate(p['continuation_history'],ready=selected['ready'],live=True)
    return R.review(p['review_plan'])



def prepare_review(plan_binding):
    p=json.loads(Path(plan_binding['path']).read_text());R=helper(p['response_helper']);R.checked(plan_binding)
    R.require(set(p)=={'schema_version','helper','response_helper','continuation_history','authorization','guidance_index','python','output'}
      and p['schema_version']=='harbor-current-response-review-preparation/v1' and p['helper']==R.binding(__file__),'closed review preparation required')
    value=validate(p['continuation_history']);ready=R.checked(value['ready']);home=Path(ready['config']['path']).parent
    directory=Path(p['output']);R.require(directory==home/'response-review-orchestration' and not directory.exists(),'one canonical review orchestration required')
    outcome=R.checked(value['author_outcome']);intent=R.checked(outcome['intent']);author=R.checked(intent['job'])
    R.require(author['workbook']==value['workbook'] and author['source']==value['source'] and author['cwd']==value['code_root'] and author['result']==value['ready']['path'],'actual author job lineage differs')
    review_plan=R.retain(directory/'review.json',dict(schema_version='harbor-response-review-worker/v1',helper=p['response_helper'],code_root=value['code_root'],ready=value['ready'],guidance_index=p['guidance_index'],authorization=p['authorization'],directory=str(home/'response-review-final')))
    wrapper=R.retain(directory/'wrapper.json',dict(schema_version='harbor-current-response-review/v1',helper=p['helper'],response_helper=p['response_helper'],review_plan=review_plan,continuation_history=p['continuation_history']))
    worker=dict(author)
    for key in ('history','continuation_history','heartbeat_reconciliation'):worker.pop(key,None)
    worker.update(driver=p['helper'],inputs=[wrapper,review_plan,p['continuation_history'],p['response_helper'],p['authorization'],p['guidance_index']],
      command=[p['python'],'-I','-B',p['helper']['path'],'review','--plan',wrapper['path'],'--plan-sha256',wrapper['sha256']],
      run=str(home/'response-review-worker-final'),result=str(home/'response-review-final/result.json'),continuation_history=p['continuation_history'],timeout_seconds=7200)
    from scripts.fleet.worker import validate as validate_worker
    validate_worker(worker)
    wb=R.retain(directory/'worker.json',worker)
    return R.retain(directory/'prepared.json',dict(schema_version='harbor-current-response-review-prepared/v1',plan=plan_binding,job=wb,review_plan=review_plan,wrapper=wrapper,history=p['continuation_history'],ready=value['ready'],models_dispatched=0,publication_performed=False,automatic_retry=False))

def publication_binding(pub,plan):
    pub.require('history' not in plan and 'source_continuation' not in plan and set(plan['ready_continuation'])=={'helper','config'},'closed READY continuation required')
    pub.require(plan['ready_continuation']['helper']==pub.binding(__file__),'READY custody implementation changed')
    value=validate(plan['continuation_history'],ready=plan['ready'])
    pub.require(value['config']==plan['ready_continuation']['config'] and value['code_root']==plan['code_root'] and value['source']['sha256']==plan['source_sha256'] and value['workbook']==plan['workbook'],'publication current custody differs')
    return plan['continuation_history']


def projected_survey(roots,known_ids):
    bound={'path':os.environ['HARBOR_FLEET_CONTINUATION_HISTORY_PATH'],'sha256':os.environ['HARBOR_FLEET_CONTINUATION_HISTORY_SHA256']}
    value=validate(bound,live=True)
    if list(map(str,roots))!=value['roots'] or sorted(known_ids)!=value['known_ids']:raise ValueError('READY history scope differs')
    return value['excluded'],value['histories']


def build_publisher(plan_binding):
    p=json.loads(Path(plan_binding['path']).read_text());R=helper(p['response_helper']);R.checked(plan_binding)
    R.require(set(p)=={'schema_version','helper','response_helper','native','intermediate','output'} and p['schema_version']=='harbor-current-response-publisher/v1' and p['helper']==R.binding(__file__),'closed publisher construction required')
    R.build_publisher(p['native'],p['intermediate']);source=Path(p['intermediate']).read_text();hb=R.binding(__file__)
    loader='\n\ndef _ready_adapter():\n    return load("selected_ready_custody", '+repr(hb)+')\n\n'
    changes=[('def continuation_binding(plan):',loader+'def continuation_binding(plan):'),
      ('    require(\'history\' not in plan, \'fresh and continued history are mutually exclusive\')','    if "ready_continuation" in plan:\n        return _ready_adapter().publication_binding(sys.modules[__name__], plan)\n    require(\'history\' not in plan, \'fresh and continued history are mutually exclusive\')'),
      ('        from scripts.fleet.continuation_history import projected_survey','        if "ready_continuation" in plan:\n            projected_survey = _ready_adapter().projected_survey\n        else:\n            from scripts.fleet.continuation_history import projected_survey'),
      ("ready['config'] == plan['source_continuation']['config']","ready['config'] == plan.get('ready_continuation', plan.get('source_continuation'))['config']")]
    for old,new in changes:
        R.require(source.count(old)==1,'native READY insertion point changed');source=source.replace(old,new)
    compile(source,p['output'],'exec');out=Path(p['output']);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as f:f.write(source);f.flush();os.fsync(f.fileno())
    return R.retain(out.with_suffix('.provenance.json'),dict(schema_version='harbor-current-response-publisher-provenance/v1',plan=plan_binding,publisher=R.binding(out),native=p['native'],intermediate=R.binding(p['intermediate']),helper=hb,response_helper=p['response_helper'],native_publication_and_collection_gates_preserved=True))


def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=('capture','prepare-review','review','build-publisher'));a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);v=a.parse_args()
    print(json.dumps(globals()[v.action.replace('-','_')]({'path':v.plan,'sha256':v.plan_sha256})))
if __name__=='__main__':main()
