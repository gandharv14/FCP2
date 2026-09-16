import copy
import zipfile
import pytest
import xl_volatile_formula_remediation as M

S='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R=M.DOC_REL_NS


def members():
    return {k:v.encode() for k,v in {
        '[Content_Types].xml': '<Types><Override PartName="/xl/externalLinks/externalLink1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml"/></Types>',
        'xl/workbook.xml': f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="Model" sheetId="1" r:id="rId1"/></sheets><externalReferences><externalReference r:id="rId2"/></externalReferences><definedNames><definedName name="Unused">\'[1]Other\'!A1</definedName></definedNames></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{M.REL_NS}"><Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{R}/externalLink" Target="externalLinks/externalLink1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1"><f>1+2</f><v>3</v></c></row></sheetData></worksheet>',
        'xl/externalLinks/externalLink1.xml': f'<externalLink xmlns="{S}" xmlns:r="{R}"><externalBook r:id="rId1"><sheetNames><sheetName val="Other"/></sheetNames></externalBook></externalLink>',
        'xl/externalLinks/_rels/externalLink1.xml.rels': f'<Relationships><Relationship Id="rId1" Type="{R}/externalLinkPath" Target="file:///other.xlsx" TargetMode="External"/></Relationships>',
    }.items()}


def write(path, values):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for k,v in values.items():z.writestr(k,v)


def test_dead_graph_removed_with_exact_worksheet_cache_preservation(tmp_path):
    source=tmp_path/'source.xlsx'; candidate=tmp_path/'candidate.xlsx'; values=members();write(source,values)
    original=M.sha256_file(source);plan=M.build_plan(source)
    assert plan['action_counts']=={'dead-defined-name/v1':1,'dead-external-book-package/v1':1}
    manifest=M.apply_plan(source,plan,candidate)
    assert M.verify_manifest(source,candidate,plan,manifest)['status']=='verified'
    assert M.sha256_file(source)==original
    with zipfile.ZipFile(candidate) as z:
        assert z.read('xl/worksheets/sheet1.xml')==values['xl/worksheets/sheet1.xml']
        assert not any(p.startswith('xl/externalLinks/') for p in z.namelist())
    assert len(manifest['removed_members'])==2
    corrupt=copy.deepcopy(manifest);corrupt['removed_members']={};corrupt['manifest_sha256']=M._unsigned_hash(corrupt,'manifest_sha256')
    with pytest.raises(M.RemediationError,match='removed external'):M.verify_manifest(source,candidate,plan,corrupt)


@pytest.mark.parametrize('defect',['live-cell','live-name','conditional-name','chart','dynamic','foreign-relation','unknown-part','orphan','dde'])
def test_potential_external_use_or_unknown_graph_is_never_pruned(tmp_path,defect):
    v=members();sheet='xl/worksheets/sheet1.xml';book='xl/workbook.xml';rels='xl/_rels/workbook.xml.rels'
    if defect=='live-cell':v[sheet]=v[sheet].replace(b'1+2',b"'[1]Other'!A1")
    elif defect=='live-name':v[sheet]=v[sheet].replace(b'1+2',b'Unused')
    elif defect=='conditional-name':v[sheet]=v[sheet].replace(b'</worksheet>',b'<conditionalFormatting><cfRule><formula>Unused</formula></cfRule></conditionalFormatting></worksheet>')
    elif defect=='chart':v['xl/charts/chart1.xml']=b"<chart><f>'[1]Other'!A1</f></chart>"
    elif defect=='dynamic':v[sheet]=v[sheet].replace(b'1+2',b'INDIRECT(A2)')
    elif defect=='foreign-relation':v[rels]=v[rels].replace(b'</Relationships>',f'<Relationship Id="foreign" Type="{R}/externalLink" Target="externalLinks/externalLink1.xml"/></Relationships>'.encode())
    elif defect=='unknown-part':v['xl/active.bin']=b'opaque'
    elif defect=='orphan':v['xl/externalLinks/other.xml']=b'<externalLink/>'
    elif defect=='dde':v['xl/externalLinks/externalLink1.xml']=b'<externalLink><ddeLink/></externalLink>'
    source=tmp_path/'source.xlsx';write(source,v);plan=M.build_plan(source)
    assert 'dead-external-book-package/v1' not in plan['action_counts']
    if defect=='conditional-name':
        assert not any(a.get('name')=='Unused' for a in plan['actions'])
        assert any(r.get('kind')=='live_restricted_defined_name' for r in plan['unresolved'])


