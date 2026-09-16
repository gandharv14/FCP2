"""Read one complete public response whose original redaction step stopped.

The old capture remains stopped. This distinct receipt covers only the observed
canonical page; it adds no HTTP reads and makes no claim about unvisited links.
"""
from pathlib import Path
import ast,hashlib,json

SCHEMA='harbor-retained-public-page/v1'
FIELDS={'schema_version','helper','original','selected_redaction','capture_plan','stopped_outcome','http','failure','phase1','inventory','source','authorization','projection','started_at_ns','completed_at_ns','accessed_at'}
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical public projection evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'public projection evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def selected_redact(P,original,selected):
    require(binding(P.__file__)==original and binding(selected['path'])==selected,'original public capture code changed')
    trees=[];functions=[]
    for r in(original,selected):
        tree=ast.parse(Path(r['path']).read_text());found=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='redact']
        require(len(found)==1,'one exact redaction function required');functions.append(found[0]);tree.body.remove(found[0]);trees.append(ast.dump(tree))
    require(trees[0]==trees[1],'public capture changed beyond redaction')
    namespace=dict(P.__dict__)
    exec(compile(ast.Module(body=[functions[1]],type_ignores=[]),selected['path'],'exec'),namespace)
    return namespace['redact']

def derive(row,canonical,forbidden,P):
    q=read(row);require(set(q)==FIELDS and q['schema_version']==SCHEMA and q['helper']==binding(__file__),'closed retained public page required')
    require(q['authorization']['sha256']==AUTH and binding(q['authorization']['path'])==q['authorization'],'explicit projection recovery authority required')
    selection=read(q['capture_plan']);outcome=read(q['stopped_outcome']);response=read(q['http']);failure=read(q['failure'])
    root=Path(q['capture_plan']['path']).parent
    require(q['capture_plan']==binding(root/'plan.json')and q['stopped_outcome']==binding(root/'outcome.json')and q['http']==binding(root/'http-01.result.json')and q['failure']==binding(root/'failure.json'),'original capture namespace differs')
    require(set(outcome)=={'schema_version','status','reason','captures','http_exchanges_used','automatic_retry','artifacts'}and outcome['schema_version']=='harbor-public-capture-outcome/v1'and outcome['status']=='STOPPED'and outcome['reason']=='capture_failed'and outcome['captures']==[]and type(outcome['http_exchanges_used'])is int and outcome['http_exchanges_used']==1 and outcome['automatic_retry']is False,'one stopped uncaptured HTTP response required')
    expected={'plan.json','failure.json','http-01.intent.json','http-01.result.json','http-01.body'}
    require(set(outcome['artifacts'])==expected and set(p.name for p in root.iterdir())==expected|{'scope.lock','outcome.json'}and all(binding(root/n)['sha256']==h for n,h in outcome['artifacts'].items()),'capture inventory is incomplete or has later effects')
    require(failure==dict(type='AdmissionError',message='projection redaction did not close',automatic_retry=False),'different capture failure cannot be recovered')
    require(set(selection)=={'schema_version','selection','source','policy_sha256','max_http_exchanges','timeout_seconds','automatic_retry','max_response_bytes'}and selection['schema_version']=='harbor-public-capture-plan/v1'and selection['policy_sha256']==q['original']['sha256']and selection['max_http_exchanges']==P.MAX_READS and selection['timeout_seconds']==P.TIMEOUT and selection['max_response_bytes']==P.MAX_BYTES and selection['automatic_retry']is False,'original public capture policy differs')
    scope=selection['selection'];require(set(scope)=={'workbook_id','original_source','phase1','inventory','campaign'}and scope['original_source']==q['source']and scope['phase1']==q['phase1']and scope['inventory']==q['inventory']and read(q['phase1'])['environment_id']=='fcp-'+scope['workbook_id'],'original source selection differs')
    for r in(q['source'],q['inventory']):require(binding(r['path'])==r,'source evidence changed')
    validator=P.validator();actual=validator.forbidden_values([Path(q['phase1']['path']),Path(q['inventory']['path'])]);require(set(forbidden)==set(actual),'redaction forbidden inputs differ')
    require(selection['source']['canonical_url']==canonical==P.allowed(canonical,canonical,forbidden),'canonical source URL differs')
    intent_row=binding(root/'http-01.intent.json');intent=read(intent_row)
    require(set(intent)=={'schema_version','url','method','started_at_ns','policy_sha256','authentication','cookies'}and intent==dict(schema_version='harbor-public-http-intent/v1',url=canonical,method='GET',started_at_ns=intent['started_at_ns'],policy_sha256=q['original']['sha256'],authentication='none',cookies=False),'original canonical public GET required')
    require(set(response)=={'schema_version','status','content_type','content_encoding','body_complete','content_length','location','addresses','connected_address','intent','body','completed_at_ns'}and response['schema_version']=='harbor-public-http-result/v1'and response['intent']==intent_row and response['body']==binding(root/'http-01.body')and type(response['status'])is int and response['status']==200 and response['location']is None and response['body_complete']is True,'complete direct HTTP200 response required')
    require(type(intent['started_at_ns'])is int and type(response['completed_at_ns'])is int and 0<intent['started_at_ns']<=response['completed_at_ns']and response['addresses']and response['connected_address']in response['addresses']and all(P.public_address(a)for a in response['addresses']),'HTTP chronology or public endpoint differs')
    require(type(q['started_at_ns'])is int and type(q['completed_at_ns'])is int and q['started_at_ns']==intent['started_at_ns']and q['completed_at_ns']==response['completed_at_ns']and q['accessed_at']==P.stamp(intent['started_at_ns']),'observed page chronology differs')
    raw=Path(response['body']['path']).read_bytes();require(len(raw)<=P.MAX_BYTES and P.complete_body(raw,response['content_length']),'HTTP body is incomplete')
    selected=selected_redact(P,q['original'],q['selected_redaction']);options=dict(content_type=response['content_type'],forbidden=forbidden,status=200,content_encoding=response['content_encoding'])
    try:P.project(raw,**options)
    except ValueError as e:require(str(e)=='projection redaction did not close','original failure was not reproduced')
    else:raise ValueError('original capture projection was not stopped')
    # A function-local module namespace keeps original policy identity and all
    # unrelated capture calls untouched. The new function is separately bound.
    from types import FunctionType
    original=P.project;require(original.__globals__ is P.__dict__ and original.__closure__ is None,'plain original projector required')
    projected=FunctionType(original.__code__,dict(original.__globals__,redact=selected),original.__name__,original.__defaults__);projected.__kwdefaults__=original.__kwdefaults__
    projection=projected(raw,**options)
    require(projection['disposition']=='ok'and validator.leaked_value(projection['text'],forbidden)is None and validator.CELL_RE.search(projection['text'])is None and read(q['projection'])==projection,'selected projection failed independent redaction')
    page=dict(requested_url=canonical,final_url=canonical,status=200,content_type=projection['content_type'],accessed_at=P.stamp(intent['started_at_ns']),body=projection['text'])
    return page,[row,*[q[k]for k in sorted(FIELDS-{'schema_version','started_at_ns','completed_at_ns','accessed_at'})],intent_row,response['body']]

def pages(bindings,canonical,forbidden):
    require(isinstance(bindings,(list,tuple))and len(bindings)==1,'one retained canonical page required')
    from scripts.batch import harbor_public_capture as P
    page,evidence=derive(bindings[0],canonical,forbidden,P)
    return [page],evidence
