"""Lossless financial-review evidence tables and deterministic local partitions.

No selection or semantic decision is made here. Full evidence remains retained;
expansion is exact and partition context follows the original producer's rule.
"""
import copy
import json
import math
import hashlib
from bisect import bisect_left
from collections import defaultdict
from openpyxl.utils.cell import coordinate_to_tuple
from scripts.batch.harbor_admission import require

SCHEMA = 'normalization-shared-evidence/v1'
MAX_REFERENCES = 128
MAX_REQUEST_BYTES = 4 * 1024 * 1024

class RequestTooLarge(ValueError):
    """A complete candidate batch requires a smaller lossless partition."""



def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def json_value(value, memo=None):
    """JSON projection preserving shared containers (including duplicate groups)."""
    if memo is None: memo={}
    if value is None or isinstance(value,(str,bool,int)): return value
    if isinstance(value,float):
        require(math.isfinite(value),'non-finite normalization evidence');return value
    if not isinstance(value,(dict,list,tuple)):return str(value)
    if id(value) in memo:return memo[id(value)]
    out={} if isinstance(value,dict) else [];memo[id(value)]=out
    if isinstance(value,dict):
        for k,v in value.items():out[str(k)]=json_value(v,memo)
    else:out.extend(json_value(v,memo) for v in value)
    return out


def equal(left,right,memo=None):
    """Exact JSON equality without repeatedly traversing shared relationships."""
    if left is right:return True
    if memo is None:memo=set()
    pair=(id(left),id(right))
    if pair in memo:return True
    if type(left) is not type(right):return False
    if isinstance(left,dict):
        if left.keys()!=right.keys():return False
        memo.add(pair);return all(equal(v,right[k],memo) for k,v in left.items())
    if isinstance(left,list):
        if len(left)!=len(right):return False
        memo.add(pair);return all(equal(a,b,memo) for a,b in zip(left,right))
    return left==right


def encoded_size(value,memo=None):
    """Exact compact UTF-8 byte count; shared objects are measured only once."""
    if memo is None:memo={}
    if not isinstance(value,(dict,list,tuple)):return len(encoded(value))
    if id(value) in memo:return memo[id(value)]
    if isinstance(value,dict):
        size=2+max(0,len(value)-1)+sum(len(encoded(str(k)))+1+encoded_size(v,memo) for k,v in value.items())
    else:size=2+max(0,len(value)-1)+sum(encoded_size(v,memo) for v in value)
    memo[id(value)]=size;return size


def candidate_id(body,reference):
    source=body.get('source',{})
    return hashlib.sha256(encoded([source.get('sha256'),reference])).hexdigest()


def compact(body):
    """Intern only exact repeated objects, never infer or discard fields."""
    groups=[];group_index={};duplicates=[];duplicate_index={};identity_cache={};copy_memo={}
    def intern(value, table, index):
        identity=(id(table),id(value))
        if identity in identity_cache:return identity_cache[identity][1]
        key=encoded(value)
        if key not in index:index[key]=len(table);table.append(copy.deepcopy(value,copy_memo))
        identity_cache[identity]=(value,index[key])
        return index[key]
    candidates=[]
    for row in body['candidates']:
        common={k:v for k,v in row.items() if k not in {'reference','cells','duplicate_refs'}}
        candidates.append(dict(group=intern(common,groups,group_index),
            reference=row['reference'],cells=copy.deepcopy(row['cells']),
            duplicate_refs_group=intern(row['duplicate_refs'],duplicates,duplicate_index)))
    checks=[]
    for row in body['candidate_primitive_checks']:
        require('duplicate_refs_group' not in row,'reserved primitive-check field')
        checks.append({**{k:copy.deepcopy(v) for k,v in row.items() if k!='duplicate_refs'},
                       'duplicate_refs_group':intern(row['duplicate_refs'],duplicates,duplicate_index)})
    result=dict(schema_version=SCHEMA,
        common={k:copy.deepcopy(v) for k,v in body.items() if k not in {'candidates','candidate_primitive_checks'}},
        shared_candidate_groups=groups,duplicate_reference_groups=duplicates,
        candidates=candidates,candidate_primitive_checks=checks)
    require(equal(expand(result),body),'lossless normalization encoding failed')
    return result


