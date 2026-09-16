"""Validate a never-claimed queue entry relocated within the same worker runtime.

Only native history roots and their containing immutable data bindings change.
The original queue remains intact; the original atomic workbook claim decides
which host runs. This authority never enrolls, reconciles, or starts a worker.
"""
from pathlib import Path
import json,time,math,hashlib
SCHEMA='fleet-ready-backlog-redistribution/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path).absolute()
    require(p.is_file()and not any(q.is_symlink()for q in(p,*p.parents)), 'canonical redistribution file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'redistribution bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def ready(status,workbook,source):
    rows=[w for w in status['workbooks']if w['workbook']==workbook]
    require(len(rows)==1,'exact existing queue entry required');w=rows[0]
    require(w['state']=='ready'and w['fence']==0 and w.get('owner')is None
            and w.get('claim_id')is None and w.get('version')is None
            and w['source_sha256']==source['sha256'],'redistribution requires never-claimed ready custody')
    require(not any(o['workbook']==workbook for o in status['operations'])
            and not any(r['workbook']==workbook for r in status.get('author_reservations',[])),
            'redistribution cannot move prior operations or reservations')
    return w


def survey(row,host,workbook):
    v=read(row)
    require(v['host']==host and v['schema_version']=='fleet-ready-queue-native-survey/v1', 'native queue survey host differs')
    require(type(v['observed_at'])in(int,float)and math.isfinite(v['observed_at'])
            and 0<=time.time()-v['observed_at']<=900,'native queue survey expired')
    require(v['infrastructure_excluded']==['releases','staging','tools'],'native history exclusions changed')
    root=Path(v['native_root']);require(root.is_absolute(),'native history root required')
    expected=[str(root/n)for n in v['top_level_entries']if n not in v['infrastructure_excluded']]+[v['task_root']]
    require(v['roots']==expected and len(expected)==len(set(expected))and bool(expected),'complete native history roots required')
    require(workbook not in v['excluded']and not v['histories'].get(workbook)
            and not v['matching_processes']and not v['consumed_paths'],'native queue already consumed')
    return v


def history_roots(value,source):
    """Exclude only the absent future job subtree, preserving every sibling."""
    planned=Path(source['home']).parent;roots=[];used=[]
    for row in value['roots']:
        root=Path(row)
        if planned.is_relative_to(root):
            require(planned.parent==root,'prospective history partition must be one exact child')
            matches=[p for p in value.get('partitions',[])if p['path']==row]
            require(len(matches)==1,'actual prospective namespace partition required');part=matches[0]
            require(set(part)=={'path','directories','files','symlinks'}and not part['files']and not part['symlinks'],
                    'history partition cannot omit files or links')
            names=part['directories'];require(names==sorted(set(names))and all(Path(n).name==n and n not in ('','.','..')for n in names)
                    and planned.name not in names,'prospective namespace must be absent')
            roots.extend(str(root/n)for n in names);used.append(part)
        else:roots.append(row)
    require(used==value.get('partitions',[]),'unrelated or repeated history partition')
    require(all(not planned.is_relative_to(Path(r))for r in roots),'current job would become its own historical evidence')
    return roots


def fresh_existing(job_row):
    """An identical Fresh job may run at either host only after the atomic claim."""
    job=read(job_row);p=read(job['inputs'][0])
    if p.get('schema_version')!='harbor-fleet-fresh-plan/v1':return None
    require(p['reconstruction']['mode']=='existing' and set(p['reconstruction'])=={'mode','handoff','generated_curation'},'existing qualified Fresh source required')
    require(job['command'][-5:]==['run','--plan',job['inputs'][0]['path'],'--plan-sha256',job['inputs'][0]['sha256']] and job['command'][3]==job['driver']['path'] and Path(job['driver']['path'])==Path(p['code_root'])/'scripts/fleet/fresh.py','exact original Fresh command required')
    handoff=read(p['reconstruction']['handoff']);require(handoff['workbook']==job['workbook']==p['workbook'] and handoff['state']=='VERIFIED' and handoff['source_attempts']==1 and handoff['automatic_retry']is False,'original Fresh source handoff differs')
    candidates=[r for r in job['inputs']if Path(r['path']).name=='snapshot.json'];require(len(candidates)==1,'exact source runtime snapshot required');snapshot=read(candidates[0])
    require(snapshot['root']==job['cwd']==p['code_root'] and snapshot['fingerprint']==job['version'] and snapshot['bindings']['scripts/fleet/fresh.py']==job['driver'],'Fresh driver/source runtime differs')
    for r in job['inputs']:require(binding(r['path'])==r,'Fresh input changed')
    require(binding(job['history']['path'])==job['history'],'Fresh history changed')
    return dict(p,snapshot=candidates[0])


def job_delta(old_row,new_row,roots):
    old=read(old_row);new=read(new_row);fresh=fresh_existing(old_row)
    if fresh is not None:
        require(new_row==old_row and new==old and roots is None,'Fresh redistribution must preserve every job byte')
        return old,new,fresh
    op=read(old['inputs'][0]);np=read(new['inputs'][0])
    require(op['schema_version']==np['schema_version']=='harbor-fleet-staged-pipeline/v1','original staged source-to-author job required')
    os=read(op['source_plan']);ns=read(np['source_plan'])
    require(os['schema_version']==ns['schema_version']=='fleet-source-first/v3','original finite historical source plan required')
    require(ns==dict(os,native_history_roots=roots),'only destination native history roots may change')
    require(np==dict(op,source_plan=np['source_plan']),'pipeline changed beyond source binding')
    replacements={old['inputs'][0]['path']:new['inputs'][0],op['source_plan']['path']:np['source_plan']}
    require(old['inputs'].count(old['inputs'][0])==1 and old['inputs'].count(op['source_plan'])==1,'ambiguous original input bindings')
    inputs=[replacements.get(r['path'],r)for r in old['inputs']]
    command=old['command'];require(command[-5:]==['run','--plan',old['inputs'][0]['path'],'--sha256',old['inputs'][0]['sha256']], 'original pipeline command differs')
    expected=dict(old,inputs=inputs,command=command[:-5]+['run','--plan',new['inputs'][0]['path'],'--sha256',new['inputs'][0]['sha256']])
    require(new==expected,'queued job changed beyond exact data bindings')
    for r in new['inputs']:read(r)
    return old,new,os


def validate(p,prepared,status):
    selected=read(p['redistribution'])
    require(set(selected)-{'enrollment_kind'}=={'schema_version','helper','original_admission','enrollment_intent','enrollment_events','rows'}
            and selected['schema_version']==SCHEMA and selected['helper']==binding(__file__),'closed redistribution authority required')
    original=read(selected['original_admission']);op=read(original['prepared']);enrollment_origin(selected,original)
    require(original['authorization']==p['authorization']and op['snapshot']==prepared['snapshot']and op['supervisor']==prepared['supervisor'], 'original source/runtime authority differs')
    events_row=selected['enrollment_events'];require(binding(events_row['path'])==events_row,'enrollment events changed')
    events=[json.loads(line)for line in Path(events_row['path']).read_text().splitlines()]
    require(set(selected['rows'])=={r['workbook']for r in prepared['rows']},'redistribution rows differ')
    for row in prepared['rows']:
        wb=row['workbook'];prior=selected['rows'][wb]
        require(set(prior)=={'source_host','job','source_survey','destination_survey'}and prior['source_host']!=row['host'],'distinct source and destination required')
        require(prior['job']in op['assignments'][prior['source_host']],'job was not originally assigned to source host')
        old_cfg=read(original['configs'][prior['source_host']]);require(prior['job']in old_cfg['jobs'],'original source configuration differs')
        source=survey(prior['source_survey'],prior['source_host'],wb);dest=survey(prior['destination_survey'],row['host'],wb)
        require(source['job']==dest['job']==prior['job'],'native surveys select another job')
        original_job=read(prior['job']);fresh=fresh_existing(prior['job'])
        original_source=fresh if fresh is not None else read(read(original_job['inputs'][0])['source_plan'])
        old,new,sp=job_delta(prior['job'],row['job'],None if fresh is not None else history_roots(dest,original_source))
        ready(status,wb,new['source'])
        records=[e for e in events if e.get('workbook')==wb]
        require([(e['action'],e['state'])for e in records]==[('enroll','INTENT'),('enroll','RECORDED'),('reconcile','INTENT'),('reconcile','RECORDED')], 'original enrollment incomplete or repeated')
        actual=records[-1]['result'];require(actual['workbook']==wb and actual['state']=='ready'and actual['fence']==0 and actual['source_sha256']==new['source']['sha256'],'original ready result differs')
        require(sp['snapshot']==prepared['snapshot']and old['version']==read(prepared['snapshot'])['fingerprint'],'source/runtime lineage differs')
    return selected


def enrollment_origin(selected,original):
    """Authenticate either original initial enrollment or its append controller."""
    intent=read(selected['enrollment_intent'])
    require(original['schema_version']=='fleet-source-pool-start/v5','original fresh enrollment authority required')
    if selected.get('enrollment_kind')=='initial-start':
        require(intent['plan']==selected['original_admission']and intent['automatic_retry']is False,'initial enrollment intent differs')
        require(set(intent['preflight'])==set(original['configs'])and all(v['status']=='PASS'and v['workers_started']==0 and v['config']==original['configs'][h]for h,v in intent['preflight'].items()),'initial enrollment preflights differ')
    else:
        require('enrollment_kind'not in selected,'unsupported original enrollment kind')
        require(read(intent['plan'])['admission']==selected['original_admission'],'original append enrollment authority differs')
