import copy
import importlib.util
from pathlib import Path
import sys
import pytest
from scripts.fleet import reconstruction_continue as C
from test_reconstruction_continue import boundary
from test_dead_external_package_remediation import members, write


def external():
    p,l,a=boundary();p.update(schema_version='fleet-pre-source-continuation/v2',restriction_inventory=None,inventory_batch_id=None,retained_curation=None,expected_failure='RemediationError:remediation left unsupported volatile or external references')
    l['reason_codes']=['external_links_present'];l['build'].update(stage='normalization',failure=p['expected_failure'])
    return p,l,a


def test_explicit_external_failure_appends_original_consumption():
    p,l,a=external();before=copy.deepcopy(l);C.validate_boundary(p,l,a);assert l==before


@pytest.mark.parametrize('fault',['implicit','other_reason','other_failure','completed_normalization','wrong_stage','generation','counter','history','inventory','models'])
def test_external_boundary_does_not_admit_other_failures(fault):
    p,l,a=external()
    if fault=='implicit':p['schema_version']='fleet-pre-source-continuation/v1'
    elif fault=='other_reason':l['reason_codes'].append('unsupported_volatile_formulas_present')
    elif fault=='other_failure':p['expected_failure']=l['build']['failure']='RemediationError:any error'
    elif fault=='completed_normalization':l['build']['normalization']={'candidate':'retained'}
    elif fault=='wrong_stage':l['build']['stage']='segmentation'
    elif fault=='generation':l['build']['source_generation_id']='already-qualified'
    elif fault=='counter':p['next_attempt']=1
    elif fault=='history':l['build_history']=[{'attempts':0}]
    elif fault=='inventory':p['restriction_inventory']={'path':'other','sha256':'a'*64}
    else:p['models']=1
    with pytest.raises(ValueError):C.validate_boundary(p,l,a)


def test_absent_curation_is_explicit_and_cannot_discard_a_later_file(tmp_path):
    p,l,_=external();p['curation_root']=str(tmp_path/'new');old=tmp_path/'old.toml';l['build'].update(curation_path=str(old),bindings={'curation_sha256':None})
    C.validate_curation(p,l)
    old.write_text('targets = ["A1"]')
    with pytest.raises(ValueError):C.validate_curation(p,l)
    old.unlink();l['build']['bindings']['curation_sha256']='a'*64
    with pytest.raises(ValueError):C.validate_curation(p,l)
    l['build']['bindings']['curation_sha256']=None;p['schema_version']='fleet-pre-source-continuation/v1'
    with pytest.raises(ValueError):C.validate_curation(p,l)


def test_actual_remediation_plan_and_only_selected_code_delta(tmp_path):
    import xl_volatile_formula_remediation as M
    source=tmp_path/'source.xlsx';write(source,members());plan=C.keep(tmp_path/'plan.json',M.build_plan(source))
    root=tmp_path/'code';root.mkdir();selected=root/'xl_volatile_formula_remediation.py';selected.write_bytes(Path(M.__file__).read_bytes())
    spec=importlib.util.spec_from_file_location('selected_remediator_fixture',selected);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original=sys.modules['xl_volatile_formula_remediation'];sys.modules['xl_volatile_formula_remediation']=module
    try:
        before={'xl_volatile_formula_remediation.py':{'sha256':'a'*64},'.pipeline-code.json':{'sha256':'b'*64},'source.py':{'sha256':'c'*64}}
        after={**before,'xl_volatile_formula_remediation.py':C.binding(selected),'.pipeline-code.json':{'sha256':'d'*64}}
        p={'source':{'path':str(source)},'remediation':{'module':C.binding(selected),'plan':plan}}
        C.validate_remediation(p,{}, {'bindings':before},{'root':str(root),'bindings':after})
        changed=copy.deepcopy(after);changed['source.py']['sha256']='e'*64
        with pytest.raises(ValueError,match='only the remediator'):C.validate_remediation(p,{}, {'bindings':before},{'root':str(root),'bindings':changed})
        v=members();v['xl/worksheets/sheet1.xml']=v['xl/worksheets/sheet1.xml'].replace(b'1+2',b'Unused');write(source,v)
        with pytest.raises(module.RemediationError):C.validate_remediation(p,{}, {'bindings':before},{'root':str(root),'bindings':after})
    finally:sys.modules['xl_volatile_formula_remediation']=original
