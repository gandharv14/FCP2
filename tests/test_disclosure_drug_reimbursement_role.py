import importlib.util
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'.cursor/skills/task-disclosure/scripts/disclose.py'
s=importlib.util.spec_from_file_location('drug_role_fix',P);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
class Book:
 def __init__(self,heading):self.heading=heading
 def row_label(self,k):return self.heading if k.endswith('6') else 'Professional service fees'
@pytest.mark.parametrize('heading',['Revenue','Revenues',' REVENUE '])
def test_explicit_revenue_heading(heading):
 assert d.method_roles_for_band(Book(heading),dict(label='Drug Reimbursement (VRMG Drug)',sheet='S',row=8,col_lo=29))[0]==['method_revenue']
@pytest.mark.parametrize('label,heading',[('Drug Reimbursement','Debt'),('Loan Drug Reimbursement','Revenue'),('Debt reimbursement','Revenue'),('Expense reimbursement','Revenue'),('Drug Reimbursement','Revenue growth')])
def test_no_unproven_reclassification(label,heading):
 assert d.method_roles_for_band(Book(heading),dict(label=label,sheet='S',row=8,col_lo=29))[0]==['method_debt_movement']
def test_neighbor_triggers_and_prior_case_ids_unchanged():
 assert d.method_roles('Drug Reimbursement (VRMG Drug)')==['method_debt_movement']