def expand(value):
    require(isinstance(value,dict) and set(value)=={'schema_version','common','shared_candidate_groups',
        'duplicate_reference_groups','candidates','candidate_primitive_checks'} and value['schema_version']==SCHEMA,
        'invalid shared-evidence schema')
    result=copy.deepcopy(value['common'])
    require(not {'candidates','candidate_primitive_checks'}&set(result),'reserved common evidence fields')
    shared_memo={}
    def item(table,index):
        require(type(index) is int and 0<=index<len(table),'unknown shared-evidence index')
        return copy.deepcopy(table[index],shared_memo)
    result['candidates']=[]
    for row in value['candidates']:
        require(set(row)=={'group','reference','cells','duplicate_refs_group'},'compact candidate fields changed')
        shared=item(value['shared_candidate_groups'],row['group'])
        require(not {'reference','cells','duplicate_refs'}&set(shared),'shared candidate shadows identity')
        result['candidates'].append({**shared,'reference':row['reference'],'cells':copy.deepcopy(row['cells']),
            'duplicate_refs':item(value['duplicate_reference_groups'],row['duplicate_refs_group'])})
    result['candidate_primitive_checks']=[]
    for row in value['candidate_primitive_checks']:
        require('duplicate_refs' not in row,'primitive check shadows duplicate evidence')
        result['candidate_primitive_checks'].append({**{k:copy.deepcopy(v) for k,v in row.items() if k!='duplicate_refs_group'},
            'duplicate_refs':item(value['duplicate_reference_groups'],row['duplicate_refs_group'])})
    return result


def local_body(body,references,*,_index=None):
    """Exact original full row / preceding rows / typed text header selection.

    The full validated producer context already includes the preceding four
    original text headers for every selected candidate. Reapplying the same
    rule to this union gives exactly each subset's independently derived rule.
    """
    require(isinstance(references,list) and len(references)==len(set(references)) and references,
            'nonempty unique partition references required')
    if _index is None:_index={}
    if not _index:
        candidates={r['reference']:r for r in body['candidates']}
        identities=set()
        for row in body['candidates']:
            identity=encoded({k:v for k,v in row.items() if k not in {'duplicate_refs','extra','leaks'}})
            require(identity not in identities,'duplicate candidate evidence');identities.add(identity)
            require(equal(row['cells'],candidates[row['reference']]['cells']),'conflicting primitive identity')
        by_row={};by_col={};cells={}
        for sheet,entries in body['context'].items():
            rows=defaultdict(list);headers=defaultdict(list);lookup={}
            for entry in entries:
                require(entry['reference'].startswith(sheet+'!'),'context sheet identity differs')
                address=entry['reference'][len(sheet)+1:];rc=coordinate_to_tuple(address)
                require(address not in lookup,'duplicate context observation');lookup[address]=entry
                rows[rc[0]].append((rc[1],address))
                # Dates/times serialize to strings but are not original string cells.
                if entry['data_type'] in {'s','inlineStr','e'} and isinstance(entry['value'],str):
                    headers[rc[1]].append((rc[0],address))
            by_row[sheet]=rows;by_col[sheet]={c:sorted(v) for c,v in headers.items()};cells[sheet]=lookup
        _index.update(candidates=candidates,by_row=by_row,by_col=by_col,cells=cells)
    candidates=_index['candidates'];by_row=_index['by_row'];by_col=_index['by_col'];cells=_index['cells']
    require(set(references)<=set(candidates),'foreign candidate')
    wanted={}
    for ref in references:
        row=candidates[ref];require(len(row['cells'])==1,'partition requires atomic candidate')
        sheet,address,_=row['cells'][0];require(ref==sheet+'!'+address,'candidate reference differs')
        rr,cc=coordinate_to_tuple(address);selected=wanted.setdefault(sheet,set());selected.add(address)
        selected.update(a for _,a in by_row[sheet].get(rr,[]))
        for preceding in range(max(1,rr-4),rr):
            selected.update(a for c,a in by_row[sheet].get(preceding,[]) if c<=cc+2)
        headers=by_col[sheet].get(cc,[]);stop=bisect_left([r for r,_ in headers],rr)
        selected.update(a for _,a in headers[max(0,stop-4):stop])
    out={k:copy.deepcopy(v) for k,v in body.items() if k not in {'candidates','candidate_primitive_checks','context'}}
    selected=set(references);memo={}
    out['candidates']=[copy.deepcopy(r,memo) for r in body['candidates'] if r['reference'] in selected]
    out['candidate_primitive_checks']=[copy.deepcopy(r,memo) for r in body['candidate_primitive_checks'] if r['reference'] in selected]
    out['context']={sheet:[copy.deepcopy(cells[sheet][a]) for a in sorted(refs,key=coordinate_to_tuple)] for sheet,refs in wanted.items()}
    require([r['reference'] for r in out['candidate_primitive_checks']]==[r['reference'] for r in out['candidates']],'primitive checks incomplete')
    return out


