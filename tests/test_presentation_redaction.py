import copy,json
from pathlib import Path
import pytest
from test_presentation_tables import source_fixture
import xl_presentation_redaction as R
from xl_presentation_tables import _package,table_inventory,check_exclusion

def policy(source,tmp_path):
 auth=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
 p=tmp_path/'policy.json';p.write_text(json.dumps(dict(schema_version=R.POLICY,source=R.binding(source),authorization=R.binding(auth),tables=table_inventory(_package(source)[1]))));return R.binding(p)

def test_redaction_preserves_other_members_and_source_bytes(tmp_path):
 source,_=source_fixture(tmp_path,answer_in_table=True);before=source.read_bytes();_,members=_package(source);tables=table_inventory(members);after=R.transformed_members(members,tables)
 assert {k for k in members if members[k]!=after[k]}=={'xl/worksheets/sheet1.xml'}
 assert b'<f>A1*3</f><v>6</v>'in after['xl/worksheets/sheet1.xml']
 assert b'<c r="D3"/>'in after['xl/worksheets/sheet1.xml']
 assert b'<c r="E3"/>'in after['xl/worksheets/sheet1.xml']
 assert source.read_bytes()==before

def test_table_in_graded_cone_cannot_be_redacted():
 p={'verification':{'status':'pass'}}
 with pytest.raises(ValueError):check_exclusion([{'cells':['S!D3']}],p)

def test_ordinary_formula_in_result_rectangle_is_rejected(tmp_path):
 source,_=source_fixture(tmp_path);_,members=_package(source);tables=table_inventory(members)
 members['xl/worksheets/sheet1.xml']=members['xl/worksheets/sheet1.xml'].replace(b'<v>9</v>',b'<f>A1</f><v>2</v>')
 with pytest.raises(ValueError,match='ordinary formula'):R.transformed_members(members,tables)

def test_empty_result_is_retained_exactly(tmp_path):
 source,_=source_fixture(tmp_path);_,members=_package(source);tables=table_inventory(members)
 members['xl/worksheets/sheet1.xml']=members['xl/worksheets/sheet1.xml'].replace(b'<c r="E3" t="n"><v>9</v></c>',b'<c r="E3" s="4"/>')
 assert b'<c r="E3" s="4"/>'in R.transformed_members(members,tables)['xl/worksheets/sheet1.xml']

def test_policy_requires_exact_authorization_and_inventory(tmp_path):
 source,_=source_fixture(tmp_path);b=policy(source,tmp_path);R.selected(b,source)
 p=json.loads(Path(b['path']).read_bytes());p['tables'][0]['cells'].pop();Path(b['path']).write_text(json.dumps(p))
 with pytest.raises(ValueError,match='inventory'):R.selected(R.binding(b['path']),source)
 p=json.loads(Path(b['path']).read_bytes());p['tables']=table_inventory(_package(source)[1]);fake=tmp_path/'fake.md';fake.write_text('approve');p['authorization']=R.binding(fake);Path(b['path']).write_text(json.dumps(p))
 with pytest.raises(ValueError,match='authorization'):R.selected(R.binding(b['path']),source)

def test_full_original_numerical_proof_and_candidate_custody(tmp_path):
 source,curation=source_fixture(tmp_path,answer_in_table=True);b=policy(source,tmp_path)
 candidate,receipt=R.create(source,'0001',tmp_path/'candidate',b,curation=curation)
 assert R.verify(source,candidate,receipt)['source_qualified']is False
 assert receipt['ordered_outputs']==['S!B1']
 with candidate.open('ab')as f:f.write(b'changed')
 with pytest.raises(ValueError,match='custody'):R.verify(source,candidate,receipt)
