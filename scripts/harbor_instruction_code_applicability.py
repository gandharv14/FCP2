"""Exact retained structured round-two request applicability after executor repair.

This proves code applicability only. Owning task, body, budget, call and current
financial gates still validate independently; no request or UUID is rewritten.
The exact c5-to-a0 change permits prospective typed transport retries. It does
not change JSON dialogue parsing or authorize replay of sealed journals.
"""
from pathlib import Path
from scripts.batch.harbor_admission import record, require
OLD_P='2dbf39912b18cade9b1509930b7c0863cbae910219e42fbd9ad95e1de29a5b32'
OLD_E='3ce097194f6219538fffa37847ee7f96d134b1185394c9355cdd55d49386ac17'
PRE_COVERAGE_P='5a362f3c3f3eb5bee20d038eabbfe4607df736629756ebada8475a1c21647da3'
COVERAGE_P='b4038e922208ac15f476956a74c4ff816b41a567634d657764abc976059ab739'
PROTOCOL_E='c5f32d45ca696a2e13bd67602b3e6b1103503ab7b69aacd18b96a692e46164dd'
EDITS=(
 (b'    return [record(path) for path in paths]\n',b'    from scripts import harbor_instruction_code_applicability as applicability\n    paths += applicability.paths()\n    return [record(path) for path in paths]\n'),
 (b"    require(context['code']==code_evidence(task),'instruction producer/validator code changed')",b"    from scripts import harbor_instruction_code_applicability as applicability\n    applicability.validate(context,code_evidence(task),request)"),
)

NEW_E='a0faa4d8a6b70ad870338660bb59ea23ad05edf0f231d76a3009c83a99a89df7'
PREVIOUS_HELPER='bca9d366b449ae24d64fb496e9732bb5f8c3716e75409ec45387a6cc3edf2551'
TRANSPORT_EDITS = ((b'Only complete explicit transient HTTP errors may retry. Unknown completion never\nreplays aut'
  b'omatically, including on a later execute() invocation.',
  b'Complete explicit transient HTTP errors and typed transient open failures may\nretry within t'
  b'he original live call. Observed response bodies and sealed unknown\ncompletion never replay, '
  b'including on a later execute() invocation.'),
 (b'import re\n', b'import re\nimport socket\nimport ssl\n'),
 (b'def execute(',
  b'def transient_open_failure(exc):\n    """Classify typed causes only; exception messages are n'
  b'ever evidence.\n\n    A timeout/reset before response headers does not prove server non-delive'
  b'ry.\n    This is bounded prospective transport retry, not an exactly-once claim.\n    """\n    '
  b'cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc\n    if isinstance(caus'
  b'e, (ssl.SSLError, http.client.HTTPException)):\n        return None\n    if isinstance(cause, '
  b"socket.gaierror):\n        return 'temporary_dns' if cause.errno == socket.EAI_AGAIN else Non"
  b"e\n    if isinstance(cause, TimeoutError):\n        return 'timeout'\n    if isinstance(cause, "
  b'(ConnectionRefusedError, ConnectionResetError,\n                          ConnectionAbortedEr'
  b"ror, BrokenPipeError)):\n        return 'connection'\n    return None\n\n\ndef execute("),
 (b"                    body_path = root/('transport-%02d.body.raw' % index)\n",
  b"                    body_path = root/('transport-%02d.body.raw' % index)\n                   "
  b" transport_stage = 'open'\n"),
 (b'                        with _open(request, timeout) as response:\n',
  b'                        with _open(request, timeout) as response:\n                          '
  b"  transport_stage = 'read'\n"),
 (b"                    retain(root/'response.raw.json', raw)\n",
  b'                    except (OSError, urllib.error.URLError) as exc:\n                        '
  b'category = transient_open_failure(exc)\n                        retry = (category is not None'
  b" and transport_stage == 'open'\n                                 and not body_path.exists() a"
  b'nd index < MAX_RETRIES)\n                        delay = retry_delay(index, None) if retry el'
  b"se None\n                        save(root/('transport-%02d.connection-error.json' % index), "
  b"dict(\n                            category=category or 'unclassified', stage=transport_stage"
  b',\n                            response_body_present=body_path.exists(), retry=retry,\n       '
  b'                     delay_seconds=delay, completed_at_ns=time.time_ns()))\n                 '
  b'       if not retry:\n                            raise\n                        time.sleep(de'
  b"lay)\n                        continue\n                    retain(root/'response.raw.json', r"
  b'aw)\n'))

