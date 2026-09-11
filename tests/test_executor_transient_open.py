"""Prospective retries with synthetic transports and immutable logical identity."""
import dataclasses
import http.client
import io
import json
from pathlib import Path
import socket
import ssl
import urllib.error

import pytest

from scripts.batch import harbor_model_executor as E
from test_harbor_model_executor import setup, Response, response_bytes, TOKEN


TRANSIENT = [TimeoutError('private-token'), ConnectionRefusedError('private-token'),
             ConnectionResetError('private-token'), ConnectionAbortedError('private-token'),
             BrokenPipeError('private-token'), socket.gaierror(socket.EAI_AGAIN, 'private-token')]


@pytest.mark.parametrize('cause', TRANSIENT)
@pytest.mark.parametrize('wrapped', [False, True])
def test_transient_open_retries_same_exact_request_then_success(setup, monkeypatch, cause, wrapped):
    campaign, prepare = setup; p = prepare(); seen = []; delays = []
    def opened(request, timeout):
        seen.append((request.data, request.full_url, timeout))
        if len(seen) == 1:
            raise urllib.error.URLError(cause) if wrapped else cause
        return Response(response_bytes(p.policy))
    monkeypatch.setattr(E, '_open', opened); monkeypatch.setattr(E.time, 'sleep', delays.append)
    assert E.execute(p, campaign, credential=TOKEN).status == 'completed'
    assert len(seen) == 2 and seen[0] == seen[1] and delays == [2]
    root = E.location(p, campaign)
    starts = [json.loads(f.read_bytes()) for f in sorted(root.glob('transport-*.started.json'))]
    assert {s['attempt_id'] for s in starts} == {p.attempt_id}
    assert {s['request_sha256'] for s in starts} == {p.request['sha256']}
    plan = json.loads((root/'plan.json').read_bytes())
    assert (plan['ordinal'], plan['limit']) == (p.ordinal, p.limit)
    assert not (root/'transport-00.body.raw').exists()
    assert all(b'private-token' not in f.read_bytes() for f in root.iterdir() if f.is_file())
    assert E.execute(p, campaign, credential=TOKEN).status == 'completed' and len(seen) == 2


@pytest.mark.parametrize('cause', [
    urllib.error.URLError('temporary timeout maybe'), ConnectionError('opaque'),
    socket.gaierror(socket.EAI_NONAME, 'permanent DNS'),
    urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, 'permanent DNS')),
    ssl.SSLCertVerificationError('certificate failure'),
    urllib.error.URLError(ssl.SSLError('TLS failure')),
    PermissionError('permission denied'), FileNotFoundError('missing'),
    http.client.RemoteDisconnected('unknown delivery'),
])
def test_opaque_permanent_certificate_or_unknown_delivery_stays_sealed(setup, monkeypatch, cause):
    campaign, prepare = setup; p = prepare(); calls = []
    def opened(*args): calls.append(1); raise cause
    monkeypatch.setattr(E, '_open', opened)
    assert E.execute(p, campaign, credential=TOKEN).status == 'incomplete'
    assert E.execute(p, campaign, credential=TOKEN).status == 'incomplete' and len(calls) == 1


@pytest.mark.parametrize('prefix', [b'', b'partial', b'{"object":"chat.completion"}'])
def test_any_started_response_read_never_retries(setup, monkeypatch, prefix):
    campaign, prepare = setup; p = prepare(); calls = []
    class Fails(Response):
        def read(self, size):
            if self.tell() < len(prefix):return super().read(size)
            raise TimeoutError('private-token')
    def opened(*args):calls.append(1);return Fails(prefix)
    monkeypatch.setattr(E, '_open', opened)
    assert E.execute(p, campaign, credential=TOKEN).status == 'incomplete'
    assert (E.location(p,campaign)/'transport-00.body.raw').read_bytes() == prefix
    assert E.execute(p, campaign, credential=TOKEN).status == 'incomplete' and len(calls) == 1


