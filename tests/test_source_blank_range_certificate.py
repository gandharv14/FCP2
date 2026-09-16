"""Source XML blank witnesses are scoped to exact static OFFSET range targets."""
from pathlib import Path
import hashlib,importlib.util,zipfile
import pytest
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z/0193-blank-repair/overlay/xl_seg/restriction_cone.py'
S=importlib.util.spec_from_file_location('xl_seg.source_blank_candidate',P);R=importlib.util.module_from_spec(S);S.loader.exec_module(R)
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RN='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
def book(tmp_path,cells='',*,relationships=None,duplicate_part=False):
 p=tmp_path/'source.xlsx'
 with zipfile.ZipFile(p,'w') as z:
  z.writestr('xl/workbook.xml',f'<workbook xmlns="{NS}" xmlns:r="{RN}"><sheets><sheet name="Sheet" sheetId="1" r:id="rId1"/></sheets></workbook>')
  z.writestr('xl/_rels/workbook.xml.rels',relationships or f'<Relationships><Relationship Id="rId1" Type="{RN}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
  z.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="{NS}"><sheetData><row r="1">{cells}</row></sheetData></worksheet>')
  if duplicate_part:z.writestr('xl/worksheets/sheet1.xml','<worksheet/>')
 return p,hashlib.sha256(p.read_bytes()).hexdigest()
@pytest.mark.parametrize('cell,state',[('', 'absent'),('<c r="B1" s="7"/>','style_only')])
def test_exact_source_blank_positive(tmp_path,cell,state):
 p,h=book(tmp_path,cell);proof=R._SourceBlankProof(p,h).prove(['Sheet!B1'])
 assert proof[0]['source_sha256']==h and proof[0]['xml_state']==state
 assert proof[0]['binding_sha256']==R._unsigned_hash(proof[0],'binding_sha256')
@pytest.mark.parametrize('cell',[
 '<c r="B1"><v>0</v></c>','<c r="B1" t="s"><v>0</v></c>',
 '<c r="B1" t="inlineStr"><is><t>hidden</t></is></c>',
 '<c r="B1"><f>1+2</f></c>','<c r="B1"><f/></c>',
 '<c r="B1"/><c r="B1"/>',
 '<c r="A1"><f t="array" ref="A1:C1">A2:C2</f></c>',
 '<c r="A1"><f t="shared" ref="A1:C1" si="0">A2</f></c>',
 '<c r="A1"><f t="array">A2:C2</f></c>',
 '<c r="A1" cm="1"><f>A2:C2</f></c>',
 '<c r="A1"><f>_xlfn.SEQUENCE(1,3)</f></c>',
 '<c r="A1"><f>FILTER(A2:C2,A2:C2)</f></c>',
 '<c r="A1"><f t="dataTable" ref="A1:C1"/></c>',
 '<c r="B1" t="n"/>',
 '<c r="A1"><f t="shared" si="7"/></c>',
 '<c xmlns="unknown" r="B1"><v>9</v></c>',
 '<c r="A1"><f>TEXTSPLIT(A2,",")</f></c>',
 '<c r="A1"><f>MAKEARRAY(1,3,LAMBDA(r,c,c))</f></c>',
 '<c r="A1"><f>UNKNOWN_ARRAY(A2)</f></c>',
 '<c r="A1"><f>A2:C2</f></c>',
])
def test_nonblank_or_ambiguous_coverage_rejects(tmp_path,cell):
 p,h=book(tmp_path,cell)
 with pytest.raises(R.RestrictionConeError):R._SourceBlankProof(p,h).prove(['Sheet!B1'])
def test_formula_span_elsewhere_does_not_hide_actual_blank(tmp_path):
 p,h=book(tmp_path,'<c r="D1"><f t="array" ref="D1:F1">D2:F2</f></c>')
 assert R._SourceBlankProof(p,h).prove(['Sheet!B1'])[0]['xml_state']=='absent'
@pytest.mark.parametrize('kind',['hash','relationship','duplicate-zip','sheet'])
def test_identity_ambiguity_rejects(tmp_path,kind):
 rel='<Relationships><Relationship Id="rId1"/><Relationship Id="rId1"/></Relationships>' if kind=='relationship' else None
 p,h=book(tmp_path,relationships=rel,duplicate_part=kind=='duplicate-zip')
 with pytest.raises(R.RestrictionConeError):R._SourceBlankProof(p,'0'*64 if kind=='hash' else h).prove(['Other!B1' if kind=='sheet' else 'Sheet!B1'])
def test_absent_range_ast_without_source_stays_rejected(tmp_path):
 from test_signed_offset_endpoint import build
 g,result,inputs,proof=build(tmp_path,'=SUM(A1:OFFSET(C2,-1,0))',cells={'A1':2,'C1':5,'C2':7})
 g.nodes.pop('Sheet!B1',None);offset=next(n for n in g.nodes.values() if n.op=='OFFSET')
 cone=R._proof_cone(['Sheet!G1'],proof['runtime_radj'])
 with pytest.raises(R.RestrictionConeError):R._enclosing_static_ranges(g,offset,proof['resolved_operation_targets'],cone)
 p=tmp_path/'book.xlsx';blank=R._SourceBlankProof(p,hashlib.sha256(p.read_bytes()).hexdigest())
 out=R._enclosing_static_ranges(g,offset,proof['resolved_operation_targets'],cone,blank)
 assert out[0]['source_blank_targets'][0]['coordinate']=='Sheet!B1'
 proof['resolved_operation_targets'][next(n.id for n in g.nodes.values() if n.op==':')].remove('Sheet!B1')
 with pytest.raises(R.RestrictionConeError):R._enclosing_static_ranges(g,offset,proof['resolved_operation_targets'],cone,blank)
