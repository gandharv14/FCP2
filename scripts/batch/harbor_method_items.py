"""Exact occurrence order with interned immutable row metadata."""
from array import array
from collections import Counter, defaultdict
from collections.abc import MutableMapping, Sequence
import json


def immutable(*args,**kwargs):raise TypeError('immutable method metadata')
class FrozenDict(dict):
    __setitem__=__delitem__=clear=pop=popitem=setdefault=update=__ior__=immutable
    def __deepcopy__(self,memo):return self
class FrozenList(list):
    __setitem__=__delitem__=append=clear=extend=insert=pop=remove=reverse=sort=__iadd__=__imul__=immutable
    def __deepcopy__(self,memo):return self
def freeze(value):
    if isinstance(value,dict):return FrozenDict((key,freeze(item)) for key,item in value.items())
    if isinstance(value,list):return FrozenList(freeze(item) for item in value)
    return value


class Item(MutableMapping):
    KEYS=('band','label','roles','role_context','blank_label')
    def __init__(self,owner,index):self.owner,self.index=owner,index
    def __getitem__(self,key):
        state=self.owner.states[self.owner.order[self.index]]
        if key=='band':return self.owner.bands[state[0]]
        if key not in self.KEYS:raise KeyError(key)
        value=state[self.KEYS.index(key)]
        return list(value) if key in ('roles','role_context') else value
    def __setitem__(self,key,value):
        if key not in ('roles','role_context'):raise TypeError('immutable method metadata')
        state=list(self.owner.states[self.owner.order[self.index]])
        state[self.KEYS.index(key)]=tuple(value)
        self.owner.order[self.index]=self.owner.intern(tuple(state))
    def __delitem__(self,key):raise TypeError('method fields are required')
    def __iter__(self):return iter(self.KEYS)
    def __len__(self):return len(self.KEYS)


class Prepared(Sequence):
    def __init__(self):
        self.bands=[];self.band_ids={};self.states=[];self.state_ids={};self.order=array('I')
    def intern(self,state):
        found=self.state_ids.get(state)
        if found is None:
            found=len(self.states);self.states.append(state);self.state_ids[state]=found
        return found
    def append(self,band,label,roles,context):
        bid=self.band_ids.get(id(band))
        if bid is None:
            bid=len(self.bands);self.bands.append(freeze(band));self.band_ids[id(band)]=bid
        self.order.append(self.intern((bid,label,tuple(roles),tuple(context),not bool(label))))
    def __len__(self):return len(self.order)
    def __getitem__(self,index):
        if isinstance(index,slice):return [self[i] for i in range(*index.indices(len(self)))]
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        return Item(self,index)
    def weighted(self):
        counts=Counter(self.order);first={}
        for i,state in enumerate(self.order):first.setdefault(state,i)
        for state,count in counts.items():yield state,self[first[state]],count
    def transform(self,function):
        replacements={}
        for state,item,_ in self.weighted():
            function(item);replacements[state]=self.order[item.index]
            self.order[item.index]=state
        self.order=array('I',(replacements[state] for state in self.order))
    def expanded_json_bytes(self,*,default=None):
        """Estimate a legacy JSON list from unique states before allocating it."""
        total=2
        for _,item,count in self.weighted():
            total+=(len(json.dumps(dict(item),ensure_ascii=False,default=default).encode())+2)*count
        return max(2,total-2)


def top_labelled_vertical_family(D,gold,band,cells,labels):
    """Keep an exact copied-down family with one heading and blank lower labels."""
    if (not 2<=len(cells)<=32 or band.get('orientation')!='vertical'
            or cells!=band.get('cell_keys') or len(set(cells))!=len(cells)
            or not labels[0] or band.get('label')!=labels[0] or any(labels[1:])):
        return False
    positions=[]
    for cell in cells:
        sheet,coord=cell.rsplit('!',1);parsed=D.split_coord(coord)
        if not parsed or not gold.formula.get(cell):return False
        positions.append((sheet,*parsed))
    sheet,col,row=positions[0]
    if positions!=[(sheet,col,row+i)for i in range(len(cells))]:return False
    return (all(D.copy_compatible(gold,a,b)for a,b in zip(cells,cells[1:]))
            and D.full_copied_scope_vertical(gold,cells,set())==cells)


