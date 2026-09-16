from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from scripts.fleet.freeze_control import freeze, PROFILES

ROOT=Path(__file__).resolve().parents[1]


class FrozenControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'source'
        folder=self.source/'scripts/fleet';folder.mkdir(parents=True)
        for name in {n for entry,deps in PROFILES.values() for n in [entry,*deps]}:
            shutil.copyfile(ROOT/'scripts/fleet'/name,folder/name)

    def run_wrapper(self, row):
        return subprocess.run([sys.executable,'-I','-B',row['wrapper']['path'],'--help'],capture_output=True,text=True)

    def test_real_oneoff_is_independent_of_later_live_edits(self):
        row=freeze(self.source,self.root/'frozen','oneoff')
        (self.source/'scripts/fleet/prepare_queue.py').write_text("raise RuntimeError('live tree changed')")
        result=self.run_wrapper(row)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('interrupt',result.stdout)

    def test_copied_dependency_tamper_rejected_before_cli(self):
        row=freeze(self.source,self.root/'frozen','oneoff')
        (self.root/'frozen/implementation/scripts/fleet/pool.py').write_text('raise RuntimeError("executed")')
        result=self.run_wrapper(row)
        self.assertNotEqual(result.returncode,0);self.assertIn('frozen control changed',result.stderr)
        self.assertNotIn('RuntimeError: executed',result.stderr)

    def test_actual_fleet_v2_copies_its_complete_import_closure(self):
        row=freeze(self.source,self.root/'frozen','fleet-v2')
        shutil.rmtree(self.source)
        result=self.run_wrapper(row)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('start',result.stdout)

    def test_held_job_launcher_has_no_live_import_dependency(self):
        row=freeze(self.source,self.root/'frozen','held-job-launch')
        shutil.rmtree(self.source)
        result=self.run_wrapper(row)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--execute',result.stdout)

    def test_added_import_file_rejected(self):
        row=freeze(self.source,self.root/'frozen','oneoff')
        (self.root/'frozen/implementation/scripts/fleet/extra.py').write_text('')
        self.assertIn('inventory changed',self.run_wrapper(row).stderr)

    def test_launch_layout_pin_and_closed_source_copy(self):
        row=freeze(self.source,self.root/'frozen','0270-refresh-launch',layout_sha256='a'*64)
        result=self.run_wrapper(row);self.assertEqual(result.returncode,0,result.stderr)
        manifest=json.loads(Path(row['manifest']['path']).read_bytes())
        self.assertIn("LAYOUT_SHA256='"+'a'*64+"'",Path(row['entry_point']['path']).read_text())
        self.assertTrue(all(str(self.source) not in b['path'] for b in manifest['files']))

    def test_bad_layout_or_symlink_creates_no_frozen_output(self):
        with self.assertRaises(ValueError):freeze(self.source,self.root/'frozen','0270-refresh-launch',layout_sha256='bad')
        p=self.source/'scripts/fleet/pool.py';p.unlink();p.symlink_to(ROOT/'scripts/fleet/pool.py')
        with self.assertRaises(ValueError):freeze(self.source,self.root/'frozen','oneoff')
        self.assertFalse((self.root/'frozen').exists())
