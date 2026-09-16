"""One physical retry of an explicit disclosure HTTP 400; no semantic budget reset.

The original request, UUID reservation, budget snapshots and sealed rejection
remain immutable. Only irrelevant method-assessment rows are omitted; every
record, sentence, workbook snapshot and assessment touching a disclosed cell is
retained. This module does not infer semantic approval from transport success.
The root instruction adapter must own the live workbook lease at dispatch.
"""
from dataclasses import asdict, replace
import copy
import json
import re
from pathlib import Path
from scripts.batch import harbor_model_executor as E

SCHEMA='harbor-disclosure-payload-repair/v1'
# Exact predecessor differs only by this relocation-aware historical binding.
PRIOR_IMPLEMENTATIONS={'2ceea26f21affb85b6ebe80f45ce1502b8abeee5b02fbbe97840e5acdc04b5bc',
                       'd980456a60dd0bf2d8a776c1aa6f1e1de29223bb9da9e6ac4ea08ea10233d090',
                       'b832346eb162db48e8149777f4c0e1d1b6ee4e7dc81d585c4fd6e9820197518a'}


def read(binding):
    return json.loads(E.checked_bytes(binding))


def canonical_cell(key):
    if not isinstance(key,str) or '!' not in key:return False
    sheet,cell=key.rsplit('!',1)
    if not sheet or sheet!=sheet.strip() or sheet.startswith("'") or sheet.endswith("'"):return False
    match=re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})',cell)
    if match is None:return False
    column=0
    for letter in match[1]:column=column*26+ord(letter)-64
    return column<=16384 and int(match[2])<=1048576


TABLE_VERSION='lossless-workbook-cell-table/v1'
RECORD_PROJECTION='complete-workbooks-lossless-records/v2'
TABLE_COLUMNS=['address','value','number_format_index','data_type','cached_value']
TABLE_NOTE=('Every sheet and cell is retained in original order. Each cell row uses the declared columns. '
            'number_format_index indexes the exact number_formats array (zero-based). '
            'Four entries mean cached_value was absent; five entries mean it was present, including null. '
            'Values, formulas, data types and all workbook/sheet metadata are unchanged.')


def encode_workbooks(workbooks):
    E.require(isinstance(workbooks,dict) and set(workbooks)=={'golden','delivered'},'unexpected complete workbook set')
    result=copy.deepcopy(workbooks);formats=[]
    for workbook in result.values():
        E.require(isinstance(workbook,dict) and isinstance(workbook.get('sheets'),list),'workbook sheets missing')
        for sheet in workbook['sheets']:
            E.require(isinstance(sheet,dict) and isinstance(sheet.get('cells'),list),'sheet cells missing')
            rows=[]
            for cell in sheet['cells']:
                E.require(isinstance(cell,dict) and set(cell) in (
                    {'address','value','number_format','data_type'},
                    {'address','value','number_format','data_type','cached_value'}),'unsupported cell key shape')
                fmt=cell['number_format'];E.require(isinstance(fmt,str),'number format must be exact text')
                if fmt not in formats:formats.append(fmt)
                row=[cell['address'],cell['value'],formats.index(fmt),cell['data_type']]
                if 'cached_value' in cell:row.append(cell['cached_value'])
                rows.append(row)
            sheet['cells']={'columns':list(TABLE_COLUMNS),'rows':rows}
    encoded={'encoding':TABLE_VERSION,'row_semantics':TABLE_NOTE,'number_formats':formats,'workbooks':result}
    E.require(E.canonical(decode_workbooks(encoded))==E.canonical(workbooks),'lossless workbook reconstruction differs')
    return encoded


def decode_workbooks(encoded):
    E.require(isinstance(encoded,dict) and set(encoded)=={'encoding','row_semantics','number_formats','workbooks'}
              and encoded['encoding']==TABLE_VERSION and encoded['row_semantics']==TABLE_NOTE,'unknown workbook table encoding')
    formats=encoded['number_formats']
    E.require(isinstance(formats,list) and all(isinstance(f,str) for f in formats)
              and len(formats)==len(set(formats)),'invalid exact format dictionary')
    result=copy.deepcopy(encoded['workbooks'])
    E.require(isinstance(result,dict) and set(result)=={'golden','delivered'},'complete workbook tables missing')
    for workbook in result.values():
        E.require(isinstance(workbook,dict) and isinstance(workbook.get('sheets'),list),'workbook tables missing')
        for sheet in workbook['sheets']:
            E.require(isinstance(sheet,dict) and isinstance(sheet.get('cells'),dict),'cell table missing')
            table=sheet['cells']
            E.require(set(table)=={'columns','rows'} and table['columns']==TABLE_COLUMNS and isinstance(table['rows'],list),'cell table columns changed')
            cells=[]
            for row in table['rows']:
                E.require(isinstance(row,list) and len(row) in (4,5),'cell row presence shape changed')
                index=row[2];E.require(type(index) is int and 0<=index<len(formats),'format index invalid')
                cell=dict(address=row[0],value=row[1],number_format=formats[index],data_type=row[3])
                if len(row)==5:cell['cached_value']=row[4]
                cells.append(cell)
            sheet['cells']=cells
    return result


