"""Synthetic HTML/HTTP fixtures only; actual networking always forbidden."""
from dataclasses import replace
import copy
import importlib.util
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'xl_ast_graph.py').is_file());sys.path.insert(0,str(ROOT))
BASE=Path(__file__).resolve().parents[1]/'scripts/batch'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
P=load('isolated_public_capture',BASE/'harbor_public_capture.py')
R=load('isolated_profile_projection',BASE/'harbor_profile_readiness.py')


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(P.durable.canonical(value));return P.record(path)


@pytest.fixture
def ctx(tmp_path,monkeypatch):
    tmp_path=tmp_path.resolve()
    def forbidden(*args,**kwargs):raise AssertionError('Actual network forbidden in synthetic capture tests')
    monkeypatch.setattr(P.socket,'getaddrinfo',forbidden);monkeypatch.setattr(P.socket,'create_connection',forbidden)
    monkeypatch.setattr(P,'exchange',forbidden)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_public_capture',P)
    raw=tmp_path/'original.xlsx';raw.write_bytes(b'Synthetic original identity only')
    run=tmp_path/'0087-variable-sources';run.mkdir()
    phase=write(run/'normalized.phase1.json',{'environment_id':'fcp-0087','variables':[{'value':1,'sources':[{'id':'private-audit-id','name':'Example Statistics','kind':'statistics','url':'https://www.example.com/','audit_notes':'private'}]}]})
    inventory=write(tmp_path/'inventory.json',{'value':[0,2024]})
    return P.CaptureSelection('0087',P.record(raw),phase,inventory,tmp_path/'persistent-capture-campaign')


def fake(monkeypatch,pages):
    seen=[]
    def exchange(url):
        seen.append(url)
        row=pages[url]
        if isinstance(row,BaseException):raise row
        if isinstance(row,str):row={'body':row.encode()}
        return {'status':200,'content_type':'text/html; charset=utf-8','content_encoding':'identity','body_complete':True,'content_length':None,'location':None,'addresses':['93.184.216.34'],'connected_address':'93.184.216.34',**row}
    monkeypatch.setattr(P,'exchange',exchange)
    return seen


@pytest.mark.parametrize('body,fragment',[
    ('<nav><a href="/login">Sign in</a></nav><main><h1>Monthly indicators</h1><p>Updated each month after the scheduled release.</p></main>','Monthly indicators'),
    ('<style>.Sheet!A1 {color:red}</style><script>const x="Sheet!B4";</script><main>Monthly releases</main>','Monthly releases'),
    ('<main>Series version 1. Year 2024. Count 0.</main>','Series version [redacted]. Year [redacted]. Count [redacted].'),
    ('<main><p>Public articles discuss sign in and paywall practices.</p></main>','Public articles discuss sign in and paywall practices.'),
])
def test_legitimate_public_content_projects_without_invented_barrier(body,fragment):
    result=P.project(body.encode(),content_type='text/html',forbidden={'0','1','2024'})
    assert result['disposition']=='ok' and fragment in result['text']
    assert 'Sheet!' not in result['text'] and 'Sign in' not in result['text']


@pytest.mark.parametrize('body,reason',[
    ('<main><form action="/login"><input type="password"></form></main>','auth_login_sso_password'),
    ('Sign in to continue.','auth_login_sso_password'),
    ('<main><form id="cf-chl-form">Verify you are human</form></main>','bot_challenge'),
    ('Verify you are human.','bot_challenge'),
    ('<main data-access="subscriber-only">Article behind subscription</main>','paywall'),
    ('Subscribe to continue reading.','paywall'),
])
def test_actual_gate_returns_no_source_details(body,reason):
    result=P.project(body.encode(),content_type='text/html',forbidden=set())
    assert result['disposition']==reason and result['text']=='' and result['links']==[]


@pytest.mark.parametrize('status,content_type,encoding,reason',[(401,'text/html','identity','http_401'),(403,'text/plain','identity','http_403'),(404,'text/html','identity','unreachable'),(200,'application/pdf','identity','unsupported_content'),(200,'text/html; charset=utf-16','identity','unsupported_content'),(200,'text/html','gzip','unsupported_content')])
def test_nonpublic_or_unsupported_responses_do_not_project(status,content_type,encoding,reason):
    assert P.project(b'<main>Never extract this blocked detail</main>',content_type=content_type,status=status,content_encoding=encoding,forbidden=set())['disposition']==reason


