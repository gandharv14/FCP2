import copy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.recursion_amendment_mask_validation import sha, verify_mask
from scripts.recursion_formatted_result_masks import mask_formatted_workbook


def write(path, value):
    path.write_text(json.dumps(value))


def rewrite(path, entry, transform):
    with ZipFile(path) as z:
        entries = [(copy.copy(info), z.read(info.filename)) for info in z.infolist()]
    with ZipFile(path, 'w') as z:
        for info, raw in entries:
            z.writestr(info, transform(raw) if info.filename == entry else raw)


@pytest.fixture
def sample(tmp_path):
    source, output = tmp_path/'before.xlsx', tmp_path/'after.xlsx'
    with ZipFile(source, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Model" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" s="2" t="s"><v>0</v></c><c r="B1" s="2"></c><c r="C1" t="s"><v>1</v></c></row></sheetData></worksheet>')
        z.writestr('xl/sharedStrings.xml', '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2"><si><t>NM/0.8x</t></si><si><t>Primitive label</t></si></sst>')
        z.writestr('xl/styles.xml', '<unchangedStyles/>')
    key, evidence, spec = tmp_path/'key.json', tmp_path/'evidence.json', tmp_path/'spec.json'
    write(key, {'targets': {'Model!B1': 3}})
    write(evidence, {'status': 'source reviewed'})
    data = {'schemaVersion':1,'taskId':'0001','activeProblemVersionId':'current-version',
        'workbookPath':str(source),'workbookSha256':sha(source),'answerKeyPath':str(key),'answerKeySha256':sha(key),
        'sourceEvidenceHashes':{str(evidence):sha(evidence)},
        'cells':[{'sheet':'Model','cell':'A1','expectedValue':'NM/0.8x','replacement':None,'classification':'calculated output'}],
        'orphanSharedStrings':[{'index':0,'expectedValue':'NM/0.8x'}]}
    write(spec,data)
    mask_formatted_workbook(source,output,{'Model!A1':'NM/0.8x'},sha(source))
    return spec, source, output, data


def test_exact_mask_and_sst_scrub_pass_with_deterministic_receipt(sample):
    spec,source,output,data=sample
    before=sha(source)
    first=verify_mask(spec,output,expected_spec_sha256=sha(spec),expected_output_sha256=sha(output))
    assert first==verify_mask(spec,output)
    assert first['maskedCells']==['Model!A1'] and first['blankedSharedStringIndices']==[0]
    assert all(first['validation'].values()) and sha(source)==before


@pytest.mark.parametrize('change', ['restored-previous-blank','changed-primitive','changed-style','foreign-zip-entry','retained-orphan','wrong-replacement','hidden-cell-text'])
def test_rejects_unapproved_or_restored_content(sample,change):
    spec,source,output,data=sample
    if change=='restored-previous-blank':
        rewrite(output,'xl/worksheets/sheet1.xml',lambda b:b.replace(b'<c r="B1" s="2"></c>',b'<c r="B1" s="2"><v>3</v></c>'))
    elif change=='changed-primitive':
        rewrite(output,'xl/sharedStrings.xml',lambda b:b.replace(b'Primitive label',b'Changed assumption'))
    elif change=='changed-style':
        rewrite(output,'xl/worksheets/sheet1.xml',lambda b:b.replace(b'r="A1" s="2"',b'r="A1" s="9"'))
    elif change=='foreign-zip-entry':
        rewrite(output,'xl/styles.xml',lambda b:b'<changed/>')
    elif change=='retained-orphan':
        rewrite(output,'xl/sharedStrings.xml',lambda b:b.replace(b'<si></si>',b'<si><t>NM/0.8x</t></si>'))
    elif change=='hidden-cell-text':
        rewrite(output,'xl/worksheets/sheet1.xml',lambda b:b.replace(b'<c r="A1" s="2"></c>',b'<c r="A1" s="2">NM/0.8x</c>'))
    else:
        rewrite(output,'xl/worksheets/sheet1.xml',lambda b:b.replace(b'<c r="A1" s="2"></c>',b'<c r="A1" s="2"><v>3</v></c>'))
    with pytest.raises(ValueError):verify_mask(spec,output)


def test_restored_root_workbook_not_accepted_as_new_output(sample):
    spec,source,output,data=sample
    # A stale generation restores a value in a previously blank cell while
    # still applying the new mask. Source binding remains fully correct.
    rewrite(output,'xl/worksheets/sheet1.xml',lambda b:b.replace(b'<c r="B1" s="2"></c>',b'<c r="B1" s="2"><v>42</v></c>'))
    with pytest.raises(ValueError,match='Unapproved worksheet bytes'):
        verify_mask(spec,output,expected_output_sha256=sha(output))


@pytest.mark.parametrize('change',['duplicate','wrong-before','unapproved-extra-cell','orphan-omitted','bad-source-hash','bad-evidence-hash'])
def test_spec_errors_fail_closed(sample,change):
    spec,source,output,data=sample
    if change=='duplicate':data['cells']*=2
    elif change=='wrong-before':data['cells'][0]['expectedValue']='NM/9.9x'
    elif change=='unapproved-extra-cell':data['cells'].append({'sheet':'Model','cell':'C1','expectedValue':'Primitive label','replacement':None,'classification':'not actually changed'})
    elif change=='orphan-omitted':data['orphanSharedStrings']=[]
    elif change=='bad-source-hash':data['workbookSha256']='0'*64
    else:data['sourceEvidenceHashes']={next(iter(data['sourceEvidenceHashes'])):'0'*64}
    write(spec,data)
    with pytest.raises(ValueError):verify_mask(spec,output)


def test_preserves_live_shared_uses_of_same_text(sample):
    spec,source,output,data=sample
    rewrite(source,'xl/worksheets/sheet1.xml',lambda b:b.replace(b'<v>1</v>',b'<v>0</v>'))
    data['workbookSha256']=sha(source);data['orphanSharedStrings']=[];write(spec,data)
    mask_formatted_workbook(source,output,{'Model!A1':'NM/0.8x'},sha(source))
    receipt=verify_mask(spec,output)
    assert receipt['blankedSharedStringIndices']==[]
    data['orphanSharedStrings']=[{'index':0,'expectedValue':'NM/0.8x'}];write(spec,data)
    with pytest.raises(ValueError,match='legitimate live references'):verify_mask(spec,output)


def test_exact_label_replacement_preserves_case_wording(sample):
    spec,source,output,data=sample
    data['cells'][0]['replacement']='Case | Returns';write(spec,data)
    mask_formatted_workbook(source,output,{'Model!A1':'NM/0.8x'},sha(source),
        text_replacements={'Model!A1':{'before':'NM/0.8x','after':'Case | Returns'}})
    assert verify_mask(spec,output)['validation']['exactApprovedMasksApplied']


def test_spec_and_output_hash_bindings_checked(sample):
    spec,source,output,_=sample
    with pytest.raises(ValueError,match='specification hash'):verify_mask(spec,output,expected_spec_sha256='0'*64)
    with pytest.raises(ValueError,match='Output workbook hash'):verify_mask(spec,output,expected_output_sha256='0'*64)
