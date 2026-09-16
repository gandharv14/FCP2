from pathlib import Path
from xml.etree import ElementTree as ET
import json,time,zipfile
import pytest
import xl_macro_source_projection as M
from test_source_cache_equivalence import workbook


def source(tmp,**kwargs):
    path=workbook(tmp/'0001.xlsm',**kwargs);infos,parts=M.package(path)
    parts['xl/workbook.xml']=parts['xl/workbook.xml'].replace(b'calcMode="manual"',b'calcMode="auto"')
    types=ET.fromstring(parts['[Content_Types].xml'])
    for item in types:
        if item.get('PartName')=='/xl/workbook.xml':item.set('ContentType',M.MACRO_TYPE)
    ET.SubElement(types,'{'+M.CT+'}Override',PartName='/xl/vbaProject.bin',ContentType=M.VBA_TYPE)
    rels=ET.fromstring(parts['xl/_rels/workbook.xml.rels'])
    ET.SubElement(rels,'{'+M.REL+'}Relationship',Id='vba',Type=M.VBA_REL,Target='vbaProject.bin')
    parts.update({'[Content_Types].xml':ET.tostring(types),'xl/_rels/workbook.xml.rels':ET.tostring(rels),'xl/vbaProject.bin':b'never execute fixture'})
    write(path,parts);return path


def write(path,parts):
    with zipfile.ZipFile(path,'w')as z:
        for name,value in parts.items():z.writestr(name,value)


def policy(tmp,path):
    now=time.time_ns();return M.keep(tmp/'policy.json',dict(schema_version=M.POLICY,workbook=path.stem,
        source_sha256=M.digest(path),authorization=M.binding(Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'),
        created_at_ns=now-10**9,expires_at_ns=now+10**12,max_formula_cells=100,relative_tolerance=1e-6))


def test_exact_projection_and_full_primitive_proof(tmp_path,monkeypatch):
    p=source(tmp_path);original=p.read_bytes();monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    candidate,manifest=M.create(p,tmp_path/'projection',policy(tmp_path,p));before=M.package(p)[1];after=M.package(candidate)[1]
    assert p.read_bytes()==original and candidate.suffix=='.xlsx'
    assert set(before)-set(after)=={'xl/vbaProject.bin'}
    assert {n for n in after if after[n]!=before[n]}=={'[Content_Types].xml','xl/_rels/workbook.xml.rels'}
    assert M.read(manifest['proof'])['formulas']==1 and manifest['source_qualified']is False
    assert M.verify(p,candidate,manifest)['macros_executed']is False


@pytest.mark.parametrize('options',[{'cache':'5'},{'cache':''},{'formula':'UDF(A1)'},{'formula':'B1','cache':'0'},{'extra':True}])
def test_substantive_errors_never_become_projection_pass(tmp_path,options):
    with pytest.raises(ValueError):M.prove(source(tmp_path,**options),100,1e-6)


@pytest.mark.parametrize('part',['xl/activeX/activeX1.bin','xl/ctrlProps/ctrlProp1.xml','customUI/customUI.xml','xl/embeddings/oleObject1.bin','_xmlsignatures/sig1.xml','xl/macrosheets/sheet1.xml'])
def test_active_content_requires_separate_policy(tmp_path,part):
    parts=M.package(source(tmp_path))[1];parts[part]=b'blocked'
    with pytest.raises(ValueError,match='another policy'):M.projected_members(parts)


def test_external_relationship_rejected(tmp_path):
    parts=M.package(source(tmp_path))[1];rels=ET.fromstring(parts['xl/_rels/workbook.xml.rels'])
    ET.SubElement(rels,'{'+M.REL+'}Relationship',Id='remote',Type='externalLink',Target='https://example.invalid',TargetMode='External')
    parts['xl/_rels/workbook.xml.rels']=ET.tostring(rels)
    with pytest.raises(ValueError,match='external'):M.projected_members(parts)


def test_hash_consistent_formula_mutation_rejected(tmp_path,monkeypatch):
    p=source(tmp_path);monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    candidate,manifest=M.create(p,tmp_path/'projection',policy(tmp_path,p));parts=M.package(candidate)[1]
    parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'A1*2',b'A1*3');write(candidate,parts);manifest['candidate']=M.binding(candidate)
    with pytest.raises(ValueError,match='unrelated package'):M.verify(p,candidate,manifest)