def test_display_brackets_do_not_create_formula_dependencies(tmp_path):
    v=members();v['xl/sharedStrings.xml']=f'<sst xmlns="{S}"><si><t>[Company]</t></si></sst>'.encode()
    v['xl/styles.xml']=b'<styleSheet><numFmts><numFmt numFmtId="164" formatCode="[Red]0.00"/></numFmts></styleSheet>'
    source=tmp_path/'source.xlsx';write(source,v)
    assert M.build_plan(source)['action_counts']['dead-external-book-package/v1']==1


@pytest.mark.parametrize('live', [False, True])
def test_bracketed_style_name_is_display_only_but_other_attributes_stay_blocking(tmp_path, live):
    v=members()
    attribute='other' if live else 'name'
    v['xl/styles.xml']=(f'<styleSheet xmlns="{S}"><cellStyles><cellStyle {attribute}="[0] Normal" xfId="0"/></cellStyles></styleSheet>').encode()
    source=tmp_path/'source.xlsx';write(source,v)
    assert ('dead-external-book-package/v1' in M.build_plan(source)['action_counts']) is (not live)


def test_self_closing_shared_formula_does_not_capture_xml_as_formula():
    values={'xl/worksheets/sheet1.xml':b'<worksheet><c r="A1"><f t="shared" si="1"/><v>3</v></c><c r="B1"><f>A1+2</f></c><conditionalFormatting><formula>RealName&amp;1</formula></conditionalFormatting></worksheet>'}
    formulas=M._formula_texts_from_other_parts(values,set(values))
    assert formulas==['','A1+2','RealName&1']
    assert set().union(*(M._name_tokens(x,{'f','v','c','realname'})for x in formulas))=={'realname'}


def test_dead_graph_pruning_composes_with_proved_offset_rewrite(tmp_path):
    values = members()
    sheet = 'xl/worksheets/sheet1.xml'
    values[sheet] = values[sheet].replace(b'1+2', b'OFFSET(B1,0,0)+1').replace(
        b'</row>', b'<c r="B1"><v>2</v></c></row>')
    source = tmp_path/'source.xlsx'
    candidate = tmp_path/'candidate.xlsx'
    write(source, values)
    original = M.sha256_file(source)
    plan = M.build_plan(source)
    assert plan['status'] == 'eligible'
    assert plan['action_counts']['dead-external-book-package/v1'] == 1
    manifest = M.apply_plan(source, plan, candidate)
    assert M.verify_manifest(source, candidate, plan, manifest)['status'] == 'verified'
    assert M.sha256_file(source) == original
    with zipfile.ZipFile(candidate) as archive:
        assert b'OFFSET(' not in archive.read(sheet)
        assert not any(p.startswith('xl/externalLinks/') for p in archive.namelist())
        assert M._value_ledger(values) == M._value_ledger(
            {name: archive.read(name) for name in archive.namelist()})


def test_proved_rewrite_does_not_hide_another_dynamic_reference(tmp_path):
    values = members()
    sheet = 'xl/worksheets/sheet1.xml'
    values[sheet] = values[sheet].replace(b'1+2', b'OFFSET(B1,0,0)+1').replace(
        b'</row>', b'<c r="B1"><f>INDIRECT(C1)</f><v>2</v></c></row>')
    source = tmp_path/'source.xlsx'
    write(source, values)
    plan = M.build_plan(source)
    assert 'dead-external-book-package/v1' not in plan['action_counts']
    assert plan['status'] == 'ineligible'


@pytest.mark.parametrize('live', [False, True])
def test_missing_external_path_graph_still_requires_no_live_references(tmp_path, live):
    values=members()
    key='xl/externalLinks/_rels/externalLink1.xml.rels'
    values[key]=values[key].replace((R+'/externalLinkPath').encode(),
        b'http://schemas.microsoft.com/office/2006/relationships/xlExternalLinkPath/xlPathMissing')
    if live:values['xl/worksheets/sheet1.xml']=values['xl/worksheets/sheet1.xml'].replace(b'1+2',b'Unused')
    source=tmp_path/'source.xlsx';write(source,values);plan=M.build_plan(source)
    assert ('dead-external-book-package/v1' in plan['action_counts']) is (not live)
    if not live:
        candidate=tmp_path/'candidate.xlsx';manifest=M.apply_plan(source,plan,candidate)
        assert M.verify_manifest(source,candidate,plan,manifest)['status']=='verified'
