import json
import pytest
import xl_source_cache_equivalence as M
from test_source_cache_equivalence import workbook,selected


def policy(tmp_path,source,**kwargs):
 value=M.read(selected(tmp_path,source));value.update(schema_version=M.LARGE_ACYCLIC_POLICY,max_formula_cells=30000,**kwargs);p=tmp_path/'large.json';p.write_text(json.dumps(value));return M.binding(p)


def test_large_acyclic_policy_exact_metadata_roundtrip(tmp_path,monkeypatch):
 source=workbook(tmp_path/'example.xlsx');row=policy(tmp_path,source);assert M.policy(row,source)['max_formula_cells']==30000
 monkeypatch.setattr(M,'implementation',lambda:'a'*64)
 candidate,manifest=M.create(source,tmp_path/'output',row)
 assert manifest['policy_version']==M.LARGE_ACYCLIC_POLICY
 assert M.verify(source,candidate,manifest)['recalculation_performed']is False


@pytest.mark.parametrize('fault',['bound','cycle-option','certified-version','old-version','wrong-cache','cycle'])
def test_large_scope_never_weakens_original_bounds_or_numeric_checks(tmp_path,monkeypatch,fault):
 source=workbook(tmp_path/'example.xlsx',**({'formula':'B1','cache':'0'}if fault=='cycle'else{'cache':'5'}if fault=='wrong-cache'else{}));row=policy(tmp_path,source);value=M.read(row)
 if fault=='bound':value['max_formula_cells']=30001
 if fault=='cycle-option':value['max_cycle_cells']=128
 if fault=='certified-version':value.update(schema_version=M.CERTIFIED_POLICY,max_cycle_cells=128)
 if fault=='old-version':value['schema_version']=M.POLICY
 p=tmp_path/'bad.json';p.write_text(json.dumps(value));monkeypatch.setattr(M,'implementation',lambda:'a'*64)
 with pytest.raises(ValueError):M.create(source,tmp_path/'output',M.binding(p))