def prepare(D,gold,bands):
    result=Prepared();split_cache={};role_cache={}
    for original in bands:
        cells=list(original.get('stated_cell_keys') or original.get('cell_keys') or [])
        labels=[gold.row_label(cell) for cell in cells]
        subbands=[original]
        if len(cells)>1 and len(set(labels))>1 and not top_labelled_vertical_family(D,gold,original,cells,labels):
            byrow=defaultdict(list)
            for cell,label in zip(cells,labels):
                sheet,coord=cell.rsplit('!',1);parsed=D.split_coord(coord)
                if not parsed:break
                col,row=parsed;byrow[(sheet,row,label)].append((D.col_to_num(col),cell))
            else:
                subbands=[]
                for (sheet,row,label),run in sorted(byrow.items()):
                    ordered=tuple(sorted(run));key=(sheet,row,ordered)
                    if key not in split_cache:split_cache[key]=D.make_band(gold,sheet,row,list(ordered))
                    subbands.append(split_cache[key])
        for band in subbands:
            label=band.get('label') or ''
            if id(band) not in role_cache:
                role_cache[id(band)]=D.method_roles_for_band(gold,band) if label else ([],[])
            roles,context=role_cache[id(band)];result.append(band,label,roles,context)
    index=defaultdict(lambda:defaultdict(set))
    for _,item,_ in result.weighted():
        if not item['blank_label'] and len(item['roles'])==1:
            band=item['band'];index[(band.get('sheet'),band.get('pattern'))][int(band.get('row',0))].add(item['roles'][0])
    for item in result:
        if item['blank_label'] or item['roles']:continue
        band=item['band'];row=int(band.get('row',0));near=index[(band.get('sheet'),band.get('pattern'))]
        roles=set().union(*(near.get(other,()) for other in range(row-6,row+7)))
        if len(roles)==1:
            item['roles']=list(roles)
            item['role_context']=item['role_context']+['role propagated from same-shape neighboring row']
            near[row].update(roles)
    return result


from scripts.batch.harbor_method_assessments import encode
import json


def role_cases(D,prepared):
    groups={}
    for _,item,count in prepared.weighted():
        if item.get('blank_label'):continue
        roles=item.get('roles') or []
        if len(roles)<=1:continue
        case_id=D.role_case_id(item['label'],roles,item.get('role_context') or [])
        if case_id not in groups:
            groups[case_id]=dict(case_id=case_id,label=item['label'],roles=sorted(roles),role_context=list(item.get('role_context') or []),
                band_count=0,sample_bands=[],triggered_by=D.triggered_by_for_item(item),
                questions={entry:D.registry().get(entry,{}).get('question','') for entry in sorted(roles)})
        groups[case_id]['band_count']+=count
        band=item['band'].get('band');samples=groups[case_id]['sample_bands']
        if band and band not in samples and len(samples)<5:samples.append(band)
    return sorted(groups.values(),key=lambda c:(c['label'],c['case_id']))


def detect(D,gold,delivered,bands,targets,resolutions_path=None,*,prepared=None):
    records=[];claimed=set()
    if prepared is None:prepared=prepare(D,gold,bands)
    cases=role_cases(D,prepared)
    if cases and resolutions_path is not None:
        payload=D.require_role_resolutions(cases,resolutions_path)
        prepared.transform(lambda item:D.apply_role_resolution(item,payload))
    expanded={};assessments={}
    for state,item,count in prepared.weighted():
        bid=prepared.states[state][0]
        if bid not in expanded:expanded[bid]=D.expand_method_band(gold,delivered,item['band'])
        band=expanded[bid];label=item['label']
        if item.get('blank_label'):
            assessments[state]=dict(band=band.get('band'),cells=band.get('cells',[]),cell_keys=band.get('cell_keys',[]),label='',roles=[],status='unclassified',reason='blank_target_label')
            continue
        roles=item['roles'];context=item['role_context']
        base=dict(band=band.get('band'),cells=band.get('cells',[]),cell_keys=band.get('cell_keys',[]),label=label,roles=roles,role_context=context)
        if not roles:
            assessments[state]={**base,'status':'unclassified','reason':'no_method_role'};continue
        ordered=[role for role in D.METHOD_ROLE_PATTERNS if role in roles]
        last=None;claimed_here=False
        for entry in ordered:
            rec,assessment=D.try_custom_role(gold,band,label,base,entry);last=assessment
            if rec is not None:
                records.append(rec);claimed.update(rec.get('cell_keys',[]));assessments[state]=assessment;claimed_here=True;break
        if not claimed_here and last is not None:
            if len(ordered)>1:last={**base,'status':'unclassified','reason':'ambiguous_role','tried':ordered,'last':last}
            assessments[state]=last
    items=[];interned={};mapping={}
    for state,row in assessments.items():
        key=json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        if key not in interned:interned[key]=len(items);items.append(row)
        mapping[state]=interned[key]
    encoded=encode(items,(mapping[state] for state in prepared.order if state in mapping))
    return records,encoded,claimed
