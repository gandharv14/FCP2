"""Lossless versioned assessment runs; readers never require an expanded list."""
from collections import Counter
import json

SCHEMA='method-assessments-indexed/v1'
FORMAT='Each index_runs row [first, stride, count] selects items[first + stride * offset] for offsets 0 through count-1, in order. All occurrences and their multiplicity are retained.'


def indices(value):
    if not isinstance(value,dict) or set(value)!={'schema_version','format','items','index_runs','length'} or value['schema_version']!=SCHEMA or value['format']!=FORMAT:
        raise ValueError('unknown assessment encoding')
    items=value['items'];runs=value['index_runs'];length=value['length']
    if not isinstance(items,list) or not all(isinstance(row,dict) for row in items) or not isinstance(runs,list) or type(length) is not int or length<0:
        raise ValueError('invalid assessment index')
    json.dumps(items,allow_nan=False)
    total=0
    for run in runs:
        if not isinstance(run,list) or len(run)!=3 or not all(type(x) is int for x in run):raise ValueError('invalid assessment run')
        start,step,count=run
        if count<=0 or not 0<=start<len(items) or not 0<=start+step*(count-1)<len(items):raise ValueError('assessment index out of range')
        total+=count
        if total>length:raise ValueError('assessment count exceeded')
    if total!=length:raise ValueError('assessment count differs')
    for start,step,count in runs:
        for offset in range(count):yield start+offset*step


def encode(items,order):
    # Item values are JSON-compatible and preserved without coercion, including
    # missing/null fields and integer/float lexical distinctions.
    if not isinstance(items,list) or not all(isinstance(row,dict) for row in items):raise ValueError('assessment items required')
    json.dumps(items,allow_nan=False)
    runs=[];start=last=step=None;count=length=0
    for index in order:
        if type(index) is not int or not 0<=index<len(items):raise ValueError('assessment index out of range')
        length+=1
        if count==0:start=last=index;step=0;count=1
        elif count==1:step=index-last;last=index;count=2
        elif index-last==step:last=index;count+=1
        else:runs.append([start,step,count]);start=last=index;step=0;count=1
    if count:runs.append([start,step,count])
    return dict(schema_version=SCHEMA,format=FORMAT,items=items,index_runs=runs,length=length)


def rows(value):
    if isinstance(value,list):
        if not all(isinstance(row,dict) for row in value):raise ValueError('assessment row required')
        yield from value
    else:
        for index in indices(value):yield value['items'][index]


def weighted(value):
    if isinstance(value,list):
        for row in rows(value):yield row,1
    else:
        for index,count in Counter(indices(value)).items():yield value['items'][index],count


def filtered(value,predicate):
    if isinstance(value,list):return [row for row in rows(value) if predicate(row)]
    # Validate before selecting: no malformed hidden run can disappear through
    # an otherwise empty scope filter.
    counts=Counter(indices(value));items=[];mapping={}
    for index in counts:
        row=value['items'][index]
        if predicate(row):mapping[index]=len(items);items.append(row)
    return encode(items,(mapping[index] for index in indices(value) if index in mapping))


def validate(value):
    if isinstance(value,list):
        if not all(isinstance(row,dict) for row in value):raise ValueError('assessment row required')
    else:
        next(indices(value),None)
    return value


def logical_length(value):
    validate(value)
    return len(value) if isinstance(value,list) else value['length']


def status_counts(value):
    result=Counter()
    for row,count in weighted(value):result[row.get('status','unknown')]+=count
    return result


def expanded_json_bytes(value):
    """Exact compact-list JSON size, computed without materializing occurrences."""
    total=2;length=0
    for row,count in weighted(value):
        total+=(len(json.dumps(row,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode())+1)*count
        length+=count
    return total-1 if length else 2
