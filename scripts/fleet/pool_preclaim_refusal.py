"""A new pool may retain an exact synchronous preclaim capacity refusal."""
import ast
import importlib.util
from pathlib import Path

SCHEMA='fleet-pool-preclaim-refusals/v1'
ERROR='scripts.fleet.coordinator.Conflict: host author capacity occupied; await finish or exact stopped reconciliation'


def require(ok,message):
    if not ok:raise ValueError(message)


def supervisor_delta(read,binding,old,new):
    require(binding(old['path'])==old and binding(new['path'])==new,'pool supervisor bytes changed')
    before=ast.parse(Path(old['path']).read_text());after=ast.parse(Path(new['path']).read_text())
    added=[n for n in after.body if isinstance(n,ast.FunctionDef)and n.name=='coordinator_room']
    require(len(added)==1 and not any(isinstance(n,ast.FunctionDef)and n.name=='coordinator_room'for n in before.body),'one explicit coordinator room check required')
    target=ast.parse('if not coordinator_room(status,host,active):return None').body[0];removed=0
    class Closed(ast.NodeTransformer):
        def visit_If(self,node):
            nonlocal removed
            if ast.dump(node)==ast.dump(target):removed+=1;return None
            return self.generic_visit(node)
    after.body.remove(added[0]);Closed().visit(after)
    require(removed==1 and ast.dump(before)==ast.dump(after),'supervisor changed beyond coordinator room hint')
    # Evaluate only this pure, closed admission hint, without importing a worker.
    room=added[0];allowed={'status','host','pending','policy','limit','rows','occupied','row','True','False','None','int','list','set','type','isinstance','ValueError','len'}
    require(all(n.id in allowed for n in ast.walk(room)if isinstance(n,ast.Name)),'room hint uses unrelated authority')
    ns={};exec(compile(ast.Module(body=[room],type_ignores=[]),new['path'],'exec'),ns);fn=ns['coordinator_room']
    base=dict(author_capacity_policy=dict(authors_per_host=2),author_reservations=[])
    require(fn(base,'host',set())and not fn(base,'host',{'a','b'}),'room hint lost pending starts')
    rows=[dict(host='host',state='hold',workbook='a'),dict(host='host',state='active',workbook='b')]
    require(not fn(dict(base,author_reservations=rows),'host',set())and fn(dict(base,author_reservations=rows[:1]),'host',{'a'}),'room hint lost held occupancy or double counted one task')


def copied(observation,read,binding):
    rows=observation['files'];result={}
    for item in rows:
        require(set(item)=={'native','copy'}and binding(item['copy']['path'])==item['copy']
            and item['native']['sha256']==item['copy']['sha256']and item['native']['path']not in result,'refusal copy changed or duplicated')
        result[item['native']['path']]=item
    def bound(row):
        require(row['path']in result and result[row['path']]['native']==row,'refusal evidence missing')
        return read(result[row['path']]['copy'])
    def at(path):
        require(str(path)in result,'refusal path absent')
        return result[str(path)]['native']
    return result,bound,at


def refusal(record,old_plan,old_prepared,read,binding):
    require(set(record)=={'job','observation'},'closed refused job required')
    job=read(record['job']);wb=job['workbook'];obs=read(record['observation'])
    require(set(obs)=={'at','host','pool_status','processes','worker_inventory','source_home_exists','files'}
        and obs['worker_inventory']==['intent.json']and obs['source_home_exists']is False
        and wb not in obs['processes'],'refused worker has task effects or remains live')
    host=obs['host'];cfgrow=old_plan['configs'][host];cfg=read(cfgrow);pool=obs['pool_status'];root=Path(old_plan['run_root'])/host
    require(record['job']in old_prepared['assignments'][host]and cfg['jobs']==old_prepared['assignments'][host]
        and pool['config']==cfgrow and pool['supervisor']==old_prepared['supervisor']and pool['host']==host,
        'refusal detached from original pool assignment')
    rows,bound,at=copied(obs,read,binding);gen=root/'generations'/pool['generation'];generation=bound(at(gen/'generation.json'))
    launched=bound(at(gen/'launched.json'))
    require(launched==bound(at(root/'current.json'))and generation==dict(launched,slots=[])
        and generation['config']==cfgrow and generation['supervisor']==old_prepared['supervisor']
        and generation['host']==host and generation['generation']==pool['generation'],'original pool generation changed')
    marker=at(root/'dispatches'/(wb+'.json'));dispatch=bound(marker);slot=dispatch['slot']
    require(type(slot)is int and slot in (0,1)and dispatch['job']==record['job']and dispatch['host']==host
        and dispatch['automatic_retry']is False,'original dispatch marker changed')
    resultrow=at(gen/('slot-%s.workbook-%s.result.json'%(slot,wb)));result=bound(resultrow)
    require(set(result)=={'job','dispatch','returncode','failure_type','forced','outcome','log','finished_at','automatic_retry','publication_performed'}
        and result['job']==record['job']and result['dispatch']==marker and result['returncode']==1
        and result['failure_type']is None and result['forced']is False and result['outcome']is None
        and result['automatic_retry']is False and result['publication_performed']is False,
        'original pool result is not a stopped preclaim refusal')
    log=at(gen/('slot-%s.workbook-%s.log'%(slot,wb)));require(result['log']==log,'refusal log detached')
    text=Path(rows[log['path']]['copy']['path']).read_text()
    require(text.rstrip().endswith(ERROR)and text.count('claim = client.call("claim"')==1
        and ('File "'+cfg['worker']['path']+'"')in text and 'raise Conflict(value[\'error\']) from None'in text,
        'only a sealed synchronous capacity refusal is supported')
    intentrow=at(Path(job['run'])/'intent.json');intent=bound(intentrow)
    require(set(intent)=={'job','owner','claim_id','created_at_ns','automatic_retry'}and intent['job']==record['job']
        and intent['owner']==host+'/slot-'+str(slot)+'/'+pool['generation']and intent['automatic_retry']is False
        and isinstance(intent['claim_id'],str)and intent['claim_id']
        and dispatch['at']*10**9<=intent['created_at_ns']<=result['finished_at']*10**9<=obs['at'],
        'refused claim identity or ordering changed')
    source=read(job['inputs'][0]);require(Path(job['driver']['path']).name=='source_first.py'
        and source['workbook']==wb and {k:source['source'][k]for k in ('path','sha256')}==job['source']
        and type(source['models'])is int and source['models']==0
        and source['automatic_retry']is False and job['result']==str(Path(source['home'])/'result.json'),
        'only the unchanged original source-only job is supported')
    return host,job,source,obs,rows


