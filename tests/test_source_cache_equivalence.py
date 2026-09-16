from pathlib import Path
import json,time,zipfile
import pytest
import openpyxl
from openpyxl.workbook.properties import CalcProperties
import xl_source_cache_equivalence as M


def workbook(path,cache='4',formula='A1*2',extra=False):
    book=openpyxl.Workbook();sheet=book.active;sheet['A1']=2;sheet['B1']='='+formula
    if extra:sheet['C1']='=A1*3'
    book.calculation=CalcProperties(calcMode='manual',fullCalcOnLoad=False,forceFullCalc=False,iterate=False);book.save(path)
    with zipfile.ZipFile(path)as z:infos=z.infolist();parts={i.filename:z.read(i)for i in infos}
    raw=parts['xl/worksheets/sheet1.xml'].replace(b'<v />',b'<v></v>');raw=raw.replace(b'<f>'+formula.encode()+b'</f><v></v>',b'<f>'+formula.encode()+b'</f><v>'+cache.encode()+b'</v>');parts['xl/worksheets/sheet1.xml']=raw
    with zipfile.ZipFile(path,'w')as z:
        for i in infos:z.writestr(i,parts[i.filename])
    return path


def selected(tmp_path,source):
    auth=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
    now=time.time_ns();p=tmp_path/'policy.json';p.write_text(json.dumps(dict(schema_version=M.POLICY,workbook=source.stem,source_sha256=M.digest(source),authorization=M.binding(auth),created_at_ns=now-10**9,expires_at_ns=now+10**12,max_formula_cells=100,relative_tolerance=1e-6)))
    return M.binding(p)


def test_full_proof_and_exact_metadata_only_normalization(tmp_path,monkeypatch):
    source=workbook(tmp_path/'example.xlsx');policy=selected(tmp_path,source);before=source.read_bytes();monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    candidate,manifest=M.create(source,tmp_path/'normalized',policy)
    assert source.read_bytes()==before and manifest['recalculation_performed']is False
    assert M.read(manifest['proof'])['formulas']==1 and M.verify(source,candidate,manifest)['recalculation_performed']is False
    original=M.package(source)[1];after=M.package(candidate)[1]
    assert {n for n in original if original[n]!=after[n]}=={'xl/workbook.xml'}


@pytest.mark.parametrize('kind',['stale','missing','unsupported','extra-uncomputed','bool-type'])
def test_every_formula_cache_and_type_must_verify(tmp_path,kind):
    kwargs={'cache':'5'}if kind=='stale'else {'cache':''}if kind=='missing'else {'formula':'UNKNOWN(A1)'}if kind=='unsupported'else {'extra':True}if kind=='extra-uncomputed'else{'cache':'1','formula':'TRUE()'}
    source=workbook(tmp_path/'example.xlsx',**kwargs)
    with pytest.raises(ValueError):M.prove(source,100,1e-6)


def test_formula_count_bound_is_not_sampling(tmp_path):
    source=workbook(tmp_path/'example.xlsx')
    with pytest.raises(ValueError,match='scope'):M.prove(source,0,1e-6)


@pytest.mark.parametrize('fault',['source','expired','future','count','tolerance','duration','authority','foreign-authority'])
def test_policy_scope_and_authorization_are_bound(tmp_path,fault):
    source=workbook(tmp_path/'example.xlsx');row=selected(tmp_path,source);p=M.read(row)
    if fault=='source':p['source_sha256']='0'*64
    elif fault=='expired':p['expires_at_ns']=p['created_at_ns']+1
    elif fault=='future':p['created_at_ns']=time.time_ns()+10**11
    elif fault=='count':p['max_formula_cells']=10001
    elif fault=='tolerance':p['relative_tolerance']=.01
    elif fault=='duration':p['expires_at_ns']=p['created_at_ns']+25*3600*10**9
    elif fault=='foreign-authority':
        foreign=tmp_path/'other-auth.txt';foreign.write_text('Self-consistent but not authorized.');p['authorization']=M.binding(foreign)
    else:p['authorization']['sha256']='0'*64
    changed=tmp_path/'changed.json';changed.write_text(json.dumps(p))
    with pytest.raises(ValueError):M.policy(M.binding(changed),source)


