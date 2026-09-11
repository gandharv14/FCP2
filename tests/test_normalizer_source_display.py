import copy,json
from pathlib import Path
import pytest
import gen_normalizer as gn
@pytest.fixture
def example(tmp_path):
 run=tmp_path/'runs/0001-variable-sources';run.mkdir(parents=True)
 review=run/'normalization-review.json';review.write_text('{}')
 evidence={k:'a'*64 for k in ('draft_sha256','source_sha256','baseline_sha256')}
 row={'reference':'Sheet!A1','clean':True,'evidence':evidence,'row':{'draft_id':'project','source_notes':'Original likely private financing documents','source_urls':[]}}
 addendum={'schema_version':'normalization-source-display/v1','reviewer':'independent-root-review','normalization_review_sha256':gn._sha256(review),**evidence,'cells':{'Sheet!A1':{'original_sources':gn.audit_sources(row['row']),'display_name':'Supplied-workbook project debt assumptions','reason':'Original worksheet heading identifies project debt; no external document verified.','evidence_refs':['Sheet!A1']}}}
 path=run/'source-display-review.json'
 return run,row,addendum,path

def save(path,d):path.write_text(json.dumps(d))
def test_absent_is_identical(example):
 run,row,d,p=example;old=copy.deepcopy(row);assert gn.apply_source_display_review([row],run)=={};assert row==old

def test_only_source_display_changes(example):
 run,row,d,p=example;save(p,d);before=copy.deepcopy(row);bound=gn.apply_source_display_review([row],run)
 assert bound=={str(p):gn._sha256(p)}
 sources=row.pop('reviewed_sources');assert row==before
 for old,new in zip(d['cells']['Sheet!A1']['original_sources'],sources):
  assert {k:v for k,v in old.items()if k!='name'}=={k:v for k,v in new.items()if k!='name'}
  assert new['name']==d['cells']['Sheet!A1']['display_name']

@pytest.mark.parametrize('bad',['draft','source','baseline','review','unknown_ref','rejected_ref','source_id','url','notes','empty_name','public_claim','empty_reason','missing_ref','extra_field','empty_cells'])
def test_reject_changed_or_unreviewed_evidence(example,bad):
 run,row,d,p=example;x=d['cells']['Sheet!A1']
 if bad in ('draft','source','baseline'):d[bad+'_sha256']='b'*64
 elif bad=='review':(run/'normalization-review.json').write_text('{"changed":true}')
 elif bad=='unknown_ref':d['cells']['Sheet!Z9']=d['cells'].pop('Sheet!A1')
 elif bad=='rejected_ref':row['clean']=False
 elif bad=='source_id':x['original_sources'][0]['id']='changed'
 elif bad=='url':x['original_sources'][0]['url']='https://example.com'
 elif bad=='notes':row['row']['source_notes']='Changed audit text'
 elif bad=='empty_name':x['display_name']='Supplied-workbook '
 elif bad=='public_claim':x['display_name']='Verified lender confirmation'
 elif bad=='empty_reason':x['reason']=''
 elif bad=='missing_ref':x['evidence_refs']=['Sheet!B1']
 elif bad=='extra_field':x['value']=123
 elif bad=='empty_cells':d['cells']={}
 save(p,d)
 with pytest.raises(SystemExit):gn.apply_source_display_review([row],run)

def test_reject_public_source_even_when_original_matches(example):
 run,row,d,p=example;row['row']['source_urls']=['https://example.com']
 d['cells']['Sheet!A1']['original_sources']=gn.audit_sources(row['row']);save(p,d)
 with pytest.raises(SystemExit):gn.apply_source_display_review([row],run)

def test_emitted_normalizer_revalidates_addendum_without_financial_changes(tmp_path):
 import openpyxl
 source=tmp_path/'0001.xlsx';inputs=tmp_path/'inputs';inputs.mkdir()
 book=openpyxl.Workbook();book.active.title='Sheet';book.active['A1']=47
 book.save(source);book.save(inputs/'0001-inputs.xlsx');book.close()
 run=tmp_path/'runs/0001-variable-sources';run.mkdir(parents=True)
 draft={'rows':[{'draft_id':'project','variable_text':'Project debt','value_text':'Sheet!A1','source_notes':'Likely Dailly documents or debt ledger','source_urls':[],'normalization_status':'needs_review'}]}
 save(run/'draft.json',draft)
 draft,rows=gn.analyse('0001',source,gn._sha256(source),runs_root=run.parent,inputs_root=inputs)
 save(run/'normalization-review.json',{'schema_version':'normalization-review/v1',**rows[0]['evidence'],'reviewer':'independent fixture review','cells':{'Sheet!A1':{'eligible':True,'reason':'Typed debt assumption','evidence_refs':['Sheet!A1'],'entity':'SPV','period':'2014-12-31','scenario':'FP2','basis':'Unspecified workbook units'}}})
 addendum={'schema_version':'normalization-source-display/v1','reviewer':'independent root fixture',**rows[0]['evidence'],'normalization_review_sha256':gn._sha256(run/'normalization-review.json'),'cells':{'Sheet!A1':{'original_sources':gn.audit_sources(rows[0]['row']),'display_name':'Supplied-workbook project debt assumptions','reason':'Explicit project debt worksheet header','evidence_refs':['Sheet!A1']}}}
 save(run/'source-display-review.json',addendum)
 gn.emit('0001',draft,rows,runs_root=run.parent)
 script=run/'normalize_0001.py';ns={'__name__':'fixture','__file__':str(script)};exec(compile(script.read_text(),str(script),'exec'),ns);ns['main']()
 var=json.loads((run/'normalized.json').read_text())['variables'][0]
 assert var['value']==47 and var['basis']=='Unspecified workbook units' and var['workbook']['cells']==['Sheet!A1']
 assert var['sources'][0]['name']=='Supplied-workbook project debt assumptions'
 assert var['source_notes']==draft['rows'][0]['source_notes']
 assert var['sources'][0]['url']==addendum['cells']['Sheet!A1']['original_sources'][0]['url']
 before=(run/'normalized.json').read_bytes();addendum['cells']['Sheet!A1']['reason']='altered';save(run/'source-display-review.json',addendum)
 with pytest.raises(SystemExit,match='normalizer evidence changed'):ns['main']()
 assert (run/'normalized.json').read_bytes()==before


def test_reject_vacuous_empty_sources(example, monkeypatch):
 run,row,d,p=example;d['cells']['Sheet!A1']['original_sources']=[];save(p,d)
 monkeypatch.setattr(gn, 'audit_sources', lambda _: [])
 with pytest.raises(SystemExit,match='invalid private-source display'):gn.apply_source_display_review([row],run)


def test_reject_ambiguous_duplicate_candidate_reference(example):
 run,row,d,p=example;save(p,d)
 with pytest.raises(SystemExit,match='ambiguous source display cell'):
  gn.apply_source_display_review([row,copy.deepcopy(row)],run)