def test_http_and_connection_failures_share_total_four_start_ceiling(setup, monkeypatch):
    campaign, prepare = setup; p = prepare(); calls = []; delays = []
    def opened(*args):
        calls.append(1)
        if len(calls) % 2:
            raise urllib.error.HTTPError(p.policy.endpoint,503,'synthetic',{},io.BytesIO(b'error'))
        raise TimeoutError('private-token')
    monkeypatch.setattr(E, '_open', opened); monkeypatch.setattr(E.time, 'sleep', delays.append)
    assert E.execute(p,campaign,credential=TOKEN).status == 'incomplete'
    assert len(calls) == 4 and delays == [2,4,8]
    assert len(list(E.location(p,campaign).glob('transport-*.started.json'))) == 4
    assert E.execute(p,campaign,credential=TOKEN).status == 'incomplete' and len(calls) == 4
    with pytest.raises(E.ExecutorError,match='unresolved prior'):
        E.execute(prepare(ordinal=2),campaign,credential=TOKEN)


def test_existing_sealed_timeout_journal_is_never_reopened(setup, monkeypatch):
    campaign, prepare = setup; p = prepare()
    # The old execution shape: start recorded, no response, then outer failure
    # sealing. Preserve that entire journal rather than applying new policy.
    def interrupted(stage):
        if stage == 'after_dispatch_started':raise TimeoutError('private-token')
    assert E.execute(p,campaign,credential=TOKEN,_failpoint=interrupted).status == 'incomplete'
    root=E.location(p,campaign);before={f.name:f.read_bytes() for f in root.iterdir()}
    def forbidden(*args):raise AssertionError('Sealed history must never dispatch')
    monkeypatch.setattr(E,'_open',forbidden)
    assert E.inspect(p,campaign).status == 'incomplete'
    assert E.execute(p,campaign,credential=TOKEN).status == 'incomplete'
    assert before == {f.name:f.read_bytes() for f in root.iterdir()}


def test_interruption_during_backoff_does_not_resume(setup, monkeypatch):
    campaign,prepare=setup;p=prepare();calls=[]
    def opened(*args):calls.append(1);raise TimeoutError('private-token')
    def interrupted(delay):raise KeyboardInterrupt()
    monkeypatch.setattr(E,'_open',opened);monkeypatch.setattr(E.time,'sleep',interrupted)
    with pytest.raises(KeyboardInterrupt):E.execute(p,campaign,credential=TOKEN)
    assert E.execute(p,campaign,credential=TOKEN).status == 'incomplete' and len(calls)==1


def test_final_allowed_start_can_complete_without_extra_semantic_attempt(setup, monkeypatch):
    campaign,prepare=setup;p=prepare();calls=[];delays=[]
    def opened(*args):
        calls.append(1)
        if len(calls)<4:raise ConnectionResetError('private-token')
        return Response(response_bytes(p.policy))
    monkeypatch.setattr(E,'_open',opened);monkeypatch.setattr(E.time,'sleep',delays.append)
    assert E.execute(p,campaign,credential=TOKEN).status == 'completed'
    assert len(calls)==4 and delays==[2,4,8]
    assert not E.location(p,campaign).with_name('attempt-02').exists()


def test_partial_http_error_body_remains_uncertain_and_closed(setup, monkeypatch):
    campaign,prepare=setup;p=prepare();calls=[]
    class Partial(Response):
        def read(self,size):
            if self.tell():raise TimeoutError('private-token')
            return super().read(size)
    response=Partial(b'partial-error')
    def opened(*args):
        calls.append(1)
        raise urllib.error.HTTPError(p.policy.endpoint,503,'synthetic',{},response)
    monkeypatch.setattr(E,'_open',opened)
    assert E.execute(p,campaign,credential=TOKEN).status == 'incomplete'
    assert response.closed and len(calls)==1
    assert (E.location(p,campaign)/'transport-00.body.raw').read_bytes()==b'partial-error'
