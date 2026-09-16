"""Lossless status transport; no freshness, history, or mutation retry changes."""
import gzip
import io
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from email.message import Message

import pytest
from scripts.fleet import coordinator as C


@contextmanager
def running(coordinator):
    server=C.serve(coordinator,'127.0.0.1',0,'w'*32,'a'*32)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)
    finally:server.shutdown();server.server_close();thread.join()


def ledger(tmp_path):
    c=C.Coordinator(tmp_path/'ledger.sqlite',clock=lambda:1000)
    for i in range(20):c.enroll(str(i),'a'*64,'retained inventory '+str(i))
    c.reconcile('0','stopped');c.reconcile('1','stopped')
    a=c.claim('0','vm-1/a','first','version',lease_seconds=600)
    b=c.claim('1','vm-2/b','second','version',lease_seconds=600)
    keys=('workbook','owner','token','fence')
    a={k:a[k]for k in keys};b={k:b[k]for k in keys}
    c.begin('model','unknown',**a,request_sha256='b'*64)
    c.end('model','unknown',**a,evidence='incomplete response',outcome='uncertain')
    c.begin('publication','active',**b,request_sha256='c'*64)
    return c


def request(url,encoding=None,action='status',token='w'*32,arguments=None):
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    if encoding is not None:headers['Accept-Encoding']=encoding
    return urllib.request.Request(url+'/v1',data=C.canonical(dict(action=action,arguments=arguments or{})).encode(),headers=headers)


def test_real_full_ledger_losslessly_compressed_and_legacy_unchanged(tmp_path):
    c=ledger(tmp_path);expected=C.canonical(c.status()).encode();token=tmp_path/'worker';token.write_text('w'*32)
    with running(c)as url:
        with urllib.request.urlopen(request(url))as r:
            assert r.headers.get('Content-Encoding')is None
            assert r.read()==expected
        with urllib.request.urlopen(request(url,'gzip'))as r:
            raw=r.read();assert r.headers['Content-Encoding']=='gzip'
            assert int(r.headers['Content-Length'])==len(raw)
            assert gzip.decompress(raw)==expected and len(raw)<len(expected)//2
        assert C.Client(url,token).call('status')==json.loads(expected)
    assert C.canonical(c.status()).encode()==expected
    assert {x['state']for x in c.status()['operations']}=={'hold','active'}


@pytest.mark.parametrize('encoding',['identity','gzip;q=0','br','gzip, br'])
def test_no_unnegotiated_encoding(tmp_path,encoding):
    c=ledger(tmp_path)
    with running(c)as url,urllib.request.urlopen(request(url,encoding))as r:
        assert r.headers.get('Content-Encoding')is None
        assert json.load(r)==c.status()


def test_only_successful_read_status_compressed(tmp_path):
    c=ledger(tmp_path)
    with running(c)as url:
        with pytest.raises(urllib.error.HTTPError)as caught:
            urllib.request.urlopen(request(url,'gzip',token='wrong'))
        with caught.value as r:
            assert r.code==403 and r.headers.get('Content-Encoding')is None
            assert json.load(r)=={'error':'forbidden'}
        with urllib.request.urlopen(request(url,'gzip','enroll','a'*32,dict(workbook='new',source_sha256='d'*64,evidence='source inventory')))as r:
            assert r.headers.get('Content-Encoding')is None
            assert json.load(r)


class Response(io.BytesIO):
    def __init__(self,data,encoding=None):
        super().__init__(data);self.headers=Message()
        if encoding is not None:self.headers['Content-Encoding']=encoding


@pytest.mark.parametrize('encoding',[None,'identity','gzip'])
def test_client_old_server_compatibility_complete_timestamp_and_timeout(tmp_path,monkeypatch,encoding):
    token=tmp_path/'worker';token.write_text('w'*32);body=dict(at=123.25,operations=[dict(state='hold')],retained={'full':'history'})
    raw=C.canonical(body).encode();calls=[]
    def open_(req,timeout):
        calls.append((req,timeout));return Response(gzip.compress(raw)if encoding=='gzip'else raw,encoding)
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    assert C.Client('http://127.0.0.1:8765',token,timeout=7).call('status')==body
    assert len(calls)==1 and calls[0][1]==7 and calls[0][0].get_header('Accept-encoding')=='gzip'


@pytest.mark.parametrize('raw,encoding,error',[(b'not gzip','gzip',gzip.BadGzipFile),(gzip.compress(b'{}')[:-4],'gzip',EOFError),(b'{}','br',ValueError)])
def test_bad_status_transport_never_retried(tmp_path,monkeypatch,raw,encoding,error):
    token=tmp_path/'worker';token.write_text('w'*32);calls=[]
    def open_(req,timeout):calls.append(req);return Response(raw,encoding)
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    with pytest.raises(error):C.Client('http://127.0.0.1:8765',token).call('status')
    assert len(calls)==1


def test_mutation_request_does_not_negotiate_or_retry(tmp_path,monkeypatch):
    token=tmp_path/'worker';token.write_text('w'*32);calls=[]
    def open_(req,timeout):calls.append(req);raise TimeoutError('response lost after commit')
    monkeypatch.setattr(C.urllib.request,'urlopen',open_)
    with pytest.raises(TimeoutError):C.Client('http://127.0.0.1:8765',token).call('heartbeat',workbook='one')
    assert len(calls)==1 and calls[0].get_header('Accept-encoding')is None
