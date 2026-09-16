"""A selected output after timeout may reuse an unchanged restricted source AST."""
import copy,json,zipfile
from pathlib import Path
from unittest.mock import Mock
import pytest
from scripts import corpus_build as B
from scripts.corpus import digest,object_hash
from test_corpus_source_checkpoint import completed
from xl_source_recalc import prepare_source_generation

@pytest.fixture
def restricted(completed,monkeypatch,tmp_path):
    import xl_inventory_approval as approval
    from xl_source_inventory import build_inventory_manifest
    args,source,ledger,_=completed
    raw=tmp_path/'restricted/0001.xlsx';raw.parent.mkdir()
    with zipfile.ZipFile(source['path'])as z:parts={n:z.read(n)for n in z.namelist()}
    parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'A1*2',b'OFFSET(A1,0,0)*2')
    with zipfile.ZipFile(raw,'w')as z:
        for n,v in parts.items():z.writestr(n,v)
    source.update(path=str(raw),sha256=digest(raw))
    inventory=build_inventory_manifest(raw.parent,workbook_ids=['0001']);inv=tmp_path/'inventory.json';inv.write_text(json.dumps(inventory))
    core=dict(schema_version='source-inventory-approval-registry/v1',approvals=[approval.approval_claims(inventory,batch_id='approved')]);registry=tmp_path/'approval.json';registry.write_text(json.dumps(dict(core,registry_sha256=approval.object_hash(core))));monkeypatch.setattr(approval,'DEFAULT_REGISTRY',registry)
    args.source_inventory=str(inv);args.inventory_batch_id='approved';opts=dict(inventory=str(inv),inventory_batch_id='approved')
    gen,manifest=prepare_source_generation(raw,'0001',Path(args.run_root)/'sources/0001',**opts)
    previous=ledger['build_history'][0];previous.update(source_generation_id=manifest['generation_id'],source_manifest_sha256=digest(gen/'generation-manifest.json'));previous['bindings'].update(source_sha256=source['sha256'],inventory_sha256=digest(inv),inventory_batch_id='approved');ledger['build']['bindings'].update(previous['bindings'])
    result=B.reuse_completed_source(args,source,ledger,raw,opts,{});assert result is not None
    previous.update(source_checkpoint=result[2],source_checkpoint_sha256=object_hash(result[2]))
    return args,source,ledger,gen,opts,registry

def call(f):
    args,source,ledger,_,opts,_=f
    return B.reuse_completed_source(args,source,ledger,source['path'],opts,{})

def test_retained_selection_preserves_generation_and_consumed_history(restricted,monkeypatch):
    import xl_ast_graph
    args,source,ledger,gen,opts,_=restricted;prior=copy.deepcopy(ledger['build_history']);files={str(p.relative_to(gen)):p.read_bytes()for p in gen.rglob('*')if p.is_file()}
    ledger['build']['bindings']['curation_sha256']='selected-after-original-source'
    ast=Mock(side_effect=AssertionError('no AST rebuild'));monkeypatch.setattr(xl_ast_graph,'main',ast)
    result=call(restricted);assert result is not None and result[0]==gen and not ast.called
    assert result[2]['bindings']['curation_sha256']=='selected-after-original-source'
    assert ledger['build_history']==prior and files=={str(p.relative_to(gen)):p.read_bytes()for p in gen.rglob('*')if p.is_file()}

@pytest.mark.parametrize('change',['inventory','batch','approval','builder','publication','normalization','recalculation','source'])
def test_curation_exception_retains_every_substantive_gate(restricted,monkeypatch,tmp_path,change):
    import xl_source_publication as P
    args,source,ledger,gen,opts,registry=restricted;ledger['build']['bindings']['curation_sha256']='later-selection'
    if change=='inventory':
        inv=Path(opts['inventory']);inv.write_text(inv.read_text()+' ')
    elif change=='batch':args.inventory_batch_id=opts['inventory_batch_id']='different'
    elif change=='approval':registry.write_text('{}')
    elif change=='builder':
        wrong=tmp_path/'wrong.py';wrong.write_text('# changed source producer');monkeypatch.setattr(P,'_default_builder_paths',lambda:(wrong,))
    elif change=='publication':monkeypatch.setattr(P,'publish_source_generation',Mock(side_effect=P.SourcePublicationError('current source gate refused')))
    elif change in ('normalization','recalculation'):ledger['build_history'][0][change]={}
    elif change=='source':
        raw=Path(source['path']);raw.write_bytes(raw.read_bytes()+b'changed bytes');source['sha256']=digest(raw)
    assert call(restricted) is None