def test_exact_raw_body_retained_projection_rederived_no_refetch(ctx,monkeypatch):
    body='<nav>Sign in</nav><main>Monthly indicators version 1.</main>'
    seen=fake(monkeypatch,{'https://www.example.com/':body})
    inventory,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='COMPLETE' and len(seen)==1
    cap=P.read(inventory['sources'][0]['captures'][0]);assert Path(cap['body']['path']).read_bytes()==body.encode()
    sources,forbidden=P.context(ctx)
    pages,evidence=R.captured_pages(inventory['sources'][0]['captures'],sources[0]['canonical_url'],forbidden)
    assert pages[0]['body']=='Monthly indicators version [redacted].'
    assert P.collect(ctx)==(inventory,outcomes) and len(seen)==1


def test_zero_call_internal_source(ctx,monkeypatch):
    spec=P.read(ctx.phase1);spec['variables'][0]['sources'][0].update(url='internal://supplied/book',kind='internal')
    selected=replace(ctx,phase1=write(Path(ctx.phase1['path']),spec))
    inventory,outcomes=P.collect(selected)
    assert inventory['sources']==[] and outcomes==[] and not ctx.campaign.exists()


def test_successful_first_page_cannot_hide_later_incomplete_exchange(ctx,monkeypatch):
    fake(monkeypatch,{'https://www.example.com/':'<main>Public metadata<a href="/next">Details</a></main>',
                      'https://www.example.com/next':TimeoutError('incomplete linked read')})
    inventory,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='STOPPED' and len(inventory['sources'][0]['captures'])==1
    with pytest.raises(ValueError):
        R.captured_pages(inventory['sources'][0]['captures'],'https://www.example.com/',{'1','0','2024'})


def test_consumer_cannot_omit_successful_later_page(ctx,monkeypatch):
    fake(monkeypatch,{'https://www.example.com/':'<main>Public metadata<a href="/next">Details</a></main>',
                      'https://www.example.com/next':'<main>More metadata</main>'})
    inventory,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='COMPLETE' and len(inventory['sources'][0]['captures'])==2
    with pytest.raises(ValueError):
        R.captured_pages(inventory['sources'][0]['captures'][:1],'https://www.example.com/',{'1','0','2024'})


def test_three_reads_include_redirects_and_only_direct_same_origin_links(ctx,monkeypatch):
    seen=fake(monkeypatch,{
        'https://www.example.com/':{'status':302,'location':'/home','body':b'redirect'},
        'https://www.example.com/home':'<main>Public data<a href="/next">Details</a><a href="/other">Other</a><a href="https://external.example.com/">Outside</a><a href="/login">Sign in</a></main>',
        'https://www.example.com/next':'<main>Public metadata</main>',
    })
    inventory,outcomes=P.collect(ctx)
    assert seen==['https://www.example.com/','https://www.example.com/home','https://www.example.com/next']
    assert outcomes[0]['status']=='COMPLETE' and outcomes[0]['http_exchanges_used']==3
    assert len(inventory['sources'][0]['captures'])==2


@pytest.mark.parametrize('target',['https://other.example.org/','/login','/2024','https://user:password@www.example.com/'])
def test_redirect_to_external_auth_or_private_url_stops_without_following(ctx,monkeypatch,target):
    seen=fake(monkeypatch,{'https://www.example.com/':{'status':302,'location':target,'body':b'redirect'}})
    _,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='STOPPED' and len(seen)==1
    assert P.collect(ctx)[1]==outcomes and len(seen)==1


@pytest.mark.parametrize('response',[{'status':403,'body':b'Forbidden'}, {'body':b'<main><input type="password"></main>'}, {'body':b'<main data-paywall="true">Subscribe</main>'}])
def test_barrier_closed_without_access_retry(ctx,monkeypatch,response):
    seen=fake(monkeypatch,{'https://www.example.com/':response})
    inventory,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='STOPPED' and len(seen)==1
    with pytest.raises(ValueError):R.captured_pages(inventory['sources'][0]['captures'],'https://www.example.com/',{'1','0','2024'})
    P.collect(ctx);assert len(seen)==1


def test_actual_exchange_limit_closes_redirect_chain(ctx,monkeypatch):
    seen=fake(monkeypatch,{
        'https://www.example.com/':{'status':302,'location':'/a','body':b'a'},
        'https://www.example.com/a':{'status':302,'location':'/b','body':b'b'},
        'https://www.example.com/b':{'status':302,'location':'/c','body':b'c'},
    })
    _,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='STOPPED' and len(seen)==3