def validate(S,p,prepared,status):
    read,binding=S.read,S.binding;spec=p['preclaim_recovery'];require(set(spec)=={'helper','proof'}and spec['helper']==binding(__file__),'closed preclaim reader required')
    proof=read(spec['proof']);require(set(proof)=={'schema_version','previous_start','records','coordinator','authorization'}and proof['schema_version']==SCHEMA
        and proof['coordinator']==p['coordinator']and proof['authorization']==p['authorization'],'closed original preclaim authority required')
    start=read(proof['previous_start']);old=read(start['plan']);prior=read(old['prepared'])
    require(start['status']=='START_RECORDED'and start['automatic_retry']is False and old['schema_version']=='fleet-source-pool-start/v5'
        and old['authorization']==p['authorization']and old['coordinator']==p['coordinator']and old['admission_policy']==p['admission_policy']
        and Path(p['run_root']).parent==Path(old['run_root']).parent and p['run_root']!=old['run_root'],
        'refused pool ancestry or new namespace differs')
    require({k:v for k,v in prepared.items()if k not in {'rows','assignments','supervisor'}}=={k:v for k,v in prior.items()if k not in {'rows','assignments','supervisor'}},'preclaim recovery changed source runtime or authorization')
    supervisor_delta(read,binding,prior['supervisor'],prepared['supervisor'])
    ids={r['workbook']for r in prepared['rows']};require(ids and set(proof['records'])==ids,'preclaim scope differs')
    current={r['workbook']:r for r in status['workbooks']};require(len(current)==len(status['workbooks']),'duplicate coordinator workbook')
    for host,cfgrow in p['configs'].items():
        cfg=read(cfgrow);original=read(old['configs'][host]);require(dict(cfg,jobs=original['jobs'])==original,'preclaim recovery changed original host policy')
    for item in prepared['rows']:
        wb=item['workbook'];host,job,source,obs,rows=refusal(proof['records'][wb],old,prior,read,binding);new=read(item['job']);original=next(r for r in prior['rows']if r['workbook']==wb)
        starts=[h for h in start['hosts']if h['host']==host]
        require(len(starts)==1 and starts[0]['returncode']==0 and starts[0]['automatic_retry']is False,'original host start was not confirmed')
        started=read(starts[0]['stdout']);_,bound,at=copied(obs,read,binding)
        require(started==bound(at(Path(old['run_root'])/host/'current.json'))
            and started.get('resumed')is False,'refused generation differs from original confirmed start')
        require(item==dict(original,job=item['job'])and item['host']==host and new==dict(job,run=new['run'])
            and new['run']!=job['run']and Path(new['run']).parent==Path(job['run']).parent,'only a fresh worker namespace may change')
        w=current.get(wb);require(w is not None and w['state']=='ready'and type(w['fence'])is int and w['fence']==0
            and all(w.get(k)is None for k in ('owner','claim_id','version','checkpoint','expires'))and w['source_sha256']==job['source']['sha256'],
            'refused source acquired central custody')
    require(not any(r.get('workbook')in ids for r in status['operations'])and not any(r.get('workbook')in ids for r in status.get('author_reservations',[])),'refused source has operation or author history')
    return old,prior,proof


def native(S,p,prepared,status,host,pool):
    old,prior,proof=validate(S,p,prepared,status)
    for wb,record in proof['records'].items():
        original_host,job,source,obs,rows=refusal(record,old,prior,S.read,S.binding)
        if original_host!=host:continue
        root=Path(old['run_root'])/host;state=pool.status(root)
        require(state['generation']==obs['pool_status']['generation']and state['config']==old['configs'][host]
            and state['stop_requested']and not S.read(S.binding(root/'stop.json'))['force']
            and all(not s['supervisor_alive']and s.get('worker_identity')is None for s in state['slots']),
            'original refused pool must be gracefully stopped')
        for r in rows.values():require(S.binding(r['native']['path'])==r['native'],'original refused pool evidence changed')
        run=Path(job['run']);require(sorted(str(f.relative_to(run))for f in run.rglob('*')if f.is_file())==['intent.json']
            and not Path(source['home']).exists()and wb not in pool.native_census(host),'refused source gained native effects')
