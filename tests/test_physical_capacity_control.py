import json,threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import physical_capacity_control as H,coordinator as M,capacity_policy as P
from test_physical_capacity_extension import fixture


def test_real_database_copy_uses_original_api_and_preserves_unknowns(tmp_path,monkeypatch):
    h=tmp_path.resolve();c,_,_,args=fixture(h);monkeypatch.setattr(H.time,'time',lambda:1000.)
    row=H.keep(h/'args.json',args);before=c.status();proof=H.copy_proof((dict(arguments=row),dict(database=str(c.database)),M,P,args),h/'copy')
    assert H.read(proof)['status']=='FULL_DATABASE_COPY_PHYSICAL_EXTENSION_PASS'
    assert c.status()==before
    after=H.read(H.binding(h/'copy/after.json'));assert len(after['physical_capacity_extensions'])==1
    assert after['operations']==before['operations']


@pytest.mark.parametrize('failure',[None,'refused','lost'])
def test_once_http_retains_actual_response_and_cannot_reuse_journal(tmp_path,monkeypatch,failure):
    h=tmp_path.resolve();c,_,_,args=fixture(h);monkeypatch.setattr(H.time,'time',lambda:1000.)
    server=M.serve(c,'127.0.0.1',0,'w'*32,'a'*32);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    (h/'worker.token').write_text('w'*32);(h/'admin.token').write_text('a'*32)
    a=H.keep(h/'arguments.json',args);p=dict(arguments=a);old=dict(database=str(c.database),url='http://127.0.0.1:'+str(server.server_address[1]),private_directory=str(h))
    data=(p,old,M,P,args);monkeypatch.setattr(H,'load',lambda row:data);row=H.keep(h/'control.json',{})
    original=H.urllib.request.urlopen;calls=[]
    def send(request,**kw):
        payload=json.loads(request.data)
        if payload['action']=='permit_physical_capacity_extension':
            calls.append(payload)
            if failure=='lost':raise TimeoutError('no response')
            if failure=='refused':
                payload['arguments']['expected_revision']=4
                request=H.urllib.request.Request(request.full_url,data=json.dumps(payload).encode(),headers=dict(request.headers))
        return original(request,**kw)
    monkeypatch.setattr(H.urllib.request,'urlopen',send)
    try:
        if failure:
            with pytest.raises((ValueError,TimeoutError)):H.run(row,h/'once',execute=True)
            assert not(h/'once/result.json').exists()
            if failure=='refused':
                assert json.loads((h/'once/response-meta.json').read_text())['status']==409
                assert 'error'in json.loads((h/'once/response.raw').read_bytes())
            else:assert (h/'once/transport-error.json').exists()
        else:
            result=H.read(H.run(row,h/'once',execute=True));assert result['status']=='PHYSICAL_CAPACITY_EXTENSION_ACTIVE'
            assert json.loads((h/'once/response.raw').read_bytes())==result['record']
        with pytest.raises(ValueError,match='fresh'):H.run(row,h/'once',execute=True)
        assert len(calls)==1
    finally:server.shutdown();server.server_close();thread.join()


def test_changed_authorization_refused_before_process_or_database(tmp_path):
    h=tmp_path.resolve();auth=h/'auth.md';auth.write_text('unauthorized')
    p=dict(schema_version=H.SCHEMA,helper=H.binding(Path(H.__file__).resolve()),coordinator={},reader={},arguments={},authorization=H.binding(auth),process_identity='',declaration={})
    with pytest.raises(ValueError,match='authorization'):H.load(H.keep(h/'plan.json',p))
