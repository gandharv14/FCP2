"""Full original numerical/table gates followed by a separately verified rewrite."""
import copy,re,zipfile
from pathlib import Path
import pytest
from test_presentation_tables import source_fixture
from xl_presentation_tables import materialize,verify_materialization,composable_source,COMPOSED_POLICY
from xl_source_health import inspect_workbook
from xl_volatile_formula_remediation import canonical_bytes,sha256_bytes


def fixture(tmp_path,**kwargs):
    source,curation=source_fixture(tmp_path,**kwargs)
    with zipfile.ZipFile(source)as z:parts={n:z.read(n)for n in z.namelist()}
    xml=parts['xl/workbook.xml'].decode();xml=re.sub(r'<definedNames\s*/>','<definedNames><definedName name="DeadExternal">[1]Gone!$A$1</definedName></definedNames>',xml)
    assert 'DeadExternal' in xml
    parts['xl/workbook.xml']=xml.encode()
    with zipfile.ZipFile(source,'w')as z:
        for n,v in parts.items():z.writestr(n,v)
    return source,curation


def seal(receipt):
    receipt['manifest_sha256']=sha256_bytes(canonical_bytes({k:v for k,v in receipt.items()if k!='manifest_sha256'}))


def test_full_original_gates_then_remediation_and_tamper(tmp_path):
    source,curation=fixture(tmp_path);original=source.read_bytes();health=inspect_workbook(source)
    assert composable_source(health,source)
    from scripts.fleet.source_first import eligible_source
    assert eligible_source(health,source)=='verified_table_remediation_plan'
    candidate,receipt=materialize(source,'0001',curation,tmp_path/'chain',remediate=True)
    assert receipt['policy_version']==COMPOSED_POLICY and receipt['ordered_outputs']==['S!B1']
    assert source.read_bytes()==original and inspect_workbook(candidate)['route']=='pass'
    assert verify_materialization(source,candidate,curation,receipt)['status']=='verified'
    from xl_source_inventory import transformed_workbook_record,assemble_inventory_manifest,validate_inventory_manifest
    record=transformed_workbook_record('0001',source,candidate,curation,receipt)
    assert record['transformation']['receipt']==receipt
    for key in ('intermediate_sha256','intermediate'):
        changed=copy.deepcopy(receipt);changed['remediation'][key]='bad';seal(changed)
        with pytest.raises((ValueError,FileNotFoundError)):
            verify_materialization(source,candidate,curation,changed)
    changed=copy.deepcopy(receipt);changed['remediation']['manifest']['changed_members']={};seal(changed)
    with pytest.raises(ValueError):verify_materialization(source,candidate,curation,changed)
    changed=copy.deepcopy(receipt);changed['policy_version']='presentation-data-tables/v2';seal(changed)
    with pytest.raises(ValueError):verify_materialization(source,candidate,curation,changed)
    intermediate=Path(receipt['remediation']['intermediate']);intermediate.write_bytes(intermediate.read_bytes()+b'tampered')
    with pytest.raises(ValueError):verify_materialization(source,candidate,curation,receipt)


@pytest.mark.parametrize('kwargs',[{'answer_in_table':True},{'in_cone':True}])
def test_composition_never_bypasses_target_or_leak_proof(tmp_path,kwargs):
    source,curation=fixture(tmp_path,**kwargs)
    with pytest.raises(ValueError):materialize(source,'0001',curation,tmp_path/'chain',remediate=True)
    assert not (tmp_path/'chain/0001.xlsx').exists()


def test_composition_must_be_explicit_and_cannot_waive_other_policy(tmp_path):
    source,curation=fixture(tmp_path)
    with pytest.raises(ValueError,match='still requires source support'):
        materialize(source,'0001',curation,tmp_path/'old')
    health=inspect_workbook(source)
    for reason in ('manual_calculation_mode','vba_project_present','unknown'):
        changed=copy.deepcopy(health);changed['reason_codes'].append(reason)
        assert not composable_source(changed,source)
