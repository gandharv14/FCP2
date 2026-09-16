"""A retained publication failure must not idle an unrelated authoring slot."""
import copy
from test_fleet_expired_launch import fixture
from test_fleet_quarantine import setup, retained
from test_selected_lease_capacity import M, plan
from scripts.fleet import launch_job_v3 as L


def test_stopped_author_admission_leaves_unrelated_publication_hold_untouched(tmp_path):
    c, status, row, p, job = fixture(tmp_path)
    status['operations'].append(dict(kind='publication', state='hold', workbook='0141'))
    before = copy.deepcopy(status)
    assert L.central_gate(status, p, job) == row
    assert status == before


def test_takeover_and_real_models_progress_but_publication_stays_serialized(tmp_path):
    c, now, claims, args = setup(tmp_path)
    c.quarantine_model(**args)
    c.begin('publication', 'retained-pub', request_sha256='f'*64, **claims['0141'])
    c.end('publication', 'retained-pub', evidence='retained terminal verification failure',
          outcome='uncertain', **claims['0141'])
    before = retained(c)
    M.admission(c.status(), plan(tmp_path))
    assert retained(c) == before
    assert c.begin('model', 'new-author-call', request_sha256='e'*64,
                   **claims['0140'])['execute']
    blocked = c.begin('publication', 'new-pub', request_sha256='d'*64, **claims['0140'])
    assert blocked['execute'] is False
    original = next(x for x in c.status()['operations'] if x['operation_id']=='retained-pub')
    assert original['state']=='hold'