def test_hash_consistent_candidate_formula_mutation_still_rejected(tmp_path,monkeypatch):
    source=workbook(tmp_path/'example.xlsx');monkeypatch.setattr(M,'implementation',lambda:'a'*64);candidate,manifest=M.create(source,tmp_path/'normalized',selected(tmp_path,source))
    infos,parts=M.package(candidate);parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'A1*2',b'A1*3')
    with zipfile.ZipFile(candidate,'w')as z:
        for i in infos:z.writestr(i,parts[i.filename])
    manifest['candidate']=M.binding(candidate)
    with pytest.raises(ValueError,match='package member'):M.verify(source,candidate,manifest)


def test_matching_but_uncertified_cycle_is_not_cache_qualification(tmp_path):
    source=workbook(tmp_path/'example.xlsx',cache='0',formula='B1')
    with pytest.raises(ValueError):M.prove(source,100,1e-6)


@pytest.mark.parametrize('certified',[False,True])
def test_original_corpus_worker_publishes_source_through_existing_gates(tmp_path,monkeypatch,certified):
    from argparse import Namespace
    from scripts import corpus_build as B
    from scripts.corpus_lineage import validate_normalization
    from xl_source_publication import validate_source_generation
    if certified:
        from test_source_cache_certified_cycles import cyclic,policy
        source=cyclic(tmp_path);selected_policy=policy(tmp_path,source)
    else:
        source=workbook(tmp_path/'0001.xlsx');selected_policy=selected(tmp_path,source)
    original=source.read_bytes()
    args=Namespace(run_root=str(tmp_path/'run'),curation_root=str(tmp_path/'curation'),inventory='unused',source_inventory=None,inventory_batch_id=None,recalc_root=None,trusted_public_key='/unused/key',cache_equivalence_policy=selected_policy['path'],cache_equivalence_policy_sha256=selected_policy['sha256'])
    directory=Path(args.run_root)/'workbooks/0001';directory.mkdir(parents=True);curation=Path(args.curation_root)/'0001/curation.toml';curation.parent.mkdir(parents=True);curation.write_text('# original selection\n')
    row=dict(workbook_id='0001',path=str(source),sha256=M.digest(source),format='.xlsx')
    ledger=dict(state='STOPPED',route='recalc_candidate',failure='trusted_recalculation_required',reason_codes=['manual_calculation_mode'],build=dict(attempts=1,state='RUNNING',stage='source',curation_path=str(curation),bindings=dict(code_sha256='frozen',source_sha256=row['sha256'],curation_sha256=M.digest(curation),inventory_sha256=None,inventory_batch_id=None,cache_equivalence_policy_sha256=selected_policy['sha256'])))
    path=directory/'ledger.json';path.write_text(json.dumps(ledger));monkeypatch.setattr(B,'code_hash',lambda:'frozen');monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    class ReachedSegmentation(Exception):pass
    def stop(*a,**kw):raise ReachedSegmentation()
    monkeypatch.setattr('xl_segment.segment',stop)
    with pytest.raises(ReachedSegmentation):B.worker(args,row)
    after=json.loads(path.read_bytes());build=after['build'];assert build['attempts']==1 and build['normalization']['policy_version']==(M.CERTIFIED_POLICY if certified else M.POLICY)
    assert source.read_bytes()==original and 'recalculation'not in build
    generation=Path(args.run_root)/'sources/0001/generations'/build['source_generation_id'];manifest=validate_source_generation(generation)
    assert manifest['bindings']['source_sha256']==build['normalization']['candidate_sha256']
    request=json.loads((generation/'request.json').read_bytes());assert request['schema_version']=='source-identity-request/v2'
    validate_normalization(source,build['normalization']['candidate'],build['normalization'])


def test_unselected_original_manual_source_is_still_blocked(tmp_path):
    from argparse import Namespace
    from scripts import corpus_build as B
    source=workbook(tmp_path/'0001.xlsx');directory=tmp_path/'run/workbooks/0001';directory.mkdir(parents=True)
    ledger=dict(state='STOPPED',route='recalc_candidate',failure='trusted_recalculation_required',reason_codes=['manual_calculation_mode']);(directory/'ledger.json').write_text(json.dumps(ledger))
    args=Namespace(run_root=str(tmp_path/'run'),recalc_root=None)
    result=B.execute_one(args,dict(workbook_id='0001',path=str(source),sha256=M.digest(source),format='.xlsx'),'frozen')
    assert result['state']=='STOPPED' and result['failure']=='trusted_recalculation_required'
    assert json.loads((directory/'ledger.json').read_bytes())==ledger
