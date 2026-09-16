"""Versioned compact evidence for new reviews; historical requests stay intact."""
import copy
import json
from scripts.batch import harbor_disclosure_payload_repair as tables
from scripts.batch.harbor_admission import require
from scripts.batch import harbor_method_assessments as indexed

VERSION='complete-workbooks-scoped-method-assessments/v1'
RECORD_VERSION='complete-workbooks-lossless-records/v2'
RECORD_TABLE='lossless-disclosure-record-columns/v1'
RECORD_NOTE=('Each row is one original disclosure record, in original order. Columns name its fields. '
             'A nonnegative integer indexes the corresponding column in values. -1 means the field '
             'was absent; null is retained as an explicit dictionary value. All nested values, '
             'formulas, labels, source references and excluded records are preserved exactly.')
FIELDS={'manifest','manifest_sha256','workbooks','instruction','disclosure'}


def packed(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def encode_records(records):
    require(isinstance(records,list)and all(isinstance(r,dict)and all(isinstance(k,str)for k in r)for r in records),'complete record objects required')
    columns=list(dict.fromkeys(k for r in records for k in r));values=[[]for _ in columns];indexes=[{}for _ in columns];rows=[]
    for record in records:
        row=[]
        for i,key in enumerate(columns):
            if key not in record:row.append(-1);continue
            identity=packed(record[key]);known=indexes[i]
            if identity not in known:known[identity]=len(values[i]);values[i].append(copy.deepcopy(record[key]))
            row.append(known[identity])
        rows.append(row)
    result=dict(encoding=RECORD_TABLE,row_semantics=RECORD_NOTE,columns=columns,values=values,rows=rows)
    require(packed(decode_records(result))==packed(records),'lossless record roundtrip differs')
    return result


def decode_records(table):
    require(isinstance(table,dict)and set(table)=={'encoding','row_semantics','columns','values','rows'}
            and table['encoding']==RECORD_TABLE and table['row_semantics']==RECORD_NOTE,'unknown complete record encoding')
    columns=table['columns'];values=table['values'];rows=table['rows']
    require(isinstance(columns,list)and all(isinstance(k,str)for k in columns)and len(columns)==len(set(columns))
            and isinstance(values,list)and len(values)==len(columns)and all(isinstance(v,list)for v in values)
            and isinstance(rows,list),'record dictionary columns differ')
    for v in values:require(len(v)==len({packed(x)for x in v}),'record dictionary repeats a value')
    result=[]
    for row in rows:
        require(isinstance(row,list)and len(row)==len(columns),'record row shape differs');record={}
        for i,index in enumerate(row):
            require(type(index)is int and -1<=index<len(values[i]),'record dictionary index invalid')
            if index>=0:record[columns[i]]=copy.deepcopy(values[i][index])
        result.append(record)
    return result


def compact_records(body):
    """An explicit lossless protocol upgrade; never drop a record or clause."""
    require(isinstance(body,dict)and set(body)==FIELDS|{'evidence_projection'}and body['evidence_projection']==VERSION,'original projected disclosure required')
    result=copy.deepcopy(body);records=result['disclosure']['records'];encoded=encode_records(records)
    result['disclosure']['records']=encoded;result['evidence_projection']=RECORD_VERSION
    require(packed(decode_body(result))==packed(decode_body(body)),'record projection changed review evidence')
    return result


def scoped_disclosure(disclosure):
    result=copy.deepcopy(disclosure)
    records=result.get('records');agents=result.get('agent_records')
    assessments=result.get('method_assessments')
    require(isinstance(records,list) and isinstance(agents,list) and isinstance(assessments,(list,dict)),
            'complete disclosure evidence required')
    indexed.validate(assessments)
    keys=set()
    for item in records+agents:
        require(isinstance(item,dict),'invalid disclosed record')
        scope=item.get('cell_keys')
        # Whole-row or otherwise uncertain scope must retain all assessments.
        if not isinstance(scope,list) or not scope or not all(tables.canonical_cell(k) for k in scope):
            return result
        keys.update(scope)
    if not keys:return result
    def relevant(item):
        require(isinstance(item,dict),'invalid method assessment')
        scope=item.get('cell_keys')
        return not isinstance(scope,list) or not scope or not all(tables.canonical_cell(k) for k in scope) or bool(keys.intersection(scope))
    result['method_assessments']=indexed.filtered(assessments,relevant)
    return result


def project_body(body):
    require(isinstance(body,dict) and set(body)==FIELDS,'unexpected disclosure body')
    result=copy.deepcopy(body)
    result['workbooks']=tables.encode_workbooks(body['workbooks'])
    result['disclosure']=scoped_disclosure(body['disclosure'])
    result['evidence_projection']=VERSION
    if len(packed(result['disclosure']['records']).encode())>250000:
        compact=compact_records(result)
        if len(packed(compact).encode())<.8*len(packed(result).encode()):return compact
    return result


def decode_body(body):
    require(isinstance(body,dict) and set(body)==FIELDS|{'evidence_projection'}
            and body['evidence_projection']in (VERSION,RECORD_VERSION),'unknown disclosure evidence projection')
    result=copy.deepcopy(body)
    result.pop('evidence_projection')
    result['workbooks']=tables.decode_workbooks(body['workbooks'])
    if body['evidence_projection']==RECORD_VERSION:result['disclosure']['records']=decode_records(body['disclosure']['records'])
    return result
