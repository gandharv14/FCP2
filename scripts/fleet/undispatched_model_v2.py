"""Explicit first-transport continuation of one proven undispatched attempt.

The original executor, journal, UUID reservation and semantic ordinal remain
unchanged. This adapter executes a separately witnessed copy of two executor
functions against a new sibling journal. It does not install itself in an owning
partition reader, grant a lease, or authorize a semantic review.
"""
from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path

SCHEMA = 'fleet-undispatched-first-transport/v1'


def require(value, message):
    if not value:
        raise ValueError(message)


def binding(path):
    p = Path(path).absolute()
    require(not any(q.is_symlink() for q in (p, *p.parents)), 'symlink evidence')
    return {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}


def read(item):
    require(binding(item['path']) == item, 'bound recovery evidence changed')
    return json.loads(Path(item['path']).read_bytes())


def patch_once(source, before, after):
    require(source.count(before) == 1, 'unreviewed executor function shape')
    return source.replace(before, after, 1)


class Recovery:
    """Closed adapter; callers must separately validate the owning lane boundary."""
    def __init__(self, executor, prepared, campaign, *, native_proof, central_proof):
        self.E, self.prepared, self.campaign = executor, prepared, campaign
        self.native_binding, self.central_binding = native_proof, central_proof
        p = self.proof = read(native_proof)
        central = read(central_proof)
        require(p['status'] == 'SEALED_UNDISPATCHED_ATTEMPT_VERIFIED'
                and p['active_original_processes'] == []
                and p['transport_started'] is False and p['fleet_acquired'] is False
                and p['attempt_consumed'] is True and p['automatic_replay'] is False
                and p['semantic_approval'] is False and p['model_calls_dispatched_by_probe'] == 0,
                'native proof is not an undispatched stopped attempt')
        require(central['status'] == 'VERIFIED_UNDISPATCHED_NOT_REPLAY_AUTHORIZED'
                and central['semantic_budget_reset'] is False
                and central['owning_recovery_performed'] is False,
                'central absence proof required')
        rows = [r for r in central['checks'] if r['operation_id'] == prepared.attempt_id]
        require(len(rows) == 1, 'missing or ambiguous central operation proof')
        self.prior = rows[0]
        require(self.prior['native_proof'] == native_proof
                and self.prior['central_events'] == self.prior['central_operations'] == []
                and self.prior['workbook']['state'] == 'hold'
                and self.prior['workbook']['workbook'] == prepared.scope.workbook_id
                and self.prior['workbook']['source_sha256'] == prepared.scope.original_sha256,
                'operation existed, or source custody differs')
        require(binding(executor.__file__) == p['executor'], 'executor differs from inspected producer')
        require(prepared.physical_repair is None and prepared.audit_context is None
                and prepared.scope.lane == 'harbor-normalization-engineer'
                and prepared.ordinal == p['semantic_ordinal'] == 1
                and prepared.limit == p['semantic_limit'] == 2
                and prepared.attempt_id == p['operation_id']
                and prepared.scope.workbook_id == p['workbook'],
                'recovery cannot change lane, request scope, UUID or consumed budget')
        self.old = executor.location(prepared, campaign)
        self.root = self.old.parent / 'undispatched-continuation-01'
        require(str(self.old / 'outcome.json') == p['outcome']['path'], 'original journal differs')
        plan, _, _, _ = executor.prepared_plan(prepared, campaign)
        require(plan == read(p['plan']) and plan['max_transport_retries'] == 0
                and plan.get('fleet_transport_policy') == 'single-dispatch-durable-global-gate/v1'
                and plan['semantic_budget_reset'] is False and campaign.model_slots == 1,
                'original request or transport policy changed')
        self.plan = plan
        self.identity = read(p['identity'])
        require(self.identity == {'attempt_id': prepared.attempt_id,
                'logical_path': str(self.old), 'plan_sha256': p['plan']['sha256']},
                'original UUID reservation differs')
        self.validate_original()
        # These exact functions retain their normal transport, sealing, parsing,
        # locks, resource parameters, and no-replay behavior. Only the declared
        # journal/identity/provenance boundary differs.
        execute = inspect.getsource(executor.execute)
        execute = patch_once(execute,
            "        if prepared.physical_repair is None:\n            require(not identity.exists(), 'attempt UUID already reserved; no cross-scope reuse')\n            save(identity, {'attempt_id':prepared.attempt_id, 'logical_path':str(root),\n                            'plan_sha256':sha(canonical(plan))})",
            "        if prepared.physical_repair is None:\n            require(strict_json(identity.read_bytes()) == recovery_identity, 'original UUID reservation changed')")
        execute = patch_once(execute, "        save(root/'identity.json', record(identity))",
            "        save(root/'identity.json', record(identity))\n        save(root/'undispatched-recovery.json', recovery_witness)")
        execute = patch_once(execute, "        save(root/'submitted.json', submitted)",
            "        submitted['undispatched_recovery'] = record(root/'undispatched-recovery.json')\n        save(root/'submitted.json', submitted)")
        inspector = inspect.getsource(executor.inspect_locked)
        inspector = patch_once(inspector,
            "        expected_identity={'attempt_id':plan['attempt_id'],\n                'logical_path':str(root), 'plan_sha256':sha(canonical(plan))}",
            "        expected_identity = recovery_identity")
        self.witness = dict(schema_version=SCHEMA, native_proof=native_proof,
            central_proof=central_proof, original_executor=p['executor'], adapter=binding(__file__),
            original_outcome=p['outcome'], original_identity=p['identity'],
            original_request=p['request'], original_plan=p['plan'],
            operation_id=prepared.attempt_id, semantic_ordinal=1, semantic_limit=2,
            semantic_budget_reset=False, original_attempt_consumed=True,
            transport_continuation_index=1, original_journal=str(self.old),
            continuation_journal=str(self.root),
            effective_execute_sha256=hashlib.sha256(execute.encode()).hexdigest(),
            effective_inspect_sha256=hashlib.sha256(inspector.encode()).hexdigest())
        namespace = dict(executor.__dict__)
        namespace.update(recovery_identity=self.identity, recovery_witness=self.witness,
                         location=lambda selected, selected_campaign: self.selected_location(selected, selected_campaign))
        exec(compile(inspector, str(Path(__file__).absolute()) + ':declared-inspector', 'exec'), namespace)
        exec(compile(execute, str(Path(__file__).absolute()) + ':declared-executor', 'exec'), namespace)
        self._execute, self._inspect = namespace['execute'], namespace['inspect_locked']

    def selected_location(self, prepared, campaign):
        require(prepared == self.prepared and campaign == self.campaign, 'adapter selects exactly one call')
        return self.root

    def validate_original(self):
        E, p = self.E, self.proof
        require(sorted((binding(x) for x in self.old.iterdir()), key=lambda x: x['path']) ==
                sorted(p['sealed_files'], key=lambda x: x['path']), 'original sealed journal changed')
        expected = {'budget-00.snapshot', 'budget-01.snapshot', 'identity.json', 'outcome.json',
                    'plan.json', 'prior-attempts.json', 'request.json', 'submitted.json'}
        require({x.name for x in self.old.iterdir()} == expected, 'transport or unknown journal artifact exists')
        outcome = read(p['outcome'])
        require(outcome['status'] == 'incomplete' and outcome['details'] ==
                {'automatic_replay': False, 'reason_type': 'Conflict'}, 'not a proven pre-transport conflict')
        require(read(p['request']) == E.strict_json(E.checked_bytes(self.prepared.request)), 'request differs')
        submitted = read(p['submitted'])
        require(submitted['attempt_id'] == self.prepared.attempt_id
                and submitted['scope'] == asdict(self.prepared.scope)
                and submitted['semantic_ordinal'] == 1 and submitted['semantic_limit'] == 2,
                'original submission differs')
        intent = read(p['intent'])
        require(intent['operation_id'] == self.prepared.attempt_id
                and intent['request_sha256'] == self.prepared.request['sha256'], 'old gate intent differs')
        require([binding(x) for x in Path(p['intent']['path']).parent.iterdir()] == [p['intent']]
                and p['model_journal_files'] == [p['intent']], 'old gate was acquired or changed')
        require(read(p['identity']) == self.identity, 'original identity changed')
        result = E.inspect(self.prepared, self.campaign)
        require(result.status == 'incomplete' and result.call is None and result.outcome == p['outcome'],
                'owning executor does not confirm retained incomplete attempt')

    def inspect(self):
        self.validate_original()
        with self.E.scope_lock(self.root.parent, create=False):
            require(self.root.is_dir(), 'continuation has not been submitted')
            require(read(binding(self.root / 'undispatched-recovery.json')) == self.witness,
                    'continuation provenance changed')
            return self._inspect(self.root, self.plan, self.E.strict_json(
                self.E.checked_bytes(self.prepared.request)))

    def execute(self, *, credential, timeout=600):
        self.validate_original()
        if self.root.exists():
            return self.inspect()  # Never dispatch twice, even after an uncertain response.
        from scripts.fleet import model_gate
        client, owner, journal = model_gate.context()
        require(owner['workbook'] == self.prepared.scope.workbook_id
                and owner['fence'] > self.prior['workbook']['fence'], 'new fenced custody required')
        status = client.call('status')
        from scripts.fleet.capacity_policy import validate_occupancy
        validate_occupancy(status)
        require(not any(o['operation_id'] == self.prepared.attempt_id for o in status['operations']),
                'original operation exists; response may be uncertain')
        rows = [w for w in status['workbooks'] if w['workbook'] == owner['workbook']]
        require(len(rows) == 1 and rows[0]['state'] == 'active'
                and rows[0]['owner'] == owner['owner'] and rows[0]['fence'] == owner['fence']
                and rows[0]['source_sha256'] == self.prepared.scope.original_sha256,
                'current source custody differs')
        directory = Path(journal) / hashlib.sha256(self.prepared.attempt_id.encode()).hexdigest()
        require(not directory.exists(), 'new gate intent must be unused; uncertain dispatch cannot replay')
        client.call('heartbeat', lease_seconds=180, **owner)
        return self._execute(self.prepared, self.campaign, credential=credential, timeout=timeout)