def test_original_corpus_worker_and_lineage_remain_required(tmp_path,monkeypatch):
    from argparse import Namespace
    from scripts import corpus_build as B
    from scripts.corpus_lineage import validate_normalization
    from xl_source_publication import validate_source_generation
    p=source(tmp_path);selected=policy(tmp_path,p);original=p.read_bytes()
    args=Namespace(run_root=str(tmp_path/'run'),curation_root=str(tmp_path/'curation'),inventory='unused',source_inventory=None,
        inventory_batch_id=None,recalc_root=None,trusted_public_key='/unused',macro_projection_policy=selected['path'],macro_projection_policy_sha256=selected['sha256'])
    directory=Path(args.run_root)/'workbooks/0001';directory.mkdir(parents=True)
    curation=Path(args.curation_root)/'0001/curation.toml';curation.parent.mkdir(parents=True);curation.write_text('# original\n')
    row=dict(workbook_id='0001',path=str(p),sha256=M.digest(p),format='.xlsm')
    ledger=dict(state='STOPPED',route='unsupported',failure='source_policy_unsupported',reason_codes=['macros_present'],
        build=dict(attempts=1,state='RUNNING',stage='source',curation_path=str(curation),bindings=dict(code_sha256='frozen',source_sha256=row['sha256'],
        curation_sha256=M.digest(curation),inventory_sha256=None,inventory_batch_id=None,macro_projection_policy_sha256=selected['sha256'])))
    path=directory/'ledger.json';path.write_text(json.dumps(ledger));monkeypatch.setattr(B,'code_hash',lambda:'frozen');monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    class ReachedSegmentation(Exception):pass
    def stop(*a,**kw):raise ReachedSegmentation()
    monkeypatch.setattr('xl_segment.segment',stop)
    with pytest.raises(ReachedSegmentation):B.worker(args,row)
    after=json.loads(path.read_bytes())['build'];assert p.read_bytes()==original and after['normalization']['policy_version']==M.POLICY
    generation=Path(args.run_root)/'sources/0001/generations'/after['source_generation_id']
    assert validate_source_generation(generation)['bindings']['source_sha256']==after['normalization']['candidate_sha256']
    validate_normalization(p,after['normalization']['candidate'],after['normalization'])


def test_default_macro_gate_is_not_relaxed(tmp_path):
    from argparse import Namespace
    from scripts import corpus_build as B
    p=source(tmp_path);d=tmp_path/'run/workbooks/0001';d.mkdir(parents=True)
    ledger=dict(state='STOPPED',route='unsupported',failure='source_policy_unsupported',reason_codes=['macros_present']);(d/'ledger.json').write_text(json.dumps(ledger))
    assert B.execute_one(Namespace(run_root=str(tmp_path/'run'),recalc_root=None),dict(workbook_id='0001',path=str(p),sha256=M.digest(p),format='.xlsm'),'frozen')['state']=='STOPPED'
    assert json.loads((d/'ledger.json').read_bytes())==ledger


def test_offset_keeps_original_restricted_inventory_gate(tmp_path,monkeypatch):
    from xl_source_recalc import prepare_source_generation,RecalculationError
    p=source(tmp_path,formula='OFFSET(A1,0,0)*2');monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    candidate,manifest=M.create(p,tmp_path/'projection',policy(tmp_path,p))
    result=M.verify(p,candidate,manifest)
    assert result['source_route']=='restricted_pass' and result['requires_approved_inventory']is True
    assert M.package(p)[1]['xl/worksheets/sheet1.xml']==M.package(candidate)[1]['xl/worksheets/sheet1.xml']
    with pytest.raises(RecalculationError,match='approved batch inventory'):
        prepare_source_generation(candidate,'0001',tmp_path/'generation')