def test_interruption_never_replays_unknown_capture(ctx,monkeypatch):
    seen=fake(monkeypatch,{'https://www.example.com/':KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):P.collect(ctx)
    _,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='STOPPED' and outcomes[0]['reason']=='unknown_completion' and len(seen)==1


@pytest.mark.parametrize('changed',['raw','projection','policy','method','address','receipt_missing','changed_forbidden','unknown_projection_field'])
def test_rederived_capture_rejects_tampering(ctx,monkeypatch,changed):
    fake(monkeypatch,{'https://www.example.com/':'<main>Monthly indicators</main>'})
    inventory,_=P.collect(ctx);bindings=inventory['sources'][0]['captures'];cap=P.read(bindings[0]);forbidden={'1','0','2024'}
    if changed=='raw':Path(cap['body']['path']).write_bytes(b'altered')
    elif changed=='policy':cap['projection_policy_sha256']='f'*64;bindings=[write(Path(bindings[0]['path']),cap)]
    elif changed in {'projection','unknown_projection_field'}:
        projection=P.read(cap['projection'])
        if changed=='projection':projection['text']='independently fabricated metadata'
        else:projection['passed']=True
        cap['projection']=write(Path(cap['projection']['path']),projection);bindings=[write(Path(bindings[0]['path']),cap)]
    elif changed=='receipt_missing':cap['http_exchanges']=[];bindings=[write(Path(bindings[0]['path']),cap)]
    elif changed=='changed_forbidden':forbidden.add('Monthly')
    else:
        row=P.read(cap['http_exchanges'][0])
        if changed=='address':row['addresses']=['127.0.0.1'];row['connected_address']='127.0.0.1'
        else:
            intent=P.read(row['intent']);intent['method']='POST';row['intent']=write(Path(row['intent']['path']),intent)
        cap['http_exchanges']=[write(Path(cap['http_exchanges'][0]['path']),row)];bindings=[write(Path(bindings[0]['path']),cap)]
    with pytest.raises(ValueError):R.captured_pages(bindings,'https://www.example.com/',forbidden)


def test_changed_context_cannot_reset_same_capture_scope(ctx,monkeypatch):
    seen=fake(monkeypatch,{'https://www.example.com/':'<main>Public monthly series</main>'});P.collect(ctx)
    spec=P.read(ctx.phase1);spec['variables'][0]['value']=777
    new=replace(ctx,phase1=write(Path(ctx.phase1['path']),spec))
    with pytest.raises(ValueError,match='budget reset'):P.collect(new)
    assert len(seen)==1


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1','192.0.2.1'])
def test_dns_non_global_rejected_before_connection(ctx,monkeypatch,address):
    monkeypatch.setattr(P.subprocess,'run',lambda *args,**kwargs:type('DNS',(),{'stdout':json.dumps([address])})())
    with pytest.raises(ValueError):P.global_addresses('example.com',443)


def test_projection_v1_remains_strict(ctx,monkeypatch):
    # Original v1 remains unchanged even for a legitimate nav label.
    body=Path(ctx.phase1['path']).parent/'old-body.html';body.write_text('<nav>Sign in</nav><main>Public metadata</main>')
    ns=P.time.time_ns();old={'schema_version':'harbor-public-capture/v1','requested_url':'https://www.example.com/','final_url':'https://www.example.com/','redirects':[],'status':200,'content_type':'text/html','started_at_ns':ns,'completed_at_ns':ns+1,'accessed_at':P.stamp(ns),'authentication':'none','body':P.record(body)}
    binding=write(body.with_suffix('.json'),old)
    with pytest.raises(ValueError,match='barrier'):R.captured_pages([binding],'https://www.example.com/',set())


def test_public_article_with_separate_login_widget_or_challenge_script_is_available():
    html='<script src="/cdn-cgi/challenge-platform/widget.js"></script><main><article>Public monthly release metadata.</article></main><aside><form action="/login"><input type="password"></form></aside>'
    assert P.project(html.encode(),content_type='text/html',forbidden=set())['disposition']=='ok'


def test_paywall_overlay_stays_closed_even_with_public_excerpt():
    html='<main>Public introductory excerpt.</main><div class="paywall-overlay">Subscribe to continue reading.</div>'
    projected=P.project(html.encode(),content_type='text/html',forbidden=set())
    assert projected['disposition']=='paywall' and projected['text']==''


@pytest.mark.parametrize('text,value',[('Sentence 1.','1'),('1.0 items','1'),('1e0 items','1'),('Total 1,000.','1000'),('Rate 25%.','0.25'),('Version +1.','1')])
def test_equivalent_primitive_spellings_redact_without_digit_exemption(text,value):
    redacted=P.redact(text,{value})
    assert '[redacted]' in redacted


@pytest.mark.parametrize('text,forbidden',[
    ('Total 1,327.163 and 1,328.488.',{'1'}),
    ('Signed value -1.',{'1'}),
    ('Grouped value 7,123,456.',{'123'}),
])
def test_numeric_fragment_gate_redacts_whole_token(text,forbidden):
    redacted=P.redact(text,forbidden)
    assert '[redacted]' in redacted
    assert P.validator().leaked_value(redacted,forbidden) is None
    assert not any(char.isdigit()for char in redacted)


def test_numeric_fragment_redaction_preserves_unrelated_numbers_and_text():
    assert P.redact('Series metadata: 19 and 23,700.363; total 1,327.163.',{'1'}) == 'Series metadata: 19 and 23,700.363; total [redacted].'


@pytest.mark.parametrize('ip',['224.0.0.1','ff02::1','255.255.255.255'])
def test_multicast_broadcast_not_valid_http_targets(ip):
    assert not P.public_address(ip)


def test_pinned_get_uses_no_auth_cookie_or_proxy_headers(ctx,monkeypatch):
    observed={}
    monkeypatch.setattr(P.subprocess,'run',lambda *args,**kwargs:type('DNS',(),{'stdout':json.dumps(['93.184.216.34'])})())
    class Sock:
        def settimeout(self,value):assert value > 0
        def shutdown(self,how):pass
        def close(self):pass
    def connect(address,timeout):observed['connect']=address;return Sock()
    monkeypatch.setattr(P.socket,'create_connection',connect)
    class TLS:
        def wrap_socket(self,sock,server_hostname):observed['tls_hostname']=server_hostname;return sock
    monkeypatch.setattr(P.ssl,'create_default_context',lambda:TLS())
    class Response:
        status=200
        def read(self,limit):observed['limit']=limit;return b'<main>Public metadata</main>'
        def getheader(self,name,default=None):return {'Content-Type':'text/html'}.get(name,default)
    class Connection:
        def __init__(self,host,port,timeout):observed['host']=(host,port)
        def request(self,method,target,headers):observed['request']=(method,target,headers)
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(P.http.client,'HTTPConnection',Connection)
    # ctx normally blocks exchange entirely; restore only the fixed production
    # function, whose OS/TLS/HTTP boundaries above are all synthetic.
    actual=load('isolated_capture_transport',BASE/'harbor_public_capture.py')
    result=actual.exchange('https://www.example.com/metadata?series=public')
    assert result['status']==200 and observed['connect']==('93.184.216.34',443)
    assert observed['tls_hostname']=='www.example.com' and observed['request'][0]=='GET'
    assert observed['limit']==P.MAX_BYTES+1
    assert not {'authorization','cookie','proxy-authorization'} & {x.lower() for x in observed['request'][2]}


def test_oversize_prefix_retained_but_never_accepted(ctx,monkeypatch):
    seen=fake(monkeypatch,{'https://www.example.com/':{'body':b'x'*(P.MAX_BYTES+1),'body_complete':False}})
    inventory,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='STOPPED' and inventory['sources'][0]['captures']==[] and len(seen)==1
    bodies=list(ctx.campaign.glob('*/http-01.body'))
    assert len(bodies)==1 and bodies[0].stat().st_size==P.MAX_BYTES+1


def test_v2_capture_feeds_real_profile_producer_and_executor_journals(ctx,monkeypatch):
    import io,time,uuid
    from scripts.batch import harbor_model_executor as E
    producer=load('isolated_capture_consumer',ROOT/'scripts/batch/harbor_profile_producer.py');producer.profiles=R
    fake(monkeypatch,{'https://www.example.com/':'<nav>Sign in</nav><main>Updated each month after the scheduled release.</main>'})
    inventory,outcomes=P.collect(ctx);assert outcomes[0]['status']=='COMPLETE'
    root=ctx.campaign.parent;captures=write(root/'captures.json',inventory);policy=producer.Policy('synthetic-project')
    campaign=E.Campaign(root/'model-journals',root/'model-slots')
    monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root));monkeypatch.setenv('HARBOR_MODEL_SLOTS','2')
    cap=P.read(inventory['sources'][0]['captures'][0]);profile=P.validator().valid_fixture()['profiles'][0]
    profile['capture']['evidence_sha256']=None;profile['capture']['attempted_at']=cap['accessed_at']
    for attr in profile['evidence']['attributions']:attr['accessed_at']=cap['accessed_at']
    seen=[]
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    def opened(request,timeout):
        payload=json.loads(request.data);content=json.loads(payload['messages'][1]['content']);seen.append(content)
        value=copy.deepcopy(content.get('fragment',profile))
        if 'fragment' in content:value['review']={'status':'accepted','reviewer':'Independent synthetic reviewer','reviewed_at':P.stamp(time.time_ns()),'notes':'The source prose matches retained public evidence.'}
        raw={'id':str(uuid.uuid4()),'object':'chat.completion','model':policy.model,'created':int(time.time()),'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(value)}}]}
        return Response(json.dumps(raw).encode())
    monkeypatch.setattr(E,'_open',opened)
    inp=write(root/'profile-input.json',{'schema_version':producer.INPUT_SCHEMA,'workbook_id':ctx.workbook_id,'original_source':ctx.original_source,'phase1':ctx.phase1,'inventory':ctx.inventory,'captures':captures,'policy':producer.asdict(policy),'campaign':{'root':str(campaign.root),'slot_root':str(campaign.slot_root),'model_slots':2}})
    plan=producer.prepare(inp,root/'profile-prepared')
    result=producer.run(plan,dispatch=True,credential='synthetic-test-token')
    assert result['status']=='COMPLETE',result
    assert len(seen)==2 and R.validate_profiles(result['selection'])['confidence']=='high'
    assert all('<nav>' not in json.dumps(row) and 'Sign in' not in json.dumps(row) and 'private-audit-id' not in json.dumps(row) for row in seen)


@pytest.mark.parametrize('url',['https://www.example.com/%6Cogin','https://www.example.com/data?api_key=private','https://www.example.com/data?X-Amz-Signature=private','https://www.example.com/report?year=%32%30%32%34','https://www.example.com/?value=1.0'])
def test_encoded_auth_or_financial_url_is_rejected_before_get(url):
    with pytest.raises(ValueError):P.allowed(url,'https://www.example.com/',{'1','2024'})


def test_profile_body_redaction_does_not_modify_raw_capture_or_phase1(ctx,monkeypatch):
    body=b'<main>Series version 1.0 and 2024 release metadata.</main>'
    original=Path(ctx.phase1['path']).read_bytes()
    fake(monkeypatch,{'https://www.example.com/':{'body':body}})
    inventory,outcomes=P.collect(ctx);cap=P.read(inventory['sources'][0]['captures'][0])
    assert outcomes[0]['status']=='COMPLETE' and Path(cap['body']['path']).read_bytes()==body
    assert Path(ctx.phase1['path']).read_bytes()==original
    assert P.read(cap['projection'])['text']=='Series version [redacted] and [redacted] release metadata.'


def test_saved_completion_flag_cannot_override_observed_barrier(ctx,monkeypatch):
    seen=fake(monkeypatch,{'https://www.example.com/':{'status':403,'body':b'Forbidden'}})
    P.collect(ctx);outpath=next(ctx.campaign.glob('*/outcome.json'));outcome=P.read(P.record(outpath))
    outcome.update(status='COMPLETE',reason=None);write(outpath,outcome)
    with pytest.raises(ValueError):P.collect(ctx)
    assert len(seen)==1


def test_saved_consumption_cannot_drop_an_http_attempt(ctx,monkeypatch):
    seen=fake(monkeypatch,{'https://www.example.com/':'<main>Public metadata</main>'});P.collect(ctx)
    outpath=next(ctx.campaign.glob('*/outcome.json'));outcome=P.read(P.record(outpath));outcome['http_exchanges_used']=0;write(outpath,outcome)
    with pytest.raises(ValueError,match='consumption'):P.collect(ctx)
    assert len(seen)==1


@pytest.mark.parametrize('length,complete',[('3',True),('4',False),('2',False),('-1',False),('3,3',False),(None,True)])
def test_content_length_must_establish_complete_response(length,complete):
    assert P.complete_body(b'abc',length) is complete


def test_receipt_cannot_assert_complete_despite_wrong_content_length(ctx,monkeypatch):
    fake(monkeypatch,{'https://www.example.com/':{'body':b'<main>Public metadata</main>','content_length':'999'}})
    _,outcomes=P.collect(ctx)
    assert outcomes[0]['status']=='STOPPED'


def test_dns_uses_fixed_program_and_absolute_timeout(ctx,monkeypatch):
    seen=[]
    def resolver(argv,**kwargs):
        seen.append((argv,kwargs));return type('DNS',(),{'stdout':'["93.184.216.34"]'})()
    monkeypatch.setattr(P.subprocess,'run',resolver)
    assert P.global_addresses('www.example.com',443,timeout=2)==['93.184.216.34']
    assert seen[0][0]==[sys.executable,'-I','-c',P.DNS_PROGRAM,'www.example.com','443']
    assert seen[0][1]['timeout']==2 and seen[0][1]['check'] is True


def test_dns_timeout_propagates_without_connection_or_retry(ctx,monkeypatch):
    def timeout(*args,**kwargs):raise P.subprocess.TimeoutExpired('fixed DNS',2)
    monkeypatch.setattr(P.subprocess,'run',timeout)
    with pytest.raises(P.subprocess.TimeoutExpired):P.global_addresses('www.example.com',443,timeout=2)


def test_total_deadline_interrupts_slow_header_or_body_peer(ctx,monkeypatch):
    import threading
    observed={};closed=threading.Event()
    monkeypatch.setattr(P.subprocess,'run',lambda *args,**kwargs:type('DNS',(),{'stdout':'["93.184.216.34"]'})())
    class Sock:
        def settimeout(self,value):pass
        def shutdown(self,how):observed['interrupted']=True;closed.set()
        def close(self):observed['closed']=True
    monkeypatch.setattr(P.socket,'create_connection',lambda *args,**kwargs:Sock())
    class TLS:
        def wrap_socket(self,sock,server_hostname):return sock
    monkeypatch.setattr(P.ssl,'create_default_context',lambda:TLS())
    class Response:
        status=200
        def read(self,limit):assert closed.wait(.5);return b'late public bytes'
        def getheader(self,name,default=None):return {'Content-Type':'text/plain'}.get(name,default)
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):pass
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(P.http.client,'HTTPConnection',Connection)
    actual=load('isolated_capture_deadline',BASE/'harbor_public_capture.py');monkeypatch.setattr(actual,'TIMEOUT',.03)
    with pytest.raises(ValueError,match='deadline'):actual.exchange('https://www.example.com/')
    assert observed['interrupted'] and observed['closed']


