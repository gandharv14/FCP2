"""Closed c5→a0 executor applicability for retained normalization partitions.

This proves a particular prospective transport-policy change, not non-delivery.
No historical code executes and no sealed call is redispatched.
"""
from pathlib import Path
from scripts.batch.harbor_admission import record, require

OLD_E = 'c5f32d45ca696a2e13bd67602b3e6b1103503ab7b69aacd18b96a692e46164dd'
NEW_E = 'a0faa4d8a6b70ad870338660bb59ea23ad05edf0f231d76a3009c83a99a89df7'
OLD_A = '8d1b7d712fba9c3bbde11953309e12f6a9b42ce9a5f75021aa67c16e9ed6b6e7'
OLD_P = '6bff27aa5d1082c957ae2f7600e750b2e3fdd5b3290825bba76d3c6210b9e21f'
EDITS = ((b'Only complete explicit transient HTTP errors may retry. Unknown completion never\nreplays automatically, including on a later execute() invocation.', b'Complete explicit transient HTTP errors and typed transient open failures may\nretry within the original live call. Observed response bodies and sealed unknown\ncompletion never replay, including on a later execute() invocation.'), (b'import re\n', b'import re\nimport socket\nimport ssl\n'), (b'def execute(', b'def transient_open_failure(exc):\n    """Classify typed causes only; exception messages are never evidence.\n\n    A timeout/reset before response headers does not prove server non-delivery.\n    This is bounded prospective transport retry, not an exactly-once claim.\n    """\n    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc\n    if isinstance(cause, (ssl.SSLError, http.client.HTTPException)):\n        return None\n    if isinstance(cause, socket.gaierror):\n        return \'temporary_dns\' if cause.errno == socket.EAI_AGAIN else None\n    if isinstance(cause, TimeoutError):\n        return \'timeout\'\n    if isinstance(cause, (ConnectionRefusedError, ConnectionResetError,\n                          ConnectionAbortedError, BrokenPipeError)):\n        return \'connection\'\n    return None\n\n\ndef execute('), (b"                    body_path = root/('transport-%02d.body.raw' % index)\n", b"                    body_path = root/('transport-%02d.body.raw' % index)\n                    transport_stage = 'open'\n"), (b'                        with _open(request, timeout) as response:\n', b"                        with _open(request, timeout) as response:\n                            transport_stage = 'read'\n"), (b"                    retain(root/'response.raw.json', raw)\n", b"                    except (OSError, urllib.error.URLError) as exc:\n                        category = transient_open_failure(exc)\n                        retry = (category is not None and transport_stage == 'open'\n                                 and not body_path.exists() and index < MAX_RETRIES)\n                        delay = retry_delay(index, None) if retry else None\n                        save(root/('transport-%02d.connection-error.json' % index), dict(\n                            category=category or 'unclassified', stage=transport_stage,\n                            response_body_present=body_path.exists(), retry=retry,\n                            delay_seconds=delay, completed_at_ns=time.time_ns()))\n                        if not retry:\n                            raise\n                        time.sleep(delay)\n                        continue\n                    retain(root/'response.raw.json', raw)\n"))


def archives():
    home = Path(__file__).resolve().parent/'partition_code_transport_v2'
    executor = home/'executor-before.py.txt'
    applicability = home/'applicability-before.py.txt'
    require(record(executor)['sha256']==OLD_E and record(applicability)['sha256']==OLD_A,
            'transport applicability archive changed')
    return executor, applicability


def code_records():
    return {str(path.resolve()):record(path)['sha256'] for path in (Path(__file__), *archives())}


def previous_code_records():
    from scripts.batch import harbor_partition_code_applicability as A
    e,p = A.archives()
    previous = Path(A.__file__).resolve().parent/'partition_code_v1/applicability-before.py.txt'
    require(record(previous)['sha256']==A.PREVIOUS_A, 'previous applicability archive changed')
    return {str(Path(A.__file__).resolve()):OLD_A,
            **{str(path.resolve()):record(path)['sha256'] for path in (e,p,previous)}}


def previous_profile(current):
    from scripts.batch import harbor_model_executor as E, harbor_partition_code_applicability as A
    from scripts.batch import harbor_normalization_partitioned as P
    source,_ = archives(); expected = source.read_bytes()
    for before,after in EDITS:
        require(expected.count(before)==1, 'transport upgrade anchor differs')
        expected = expected.replace(before,after,1)
    require(Path(E.__file__).read_bytes()==expected and record(E.__file__)['sha256']==NEW_E,
            'unreviewed transport executor delta')
    require(record(P.__file__)['sha256']==OLD_P, 'partition producer changed')
    added=code_records()
    for module in (A,E,P):
        require(current.get(str(Path(module.__file__).resolve()))==record(module.__file__)['sha256'],
                'current transport code profile differs from actual modules')
    require(all(current.get(k)==v for k,v in added.items()), 'transport applicability evidence omitted')
    old={k:v for k,v in current.items() if k not in added}
    old[str(Path(E.__file__).resolve())]=OLD_E
    old[str(Path(A.__file__).resolve())]=OLD_A
    require(all(old.get(k)==v for k,v in previous_code_records().items()),
            'prior applicability code evidence differs')
    return old


def policy_evidence():
    return dict(changed_executor_branch_reachable=True,
                dialogue_mode_transition_reachable=False,
                prospective_transport_retries=True,
                sealed_journal_replay=False,
                request_identity_changed=False,
                max_transport_retries=3,
                historical_code_executed=False)
