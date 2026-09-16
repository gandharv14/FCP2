import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_recursion_hardening_revisions as base_tests
import recursion_hardening_revisions as r
import recursion_hardening_content as content


class ContentTests(unittest.TestCase):
    save = staticmethod(base_tests.RevisionTests.save)

    def setUp(self):
        base_tests.RevisionTests.setUp(self)
        # a: prompt+asset; b: prompt only; c: asset only; d: unaffected.
        for task in ('c', 'd'):
            row = copy.deepcopy(self.rows[1])
            row.update(taskId=task, problemId='problem-' + task)
            self.rows.append(row)
            version = r.read(self.work / 'problems/b/verified.json')
            version.update(id='base-' + task, problemId='problem-' + task)
            self.save(self.work / 'problems' / task / 'verified.json', version)
        self.save(self.work / 'final-assets.json', self.rows)
        self.save(self.work / 'campaign.json', {'assetsSha256': r.sha(self.work / 'final-assets.json'),
                   'tasks': self.rows, 'runtimeManifestSha256': 'f' * 64})
        self.patches = []
        for task in ('a', 'c'):
            book = self.work / ('patched-' + task + '.xlsx')
            book.write_text('patched calculated string only ' + task)
            proof = self.work / (task + '-patch-proof.json')
            self.save(proof, {'inputsPreserved': True, 'calculatedDisclosuresOnly': True, 'solvable': True})
            base = next(x for x in self.rows if x['taskId'] == task)
            self.patches.append({'taskId': task, 'baseWorkbookSha256': base['workbookSha256'],
                'workbookPath': str(book), 'workbookSha256': r.sha(book),
                'validationPath': str(proof), 'validationSha256': r.sha(proof)})
        self.patch_path = self.work / 'patch-manifest.json'
        self.save(self.patch_path, self.patches)
        payload_proof = {'baseAssetsSha256': r.sha(self.work / 'final-assets.json'),
            'baseRuntimeManifestSha256': 'f' * 64, 'nonWorkbookPayloadBaseSha256': 'd' * 64,
            'nonWorkbookPayloadSuccessorSha256': 'd' * 64,
            'workbooks': {x['taskId']: {'baseSha256': x['workbookSha256'],
                'successorSha256': next((p['workbookSha256'] for p in self.patches if p['taskId'] == x['taskId']), x['workbookSha256'])} for x in self.rows},
            'probeResults': {m: {'status': 'succeeded', 'runConfigVersionId': 'new-' + m} for m in r.MODELS}}
        proof_path = self.work / 'payload-proof.json'
        self.save(proof_path, payload_proof)
        runtime = {'baseAssetsSha256': payload_proof['baseAssetsSha256'], 'baseRuntimeManifestSha256': 'f' * 64,
                   'payloadProofPath': str(proof_path), 'payloadProofSha256': r.sha(proof_path), 'models': {}}
        for model in r.MODELS:
            old = r.read(self.work / 'final-images/configs' / model / 'verified.json')
            old['config'] = {'harnessImageUrl': 'registry/' + model + '@sha256:' + 'a' * 64,
                             'args': model + ' fixed reasoning', 'privileged': False}
            self.save(self.work / 'final-images/configs' / model / 'verified.json', old)
            new = copy.deepcopy(old)
            new['id'] = 'new-' + model
            new['config']['harnessImageUrl'] = 'registry/' + model + '@sha256:' + 'b' * 64
            cp = self.work / 'successor-configs' / model / 'verified.json'
            self.save(cp, new)
            runtime['models'][model] = {'configPath': str(cp), 'configSha256': r.sha(cp),
                                       'image': new['config']['harnessImageUrl']}
        self.runtime_path = self.work / 'runtime-manifest.json'
        self.save(self.runtime_path, runtime)

    def planned(self):
        return r.prepare_content_successor(self.patch_path, self.runtime_path, self.work)

    def content_activated(self):
        plan = self.planned()
        for row in plan['revisions']:
            v = r.read(row['baseVersionPath'])
            v.update(id='content-' + row['taskId'], version='v3', lockedAt='today',
                     prompt=Path(row['promptPath']).read_text(),
                     solverRunConfigVersionId=row['defaultSolverRunConfigVersionId'])
            self.save(self.work / 'content-successor/versions' / row['taskId'] / 'verified.json', v)
        return r.activate_content_successor(self.work, confirmed=True)

    def test_union_deduplicates_and_asset_only_keeps_prompt(self):
        plan = self.planned()
        self.assertEqual([x['taskId'] for x in plan['revisions']], ['a', 'b', 'c'])
        by = {x['taskId']: x for x in plan['revisions']}
        self.assertTrue(by['a']['assetRepaired'] and by['a']['promptRepaired'])
        self.assertEqual(by['c']['promptPath'], self.rows[2]['promptPath'])
        self.assertEqual(by['c']['defaultSolverRunConfigVersionId'], 'new-opus')
        self.assertEqual(by['b']['defaultSolverRunConfigVersionId'], 'opus')
        self.assertEqual(r.version_for('c', self.work)['id'], 'base-c')

    def test_nested_mask_receipt_is_bound_to_task_and_source_evidence(self):
        item = self.patches[0]
        base = next(x for x in self.rows if x['taskId'] == item['taskId'])
        source = self.work / 'mask-source-evidence.json'
        self.save(source, {'computedOutputConfirmed': True})
        path = Path(item['validationPath'])
        proof = {'taskId': item['taskId'], 'workbookSha256': item['workbookSha256'],
                 'baseWorkbookSha256': base['workbookSha256'], 'answerKeySha256': base['answerKeySha256'],
                 'sourceEvidencePath': str(source), 'sourceEvidenceSha256': r.sha(source),
                 'validation': r.read(path)}
        self.save(path, proof)
        item['validationSha256'] = r.sha(path)
        self.save(self.patch_path, self.patches)
        plan = self.planned()
        self.assertEqual(plan['artifactHashes'][str(source.resolve())], r.sha(source))
        self.save(source, {'computedOutputConfirmed': False})
        with self.assertRaises(ValueError): content.validate_plan(self.work)

    def test_nested_mask_receipt_for_different_task_is_rejected(self):
        item = self.patches[0]
        path = Path(item['validationPath'])
        self.save(path, {'taskId': 'different-task', 'validation': r.read(path)})
        item['validationSha256'] = r.sha(path)
        self.save(self.patch_path, self.patches)
        with self.assertRaises(ValueError): self.planned()

    def test_content_four_field_overlay_and_unaffected_base(self):
        self.content_activated()
        old = copy.deepcopy(self.rows)
        active = {x['taskId']: x for x in r.active_assets(self.rows, self.work)}
        self.assertEqual(self.rows, old)
        self.assertEqual({k for k in old[0] if old[0][k] != active['a'][k]},
                         {'promptPath', 'promptSha256', 'workbookPath', 'workbookSha256'})
        self.assertEqual(active['d'], old[3])
        self.assertEqual(r.versions_for(self.rows, self.work)['c']['id'], 'content-c')
        self.assertEqual(r.versions_for(self.rows, self.work)['d']['id'], 'base-d')
        self.assertEqual({k: v['id'] for k, v in r.active_configs(self.work).items()},
                         {'opus': 'new-opus', 'gemini': 'new-gemini'})

    def test_run_binding_rejects_cross_image_versions_preserves_unaffected(self):
        self.content_activated()
        context = r.run_binding_context(self.work)
        for task in ['a', 'c']:
            r.validate_run_binding(task, 'base-' + task, 'opus', self.work, context=context)
            r.validate_run_binding(task, 'content-' + task, 'new-gemini', self.work, context=context)
            for version, config in [('base-' + task, 'new-opus'), ('content-' + task, 'gemini')]:
                with self.assertRaises(ValueError):
                    r.validate_run_binding(task, version, config, self.work, context=context)
        for config in ['opus', 'gemini', 'new-opus', 'new-gemini']:
            r.validate_run_binding('d', 'base-d', config, self.work, context=context)
            r.validate_run_binding('b', 'base-b', config, self.work, context=context)
            r.validate_run_binding('b', 'content-b', config, self.work, context=context)
        with self.assertRaises(ValueError):
            r.validate_run_binding('d', 'content-a', 'new-opus', self.work, context=context)

    def test_successor_model_behavior_change_rejected(self):
        data = r.read(self.runtime_path)
        cp = Path(data['models']['opus']['configPath'])
        cfg = r.read(cp)
        cfg['config']['args'] = 'different model or reasoning'
        self.save(cp, cfg)
        data['models']['opus']['configSha256'] = r.sha(cp)
        self.save(self.runtime_path, data)
        with self.assertRaises(ValueError): self.planned()

    def test_stale_workbook_patch_or_activation_is_rejected(self):
        self.content_activated()
        Path(self.patches[0]['workbookPath']).write_text('changed after activation')
        with self.assertRaises(ValueError): r.active_configs(self.work)

    def test_unlinked_legacy_activation_rejected(self):
        self.content_activated()
        self.save(self.work / 'validity-fixes/activation.json', {'not': 'linked'})
        with self.assertRaises(ValueError): r.activation(self.work)

    def test_activation_cannot_change_frozen_plan_binding(self):
        self.content_activated()
        path = self.work / 'content-successor/activation.json'
        data = r.read(path)
        data['revisions'][0]['assetRepaired'] = False
        self.save(path, data)
        with self.assertRaises(ValueError): r.run_binding_context(self.work)

    def test_content_correction_uses_new_pair_and_only_affected_tasks(self):
        self.content_activated()
        self.save(self.work / 'evaluation-1.request.json', {'problems': [
            {'problemId': 'problem-' + t, 'problemVersionId': 'base-' + t} for t in ['a', 'b', 'c', 'd']]})
        self.save(self.work / 'evaluation-1.response.json', {'id': 'old'})
        payload = r.correction_payload(self.work)
        self.assertEqual({x['problemVersionId'] for x in payload['problems']}, {'content-a', 'content-b', 'content-c'})
        self.assertEqual({x['runConfigVersionId'] for x in payload['solvers']}, {'new-opus', 'new-gemini'})
        self.assertEqual(payload['metadata']['attemptsPerProblem'], 3)

    def test_asset_only_provision_changes_binding_not_prompt(self):
        plan = self.planned()
        versions = {row['baseProblemVersionId']: r.read(row['baseVersionPath']) for row in plan['revisions']}
        updates = {}
        def cli(*args, payload=None):
            action = args[1]
            if action == 'get': return copy.deepcopy(versions[args[-1]])
            if action == 'create':
                v = copy.deepcopy(versions[args[-1]])
                v.update(id=v['id'].replace('base-', 'content-'), lockedAt=None)
                versions[v['id']] = v
                return {'id': v['id']}
            if action == 'update':
                updates[args[-1]] = r.read(payload)
                versions[args[-1]].update(updates[args[-1]])
            if action == 'lock': versions[args[-1]]['lockedAt'] = 'today'
        with patch.object(r.c, 'cli', side_effect=cli): r.provision_content_successor(self.work)
        self.assertEqual(updates['content-c'], {'solverRunConfigVersionId': 'new-opus'})
        self.assertEqual(set(updates['content-b']), {'prompt'})
        self.assertEqual(set(updates['content-a']), {'prompt', 'solverRunConfigVersionId'})
        self.assertFalse((self.work / 'content-successor/activation.json').exists())

    def test_solo_correction_dispatch_keeps_exact_cohort_and_avoids_duplicates(self):
        self.content_activated()
        self.save(self.work / 'evaluation-1.request.json', {'problems': [
            {'problemId': 'problem-' + t, 'problemVersionId': 'base-' + t} for t in ['a', 'b', 'c', 'd']]})
        self.save(self.work / 'evaluation-1.response.json', {'id': 'old'})
        cap = {'checkedAt': r.datetime.now(r.timezone.utc).isoformat(),
               'availableConcurrencyByModel': {'opus': 50, 'gemini': 0}}
        with patch.object(r, 'fresh_capacity', return_value=cap), patch.object(r.c, 'cli', return_value={'id': 'opus-only'}) as cli:
            r.launch_corrections(self.work, confirmed=True)
            self.assertEqual(cli.call_count, 1)
        first = r.read(self.work / 'correction-content-1-opus.request.json')
        self.assertEqual([s['runConfigVersionId'] for s in first['solvers']], ['new-opus'])
        cap['availableConcurrencyByModel'] = {'opus': 0, 'gemini': 50}
        with patch.object(r, 'fresh_capacity', return_value=cap), patch.object(r.c, 'cli', return_value={'id': 'gemini-only'}) as cli:
            r.launch_corrections(self.work, confirmed=True)
            r.launch_corrections(self.work, confirmed=True)
            self.assertEqual(cli.call_count, 1)
        second = r.read(self.work / 'correction-content-1-gemini.request.json')
        self.assertEqual(first['problems'], second['problems'])
        self.assertEqual(second['metadata']['attemptsPerProblem'], 3)

    def test_uncertain_solo_intent_blocks_other_model_dispatch(self):
        self.content_activated()
        self.save(self.work / 'evaluation-1.request.json', {'problems': [
            {'problemId': 'problem-a', 'problemVersionId': 'base-a'}]})
        self.save(self.work / 'evaluation-1.response.json', {'id': 'old'})
        self.save(self.work / 'correction-content-1-opus.intent.json', {'uncertain': True})
        with patch.object(r.c, 'mutation') as mutate:
            with self.assertRaises(ValueError): r.launch_corrections(self.work, confirmed=True, models=['gemini'])
            mutate.assert_not_called()
