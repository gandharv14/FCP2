"""Pure exact-cover slices of the owning global dialogue scaffold.

No request preparation, model execution, artifact writes, or inferred grouping.
"""
from pathlib import Path
from copy import deepcopy
import hashlib,json
from scripts.batch.harbor_admission import require
from scripts.batch.harbor_readiness import skill_module
ROOT=Path(__file__).resolve().parents[2]
SCHEMA='harbor-dialogue-partition-plan/v1'

def canonical(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()

def projected(claims,ids,template,*,root):
    """Keep exact claim facts; scope duplicated narrative to assigned claims."""
    selected=set(ids);out={k:deepcopy(v) for k,v in claims.items() if k not in ('claims','writer_pack','disclosure_body')}
    out['writer_pack']={k:deepcopy(v) for k,v in claims['writer_pack'].items() if k not in ('claims','disclosure_body','draft_skeleton')}
    out['writer_pack']['claims']=[deepcopy(c) for c in claims['writer_pack']['claims'] if c['record_id'] in selected]
    out['claims']=[deepcopy(c) for c in claims['claims'] if c['record_id'] in selected]
    require([c['record_id'] for c in out['claims']]==ids,'partition changes original claim order')
    extractor=skill_module(root,'additional-assumptions-dialogue','extract_claims')
    out['disclosure_body']=extractor.writer_disclosure_body(out['claims'],'')
    pack=out['writer_pack'];pack['claims']=[c for c in pack['claims'] if c['record_id'] in selected]
    pack['disclosure_body']=out['disclosure_body'];pack['draft_skeleton']=template
    shared=out.get('shared_source_links')
    if shared is not None:
        shared['groups']=[g for g in shared['groups'] if set(g)<=selected]
        require(not any(set(g)&selected and not set(g)<=selected for g in claims['shared_source_links']['groups']),'partition splits certified group')
        shared['proven_claim_ids']=[x for x in shared['proven_claim_ids'] if x in selected]
        pack['shared_source_links']=deepcopy(shared)
    return out

def build(claims,*,root=ROOT,max_claims=50,max_bytes=180000):
    require(type(max_claims) is int and 1<=max_claims<=100,'bounded partition claim count required')
    require(type(max_bytes) is int and 16384<=max_bytes<=262144,'bounded partition bytes required')
    composer=skill_module(root,'additional-assumptions-dialogue','compose_draft')
    template,slots=composer.compose(claims,claims['writer_pack'])
    ids=[c['record_id'] for c in claims['claims']]
    require(ids and len(ids)==len(set(ids)),'unique nonempty original claims required')
    order=slots['slot_order'];require(len(order)%2==0,'complete exchanges required')
    exchanges=[]
    for i in range(0,len(order),2):
        junior,senior=order[i:i+2];j,s=slots['slots'][junior],slots['slots'][senior]
        require(j['kind']=='junior' and s['kind']=='senior' and j['claim_ids']==s['claim_ids'],'canonical exchange pair required')
        start=template.index('<!-- claim:'+j['claim_ids'][0]+' -->')
        exchanges.append(dict(slot_ids=[junior,senior],claim_ids=j['claim_ids'],start=start))
    require(exchanges[0]['start']==0 and [x['start'] for x in exchanges]==sorted(x['start'] for x in exchanges),'global scaffold order changed')
    require([x for e in exchanges for x in e['claim_ids']]==ids,'grouped scaffold changes original claim order')
    for i,e in enumerate(exchanges):e['end']=exchanges[i+1]['start'] if i+1<len(exchanges) else len(template)
    def partition(first,last):
        es=exchanges[first:last];ci=[x for e in es for x in e['claim_ids']];si=[x for e in es for x in e['slot_ids']]
        text=template[es[0]['start']:es[-1]['end']];sm={**slots,'slot_order':si,'slots':{x:slots['slots'][x] for x in si}}
        sub=projected(claims,ci,text,root=root)
        return dict(first_exchange=first,last_exchange=last,claim_ids=ci,slot_ids=si,template=text,slots=sm,claims=sub)
    partitions=[];first=0
    while first<len(exchanges):
        end=first+1;best=None
        while end<=len(exchanges):
            p=partition(first,end)
            # Both author scaffolding and reviewer facts must fit before prose.
            size=max(len(canonical({'writer_pack.json':p['claims']['writer_pack'],'draft_template.md':p['template'],'slots.json':p['slots']})),len(canonical(p['claims'])))
            if len(p['claim_ids'])>max_claims or size>max_bytes:break
            best=p;end+=1
        require(best is not None,'one complete certified exchange exceeds partition bound')
        best['index']=len(partitions);partitions.append(best);first=best['last_exchange']
    seams=[dict(id='seam-%04d'%i,left_exchange=partitions[i-1]['last_exchange']-1,right_exchange=partitions[i]['first_exchange'],owner=i) for i in range(1,len(partitions))]
    return dict(schema_version=SCHEMA,claims_sha256=digest(claims),max_claims=max_claims,max_bytes=max_bytes,template=template,slots=slots,exchanges=exchanges,partitions=partitions,seams=seams)

def validate(plan,claims,*,root=ROOT):
    require(plan==build(claims,root=root,max_claims=plan['max_claims'],max_bytes=plan['max_bytes']),'dialogue partition plan changed')
    return plan

def assemble(plan,responses,*,root=ROOT):
    require(len(responses)==len(plan['partitions']),'author partition coverage incomplete')
    composer=skill_module(root,'additional-assumptions-dialogue','compose_draft');merged={}
    for p,response in zip(plan['partitions'],responses):
        composer.assemble_slots(p['template'],p['slots'],response)
        require(not set(merged)&set(response['slots']),'duplicate authored slot')
        merged.update(response['slots'])
    return composer.assemble_slots(plan['template'],plan['slots'],{'schema_version':'dialogue-slot-prose/v1','slots':merged})
