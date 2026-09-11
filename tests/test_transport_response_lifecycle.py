"""Synthetic urllib responses; no network or model execution."""
import io
import http.client
import socket
import ssl
import urllib.error

import pytest

import xl_transport as transport


@pytest.mark.parametrize('status,count', [(400, 1), (401, 1), (429, 4), (503, 4)])
def test_terminal_http_errors_close_every_stream_and_preserve_exception(monkeypatch, status, count):
    errors = []
    delays = []

    def opened(request, timeout):
        assert request == 'synthetic-request' and timeout == 12
        error = urllib.error.HTTPError('https://fixture.invalid', status, 'synthetic',
                                       {'Retry-After': '0.25'}, io.BytesIO(b'failure'))
        errors.append(error)
        raise error

    def sleep(delay):
        assert errors[-1].fp.closed  # Release before backoff, not after it.
        delays.append(delay)

    monkeypatch.setattr(transport.urllib.request, 'urlopen', opened)
    monkeypatch.setattr(transport.time, 'sleep', sleep)
    with pytest.raises(urllib.error.HTTPError) as caught:
        transport._read_response('synthetic-request', timeout=12)
    assert caught.value is errors[-1]
    assert caught.value.code == status and caught.value.headers['Retry-After'] == '0.25'
    assert len(errors) == count and delays == [.25] * (count - 1)
    assert all(error.fp.closed for error in errors)


@pytest.mark.parametrize('header,expected', [
    ('0.25', .25), ('9999', 60), ('-10', 0), ('nan', 2),
    ('inf', 2), ('not a date', 2),
    ('Thu, 01 Jan 1970 00:00:10 GMT', 10),
    ('Wed, 31 Dec 1969 23:59:59 GMT', 0),
])
def test_retry_then_success_preserves_delay_and_closes_responses(monkeypatch, header, expected):
    error = urllib.error.HTTPError('https://fixture.invalid', 429, 'synthetic',
                                   {'Retry-After': header}, io.BytesIO(b'limited'))
    response = io.BytesIO(b'complete')
    calls = []
    delays = []

    def opened(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise error
        assert error.fp.closed
        return response

    monkeypatch.setattr(transport.urllib.request, 'urlopen', opened)
    monkeypatch.setattr(transport.time, 'sleep', delays.append)
    monkeypatch.setattr(transport.time, 'time', lambda: 0)
    assert transport._read_response('synthetic-request', timeout=12) == b'complete'
    assert len(calls) == 2 and delays == [expected]
    assert error.fp.closed and response.closed


@pytest.mark.parametrize('failure', [
    TimeoutError('read timed out'),
    ConnectionResetError('connection reset'),
    http.client.IncompleteRead(b'partial'),
])
def test_started_response_read_is_not_retried(monkeypatch, failure):
    class PartialResponse(io.BytesIO):
        def read(self):
            super().read(3)
            raise failure

    response = PartialResponse(b'partial response')
    calls = []

    def opened(*args, **kwargs):
        calls.append(1)
        return response

    monkeypatch.setattr(transport.urllib.request, 'urlopen', opened)
    monkeypatch.setattr(transport.time, 'sleep', lambda delay: None)
    with pytest.raises(type(failure)) as caught:
        transport._read_response('synthetic-request', timeout=12)
    assert caught.value is failure
    assert calls == [1]
    assert response.closed


@pytest.mark.parametrize('failure', [
    urllib.error.URLError('opaque failure'),
    urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, 'permanent DNS')),
    urllib.error.URLError(ssl.SSLCertVerificationError('certificate')),
    http.client.RemoteDisconnected('unknown delivery'),
    ConnectionError('opaque connection error'),
])
def test_permanent_or_unknown_open_failures_are_not_retried(monkeypatch, failure):
    calls = []

    def opened(*args, **kwargs):
        calls.append(1)
        raise failure

    monkeypatch.setattr(transport.urllib.request, 'urlopen', opened)
    monkeypatch.setattr(transport.time, 'sleep', lambda delay: None)
    with pytest.raises(type(failure)) as caught:
        transport._read_response('synthetic-request', timeout=12)
    assert caught.value is failure
    assert calls == [1]


@pytest.mark.parametrize('cause', [
    TimeoutError(), ConnectionRefusedError(), ConnectionResetError(),
    ConnectionAbortedError(), BrokenPipeError(),
    socket.gaierror(socket.EAI_AGAIN, 'temporary DNS'),
])
@pytest.mark.parametrize('wrapped', [False, True])
def test_transient_open_failures_share_http_retry_budget(monkeypatch, cause, wrapped):
    calls = []
    delays = []
    error = urllib.error.HTTPError('https://fixture.invalid', 503, 'synthetic', {}, io.BytesIO())
    failure = urllib.error.URLError(cause) if wrapped else cause

    def opened(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise error
        raise failure

    monkeypatch.setattr(transport.urllib.request, 'urlopen', opened)
    monkeypatch.setattr(transport.time, 'sleep', delays.append)
    with pytest.raises(type(failure)) as caught:
        transport._read_response('synthetic-request', timeout=12)
    assert caught.value is failure
    assert len(calls) == 4
    assert delays == [2, 4, 8]
    assert error.fp.closed