def partitions(body,request_builder,*,max_references=MAX_REFERENCES):
    """Frozen greedy partition order; single oversized context fails closed."""
    require(type(max_references) is int and 1<=max_references<=MAX_REFERENCES,'unbounded review partition')
    references=list(dict.fromkeys(r['reference'] for r in body['candidates']))
    require(references,'maskable candidates required')
    result=[]
    index={}
    def request(refs):
        value=compact(local_body(body,refs,_index=index))
        try:return request_builder(value,refs)
        except RequestTooLarge:return None
    def size(payload):return float('inf') if payload is None else encoded_size(payload)
    start=0
    while start<len(references):
        count=min(max_references,len(references)-start)
        selected=references[start:start+count];payload=request(selected)
        if size(payload)>MAX_REQUEST_BYTES:
            # Adding candidates only adds union context, references and interned
            # entries, so byte size is monotone. Find the largest bounded prefix
            # without rebuilding a 16k-cell context once for every candidate.
            low,high=1,count-1;chosen=None
            while low<=high:
                middle=(low+high)//2;refs=references[start:start+middle];candidate=request(refs)
                if size(candidate)<=MAX_REQUEST_BYTES:
                    chosen=(refs,candidate);low=middle+1
                else:high=middle-1
            require(chosen is not None,'one complete candidate context exceeds bounded request size')
            selected,payload=chosen
        result.append((selected,payload));start+=len(selected)
    require([r for refs,_ in result for r in refs]==references,'partition candidate coverage differs')
    union={}
    for refs,_ in result:
        for sheet,entries in local_body(body,refs,_index=index)['context'].items():
            table=union.setdefault(sheet,{})
            for entry in entries:
                old=table.setdefault(entry['reference'],entry);require(old==entry,'partition context conflict')
    expected={sheet:{e['reference']:e for e in entries} for sheet,entries in body['context'].items()}
    require(union==expected,'partition contexts dropped original evidence')
    return result


def reconcile(body, decisions):
    """Close cross-partition typed-value dependencies without changing reviews.

    The receipt states which accepted cells emission must drop when another
    exposed typed value survives. Foreign references are valid evidence, but
    can only be covered by an explicitly retained picklist extra.
    """
    rows=[r for r in body['candidates'] if r['clean']]
    refs=list(dict.fromkeys(r['reference'] for r in rows))
    require(set(decisions)==set(refs),'dependency decision coverage differs')
    included={r for r in refs if decisions[r]['eligible']}
    rounds=[]
    while True:
        covered=set(included)
        covered.update(ref for row in rows if row['reference'] in included for ref in row.get('extra',[]))
        cache={};drop=set()
        for row in rows:
            if row['reference'] not in included:continue
            group=row['duplicate_refs']
            if id(group) not in cache:cache[id(group)]=any(ref not in covered for ref in group)
            if cache[id(group)]:drop.add(row['reference'])
        if not drop:break
        rounds.append(sorted(drop));included-=drop
    return dict(schema_version='normalization-dependency-reconciliation/v1',
        candidate_ids={ref:candidate_id(body,ref) for ref in refs},
        reviewed_references=refs,eligible_references=sorted(included),
        duplicate_exclusion_rounds=rounds,review_decisions_changed=False)
