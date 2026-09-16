import copy
import json
import tempfile
import unittest
from dataclasses import dataclass, asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.fleet import source_card_profile as M


@dataclass(frozen=True)
class Lease:
    owner: str
    token: str
    revision: int
    sha256: str


class LeaseBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.lease = Lease('same-owner', 'private-token', 12, 'a'*64)
        self.record = dict(workbook_id='0199', bindings=dict(
            source_file={'path':'published','sha256':'b'*64},
            original_raw_source={'path':'original','sha256':'c'*64}), revision=12, leases={'task_agent':{'owner':'same-owner','token':'private-token'}})
        self.path = self.root/'tracker.json'; self.path.write_text(json.dumps(self.record))
        self.lease=Lease('same-owner','private-token',12,M.binding(self.path)['sha256'])
        blocked=self.root/'blocked.json';blocked.write_text(json.dumps(dict(status='DIALOGUE_REPAIR_REQUIRED',lease=asdict(self.lease))))
        self.p = dict(workbook='0199', published_source=self.record['bindings']['source_file'],
                      source=self.record['bindings']['original_raw_source'], blocked=M.binding(blocked), snapshot={'path':'original-snapshot'},
                      lease_boundary=dict(kind='retained-live', tracker=M.binding(self.path),
                                          lease=asdict(self.lease), takeover=None))
        self.worker = SimpleNamespace(context=SimpleNamespace(tracker_root=self.root))
        self.prior = dict(tracker={'path':'validated-prior'},lease=asdict(self.lease))
        self.R = SimpleNamespace(validate_historical_integrity=Mock(return_value=self.prior))
        self.T = SimpleNamespace(continuation_chain=Mock(), validate_completion=Mock())

    def run_boundary(self, record=None, lease=None, exact=True):
        return M.validate_lease(self.p,self.worker,record or self.record,lease or self.lease,
                               renewal=self.R,takeover=self.T,exact_boundary=exact)

    def test_retained_live_uses_original_history_and_both_exact_chains(self):
        self.assertEqual(self.run_boundary(),self.prior)
        self.R.validate_historical_integrity.assert_called_once_with(self.worker,self.record,self.p['snapshot'])
        self.assertEqual(self.T.continuation_chain.call_count,2)
        self.T.validate_completion.assert_not_called()
        self.assertIsNone(self.T.continuation_chain.call_args_list[0].kwargs['takeover'])
        self.assertEqual(self.T.continuation_chain.call_args_list[1].args[:2],
                         (self.p['lease_boundary']['tracker'],asdict(self.lease)))

    def test_actual_original_validator_rejection_cannot_be_hidden(self):
        self.R.validate_historical_integrity.side_effect=ValueError('retained Call changed')
        with self.assertRaisesRegex(ValueError,'retained Call'): self.run_boundary()
        self.T.continuation_chain.assert_not_called()

    def test_source_workbook_and_original_hash_tampering_rejected_before_history(self):
        for field in ('workbook','published','original'):
            with self.subTest(field=field):
                value=copy.deepcopy(self.record)
                if field=='workbook':value['workbook_id']='0200'
                else:value['bindings']['source_file' if field=='published' else 'original_raw_source']['sha256']='d'*64
                with self.assertRaisesRegex(ValueError,'another source'):self.run_boundary(value)
        self.R.validate_historical_integrity.assert_not_called()

    def test_exact_authorization_cannot_reuse_an_advanced_revision(self):
        value={**self.record,'revision':13}
        with self.assertRaisesRegex(ValueError,'consumed or changed'):self.run_boundary(value)

    def test_exact_authorization_cannot_swap_owner_or_token(self):
        for lease in (Lease('foreign','private-token',12,'a'*64),Lease('same-owner','foreign',12,'a'*64)):
            with self.assertRaisesRegex(ValueError,'consumed or changed'):self.run_boundary(lease=lease)

    def test_later_steps_still_require_original_chain(self):
        self.T.continuation_chain.side_effect=[None,ValueError('unselected owner replacement')]
        with self.assertRaisesRegex(ValueError,'unselected owner'):self.run_boundary({**self.record,'revision':13},exact=False)

    def test_takeover_must_be_actual_exact_completed_receipt(self):
        stop=self.root/'stopped-owner.json';stop.write_text(json.dumps({'before':self.p['lease_boundary']['tracker']}))
        receipt=self.root/'takeover.json';receipt.write_text(json.dumps({'stopped_owner_proof':M.binding(stop),'after':self.p['lease_boundary']['tracker']}))
        self.p['lease_boundary'].update(kind='completed-takeover',takeover=M.binding(receipt))
        self.T.validate_completion.return_value=({},self.record,self.lease)
        self.run_boundary()
        self.T.validate_completion.assert_called_once_with(M.binding(receipt))
        self.assertEqual(self.T.continuation_chain.call_args_list[0].kwargs['takeover'],M.binding(receipt))
        self.T.validate_completion.return_value=({},dict(self.record,revision=99),self.lease)
        with self.assertRaisesRegex(ValueError,'takeover changed'):self.run_boundary()

    def test_changed_tracker_bytes_fail_before_history(self):
        self.path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'evidence changed'):self.run_boundary()
        self.R.validate_historical_integrity.assert_not_called()

    def test_boundary_mode_is_not_truthy_coercion(self):
        with self.assertRaisesRegex(ValueError,'explicit'):self.run_boundary(exact=1)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        def write(name,value):
            path=self.root/name;path.write_text(json.dumps(value));return M.binding(path)
        self.write=write
        source=write('original.json',{'source':'raw'});published=write('published.json',{'source':'published'})
        claim=dict(workbook='0199',claims=[{'record_id':'method::0199::001','spoken':'old'}])
        old=write('claims.json',claim);fixed=copy.deepcopy(claim);fixed['claims'][0]['spoken']='corrected'
        changes=[dict(record_id='method::0199::001',source_formula='=AVERAGEIF(A1:A3,"<>0")')]
        descriptor=write('descriptor.json',{});blocked=write('blocked.json',{});check=write('check.json',{'passed':False});draft=write('draft.json',{})
        self.projection=dict(status='SOURCE_PROJECTION_VERIFIED',source=source,published_source=published,
            original_claims=old,original_check=check,qualification=False,original_failure_preserved=True,
            models=0,authorizations=0,owning_steps=0,corrected_claims=fixed,changes=changes,changed_cards=1,total_cards=1)
        self.pair=dict(status='ORIGINAL_AUTHOR_PAIR_VERIFIED',original_pair_actual=True,old_descriptor=descriptor,
            blocked=blocked,original_claims=old,old_check=check,old_draft=draft,prior_writer_generations=1,
            prior_paraphraser_generations=1,prior_review_attempts=0,models=0,owning_mutations=0,
            author_calls=[{},{}],author_requests=[{},{}])
        self.p=dict(schema_version=M.SCHEMA,workbook='0199',source=source,published_source=published,
            correction_kind='averageif-string-criterion/v1',budget=dict(author_generation=2,author_limit=2,
            prior_writer_generations=1,prior_paraphraser_generations=1,review_ordinal=1,review_limit=2,
            prior_review_attempts=0,semantic_budget_reset=False),projection=write('projection.json',self.projection),
            original_pair=write('pair.json',self.pair),corrected_claims=write('fixed.json',fixed),
            changes=write('changes.json',changes),old_descriptor=descriptor,blocked=blocked,
            snapshot=write('snapshot.json',{}),directory=str(self.root/'new-repair'),dependencies=[M.binding(M.__file__)],
            lease_boundary=dict(kind='retained-live',tracker=write('tracker.json',{}),lease={},takeover=None))

    def load(self):return M.load(self.write('profile.json',self.p))

    def test_explicit_profile_retains_original_pair_and_unused_review(self):
        self.assertEqual(self.load(),self.p)

    def test_semantic_or_generation_budget_is_not_reset(self):
        for key,value in [('review_ordinal',2),('prior_review_attempts',1),('author_limit',3),('semantic_budget_reset',True),('author_generation',True)]:
            with self.subTest(key=key):
                prior=copy.deepcopy(self.p['budget']);self.p['budget'][key]=value
                with self.assertRaisesRegex(ValueError,'budget'):self.load()
                self.p['budget']=prior

    def test_unvalidated_pair_cannot_be_mistaken_for_full_source_projection(self):
        self.pair['original_pair_actual']=False;self.p['original_pair']=self.write('pair.json',self.pair)
        with self.assertRaisesRegex(ValueError,'original author'):self.load()

    def test_changed_exact_claim_or_failure_binding_rejected(self):
        for kind in ('claims','blocked','count'):
            with self.subTest(kind=kind):
                prior=copy.deepcopy(self.p)
                if kind=='claims':self.p['corrected_claims']=self.write('foreign-claims.json',{'workbook':'0200','claims':[]})
                elif kind=='blocked':self.p['blocked']=self.write('foreign-blocked.json',{})
                else:
                    projection={**self.projection,'changed_cards':True};self.p['projection']=self.write('altered-projection.json',projection)
                with self.assertRaises(ValueError):self.load()
                self.p=prior

    def test_undeclared_code_or_mixed_lease_route_rejected(self):
        self.p['dependencies']=[]
        with self.assertRaisesRegex(ValueError,'dependency'):self.load()
        self.p['dependencies']=[M.binding(M.__file__)]
        self.p['lease_boundary']['kind']='completed-takeover'
        with self.assertRaisesRegex(ValueError,'lease boundary'):self.load()


if __name__=='__main__':unittest.main()


def test_blocked_checkpoint_cannot_substitute_another_revision_or_token():
    case=LeaseBoundaryTests();case.setUp()
    try:
        path=Path(case.p['blocked']['path']);original=json.loads(path.read_text())
        for key,value in [('sha256','f'*64),('revision',11),('token','foreign')]:
            changed=copy.deepcopy(original);changed['lease'][key]=value;path.write_text(json.dumps(changed));case.p['blocked']=M.binding(path)
            try:case.run_boundary()
            except ValueError as exc:assert 'pre-repair tracker'in str(exc)
            else:raise AssertionError('foreign blocked lease accepted')
        assert not case.T.continuation_chain.called
    finally:case.doCleanups()


def test_commits_after_old_renewal_use_exact_blocked_checkpoint():
    case=LeaseBoundaryTests();case.setUp()
    try:
        case.prior.update(tracker={'path':'old-R-precommit','sha256':'0'*64},lease={'revision':4})
        assert case.run_boundary()==case.prior
        first=case.T.continuation_chain.call_args_list[0]
        assert first.args[:2]==(case.p['lease_boundary']['tracker'],asdict(case.lease))
        assert first.args[0]!=case.prior['tracker']
    finally:case.doCleanups()