@pytest.mark.parametrize('container',[list,tuple])
def test_completed_capture_exact_ordered_sequence_contract(ctx,monkeypatch,container):
    fake(monkeypatch,{'https://www.example.com/':'<main>Public metadata</main>'})
    inventory,outcomes=P.collect(ctx)
    bindings=inventory['sources'][0]['captures']
    before={p.name:P.record(p)['sha256'] for p in Path(bindings[0]['path']).parent.iterdir() if p.is_file()}
    actual=P.pages(container(bindings),'https://www.example.com/',P.context(ctx)[1])
    assert actual==P.pages(bindings,'https://www.example.com/',P.context(ctx)[1])
    assert before=={p.name:P.record(p)['sha256'] for p in Path(bindings[0]['path']).parent.iterdir() if p.is_file()}


@pytest.mark.parametrize('change',['omit','reverse','duplicate','altered','iterator','mapping'])
def test_completed_capture_sequence_normalization_never_loses_closure(ctx,monkeypatch,change):
    fake(monkeypatch,{'https://www.example.com/':'<main>Public metadata<a href="/about">About</a></main>',
                     'https://www.example.com/about':'<main>Method metadata</main>'})
    inventory,outcomes=P.collect(ctx);assert outcomes[0]['status']=='COMPLETE'
    bindings=inventory['sources'][0]['captures'];assert len(bindings)==2
    if change=='omit':candidate=tuple(bindings[:1])
    elif change=='reverse':candidate=tuple(reversed(bindings))
    elif change=='duplicate':candidate=(bindings[0],bindings[0])
    elif change=='altered':candidate=(bindings[0],{**bindings[1],'sha256':'a'*64})
    elif change=='iterator':candidate=iter(bindings)
    else:candidate={0:bindings[0],1:bindings[1]}
    with pytest.raises(ValueError):P.pages(candidate,'https://www.example.com/',P.context(ctx)[1])