def project(request,*,projection=None):
    E.require(isinstance(request,dict) and set(request)=={'model','reasoning_effort','messages'},'unexpected disclosure request')
    messages=request['messages']
    E.require(isinstance(messages,list) and len(messages)==2 and all(isinstance(m,dict) and set(m)=={'role','content'} and isinstance(m['content'],str) for m in messages)
              and [m['role'] for m in messages]==['system','user'],'expected exact system/user disclosure messages')
    E.require(projection in (None,TABLE_VERSION,RECORD_PROJECTION),'unknown physical projection version')
    result=copy.deepcopy(request)
    body=json.loads(result['messages'][1]['content'])
    if isinstance(body,dict) and 'evidence_projection' in body:
        # New owning requests already carry scoped assessments and complete
        # workbook tables. Preserve that exact six-field protocol: decoding is
        # validation only, never permission to drop its projection declaration
        # or filter any second set of facts. This is only a serialization change.
        E.require(projection in (TABLE_VERSION,RECORD_PROJECTION),'already projected payload requires explicit lossless version')
        from scripts.batch.harbor_disclosure_evidence import decode_body,compact_records
        decoded=decode_body(body)
        if projection==RECORD_PROJECTION:body=compact_records(body)
        result['messages'][1]['content']=json.dumps(body,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        restored=json.loads(result['messages'][1]['content'])
        E.require(E.canonical(restored)==E.canonical(body) and E.canonical(decode_body(restored))==E.canonical(decoded),
                  'compacted projected payload changed evidence')
        return result
    E.require(projection!=RECORD_PROJECTION,'record encoding requires original versioned evidence')
    E.require(set(body)=={'manifest','manifest_sha256','workbooks','instruction','disclosure'},'unexpected disclosure payload')
    disclosure=body['disclosure'];records=disclosure['records'];agents=disclosure['agent_records']
    E.require(isinstance(records,list) and records and isinstance(agents,list) and agents,'disclosure records missing')
    keys=set()
    for record in records+agents:
        E.require(isinstance(record,dict) and isinstance(record.get('cell_keys'),list),'record cell identities missing')
        E.require(record['cell_keys'] and all(canonical_cell(k) for k in record['cell_keys']),'uncertain disclosed cell scope cannot be compacted')
        keys.update(record['cell_keys'])
    assessments=disclosure['method_assessments']
    from scripts.batch import harbor_method_assessments as indexed
    E.require(isinstance(assessments,(list,dict)),'method assessments missing')
    def relevant(assessment):
        E.require(isinstance(assessment,dict) and isinstance(assessment.get('cell_keys'),list),'assessment cell identities missing')
        # Unknown/empty scope cannot safely be classified as irrelevant.
        return not assessment['cell_keys'] or not all(canonical_cell(k) for k in assessment['cell_keys']) or bool(keys.intersection(assessment['cell_keys']))
    retained=indexed.filtered(assessments,relevant)
    E.require(indexed.logical_length(retained)<indexed.logical_length(assessments),'projection removes no irrelevant assessments')
    disclosure['method_assessments']=retained
    if projection==TABLE_VERSION:body['workbooks']=encode_workbooks(body['workbooks'])
    result['messages'][1]['content']=json.dumps(body,ensure_ascii=False,allow_nan=False,
        **({'separators':(',',':')} if projection==TABLE_VERSION else {}))
    return result


def original(permit):
    from scripts.batch.harbor_model_review_provenance import Policy
    value=read(permit)['original_prepared']
    E.require(value.get('physical_repair') is None,'a physical repair cannot be repaired')
    return E.PreparedCall(**{**value,'policy':Policy(**value['policy']),
        'scope':E.SemanticScope(**value['scope']),'budget_evidence':tuple(value['budget_evidence'])})


def _rejected(prepared,campaign):
    E.require(prepared.physical_repair is None and prepared.audit_context is None,'only original disclosure requests may be repaired')
    E.require(prepared.scope.lane=='harbor-disclosure-writer' and prepared.scope.budget_id=='disclosure-review'
              and prepared.ordinal==prepared.limit==1 and prepared.response_mode=='json_object','not the original disclosure logical attempt')
    result=E.inspect(prepared,campaign)
    E.require(result.status=='http_rejected' and result.call is None,'completed or uncertain calls cannot be repaired')
    root=E.location(prepared,campaign);outcome=read(result.outcome)
    E.require(outcome['details']=={'http_status':400,'automatic_replay':False},'not a terminal explicit HTTP 400')
    E.require(not any((root/name).exists() for name in ('response.raw.json','completed.json','parsed.json','content.txt')),'response evidence forbids repair')
    errors=list(root.glob('transport-*.http-error.json'));starts=list(root.glob('transport-*.started.json'))
    E.require(len(errors)==len(starts)==1 and errors[0].name=='transport-00.http-error.json','multiple or uncertain transports cannot be repaired')
    error=read(E.record(errors[0]));body=read(error['body'])
    E.require(error['status']==400 and error['retry'] is False and isinstance(body,dict)
              and set(body)=={'error'} and isinstance(body['error'],dict),'HTTP error body is not an explicit rejection')
    E.require(not list(root.glob('transport-*.connection-error.json')),'uncertain connection evidence forbids repair')
    return result.outcome,E.record(root/'plan.json'),E.record(root/'identity.json')


def original_identity(permit):
    value=read(permit)
    if value.get('schema_version')=='harbor-model-pretransport-recovery/v1':
        from scripts.batch.harbor_model_pretransport_recovery import original_identity as validated
        return validated(permit)
    return read(read(value['original_identity']))


def validate_prepared(prepared,campaign):
    permit=prepared.physical_repair;value=read(permit)
    if value.get('schema_version')=='harbor-model-pretransport-recovery/v1':
        from scripts.batch.harbor_model_pretransport_recovery import validate_prepared as validated
        return validated(prepared,campaign)
    E.require(value['schema_version']==SCHEMA and value['physical_count']==1 and value['semantic_budget_reset'] is False,'invalid repair ceiling')
    implementation=value['implementation'];E.checked_bytes(implementation)
    E.require(implementation['sha256'] in ({E.record(__file__)['sha256']}|PRIOR_IMPLEMENTATIONS),'payload repair implementation changed')
    old=original(permit)
    E.require(asdict(campaign)=={**value['campaign'],'root':Path(value['campaign']['root']), 'slot_root':Path(value['campaign']['slot_root'])},'original campaign or model slots changed')
    rejected,plan,identity=_rejected(old,campaign)
    E.require((rejected,plan,identity)==(value['original_outcome'],value['original_plan'],value['original_identity']),'original rejection evidence substituted')
    old_body=json.loads(read(old.request)['messages'][1]['content'])
    if isinstance(old_body,dict) and 'evidence_projection' in old_body:
        # Earlier producers rejected this six-field protocol before preparing
        # any permit. Do not reinterpret their historical code hashes as if
        # they already supported this prospective serialization branch.
        E.require(implementation['sha256']==E.record(__file__)['sha256'],
                  'historical physical producer did not support projected payload')
    expected=replace(old,request=value['request'],physical_repair=permit)
    E.require(prepared==expected,'physical repair changed original semantic identity or budget')
    E.require(read(prepared.request)==project(read(old.request),projection=value.get('projection')),'repaired payload is not the exact complete projection')
    E.require(original_identity(permit)['attempt_id']==old.attempt_id,'original UUID changed')
    marker=E.location(old,campaign).parent/'physical-repair.json'
    E.require(not E.location(prepared,campaign).exists() or marker.exists(),'retained physical repair lacks its scope reservation')
    E.require(not marker.exists() or read(E.record(marker))==permit,'physical repair already reserved elsewhere')
    return value


def prepare(prepared,campaign,directory,*,projection=None):
    """Offline proposal only; execute reserves the original scope marker atomically."""
    rejected,plan,identity=_rejected(prepared,campaign)
    marker=E.location(prepared,campaign).parent/'physical-repair.json'
    E.require(not marker.exists(),'physical repair already reserved')
    request=project(read(prepared.request),projection=projection)
    directory=E.safe(directory);E.require(not directory.exists(),'repair preparation already exists')
    directory.mkdir(parents=True)
    E.save(directory/'request.json',request)
    E.save(directory/'permit.json',dict(schema_version=SCHEMA,physical_count=1,semantic_budget_reset=False,
        implementation=E.record(__file__),original_prepared=asdict(prepared),
        **({'projection':projection} if projection is not None else {}),
        campaign={'root':str(campaign.root),'slot_root':str(campaign.slot_root),'model_slots':campaign.model_slots},
        original_outcome=rejected,original_plan=plan,original_identity=identity,request=E.record(directory/'request.json')))
    repaired=replace(prepared,request=E.record(directory/'request.json'),physical_repair=E.record(directory/'permit.json'))
    validate_prepared(repaired,campaign)
    return repaired