def test_macro_inventory_retains_original_and_rejects_forged_lineage(tmp_path,monkeypatch):
    import copy
    from xl_source_inventory import macro_projected_workbook_record,assemble_inventory_manifest,_validate_transformation,SourceInventoryError
    from xl_inventory_approval import original_source_ledger,resolve_inventory_approval,InventoryApprovalError
    p=source(tmp_path,formula='OFFSET(A1,0,0)*2');monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    candidate,manifest=M.create(p,tmp_path/'projection',policy(tmp_path,p))
    row=macro_projected_workbook_record('0001',p,candidate,manifest);inventory=assemble_inventory_manifest([row])
    assert row['route']=='restricted_pass' and row['conversion']is None
    assert original_source_ledger(inventory)[0]['original_source']['sha256']==M.digest(p)
    with pytest.raises(InventoryApprovalError):resolve_inventory_approval(inventory,batch_id='not-approved')
    for fault in ('original','receipt','candidate','identity'):
        bad=copy.deepcopy(row)
        if fault=='original':bad['transformation']['original_source']['sha256']='0'*64
        elif fault=='receipt':bad['transformation']['receipt']['source_qualified']=True
        elif fault=='candidate':bad['sha256']='0'*64
        else:bad['workbook_id']='0002'
        with pytest.raises(SourceInventoryError):_validate_transformation(bad)


def test_proof_implementation_binds_code_and_excludes_only_downstream_registry(monkeypatch):
    from scripts import corpus
    values={'xl_macro_source_projection.py':'a'*64,'xl_seg/evaluate.py':'b'*64,
            'verification_manifests/approved_source_inventories.v2.json':'c'*64}
    monkeypatch.setattr(corpus,'code_files',lambda:dict(values));before=M.implementation()
    values['verification_manifests/approved_source_inventories.v2.json']='d'*64
    assert M.implementation()==before
    values['xl_seg/evaluate.py']='e'*64
    assert M.implementation()!=before


def test_required_macro_generation_reuses_exact_normalization_and_ast(tmp_path,monkeypatch):
    from argparse import Namespace
    import copy
    from scripts import corpus_build as B
    p=source(tmp_path);selected=policy(tmp_path,p)
    args=Namespace(run_root=str(tmp_path/'run'),curation_root=str(tmp_path/'curation'),inventory='unused',source_inventory=None,
        inventory_batch_id=None,recalc_root=None,trusted_public_key='/unused',macro_projection_policy=selected['path'],macro_projection_policy_sha256=selected['sha256'])
    directory=Path(args.run_root)/'workbooks/0001';directory.mkdir(parents=True)
    curation=Path(args.curation_root)/'0001/curation.toml';curation.parent.mkdir(parents=True);curation.write_text('# original\n')
    row=dict(workbook_id='0001',path=str(p),sha256=M.digest(p),format='.xlsm')
    ledger=dict(state='STOPPED',route='unsupported',failure='source_policy_unsupported',reason_codes=['macros_present'],
        build=dict(attempts=1,state='RUNNING',stage='source',curation_path=str(curation),bindings=dict(code_sha256='frozen',source_sha256=row['sha256'],
        curation_sha256=M.digest(curation),inventory_sha256=None,inventory_batch_id=None,macro_projection_policy_sha256=selected['sha256'])))
    path=directory/'ledger.json';path.write_text(json.dumps(ledger));monkeypatch.setattr(B,'code_hash',lambda:'frozen');monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    class SegmentationBoundary(Exception):pass
    monkeypatch.setattr('xl_segment.segment',lambda *_a,**_k:(_ for _ in()).throw(SegmentationBoundary()))
    with pytest.raises(SegmentationBoundary):B.worker(args,row)
    first=json.loads(path.read_text());previous=copy.deepcopy(first['build']);generation=previous['source_generation_id']
    retained=Path(args.run_root)/'sources/0001/generations'/generation
    before={str(x.relative_to(retained)):x.read_bytes()for x in retained.rglob('*')if x.is_file()}
    first['build_history']=[copy.deepcopy(previous)]
    first['build']=dict(attempts=2,state='RUNNING',stage='source',curation_path=str(curation),bindings=copy.deepcopy(previous['bindings']))
    path.write_text(json.dumps(first));args.require_source_generation=generation
    monkeypatch.setattr(M,'create',lambda *_a,**_k:pytest.fail('normalization must not repeat'))
    monkeypatch.setattr('xl_source_recalc.prepare_source_generation',lambda *_a,**_k:pytest.fail('AST construction must not repeat'))
    with pytest.raises(SegmentationBoundary):B.worker(args,row)
    after=json.loads(path.read_text())
    assert after['build']['attempts']==2 and after['build_history']==[previous]
    assert after['build']['normalization']==previous['normalization']
    assert after['build']['source_reused']is True and after['build']['source_generation_id']==generation
    assert before=={str(x.relative_to(retained)):x.read_bytes()for x in retained.rglob('*')if x.is_file()}
