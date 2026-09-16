import ast,importlib.util,json,sys
from pathlib import Path
import pytest
from scripts.batch import harbor_public_capture as Current
from scripts.batch import harbor_public_projection_recovery as R

@pytest.fixture
def stopped(tmp_path,monkeypatch):
    root=tmp_path.resolve();(root/'xl_ast_graph.py').write_text('# root marker')
    selected=root/'selected.py';selected.write_text(Path(Current.__file__).read_text())
    tree=ast.parse(selected.read_text());fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name=='redact')
    numeric=next(x for x in fn.body if isinstance(x,ast.FunctionDef)and x.name=='numeric')
    numeric.body[-1]=ast.parse("return '[redacted]' if number in numbers or (percent and number/100 in numbers) else match.group()").body[0]
    # Use source segment replacement so everything except redact is identical.
    lines=selected.read_text().splitlines(True);lines[fn.lineno-1:fn.end_lineno]=[ast.unparse(fn)+'\n'];original=root/'original.py';original.write_text(''.join(lines))
    s=importlib.util.spec_from_file_location('original_capture_fixture',original);P=importlib.util.module_from_spec(s);monkeypatch.setitem(sys.modules,s.name,P)
    prior_path=list(sys.path)
    try:s.loader.exec_module(P)
    finally:sys.path[:]=prior_path
    monkeypatch.setattr(P,'validator',Current.validator)
    def put(name,value):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,sort_keys=True)+'\n');return R.binding(p)
    raw=root/'raw.xlsx';raw.write_bytes(b'original source')
    phase=put('phase.json',dict(environment_id='fcp-0215',variables=[dict(value=1)]));inventory=put('inventory.json',{})
    forbidden=Current.validator().forbidden_values([Path(phase['path']),Path(inventory['path'])]);assert forbidden=={'1'}
    source=dict(canonical_url='https://www.example.com/',source_name='Public metadata',kind='statistics');selection=P.CaptureSelection('0215',R.binding(raw),phase,inventory,root/'campaign')
    calls=[]
    def exchange(url):
        calls.append(url);return dict(status=200,content_type='text/html',content_encoding='identity',body_complete=True,content_length=None,location=None,addresses=['93.184.216.34'],connected_address='93.184.216.34',body=b'<main>Release 1,327.163.<a href="/details">Details</a></main>')
    monkeypatch.setattr(P,'exchange',exchange)
    outcome=P.capture_source(selection,source,forbidden);assert outcome['reason']=='capture_failed'and calls==[source['canonical_url']]
    home=next(selection.campaign.iterdir());selected_fn=R.selected_redact(P,R.binding(original),R.binding(selected))
    old=P.redact;P.redact=selected_fn
    try:projection=P.project((home/'http-01.body').read_bytes(),content_type='text/html',forbidden=forbidden)
    finally:P.redact=old
    auth=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
    q=dict(schema_version=R.SCHEMA,helper=R.binding(R.__file__),original=R.binding(original),selected_redaction=R.binding(selected),capture_plan=R.binding(home/'plan.json'),stopped_outcome=R.binding(home/'outcome.json'),http=R.binding(home/'http-01.result.json'),failure=R.binding(home/'failure.json'),phase1=phase,inventory=inventory,source=R.binding(raw),authorization=R.binding(auth),projection=put('projection.json',projection))
    started=P.read(P.record(home/'http-01.intent.json'))['started_at_ns'];ended=P.read(q['http'])['completed_at_ns'];q.update(started_at_ns=started,completed_at_ns=ended,accessed_at=P.stamp(started))
    def seal():return put('receipt.json',q)
    return P,q,seal,home,source['canonical_url'],forbidden,calls

def test_one_observed_page_keeps_stopped_capture_and_unvisited_link(stopped):
    P,q,seal,home,url,forbidden,calls=stopped;before={p.name:p.read_bytes()for p in home.iterdir()if p.is_file()};page,evidence=R.derive(seal(),url,forbidden,P)
    assert page['body']=='Release [redacted].Details'
    assert calls==[url]and {p.name:p.read_bytes()for p in home.iterdir()if p.is_file()}==before
    assert R.read(q['stopped_outcome'])['status']=='STOPPED'
    assert not(home/'http-02.intent.json').exists()
    assert q['projection']in evidence

@pytest.mark.parametrize('change',['body','redirect','incomplete','extra_read','projection','forbidden'])
def test_tampering_or_unobserved_capture_rejected(stopped,change):
    P,q,seal,home,url,forbidden,calls=stopped
    if change=='body':(home/'http-01.body').write_bytes(b'different')
    elif change in ('redirect','incomplete'):
        r=json.loads((home/'http-01.result.json').read_text());r.update(dict(status=302,location='/elsewhere')if change=='redirect'else dict(body_complete=False));(home/'http-01.result.json').write_text(json.dumps(r))
        q['http']=R.binding(home/'http-01.result.json');o=R.read(q['stopped_outcome']);o['artifacts']['http-01.result.json']=q['http']['sha256'];(home/'outcome.json').write_text(json.dumps(o));q['stopped_outcome']=R.binding(home/'outcome.json')
    elif change=='extra_read':(home/'http-02.intent.json').write_text('{}')
    elif change=='projection':Path(q['projection']['path']).write_text('{}');q['projection']=R.binding(q['projection']['path'])
    else:forbidden=set()
    with pytest.raises(ValueError):R.derive(seal(),url,forbidden,P)
    assert len(calls)==1

def test_selected_code_cannot_change_other_capture_behavior(stopped):
    P,q,seal,home,url,forbidden,calls=stopped;p=Path(q['selected_redaction']['path']);p.write_text(p.read_text().replace('MAX_READS = 3','MAX_READS = 4'));q['selected_redaction']=R.binding(p)
    with pytest.raises(ValueError,match='beyond redaction'):R.derive(seal(),url,forbidden,P)


def test_original_profile_reader_consumes_only_the_typed_observed_page(stopped,monkeypatch):
    P,q,seal,home,url,forbidden,calls=stopped
    import scripts.batch as package
    from scripts.batch import harbor_profile_readiness as profiles
    monkeypatch.setattr(package,'harbor_public_capture',P)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_public_capture',P)
    row=seal();pages,evidence=profiles.captured_pages([row],url,forbidden)
    assert len(pages)==1 and pages[0]['final_url']==url and row in evidence
    assert len(calls)==1 and q['completed_at_ns']==R.read(q['http'])['completed_at_ns']
    with pytest.raises(ValueError,match='one retained'):profiles.captured_pages([row,row],url,forbidden)
