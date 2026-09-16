import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.fleet import physical_model_capacity as P
import xl_transport


class PhysicalCapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        auth = self.root/'authorization.txt'
        auth.write_text('Explicit additional per-VM concurrency; preserve all attempts.')
        self.policy = dict(schema_version=P.SCHEMA, authorization=P.binding(auth),
            hosts=[socket.gethostname()], slots_per_host=2, slot_root=str(self.root/'physical'),
            publication_limit=1, per_workbook_limit=1,
            unknown_response_policy='hold-never-replay', max_wait_seconds=1, revision=1)
        self.path = self.root/'policy.json'
        self.write_policy()
        self.environment = patch.dict(os.environ, {
            'HARBOR_PHYSICAL_MODEL_POLICY': str(self.path),
            'HARBOR_PHYSICAL_MODEL_POLICY_SHA256': P.binding(self.path)['sha256'],
            'HARBOR_FLEET_URL': 'http://127.0.0.1:8765',
            'HARBOR_MODEL_SLOT_ROOT': str(self.root/'legacy'), 'HARBOR_MODEL_SLOTS': '1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_policy(self):
        self.path.write_text(json.dumps(self.policy))
        if hasattr(self, 'environment'):
            os.environ['HARBOR_PHYSICAL_MODEL_POLICY_SHA256'] = P.binding(self.path)['sha256']

    def test_two_real_lock_holders_overlap_old_capacity_stays_one(self):
        entered = [threading.Event(), threading.Event()]
        release = threading.Event()
        errors = []
        def worker(i):
            try:
                with xl_transport._local_model_slot():
                    entered[i].set()
                    self.assertTrue(release.wait(3))
            except BaseException as error:
                errors.append(error)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        try:
            for thread in threads: thread.start()
            self.assertTrue(all(event.wait(2) for event in entered))
            with self.assertRaises(TimeoutError):
                with xl_transport._local_model_slot():
                    self.fail('third operation entered')
        finally:
            release.set()
            for thread in threads: thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual((self.root/'legacy/capacity').read_bytes(), b'1\n')
        self.assertFalse((self.root/'legacy/slot-0.lock').exists())
        with xl_transport._local_model_slot(): pass

    def test_mixed_policy_cannot_reconfigure_same_physical_root(self):
        with xl_transport._local_model_slot(): pass
        self.policy['slots_per_host'] = 3
        self.policy['revision'] = 2
        self.write_policy()
        with self.assertRaisesRegex(ValueError, 'mixed'):
            with xl_transport._local_model_slot(): pass

    def test_changed_authorization_fails_before_dispatch(self):
        Path(self.policy['authorization']['path']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'authorization'):
            with xl_transport._local_model_slot(): pass

    def test_changed_policy_hash_fails(self):
        self.path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'policy changed'): P.selected()

    def test_host_scope_is_enforced(self):
        self.policy['hosts'] = ['different-host']
        self.write_policy()
        with self.assertRaisesRegex(ValueError, 'host'): P.selected()

    def test_unbounded_limits_and_unknown_replay_are_rejected(self):
        for key, value in [('slots_per_host', 100), ('max_wait_seconds', 0),
                           ('unknown_response_policy', 'retry'), ('publication_limit', 2)]:
            prior = self.policy[key]
            self.policy[key] = value
            self.write_policy()
            with self.assertRaises(ValueError): P.selected()
            self.policy[key] = prior

    def test_old_capacity_mismatch_is_still_rejected(self):
        (self.root/'legacy').mkdir()
        (self.root/'legacy/capacity').write_bytes(b'2\n')
        with self.assertRaisesRegex(ValueError, 'campaign capacity'):
            with xl_transport._local_model_slot(): pass

    def test_old_mode_keeps_original_slot_and_no_new_policy(self):
        os.environ.pop('HARBOR_PHYSICAL_MODEL_POLICY')
        os.environ.pop('HARBOR_PHYSICAL_MODEL_POLICY_SHA256')
        with xl_transport._local_model_slot():
            self.assertTrue((self.root/'legacy/slot-0.lock').is_file())
        self.assertFalse((self.root/'physical').exists())

    def test_policy_cannot_activate_without_fleet_exclusion(self):
        os.environ.pop('HARBOR_FLEET_URL')
        with self.assertRaisesRegex(ValueError, 'complete fleet'): P.selected()

    def test_killed_process_releases_physical_permit(self):
        self.policy['slots_per_host'] = 1
        self.write_policy()
        command = "import xl_transport,time; c=xl_transport._local_model_slot(); c.__enter__(); print('held',flush=True); time.sleep(30)"
        child = subprocess.Popen([sys.executable, '-B', '-c', command],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, cwd=Path(xl_transport.__file__).parent)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'held')
            child.kill()
            child.wait(timeout=5)
            with xl_transport._local_model_slot(): pass
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=5)


if __name__ == '__main__': unittest.main()
