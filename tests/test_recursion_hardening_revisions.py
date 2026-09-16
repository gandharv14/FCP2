import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_hardening_revisions as r


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.rows, self.staged = [], []
        for task, value in [('a', 2.0), ('b', 3.0)]:
            folder = self.work / 'assets' / task
            folder.mkdir(parents=True)
            paths = {}
            for name, text in [('prompt', 'Base instructions ' + task), ('new', 'Base instructions ' + task + '\nClarify convention.'),
                               ('workbook', 'unchanged inputs'), ('source', 'native source'), ('evidence', 'source proof')]:
                path = folder / name
                path.write_text(text)
                paths[name] = path
            keypath = folder / 'key.json'
            self.save(keypath, {'kind': 'cell_value', 'targets': {'Sheet!A1': value},
                                'tolerance': {'numeric_abs': 1e-6, 'numeric_rel': 1e-6}})
            vp = folder / 'validation.json'
            self.save(vp, {'nativeOracle': {'Sheet!A1': value}, 'independentOracle': {'Sheet!A1': value},
                           'promptChangesOnlyAddedClarification': True, 'newEconomicInputs': False,
                           'newTargets': False, 'tolerancesUnchanged': True, 'targetKeysUnchanged': True})
            base = {'taskId': task, 'problemId': 'problem-' + task, 'promptPath': str(paths['prompt']),
                    'promptSha256': r.sha(paths['prompt']), 'answerKeyPath': str(keypath),
                    'answerKeySha256': r.sha(keypath), 'workbookPath': str(paths['workbook']),
                    'workbookSha256': r.sha(paths['workbook']), 'additionalAssumptionsPath': None,
                    'unrelated': {'nested': ['must remain unchanged']}}
            self.rows.append(base)
            version = {'id': 'base-' + task, 'problemId': base['problemId'], 'environmentId': 'env',
                       'prompt': paths['prompt'].read_text(), 'lockedAt': 'yesterday', 'version': 'v1',
                       'gradingConfig': {'command': 'same-key'}, 'allowedDomains': ['same.domain'],
                       'timeoutSeconds': 10800, 'solverRunConfigVersionId': 'opus', 'createdAt': 'before', 'updatedAt': 'before'}
            self.save(self.work / 'problems' / task / 'verified.json', version)
            self.staged.append({'taskId': task, 'baseProblemVersionId': version['id'],
                'basePromptPath': base['promptPath'], 'basePromptSha256': base['promptSha256'],
                'promptPath': str(paths['new']), 'promptSha256': r.sha(paths['new']),
                'answerKeyPath': str(keypath), 'answerKeySha256': r.sha(keypath),
                'workbookPath': str(paths['workbook']), 'workbookSha256': r.sha(paths['workbook']),
                'nativeSourcePath': str(paths['source']), 'nativeSourceSha256': r.sha(paths['source']),
                'additionalAssumptionsPath': None, 'validationPath': str(vp), 'sourceEvidencePaths': [str(paths['evidence'])]})
        self.save(self.work / 'final-assets.json', self.rows)
        self.save(self.work / 'campaign.json', {'assetsSha256': r.sha(self.work / 'final-assets.json'), 'tasks': self.rows})
        self.save(self.work / 'validity-fixes/manifest.json', self.staged)
        self.save(self.work / 'task-validity-review.json', {'status': 'complete'})
        for model in r.MODELS:
            self.save(self.work / 'final-images/configs' / model / 'verified.json',
                      {'id': model, 'lockedAt': 'yes', 'verifiedAt': 'yes'})

    @staticmethod
    def save(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def prepared(self):
        for row in self.staged:
            task = row['taskId']
            v = r.read(self.work / 'problems' / task / 'verified.json')
            v.update(id='corrected-' + task, version='v2', prompt=Path(row['promptPath']).read_text(), lockedAt='today')
            self.save(self.work / 'validity-fixes/versions' / task / 'verified.json', v)

    def activated(self):
        self.prepared()
        r.activate(self.work, confirmed=True)

    def dispatched(self):
        self.save(self.work / 'evaluation-1.request.json', {'problems': [
            {'problemId': 'problem-' + t, 'problemVersionId': 'base-' + t} for t in ['a', 'b']]})
        self.save(self.work / 'evaluation-1.response.json', {'id': 'old-evaluation'})

    def test_pre_activation_is_base_even_when_provisioned(self):
        self.prepared()
        self.assertEqual(r.active_assets(self.rows, self.work), self.rows)
        self.assertEqual(r.version_for('a', self.work)['id'], 'base-a')

    def test_activation_only_overlays_two_fields_without_mutating(self):
        before = copy.deepcopy(self.rows)
        self.activated()
        revised = r.active_assets(self.rows, self.work)
        self.assertEqual(self.rows, before)
        for base, active in zip(before, revised):
            self.assertEqual({k for k in base if base[k] != active[k]}, {'promptPath', 'promptSha256'})
        self.assertEqual(r.version_for('a', self.work)['id'], 'corrected-a')
        with self.assertRaises(ValueError):
            r.activate(self.work, confirmed=True)

    def test_requires_explicit_activation_and_all_verified(self):
        with self.assertRaises(ValueError):
            r.activate(self.work)
        with self.assertRaises(FileNotFoundError):
            r.activate(self.work, confirmed=True)
        self.assertFalse((self.work / 'validity-fixes/activation.json').exists())

    def test_bulk_versions_validate_activation_only_once(self):
        self.activated()
        with patch.object(r, 'activation', wraps=r.activation) as checked:
            versions = r.versions_for(self.rows, self.work)
            checked.assert_called_once_with(self.work)
        self.assertEqual({k: v['id'] for k, v in versions.items()},
                         {'a': 'corrected-a', 'b': 'corrected-b'})

    def test_stale_manifest_and_artifacts_fail_closed(self):
        self.activated()
        Path(self.staged[0]['sourceEvidencePaths'][0]).write_text('changed proof')
        with self.assertRaises(ValueError):
            r.version_for('a', self.work)

    def test_manifest_divergence_fails_closed(self):
        self.activated()
        self.save(self.work / 'validity-fixes/manifest.json', self.staged[:1])
        with self.assertRaises(ValueError):
            r.active_assets(self.rows, self.work)

    def test_nonprompt_behavior_change_rejected(self):
        self.prepared()
        p = self.work / 'validity-fixes/versions/a/verified.json'
        v = r.read(p)
        v['timeoutSeconds'] = 1
        self.save(p, v)
        with self.assertRaises(ValueError):
            r.activate(self.work, confirmed=True)

    def test_independent_oracle_is_regraded(self):
        p = Path(self.staged[0]['validationPath'])
        v = r.read(p)
        v['independentOracle']['Sheet!A1'] = 999
        self.save(p, v)
        with self.assertRaises(ValueError):
            r.validate_staged(self.work)

    def test_provision_only_updates_prompt(self):
        versions = {}
        calls = []
        for task in ['a', 'b']:
            versions['base-' + task] = r.read(self.work / 'problems' / task / 'verified.json')
        def cli(*args, payload=None):
            calls.append((args, r.read(payload) if payload else None))
            action = args[1]
            if action == 'get':
                return copy.deepcopy(versions[args[-1]])
            if action == 'create':
                v = copy.deepcopy(versions[args[-1]])
                v['id'] = v['id'].replace('base-', 'corrected-')
                v['lockedAt'] = None
                versions[v['id']] = v
                return {'id': v['id']}
            if action == 'update':
                versions[args[-1]].update(r.read(payload))
            if action == 'lock':
                versions[args[-1]]['lockedAt'] = 'today'
        with patch.object(r.c, 'cli', side_effect=cli):
            r.provision(self.work)
        updates = [data for args, data in calls if args[1] == 'update']
        self.assertEqual(len(updates), 2)
        self.assertTrue(all(set(data) == {'prompt'} for data in updates))
        self.assertFalse((self.work / 'validity-fixes/activation.json').exists())

    def test_uncertain_mutation_is_not_retried(self):
        base = r.read(self.work / 'problems/a/verified.json')
        calls = []
        def cli(*args, payload=None):
            calls.append(args)
            if args[1] == 'get':
                return base
            raise RuntimeError('network result uncertain')
        with patch.object(r.c, 'cli', side_effect=cli):
            with self.assertRaises(RuntimeError):
                r.provision(self.work)
            with self.assertRaises(RuntimeError):
                r.provision(self.work)
        self.assertEqual(sum(args[1] == 'create' for args in calls), 1)

    def test_correction_replaces_all_attempts_both_models(self):
        self.activated()
        self.dispatched()
        p = r.correction_payload(self.work)
        self.assertEqual(len(p['problems']), 2)
        self.assertEqual(p['metadata']['attemptsPerProblem'], 3)
        self.assertEqual({s['runConfigVersionId'] for s in p['solvers']}, {'opus', 'gemini'})
        self.assertEqual({s['concurrency'] for s in p['solvers']}, {6})
        self.assertEqual({v['problemVersionId'] for v in p['problems']}, {'corrected-a', 'corrected-b'})

    def test_capacity_is_fresh_and_dispatch_receipts_live_at_work_root(self):
        self.activated()
        self.dispatched()
        cap = {'checkedAt': r.datetime.now(r.timezone.utc).isoformat(), 'availableConcurrencyByModel': {'opus': 6, 'gemini': 6}}
        with patch.object(r, 'fresh_capacity', return_value=cap) as fresh, patch.object(r.c, 'cli', return_value={'id': 'correction'}) as cli:
            r.launch_corrections(self.work, confirmed=True)
            self.assertEqual(cli.call_count, 1)
            fresh.assert_called_once()
            self.assertTrue((self.work / 'correction-1.request.json').exists())
            self.assertTrue((self.work / 'correction-1.response.json').exists())
            r.launch_corrections(self.work, confirmed=True)
            self.assertEqual(cli.call_count, 1)

    def test_external_opus_overcommit_allows_gemini_only_dispatch(self):
        self.activated()
        self.dispatched()
        cap = {'checkedAt': r.datetime.now(r.timezone.utc).isoformat(),
               'availableConcurrencyByModel': {'opus': 0, 'gemini': 50},
               'overReservationByModel': {'opus': 65, 'gemini': 0}}
        with patch.object(r, 'fresh_capacity', return_value=cap), patch.object(r.c, 'cli', return_value={'id': 'gemini-correction'}):
            r.launch_corrections(self.work, confirmed=True, models=['gemini'])
        request = r.read(self.work / 'correction-1-gemini.request.json')
        self.assertEqual([s['runConfigVersionId'] for s in request['solvers']], ['gemini'])
        self.assertEqual(request['metadata']['attemptsPerProblem'], 3)
        self.assertEqual({x['problemVersionId'] for x in request['problems']}, {'corrected-a', 'corrected-b'})
        self.assertFalse((self.work / 'correction-1-opus.intent.json').exists())

    def test_capacity_exhaustion_blocks_before_mutation(self):
        self.activated()
        self.dispatched()
        cap = {'checkedAt': r.datetime.now(r.timezone.utc).isoformat(), 'availableConcurrencyByModel': {'opus': 5, 'gemini': 10}}
        with patch.object(r, 'fresh_capacity', return_value=cap), patch.object(r.c, 'mutation') as mutate:
            with self.assertRaises(ValueError):
                r.launch_corrections(self.work, confirmed=True, models=r.MODELS)
            mutate.assert_not_called()

    def test_capacity_counts_all_active_reservations(self):
        def cli(*args):
            if args[1] == 'list':
                return {'items': [{'id': 'e1', 'status': 'running'}, {'id': 'done', 'status': 'completed'}]}
            return {'solvers': [{'runConfigVersionId': 'opus', 'jobStatus': 'running', 'metadata': {'concurrency': 50}},
                                {'runConfigVersionId': 'gemini', 'jobStatus': 'queued', 'metadata': {'concurrency': 75}}]}
        with patch.object(r.c, 'cli', side_effect=cli), patch.object(r.c, 'CONFIG', {'concurrencyPerModel': 150}), patch.object(r.c, 'REPORTS', self.work / 'none'):
            cap = r.fresh_capacity(self.work)
        self.assertEqual(cap['availableConcurrencyByModel'], {'opus': 100, 'gemini': 75})

    def test_partial_oracle_requires_complete_coverage_and_source_host_proof(self):
        import openpyxl
        book = openpyxl.Workbook()
        book.active.title = 'Sheet'
        book.active['A2'] = '=1+1'
        source = self.work / 'partial-source.xlsx'
        book.save(source)
        key = {'kind': 'cell_value', 'targets': {'Sheet!A1': 2, 'Sheet!A3': 3}}
        evidence = {'nativeOracle': key['targets'], 'independentOracle': {'Sheet!A1': 2},
            'independentTargetScope': 'partial_with_source_host_validation',
            'independentlyVerifiedTargets': ['Sheet!A1'],
            'notIndependentlyVerifiedTargets': {'Sheet!A3': 'Circular forecast path unsupported'},
            'clarifiedSourceHosts': ['Sheet!A2'],
            'independentSourceHostOracle': {'Sheet!A2': {'sourceFormula': '=1+1', 'independentValue': 2}}}
        r.validate_oracles(evidence, key, source)
        bad = copy.deepcopy(evidence)
        bad['notIndependentlyVerifiedTargets'] = {}
        with self.assertRaises(ValueError):
            r.validate_oracles(bad, key, source)
        bad = copy.deepcopy(evidence)
        bad['independentSourceHostOracle']['Sheet!A2']['independentValue'] = 3
        with self.assertRaises(ValueError):
            r.validate_oracles(bad, key, source)

    def test_literal_arithmetic_rejects_executable_content(self):
        self.assertEqual(r.literal_arithmetic('=-4+45'), 41)
        self.assertEqual(r.literal_arithmetic('=10^6'), 1000000)
        with self.assertRaises(ValueError): r.literal_arithmetic('=10^1000000')
        with self.assertRaises(ValueError):
            r.literal_arithmetic("=__import__('os').system('anything')")

    def test_partial_terminal_requires_completed_evaluation_or_every_exact_slot(self):
        evaluation = {'id': 'e', 'nAttemptsPerProblem': 3, 'problems': [{'problemVersionId': 'p'}]}
        solver = {'id': 's', 'jobStatus': 'partial'}
        matrix = {'evaluationId': 'e', 'cells': [{'solverId': 's', 'problemVersionId': 'p', 'attempts': [
            {'problemRunId': str(n), 'attemptNumber': n, 'status': 'failed'} for n in (1, 2, 3)]}]}
        self.assertFalse(r.solver_is_terminal(evaluation, solver))
        self.assertTrue(r.solver_is_terminal({**evaluation, 'completedAt': '2026-09-15T10:00:00Z'}, solver))
        self.assertTrue(r.solver_is_terminal(evaluation, solver, matrix))
        for change in ('missing', 'running', 'duplicate'):
            bad = copy.deepcopy(matrix)
            attempts = bad['cells'][0]['attempts']
            if change == 'missing': attempts.pop()
            if change == 'running': attempts[0]['status'] = 'running'
            if change == 'duplicate': attempts[0]['attemptNumber'] = 2
            self.assertFalse(r.solver_is_terminal(evaluation, solver, bad))
        self.assertFalse(r.solver_is_terminal(evaluation, {'id': 's', 'jobStatus': 'running'}, matrix))


if __name__ == '__main__':
    unittest.main()
