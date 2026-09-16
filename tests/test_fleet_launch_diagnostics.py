"""Remote launch failures retain cause without another remote invocation."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from scripts.fleet import launch_job_v3 as L

@pytest.mark.parametrize('stderr',[
    b"FileNotFoundError: /retained/runtime.json\n",
    b"invalid remote byte: \xff\n",
    b"",
])
def test_remote_failure_reports_stderr_hash_and_never_retries(monkeypatch,stderr):
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        return SimpleNamespace(returncode=17,stderr=stderr,stdout=b'private partial custody reply')
    monkeypatch.setattr(L.subprocess,'run',run)
    with pytest.raises(RuntimeError)as error:
        L.ssh({'host':'hhu-server-4'},{'command':['/exact/python']},'check',{'selected':'payload'})
    text=str(error.value)
    assert 'remote check failed (exit 17;' in text
    assert hashlib.sha256(stderr).hexdigest() in text
    assert (stderr.decode('utf-8',errors='replace')if stderr else '[remote stderr was empty]')in text
    assert 'private partial custody reply'not in text
    assert len(calls)==1 and calls[0][1]['capture_output']is True
    assert json.loads(calls[0][1]['input'])=={'selected':'payload'}


def test_successful_response_remains_exact_and_once(monkeypatch):
    calls=[]
    def run(*a,**k):
        calls.append(a);return SimpleNamespace(returncode=0,stderr=b'harmless warning',stdout=b'{"status":"VALIDATED_NOT_STARTED"}')
    monkeypatch.setattr(L.subprocess,'run',run)
    assert L.ssh({'host':'hhu-server-4'},{'command':['/exact/python']},'check',{})=={'status':'VALIDATED_NOT_STARTED'}
    assert len(calls)==1
