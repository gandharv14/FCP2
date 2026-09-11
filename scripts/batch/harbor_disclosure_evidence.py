"""Versioned compact evidence for new reviews; historical requests stay intact."""
import copy
from scripts.batch import harbor_disclosure_payload_repair as tables
from scripts.batch.harbor_admission import require
from scripts.batch import harbor_method_assessments as indexed

VERSION='complete-workbooks-scoped-method-assessments/v1'
FIELDS={'manifest','manifest_sha256','workbooks','instruction','disclosure'}


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
    return result


def decode_body(body):
    require(isinstance(body,dict) and set(body)==FIELDS|{'evidence_projection'}
            and body['evidence_projection']==VERSION,'unknown disclosure evidence projection')
    result=copy.deepcopy(body)
    result.pop('evidence_projection')
    result['workbooks']=tables.decode_workbooks(body['workbooks'])
    return result
