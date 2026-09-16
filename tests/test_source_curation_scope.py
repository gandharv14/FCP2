import copy,tomllib
import pytest
from scripts.fleet.source_curation_scope import derive


def fixture():
    def cycle(seed,certified):return dict(seed=seed,member_count=1,members=[seed],output_relevant=True,certified=certified,converged=True,uniqueness='not_disproven'if certified else'demonstrated_non_unique',errors=0,unresolved=0,budget_exhausted=False,topology_stable=True,targets_stable=True)
    d=dict(outputs=[dict(band='Model!A1',cells=['Model!A1']),dict(band='Model!B1',cells=['Model!B1'])],proof=dict(closure=dict(stabilized=True,targets_stable=True),runtime_radj={'Model!A1':['Model!C1'],'Model!C1':['Model!C1'],'Model!B1':['Model!D1'],'Model!D1':['Model!D1']}),verification=dict(status='fail',passed=False,blocking_reasons=['active_cycle_demonstrated_non_unique','active_cycle_uncertified'],ordered_output_cells=['Model!A1','Model!B1'],outputs=dict(match=2,mismatch=0,unresolved=0,unverifiable=0),provenance=dict(proof=dict(strict=True,expected_cache_reads=0),cycles=dict(proof_cycle_groups=[['Model!C1'],['Model!D1']])),seeded_inside_output_cone_count=0,oracle_fallback_inside_output_cone_count=0,iterative_blocks=[cycle('Model!C1',False),cycle('Model!D1',True)],fingerprints=dict(selected_output_cells={'sha256':'original'})))
    text='# Preserve the source-selected unit names.\n[[output]]\nband = "Model!A1"\nname = "Cash flow"\ninclude = true\n\n[[output]]\nband = "Model!B1"\nname = "Operating profit"\ninclude = true\n'
    return d,text


def test_only_exact_dependent_band_changes_and_certified_cycle_is_kept():
    d,text=fixture();old=copy.deepcopy(d);new,proof=derive(d,text)
    assert new==text.replace('include = true','include = false',1)
    assert proof['selected_targets']==['Model!B1']and proof['retained_certified_cycles']==1
    assert proof['excluded_targets']==[dict(target='Model!A1',paths=[dict(cycle=0,path=['Model!A1','Model!C1'])])]
    assert proof['source_qualified']is False and proof['new_source_validation_required']is True
    assert d==old


def test_transitive_dependency_cannot_escape_exclusion():
    d,text=fixture();d['proof']['runtime_radj']['Model!A1']=['Model!E1'];d['proof']['runtime_radj']['Model!E1']=['Model!C1']
    _,p=derive(d,text);assert p['excluded_targets'][0]['paths'][0]['path']==['Model!A1','Model!E1','Model!C1']

@pytest.mark.parametrize('mutation',[
 lambda d:d['proof']['closure'].update(stabilized=False),
 lambda d:d['verification']['outputs'].update(mismatch=1),
 lambda d:d['verification'].update(blocking_reasons=['unknown_function']),
 lambda d:d['verification']['provenance']['proof'].update(expected_cache_reads=1),
 lambda d:d['verification'].update(seeded_inside_output_cone_count=1),
 lambda d:d['verification']['iterative_blocks'].pop(),
 lambda d:d['verification']['iterative_blocks'][0].update(member_count=2),
 lambda d:d['verification']['provenance']['cycles']['proof_cycle_groups'].append(['Model!C1']),
 lambda d:d['proof']['runtime_radj']['Model!B1'].append('Model!C1'),
])
def test_uncertain_original_proof_or_no_independent_scope_refuses(mutation):
    d,text=fixture();mutation(d)
    with pytest.raises(ValueError):derive(d,text)


def test_partial_band_cannot_silently_drop_valid_targets():
    d,text=fixture();d['outputs']=[dict(band='Model!A1:B1',cells=['Model!A1','Model!B1'])]
    text='[[output]]\nband="Model!A1:B1"\ninclude=true\n'
    with pytest.raises(ValueError,match='partial output-band'):derive(d,text)


def test_unrelated_curation_cannot_be_edited():
    d,text=fixture();text=text.replace('Model!B1','Model!Z1')
    with pytest.raises(ValueError,match='curation differs'):derive(d,text)
