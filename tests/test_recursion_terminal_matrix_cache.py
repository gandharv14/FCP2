import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_terminal_matrix_cache as cache


class TerminalCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        (self.work / 'results').mkdir()
        self.prefix = 'evaluation-1'
        self.detail = {'id': 'eval', 'status': 'completed', 'updatedAt': 'fixed',
                       'solvers': [{'id': 'solver', 'runConfigVersionId': 'config', 'jobStatus': 'completed'}]}
        self.request = {'solvers': [{'runConfigVersionId': 'config'}],
                        'problems': [{'problemVersionId': 'version'}],
                        'metadata': {'attemptsPerProblem': 3, 'successThreshold': 1}}
        self.matrix = {'evaluationId': 'eval', 'cells': [{
            'solverId': 'solver', 'problemVersionId': 'version',
            'attempts': [{'problemRunId': f'run-{i}', 'attemptNumber': i,
                          'status': 'failed' if i == 3 else 'completed',
                          'score': None if i == 3 else i - 1} for i in (1, 2, 3)]}]}
        self.write()

    def write(self):
        for name, data in [('results/evaluation-1-detail.json', self.detail),
                           ('results/evaluation-1-matrix.json', self.matrix),
                           ('evaluation-1.request.json', self.request),
                           ('evaluation-1.response.json', {'id': 'eval'})]:
            (self.work / name).write_text(json.dumps(data))

    def freeze(self):
        return cache.freeze(self.work, self.prefix, '2026-09-15T16:45:49+00:00')

    def test_reuse_preserves_null_score_and_original_read_time(self):
        self.freeze()
        matrix, source = cache.load(self.work, self.prefix, copy.deepcopy(self.detail))
        self.assertEqual(matrix, self.matrix)
        self.assertIsNone(matrix['cells'][0]['attempts'][2]['score'])
        self.assertEqual(source['matrixRetrievedAt'], '2026-09-15T16:45:49+00:00')

    def test_changed_fresh_detail_requires_remote_read(self):
        self.freeze()
        changed = {**self.detail, 'updatedAt': 'changed'}
        self.assertIsNone(cache.load(self.work, self.prefix, changed))

    def test_nonterminal_attempt_cannot_be_frozen(self):
        self.matrix['cells'][0]['attempts'][2]['status'] = 'running'
        self.write()
        with self.assertRaises(AssertionError):
            self.freeze()

    def test_duplicate_run_id_cannot_be_frozen(self):
        self.matrix['cells'][0]['attempts'][2]['problemRunId'] = 'run-1'
        self.write()
        with self.assertRaises(AssertionError):
            self.freeze()

    def test_mutated_snapshot_fails_closed(self):
        self.freeze()
        p = self.work / 'results/terminal-matrix-cache/evaluation-1/matrix.json'
        p.chmod(0o644)
        p.write_text('{}')
        with self.assertRaisesRegex(AssertionError, 'evidence changed'):
            cache.load(self.work, self.prefix, self.detail)

    def test_changed_request_fails_closed(self):
        self.freeze()
        self.request['metadata']['attemptsPerProblem'] = 4
        self.write()
        with self.assertRaises(AssertionError):
            cache.load(self.work, self.prefix, self.detail)


if __name__ == '__main__':
    unittest.main()