def paths():
    home = Path(__file__).resolve().with_name('harbor_instruction_code_v1')
    producer = home/'producer-before.py.txt'
    executor = home/'executor-before.py.txt'
    transport = home.with_name('harbor_instruction_code_transport_v2')
    protocol = transport/'executor-before.py.txt'
    helper = transport/'applicability-before.py.txt'
    for path, expected in ((producer, OLD_P), (executor, OLD_E),
                           (protocol, PROTOCOL_E), (helper, PREVIOUS_HELPER)):
        require(record(path)['sha256'] == expected, 'instruction applicability archive changed')
    return [Path(__file__).resolve(), producer, executor, protocol, helper]


def _current(current):
    from scripts.batch import harbor_instruction_producer as P, harbor_model_executor as E
    files = paths()
    expected = files[1].read_bytes()
    for before, after in EDITS:
        require(expected.count(before) == 1, 'instruction upgrade anchor differs')
        expected = expected.replace(before, after, 1)
    # The reviewed coverage-retry release changes NAT handling and adds an
    # explicit historical-call reader. With no code_repair selected it leaves
    # dialogue request bodies, scope, and transport behavior unchanged. Bind
    # exact releases; arbitrary subsequent producer edits remain rejected.
    from hashlib import sha256
    require(sha256(expected).hexdigest()==PRE_COVERAGE_P and
            (Path(P.__file__).read_bytes()==expected or record(P.__file__)['sha256']==COVERAGE_P),
            'unreviewed instruction producer delta')
    expected = files[3].read_bytes()
    for before, after in TRANSPORT_EDITS:
        require(expected.count(before) == 1, 'transport upgrade anchor differs')
        expected = expected.replace(before, after, 1)
    require(Path(E.__file__).read_bytes() == expected and record(E.__file__)['sha256'] == NEW_E,
            'unreviewed transport executor delta')
    added = [record(path) for path in files]
    require(current[-len(added):] == added, 'instruction applicability evidence not exported')
    for module in (P, E):
        path = str(Path(module.__file__).resolve())
        require([row for row in current if row['path'] == path] == [record(path)],
                'actual instruction module binding differs')
    require(len({row['path'] for row in current}) == len(current),
            'duplicate instruction code binding')
    return files, P, E


def profile(current):
    """Exact original pre-protocol profile; no historical code is executed."""
    files, P, E = _current(current)
    prior = []
    for row in current[:-len(files)]:
        value = OLD_P if row['path'] == str(Path(P.__file__).resolve()) else (
            OLD_E if row['path'] == str(Path(E.__file__).resolve()) else row['sha256'])
        prior.append(dict(path=row['path'], sha256=value))
    return prior


def protocol_profile(current):
    """Exact c5 producer/helper context, retained before typed transport retries."""
    files, P, E = _current(current)
    prior = []
    for row in current[:-2]:
        value = PRE_COVERAGE_P if row['path'] == str(Path(P.__file__).resolve()) else (PROTOCOL_E if row['path'] == str(Path(E.__file__).resolve()) else (
            PREVIOUS_HELPER if row['path'] == str(files[0]) else row['sha256'])
        )
        prior.append(dict(path=row['path'], sha256=value))
    return prior


def policy_evidence():
    return dict(prospective_transport_branch_reachable=True,
                json_dialogue_protocol_changed=False,
                sealed_journal_replay=False, request_identity_changed=False,
                semantic_budget_reset=False, max_transport_retries=3,
                historical_code_executed=False)


def validate(context, current, request):
    if context['code'] == current:
        return
    p = request.prepared
    require(context['kind'] == 'dialogue-writer' and context.get('structural_round') is None
            and context.get('writer_round') is None,
            'historical code applicability is remaining structured dialogue writer only')
    require(p.scope.lane == 'harbor-dialogue-author' and p.scope.budget_id == 'dialogue-writer'
            and p.ordinal == p.limit == 2 and p.response_mode == 'json_object',
            'historical writer scope/budget differs')
    original = profile(current)
    protocol = protocol_profile(current)
    require(context['code'] in (original, protocol),
            'prepared code changed beyond exact executor repair')
