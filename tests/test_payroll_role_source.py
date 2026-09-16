"""Salary classification requires full formula and visible operand headings."""
import importlib.util
from pathlib import Path
import openpyxl,pytest
P=Path(__file__).resolve().parents[1]/'.cursor/skills/task-disclosure/scripts/disclose.py'
s=importlib.util.spec_from_file_location('payroll_disclose',P);D=importlib.util.module_from_spec(s);s.loader.exec_module(D)

def fixture(tmp_path,formula='=+IF(OR(F$5>=$C8,E8>0),($D8/12)/1000,0)',**labels):
 p=tmp_path/'salary.xlsx';w=openpyxl.Workbook();a=w.active;a.title='Staff'
 for key,value in {'B6':'Employee','C6':'Hire Month','D6':'Base Salary','B8':'Head of Sales','C8':3,'D8':120000,'E5':1,'F5':2,'E8':'=IF(E$5>=$C8,($D8/12)/1000,0)','F8':formula,**labels}.items():a[key]=value
 w.save(p);g=D.Book(p);b=dict(sheet='Staff',row=8,col_lo=6,label=g.row_label('Staff!F8'),cell_keys=['Staff!F8'])
 return g,b

@pytest.mark.parametrize('label',['Head of Sales','Recruiting','Customer Service Rep','CEO'])
def test_payroll_beats_job_title_and_neighbor_words(tmp_path,label):
 g,b=fixture(tmp_path,B8=label);roles,evidence=D.method_roles_for_band(g,b)
 assert roles==['method_operating_expense'];assert 'Staff!D6: Base Salary'in evidence
 profile=D.formula_profile(g,b);fields=D.custom_sentence_fields(profile,g)
 assert profile['complete'] and all(x in fields['steps']for x in ('12','1000','Staff!D8','Staff!C8','Staff!E8'))

@pytest.mark.parametrize('formula',[
 '=IF(F$5>=$C8,($D8/12)/1000,0)',
 '=+IF(OR(F$5>=$C8,E8>0),($D8/12)/1000,0)',
])
def test_first_and_recurring_salary_shapes(tmp_path,formula):
 g,b=fixture(tmp_path,formula);assert D.source_payroll_role(g,b)

@pytest.mark.parametrize('change',[
 {'D6':'Annual Revenue'}, {'C6':'Collection Month'}, {'B6':'Customers'},
 {'D6':'Salary Margin'}, {'D7':'Different subheading'},
 {'formula':'=IF(F$5>=$C8,($D8/12)/1000,1)'},
 {'formula':'=IF(F$5>=$C8,($D8/13)/1000,0)'},
 {'formula':'=IF(F$5>=$C8,($D8/12)/100,0)'},
 {'formula':'=IF(F$5>=$C8,-($D8/12)/1000,0)'},
 {'formula':'=IF(F$5>=$C8,($D9/12)/1000,0)'},
 {'formula':'=IF(F$5>=$C8,($D8/12)/1000+$A8,0)'},
 {'formula':'=IF(OR(F$5>=$C8,G8>0),($D8/12)/1000,0)'},
 {'formula':'=IF(AND(F$5>=$C8,E8>0),($D8/12)/1000,0)'},
 {'formula':'=IF(F5>=$C8,($D8/12)/1000,0)'},
])
def test_other_financial_meanings_do_not_receive_payroll_override(tmp_path,change):
 g,b=fixture(tmp_path,**change);assert D.source_payroll_role(g,b)is None
