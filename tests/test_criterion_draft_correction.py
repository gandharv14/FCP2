from pathlib import Path
import copy,json,unittest
from scripts.fleet.criterion_draft_correction import corrected_draft

ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-next-001/0199-mechanical-hold'

class ActualCriterionDraft(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not H.exists():raise unittest.SkipTest('retained private source evidence unavailable')
        cls.draft=(H/'draft-evidence/1.json').read_text()
        cls.changes=json.loads((H/'proposal-001/native.json').read_bytes())['changes']

    def test_actual_four_turns_change_only_false_source_role(self):
        result,proof=corrected_draft(self.draft,self.changes)
        self.assertEqual(len(proof),4)
        self.assertEqual(result,self.draft.replace('the row labelled by the text literal "<>0"','the text literal "<>0"'))
        self.assertEqual([x['record_id']for x in proof],[x['record_id']for x in self.changes])
        self.assertEqual(result.count('<!-- claim:'),18)

    def test_duplicate_missing_or_ambiguous_original_section_rejected(self):
        for draft in (self.draft.replace('the row labelled by the text literal','the text literal',1),
                      self.draft+self.draft,
                      self.draft.replace('<!-- claim:'+self.changes[0]['record_id']+' -->','<!-- claim:foreign -->',1)):
            with self.assertRaises(ValueError):corrected_draft(draft,self.changes)

    def test_foreign_criterion_or_noncriterion_requirement_rejected(self):
        for field,value in [('retained_criteria',['other']),('removed_false_row_atoms',['locked input'])]:
            rows=copy.deepcopy(self.changes);rows[0][field]=value
            with self.assertRaises(ValueError):corrected_draft(self.draft,rows)

    def test_duplicate_changed_claim_rejected(self):
        with self.assertRaises(ValueError):corrected_draft(self.draft,self.changes+[self.changes[0]])

if __name__=='__main__':unittest.main()
