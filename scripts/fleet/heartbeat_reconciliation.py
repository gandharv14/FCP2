"""Bounded reconciliation of heartbeat transport failures; never claim or model replay."""
import hashlib
import json
import math
import time
from urllib.error import URLError
from scripts.fleet.migration import binding, checked, retain

SCHEMA = 'fleet-heartbeat-reconciliation-policy/v1'
AUTH = 'acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
LIMITS = dict(maximum_reconciliations=2, maximum_seconds=30,
              backoff_seconds=1, status_maximum_age_seconds=5,
              minimum_remaining_seconds=30)


def validate(row):
    p = checked(row)
    if (set(p) != {'schema_version', 'authorization', *LIMITS}
            or p['schema_version'] != SCHEMA
            or any(type(p[k]) is not int or p[k] != v for k, v in LIMITS.items())
            or p['authorization']['sha256'] != AUTH
            or binding(p['authorization']['path']) != p['authorization']):
        raise ValueError('explicit bounded heartbeat reconciliation policy required')
    return p


def transient(exc):
    return (isinstance(exc, (TimeoutError, URLError))
            or isinstance(exc, RuntimeError) and str(exc).startswith('coordinator HTTP 500: '))


def checkpoint(value):
    return json.loads(value) if isinstance(value, str) else value


class Heartbeats:
    def __init__(self, policy, claim, *, clock=time.time, monotonic=time.monotonic,
                 sleep=time.sleep):
        self.policy_binding = policy
        self.policy = validate(policy)
        self.claim = {k: claim[k] for k in ('workbook', 'owner', 'claim_id', 'fence', 'version', 'source_sha256')}
        self.previous = checkpoint(claim.get('checkpoint'))
        self.sequence = 0
        self.reconciliations = 0
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep

    def owned(self, row, allowed):
        remaining = row.get('expires')
        if (any(row.get(k) != v for k, v in self.claim.items())
                or row.get('state') != 'active'
                or type(remaining) not in (int, float) or not math.isfinite(remaining)
                or remaining - self.clock() < self.policy['minimum_remaining_seconds']
                or checkpoint(row.get('checkpoint')) not in allowed):
            raise ValueError('heartbeat reconciliation lost exact active custody')

    def beat(self, client, auth, base_checkpoint, run, guard):
        # Every new heartbeat has a distinct checkpoint sequence. We do not
        # resubmit the failed request, even if the status proves it committed.
        if validate(self.policy_binding) != self.policy:
            raise ValueError('heartbeat policy changed')
        started = None  # The recovery window starts at the first transport failure.
        recovery_directory = None
        def remaining():
            left = self.policy['maximum_seconds'] - (self.monotonic() - started)
            if left <= 0:
                raise TimeoutError('bounded heartbeat reconciliation elapsed')
            return left
        def call(action, **arguments):
            if started is None:
                return client.call(action, **arguments)
            left = remaining()
            absent = object()
            previous_timeout = getattr(client, 'timeout', absent)
            original_timeout = 30 if previous_timeout is absent else previous_timeout
            if type(original_timeout) not in (int, float) or not math.isfinite(original_timeout) or original_timeout <= 0:
                raise ValueError('bounded coordinator client timeout required')
            client.timeout = min(original_timeout, left)
            try:
                result = client.call(action, **arguments)
                remaining()
                return result
            finally:
                if previous_timeout is absent:
                    del client.timeout
                else:
                    client.timeout = previous_timeout
        while True:
            guard()
            self.sequence += 1
            attempted = dict(base_checkpoint, heartbeat_sequence=self.sequence)
            try:
                response = call('heartbeat', **auth, lease_seconds=180, checkpoint=attempted)
            except Exception as exc:
                if not transient(exc) or self.reconciliations >= self.policy['maximum_reconciliations']:
                    raise
                if started is None:
                    started = self.monotonic()
                remaining()
                self.reconciliations += 1
                directory = run / ('heartbeat-reconciliation-%03d' % self.reconciliations)
                directory.mkdir(mode=0o700)
                recovery_directory = directory
                intent = retain(directory / 'intent.json', dict(
                    schema_version='fleet-heartbeat-reconciliation/v1', policy=self.policy_binding,
                    claim=self.claim, previous_checkpoint=self.previous, attempted_checkpoint=attempted,
                    failure_type=type(exc).__name__,
                    failure_sha256=hashlib.sha256(str(exc).encode()).hexdigest(),
                    automatic_request_replay=False))
                guard()
                status = call('status')
                guard()
                at = status.get('at')
                if (type(at) not in (int, float) or not math.isfinite(at)
                        or not 0 <= self.clock() - at <= self.policy['status_maximum_age_seconds']):
                    raise ValueError('fresh coordinator status required')
                rows = [r for r in status['workbooks'] if r.get('workbook') == self.claim['workbook']]
                if len(rows) != 1:
                    raise ValueError('one original workbook required')
                row = rows[0]
                self.owned(row, [self.previous, attempted])
                observed = {k: row[k] for k in (*self.claim, 'state', 'expires', 'checkpoint')}
                retain(directory / 'custody.json', dict(intent=intent, at=at, observed=observed))
                self.previous = checkpoint(row['checkpoint'])
                if self.monotonic() - started + self.policy['backoff_seconds'] >= self.policy['maximum_seconds']:
                    raise TimeoutError('bounded heartbeat reconciliation elapsed')
                self.sleep(self.policy['backoff_seconds'])
                guard()
                if self.monotonic() - started >= self.policy['maximum_seconds']:
                    raise TimeoutError('bounded heartbeat reconciliation elapsed')
                continue
            if recovery_directory is not None and self.monotonic() - started >= self.policy['maximum_seconds']:
                raise TimeoutError('bounded heartbeat reconciliation elapsed')
            self.owned(response, [attempted])
            guard()
            self.previous = attempted
            if recovery_directory is not None:
                retain(recovery_directory / 'resumed.json', dict(
                    schema_version='fleet-heartbeat-reconciled/v1',
                    policy=self.policy_binding, claim=self.claim,
                    checkpoint=attempted, expires=response['expires'],
                    elapsed_seconds=self.monotonic()-started,
                    original_request_replayed=False))
            return response
