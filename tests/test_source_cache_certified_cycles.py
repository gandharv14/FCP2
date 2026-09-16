"""A separately authorized exact cycle proof cannot waive source errors."""
import json,zipfile
from types import SimpleNamespace
import pytest
import xl_source_cache_equivalence as M
from test_source_cache_equivalence import workbook,selected


def cyclic(tmp_path,formula='B1*0+4',cache='4'):
    source=workbook(tmp_path/'0001.xlsx',formula=formula,cache=cache)
    infos,parts=M.package(source)
    parts['xl/workbook.xml']=parts['xl/workbook.xml'].replace(b'iterate="0"',b'iterate="1"')
    with zipfile.ZipFile(source,'w')as z:
        for info in infos:z.writestr(info,parts[info.filename])
    return source


def policy(tmp_path,source):
    p=M.read(selected(tmp_path,source));p.update(schema_version=M.CERTIFIED_POLICY,max_cycle_cells=32)
    file=tmp_path/'certified-policy.json';file.write_text(json.dumps(p));return M.binding(file)


def test_exact_certified_cycle_is_supported_only_by_new_policy(tmp_path,monkeypatch):
    source=cyclic(tmp_path);before=source.read_bytes();monkeypatch.setattr(M,'implementation',lambda:'a'*64)
    with pytest.raises(ValueError,match='separate certified policy'):M.prove(source,100,1e-6)
    candidate,manifest=M.create(source,tmp_path/'normalized',policy(tmp_path,source))
    proof=M.read(manifest['proof'])
    assert proof['active_cycles']==proof['certified_cycles']['count']==1
    assert proof['all_formula_cells_match']and proof['formula_seeds']==proof['oracle_reads']==0
    assert source.read_bytes()==before and manifest['policy_version']==M.CERTIFIED_POLICY
    assert M.verify(source,candidate,manifest)['recalculation_performed']is False
    from scripts.corpus_lineage import validate_normalization
    record=dict(original_sha256=M.digest(source),candidate=str(candidate),candidate_sha256=M.digest(candidate),manifest=str(tmp_path/'normalized/manifest.json'),manifest_sha256=M.digest(tmp_path/'normalized/manifest.json'),policy_version=M.CERTIFIED_POLICY)
    validate_normalization(source,candidate,record)
    with pytest.raises(ValueError,match='policy version differs'):
        validate_normalization(source,candidate,{**record,'policy_version':M.POLICY})


@pytest.mark.parametrize('formula,cache',[('B1','0'),('B1*0+4','5'),('B1*0+4',''),('0.5*B1+1','1.9990234375'),('UNKNOWN(B1)','0')])
def test_cycle_policy_does_not_accept_nonunique_wrong_missing_finite_or_unknown(tmp_path,formula,cache):
    source=cyclic(tmp_path,formula,cache)
    with pytest.raises(ValueError):M.prove(source,100,1e-6,max_cycle_cells=32)


def group():
    return dict(seed='Sheet!B1',certified=True,converged=True,budget_exhausted=False,targets_stable=True,topology_stable=True,residual=0,max_change=0,errors=0,unresolved=0,member_count=1,members=['Sheet!B1'],certification=dict(kind='affine_system',certified=True,contractive=True,endpoint_error_bound=0,dimension=1,contraction_bound=0.5))


@pytest.mark.parametrize('fault',['missing-cycle','uncertified','noncontractive','bound-one','bound-nan','error-bound','residual','moving','budget','membership','oversize','errors','unstable'])
def test_complete_exact_native_cycle_evidence_required(fault):
    g=group();r=SimpleNamespace(iterated=[g],coverage={'active_cycles':1},cycle_groups={1:('Sheet!B1',)},cycle_membership={'Sheet!B1':1})
    if fault=='missing-cycle':r.coverage['active_cycles']=2
    elif fault=='uncertified':g['certified']=False
    elif fault=='noncontractive':g['certification']['contractive']=False
    elif fault=='bound-one':g['certification']['contraction_bound']=1
    elif fault=='bound-nan':g['certification']['contraction_bound']=float('nan')
    elif fault=='error-bound':g['certification']['endpoint_error_bound']=1e-12
    elif fault=='residual':g['residual']=1e-12
    elif fault=='moving':g['max_change']=1e-12
    elif fault=='budget':g['budget_exhausted']=True
    elif fault=='membership':g['members']=[]
    elif fault=='oversize':g['certification']['dimension']=129
    elif fault=='errors':g['errors']=1
    else:g['topology_stable']=False
    with pytest.raises(ValueError):M.certified_cycles(r,32)


@pytest.mark.parametrize('maximum',[0,129,True,1.5])
def test_policy_cycle_bound_is_finite_explicit_and_typed(tmp_path,maximum):
    source=cyclic(tmp_path);row=policy(tmp_path,source);v=M.read(row);v['max_cycle_cells']=maximum
    changed=tmp_path/'changed.json';changed.write_text(json.dumps(v))
    with pytest.raises(ValueError):M.policy(M.binding(changed),source)


def test_full_cycle_larger_than_twenty_cell_diagnostic_sample(tmp_path):
    import openpyxl
    source=cyclic(tmp_path);book=openpyxl.load_workbook(source)
    for row in range(1,22):book.active.cell(row,2,'=0*B'+str(row%21+1)+'+4')
    book.save(source);book.close();infos,parts=M.package(source)
    parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'<v />',b'<v></v>').replace(b'<v/>',b'<v></v>').replace(b'<v></v>',b'<v>4</v>')
    with zipfile.ZipFile(source,'w')as z:
        for info in infos:z.writestr(info,parts[info.filename])
    proof=M.prove(source,100,1e-6,max_cycle_cells=32)
    assert proof['formulas']==21 and proof['active_cycles']==1
    with pytest.raises(ValueError):M.prove(source,100,1e-6,max_cycle_cells=20)
