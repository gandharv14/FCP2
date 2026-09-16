"""Root-bound historical and prospective reader for the closed 0200 correction.

No file creation, model transport, lease operation, or publication occurs here.
The owning authorization writer and guarded worker install this same reader.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace

from scripts.fleet import source_card_authoring as core


def binding(path):
    path = Path(path)
    core.require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink in source-card custody')
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def checked(row):
    core.require(type(row) is dict and set(row) == {'path', 'sha256'} and binding(row['path']) == row,
                 'source-card evidence changed')
    return json.loads(Path(row['path']).read_bytes())


class Reader:
    """Construct only with the selected plan, exact permit and original modules."""
    def __init__(self, *, plan, authority, producer, worker, campaign, renewal, takeover):
        self.plan_binding, self.authority = plan, authority
        self.plan = checked(plan)
        self.producer, self.worker, self.campaign, self.renewal = producer, worker, campaign, renewal
        self.takeover = takeover
        p = self.plan
        fields = {'schema_version', 'workbook', 'source', 'budget', 'source_proof',
                  'corrected_claims', 'changes', 'old_descriptor', 'old_snapshot', 'blocked',
                  'worker_run', 'directory', 'guard', 'automatic_retry', 'publication_performed',
                  'dependencies', 'renderer_manifest', 'takeover', 'takeover_module'}
        core.require(set(p) == fields and p['schema_version'] == 'fleet-0200-source-card-continuation/v1' and
                     p['workbook'] == '0200' and p['source']['sha256'] ==
                     'dfea507ab1d8978d040129931a9000024ae715534ce17b24fee270ac7f074661',
                     'closed 0200 source plan required')
        core.budget_contract(p['budget'])
        expected = {
            'source_proof': '22260faae6aa5b141940d42b46c06fdbd2050577333e26d910ed77773641cdd3',
            'corrected_claims': 'e45f83b1ebf108c1c73851ca58c823ac6310635946b262a26e9976296f5cdf1e',
            'changes': 'e996b2d70a33fc77c8cfcd89305005f53c2850e1ed75305e5388c0a9fe4fa7d4',
            'old_descriptor': '0a6038538cf8d1cd68a5792008a91c16130bec0d04e199c244735ec6288314a8',
            'blocked': 'cc2ad4dd0ff3966d4f41c3546f9417eb82c17db6ecff39ab141df8303e56ea6a',
        }
        for name, digest in expected.items():
            core.require(p[name]['sha256'] == digest, 'unselected source-card original: ' + name)
        home = Path('/home/gmahajan_labelbox_com/synthetic-data-fleet/source-0200-first-authoring-002/authoring/instruction')
        core.require(p['worker_run'] == str(home) and p['directory'] == str(home/'source-card-authoring-001'),
                     'source-card correction cannot choose another worker namespace')
        core.require(p['guard'] == dict(seconds=3300, memory_bytes=8589934592) and
                     p['automatic_retry'] is False and p['publication_performed'] is False,
                     'original source-card guards or ownership changed')
        core.require(authority['path'] == str(Path(p['directory'])/'permit.json'), 'unselected source-card permit')
        for row in p['dependencies']:
            core.require(binding(row['path']) == row, 'source-card dependency changed')
        core.require(len({row['path'] for row in p['dependencies']}) == len(p['dependencies']) and
                     all(binding(path) in p['dependencies'] for path in
                         (__file__, core.__file__, takeover.__file__)) and
                     binding(takeover.__file__) == p['takeover_module'] and
                     takeover.MAX_REPLACEMENTS == 3 and takeover.MAX_DURATION_NS == 7200*10**9,
                     'source-card dependency inventory or original takeover limits changed')
        self._extractor = None

    def is_card(self, value):
        return getattr(value, 'dialogue_pre_review_repair', None) == self.authority

    def owns_directory(self, value):
        return Path(value) == Path(self.plan['directory'])/'producer/dialogue'

    def dependencies(self):
        for row in self.plan['dependencies']:
            core.require(binding(row['path']) == row, 'source-card dependency changed')
        return list(self.plan['dependencies'])

    def budget(self):
        return core.budget_contract(self.plan['budget'])

    def author(self):
        from scripts.batch import harbor_dialogue_authoring
        return harbor_dialogue_authoring

    def owned(self):
        p = checked(self.authority)
        expected = {'schema_version', 'plan', 'phase', 'budget', 'initial', 'reservation',
                    'worker_run', 'old_descriptor', 'old_snapshot', 'blocked', 'predecessor',
                    'old_selection', 'selection', 'lease', 'tracker', 'prior_continuations',
                    'source', 'original', 'original_claims', 'claims', 'old_draft', 'old_check',
                    'author_calls', 'author_requests', 'source_proof', 'code', 'history',
                    'prefix', 'takeover', 'semantic_budget_reset', 'models_dispatched'}
        core.require(set(p) == expected and p['schema_version'] == core.SCHEMA and
                     p['plan'] == self.plan_binding and p['phase'] == 'dialogue-writer' and
                     p['budget'] == self.budget() and p['semantic_budget_reset'] is False and
                     type(p['models_dispatched']) is int and p['models_dispatched'] == 0,
                     'source-card permit schema, phase or budget changed')
        run = Path(p['worker_run'])
        core.require(str(run) == self.plan['worker_run'] and
                     checked(binding(run/'dialogue-source-card-repair.json')) == self.authority,
                     'source-card reservation does not own this worker')
        reserve = checked(p['reservation'])
        core.require(reserve == dict(schema_version=core.SCHEMA, plan=self.plan_binding,
                     directory=self.plan['directory'], blocked=self.plan['blocked'], budget=self.budget()),
                     'permanent source-card reservation changed')
        for key in ('old_descriptor', 'old_snapshot', 'blocked', 'source', 'source_proof', 'takeover'):
            core.require(p[key] == self.plan[key], 'source-card original authority changed: ' + key)
        core.require(p['predecessor'] == p['blocked'] and
                     p['initial'] == binding(run/'initial.json') and
                     p['claims'] == self.plan['corrected_claims'], 'source-card ancestry or claims changed')
        for row in [p['initial'], p['old_descriptor'], p['old_snapshot'], p['blocked'],
                    p['source'], p['original'], p['original_claims'], p['claims'], p['old_draft'],
                    p['old_check'], p['tracker'], p['source_proof'], *p['history']]:
            core.require(binding(row['path']) == row, 'source-card retained evidence changed')
        proof = checked(p['source_proof'])
        core.require(proof['status'] == 'SOURCE_CARD_EXTRACTION_AND_ORIGINAL_AUTHOR_PAIR_VERIFIED' and
                     proof['workbook'] == '0200' and proof['source'] == p['source'] and
                     proof['original_claims'] == p['original_claims'] and
                     proof['old_draft'] == p['old_draft'] and proof['old_check'] == p['old_check'] and
                     proof['corrected_claims'] == p['claims'] and proof['changes'] == self.plan['changes'] and
                     all(type(proof[k]) is int and proof[k] == v for k, v in
                         dict(changed_cards=25, total_cards=43, prior_review_attempts=0,
                              prior_author_generation=1, next_author_generation=2,
                              next_review_ordinal=1, model_calls=0, owning_mutations=0).items()) and
                     proof['unchanged_stage'] is True and proof['original_pair_actual'] is True and
                     proof['exact_original_extractor'] is True and proof['publication_ready'] is False,
                     'complete actual source-card proof differs')
        core.require([{k: c[k] for k in ('request', 'submitted', 'response', 'completed')}
                      for c in p['author_calls']] == proof['author_calls'],
                     'permit substituted original actual authors')
        failure = checked(p['blocked'])
        core.require(failure['status'] == 'DIALOGUE_REPAIR_REQUIRED' and
                     failure['step'] == 'dialogue-paraphraser' and
                     failure['selection'] == p['old_selection'], 'original mechanical failure differs')
        from dataclasses import replace
        from scripts.batch import harbor_transition_codec as codec
        old = codec.decode(p['old_selection'])
        projected = replace(old, content=replace(old.content, dialogue_claims=p['claims']))
        core.require(codec.encode(projected) == p['selection'],
                     'source-card permit changed another completed selection field')
        self.dependencies()
        return p

    def old_worker(self, p=None):
        p = p or self.owned()
        descriptor = checked(p['old_descriptor'])
        old = self.campaign.worker_decode(descriptor['worker'])
        core.require(str(old.run) == p['worker_run'] and old.dialogue_pre_review_repair is None
                     and old.dialogue_semantic_retry is None and old.retry is None
                     and old.dialogue_retry is None and old.boundary_recovery is None
                     and old.code_repair is None and old.disclosure_revision is None,
                     'unreviewed original source-card continuation')
        core.require(dict(lease_renewal=old.lease_renewal) == p['prior_continuations'] and
                     old.lease_renewal is not None, 'source-card retained renewal differs')
        self.renewal._historical_inventory(old, p['old_snapshot'])
        return old

    def original_pair(self, p=None):
        p = p or self.owned()
        old = self.old_worker(p)
        requests = [self.worker.request_saved(Path(row['path'])) for row in p['author_requests']]
        for row, request, kind in zip(p['author_requests'], requests, ('dialogue-writer', 'dialogue-paraphraser')):
            core.require(binding(row['path']) == row and request.prepared.ordinal == 1 and
                         request.prepared.limit == 2 and request.prepared.scope.budget_id == kind,
                         'original author request or consumed budget changed')
        calls = tuple(self.producer.observed(request, old.campaign) for request in requests)
        from dataclasses import asdict
        core.require([asdict(c) for c in calls] == p['author_calls'] and len(calls) == 2,
                     'original complete authors differ from root authority')
        checked_pair = core.original_pair(self.author(), calls, policy=requests[0].prepared.policy,
            claims=checked(p['original_claims']), draft=Path(p['old_draft']['path']).read_text(),
            root=old.root, workbook='0200', source_sha256=p['original']['sha256'])
        return old, checked_pair

    def historical_lease(self, value, before):
        p = self.owned()
        core.require(self.is_card(value) and value.lease_renewal is not None,
                     'historical source-card renewal link changed')
        if value.lease_renewal != p['prior_continuations']['lease_renewal']:
            core.require(hasattr(self, 'current_renewal'), 'unselected later source-card renewal reader')
            return self.current_renewal.validate(value, before)
        old, _ = self.original_pair(p)
        retained = self.renewal.validate_historical_integrity(old, before, p['old_snapshot'])
        # The source correction is neither a semantic retry nor a lease renewal.
        # Its current custody comes only from the exact original T0→1 receipt.
        initial, after, new = self.takeover.validate_completion(p['takeover'])
        from scripts.batch import harbor_tracker as schema
        from scripts.batch.harbor_workbook_bootstrap import Lease
        from scripts.batch.harbor_tracker_lifecycle import canonical, validate_lifecycle
        core.require(validate_lifecycle(initial) == 0 and validate_lifecycle(after) == 1 and
                     p['tracker'] == checked(p['takeover'])['after'] and
                     p['lease'] == checked(p['takeover'])['lease'] and
                     initial['bindings']['source_file'] == p['source'],
                     'source-card replacement is not the exact first owning takeover')
        current = before['leases']['task_agent']
        lease = Lease(current['owner'], current['token'], before['revision'], schema.sha(canonical(before)))
        original_lease = checked(p['blocked'])['lease']
        self.takeover.continuation_chain(retained['tracker'], retained['lease'], before, lease,
            takeover=p['takeover'], tracker_path=old.context.tracker_root/'0200.json')
        core.require(original_lease == checked(old.lease_renewal)['lease'],
                     'failed original authors did not use the retained renewed lease')
        return retained

    def worker_renewal(self, value, before):
        result = self.historical_lease(value, before)
        if value.lease_renewal == self.owned()['prior_continuations']['lease_renewal']:
            return None  # The new source-card permit is the initial predecessor.
        return result

    def validate(self, value, before):
        p = self.owned()
        core.require(self.is_card(value) and str(value.run) == p['worker_run'] and
                     before['workbook_id'] == '0200' and before['bindings']['source_file'] == p['source'] and
                     before['bindings']['original_raw_source'] == p['original'] and
                     self.worker.code(value) == p['code'], 'source-card worker identity changed')
        self.historical_lease(value, before)
        return p

    def validate_task(self, task):
        p = self.owned()
        core.require(self.is_card(task) and str(task.run.parent) == p['worker_run'] and
                     task.root == self.old_worker(p).root, 'source-card task detached from original worker')
        before = self.producer.read(task.tracker)
        core.require(before['workbook_id'] == '0200' and before['bindings']['source_file'] == p['source']
                     and before['bindings']['original_raw_source'] == p['original'],
                     'source-card task source differs')
        self.original_pair(p)
        return self.authority

    def extractor(self):
        self.dependencies()
        if self._extractor is None:
            with self._extractor_scope() as module:
                self._extractor = SimpleNamespace(extract=self._extract, disclose=module.disclose)
        return self._extractor

    def _extract(self, stage):
        # Lazy imports inside speak_record/extract need the same exact bindings
        # as module initialization. Keep them scoped through the entire call.
        with self._extractor_scope() as module:
            return module.extract(stage)

    @contextmanager
    def _extractor_scope(self):
        self.dependencies()
        layout = checked(self.plan['renderer_manifest'])
        for row in layout['skill']:
            for name in ('old', 'new'):
                core.require(binding(row[name]['path']) == row[name], 'renderer implementation changed')
            core.require(row['new'] in self.plan['dependencies'], 'undeclared current extractor dependency')
        core.require(self.plan['renderer_manifest'] in self.plan['dependencies'], 'undeclared renderer manifest')
        paths = {Path(r['new']['path']).name: r['new']['path'] for r in layout['skill']}
        names = (*[Path(name).stem for name in paths], 'disclose')
        saved = {name: sys.modules.get(name) for name in names}
        old_path = list(sys.path)
        try:
            for name in names:
                sys.modules.pop(name, None)
            # The original extractor imports disclose by its bare skill name.
            # Its prospective location cannot infer the original sibling path.
            # Load that unchanged producer explicitly from the full old snapshot.
            original = checked(self.plan['old_snapshot'])
            root = Path(original['root'])
            relative = '.cursor/skills/task-disclosure/scripts/disclose.py'
            expected = {row['relative']: row['sha256'] for row in original['files']}
            core.require(binding(root/relative)['sha256'] == expected[relative],
                         'original disclosure extraction dependency changed')
            disclose = self.producer.skill_module(root, 'task-disclosure', 'disclose')
            core.require(binding(disclose.__file__) == binding(root/relative),
                         'foreign cached disclosure extraction dependency')
            sys.modules['disclose'] = disclose
            sys.path.insert(0, str(Path(paths['extract_claims.py']).parent))
            spec = importlib.util.spec_from_file_location('extract_claims', paths['extract_claims.py'])
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            yield module
        finally:
            sys.path[:] = old_path
            for name in names:
                if saved[name] is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = saved[name]

    def context(self, task, *, root):
        self.validate_task(task)
        p = self.owned()
        claims = checked(p['claims'])
        core.require(self.extractor().extract(task.stage) == claims,
                     'source-card claims are not the complete current extraction')
        directory = Path(self.plan['directory'])/'producer/dialogue'
        composer = self.producer.skill_module(root, 'additional-assumptions-dialogue', 'compose_draft')
        template, slots = composer.compose(claims, claims['writer_pack'])
        core.require(checked(binding(directory/'claims.json')) == claims and
                     (directory/'draft_template.md').read_text() == template and
                     checked(binding(directory/'slots.json')) == slots and
                     checked(binding(directory/'writer_pack.json')) == claims['writer_pack'],
                     'corrected source-card scaffold changed')
        return directory, claims, template, slots

    def feedback(self):
        p = self.owned()
        changes = checked(self.plan['changes'])
        return core.feedback(self.authority, Path(p['old_draft']['path']).read_text(), changes, self.budget())

    def review_guidance(self):
        return ('\nThe source_card_authoring receipt distinguishes author generation two from '
                'this unused first independent review. Evaluate every claim and source '
                'operand under the corrected cards. No earlier independent dialogue review '
                'exists. Do not waive any formula, numerical, scope, or coverage requirement.')

    def verify_pair(self, task, draft, calls=None):
        p = self.owned()
        self.validate_task(task)
        old, prior = self.original_pair(p)
        paths = [Path(self.plan['directory'])/'steps'/kind/'request.json'
                 for kind in ('dialogue-writer', 'dialogue-paraphraser')]
        observed = tuple(self.producer.observed(self.worker.request_saved(path), old.campaign) for path in paths)
        if calls is not None:
            core.require(tuple(calls) == observed, 'new authors differ from actual owning requests')
        return core.generated_pair(self.author(), observed, policy=task.policy, claims=checked(p['claims']),
            draft=draft, previous=self.feedback(), authority=self.authority, prior_checked=prior,
            root=old.root, workbook='0200', source_sha256=p['original']['sha256'])

    def review_pair(self, bound, calls, *, policy, claims, draft, root, workbook, original_sha256):
        """Read actual generation-two requests for the owning readiness gate."""
        p = self.owned()
        core.require(bound == self.authority and workbook == '0200' and
                     original_sha256 == p['original']['sha256'] and claims == checked(p['claims']),
                     'source-card readiness changed its source, claims or authority')
        old = self.old_worker(p)
        core.require(root == old.root, 'source-card readiness uses a foreign task implementation')
        from dataclasses import replace
        from scripts.batch import harbor_transition_codec as codec
        worker = replace(old, dialogue_pre_review_repair=self.authority)
        tracker = binding(old.context.tracker_root/'0200.json')
        self.validate(worker, checked(tracker))
        task = self.worker.task(worker, codec.decode(p['selection']), tracker)
        core.require(task.policy == policy, 'source-card readiness model policy changed')
        return self.verify_pair(task, draft, calls=calls)

    def review_projection(self, authority, claims, draft):
        p = self.owned()
        core.require(authority == self.authority and claims == checked(p['claims']),
                     'source-card review projection changed')
        return core.review_body(claims, draft, self.authority, self.budget())['source_card_authoring']
