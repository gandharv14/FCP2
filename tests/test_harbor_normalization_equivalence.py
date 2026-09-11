import copy
import importlib.util
import json
from pathlib import Path
import pytest
from scripts.batch import harbor_normalization_equivalence as equivalence
from scripts.batch.harbor_normalization_equivalence import expected_outputs,validate_emission
from scripts.corpus import digest

import gen_normalizer as generator


def fixture(tmp_path,eligible=True,multi=False):
    run=tmp_path/'0001-variable-sources';run.mkdir()
    row={'draft_id':'r1','variable_text':'Revenue assumption','source_notes':'Supplied workbook scenario','source_urls':[]}
    draft={'rows':[row]};(run/'draft.json').write_text(json.dumps(draft))
    review={'schema_version':'normalization-review/v1','reviewer':'independent fixture','cells':{'Sheet!B1':{'eligible':eligible,'reason':'explicit assumption fixture','evidence_refs':['Sheet!B1'],'entity':'Fixture','period':'2025','scenario':'Base','basis':'nominal'}}}
    (run/'normalization-review.json').write_text(json.dumps(review))
    cells=[('Sheet','B1',12),('Sheet','C1',13)] if multi else [('Sheet','B1',12)]
    rows=[{'clean':True,'row':row,'reference':'Sheet!B1','cells':cells,'extra':[],'duplicate_refs':[],'evidence':{},'evidence_paths':{}}]
    generator.emit('0001',draft,copy.deepcopy(rows),runs_root=tmp_path,pipeline_bindings={'source_generation_id':'f'*64})
    namespace={'__name__':'fixture_generated','__file__':str(run/'normalize_0001.py')}
    exec(compile((run/'normalize_0001.py').read_text(),'<fixture normalizer>','exec'),namespace);namespace['main']()
    return run,draft,rows


@pytest.mark.parametrize('eligible,multi',[(True,False),(True,True),(False,False)])
def test_pure_projection_equals_actual_supported_emission_without_any_writes(tmp_path,eligible,multi):
    run,draft,rows=fixture(tmp_path,eligible,multi)
    before={str(p):digest(p) for p in run.iterdir() if p.is_file()}
    result=validate_emission('0001',draft,rows,run,{'source_generation_id':'f'*64})
    assert result['atomic_variables']==(2 if multi else 1) if eligible else result['atomic_variables']==0
    assert {str(p):digest(p) for p in run.iterdir() if p.is_file()}==before


@pytest.mark.parametrize('change',['value','unit','status','variable_omission','disposition','exclusion','program','review','first_missing'])
def test_altered_normalization_or_evidence_is_rejected(tmp_path,change):
    run,draft,rows=fixture(tmp_path)
    if change=='program':(run/'normalize_0001.py').write_text('raise RuntimeError("never execute retained program")')
    elif change=='review':
        path=run/'normalization-review.json';v=json.loads(path.read_text());v['cells']['Sheet!B1']['eligible']=False;path.write_text(json.dumps(v))
    elif change=='first_missing':(run/'first_normalization.json').unlink()
    else:
        name='normalization_report.json' if change=='disposition' else 'exclusions.json' if change=='exclusion' else 'normalized.json'
        path=run/name;v=json.loads(path.read_text())
        if change in ('value','unit','status'):v['variables'][0][change]=999 if change=='value' else 'forged'
        elif change=='variable_omission':v['variables']=[]
        elif change=='disposition':v['dispositions']=[]
        elif change=='exclusion':v['exclusions']=[{'draft_id':'r1','reason':'fake'}]
        path.write_text(json.dumps(v))
    with pytest.raises((ValueError,SystemExit)):validate_emission('0001',draft,rows,run,{'source_generation_id':'f'*64})
