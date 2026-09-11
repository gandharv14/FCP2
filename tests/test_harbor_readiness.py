import dataclasses
import json
from pathlib import Path
import shutil
import pytest
from openpyxl import Workbook
import xl_output_task as builder
import xl_release_publication as release
from scripts.batch import harbor_readiness as r

ROOT=Path(builder.__file__).parent


def make_grader(tmp_path):
    source=tmp_path/'0001.xlsx';baseline=tmp_path/'0001-inputs.xlsx'
    wb=Workbook();wb.active['A1']='Revenue';wb.active['B1']=6;wb.save(source);wb.active['B1']=None;wb.save(baseline);wb.close()
    seg=tmp_path/'seg';seg.mkdir();(seg/'curation.toml').write_text('[[output]]\nband = "Sheet!B1"\nsheet = "Sheet"\nlabel = "Revenue"\nname = "Revenue"\ninclude = true\nscore = 10\n')
    stage=tmp_path/'0001-outputs';(stage/'tests').mkdir(parents=True)
    outputs,targets=builder.collect('0001',tmp_path,tmp_path,tmp_path,seg_dir=seg)
    key={'kind':'cell_value','tolerance':{'numeric_abs':1e-6,'numeric_rel':1e-6},'targets':targets,'groups':{o['band']:o['refs'] for o in outputs}}
    (stage/'tests/answer_key.json').write_text(json.dumps(key));(stage/'tests/outputs.json').write_text(json.dumps(outputs))
    (stage/'tests/test.sh').write_text(builder.TEST_SH)
    shutil.copyfile(ROOT/'grader/run_grader.py',stage/'tests/run_grader.py')
    shutil.copytree(ROOT/'grader/finance_grader',stage/'tests/finance_grader',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    return stage,source,seg,baseline


def test_grader_uses_current_code_exact_targets_and_existing_tolerances(tmp_path):
    stage,source,seg,baseline=make_grader(tmp_path);before=r.tree(stage)
    assert r.grader_content(stage,'0001',source,seg,baseline,root=ROOT)=={'targets':1,'groups':1,'current_grader_content':True}
    assert r.tree(stage)==before


@pytest.mark.parametrize('change',['target','omitted_target','added_target','abs_tolerance','rel_tolerance','groups','kind','outputs','launcher','runner','package','extra_file','baseline_answer'])
def test_grader_content_rejects_answer_tolerance_or_wiring_drift(tmp_path,change):
    stage,source,seg,baseline=make_grader(tmp_path);file=stage/'tests/answer_key.json';key=json.loads(file.read_text())
    if change=='target':key['targets']['Sheet!B1']=7
    elif change=='omitted_target':key['targets']={}
    elif change=='added_target':key['targets']['Sheet!A1']=3
    elif change=='abs_tolerance':key['tolerance']['numeric_abs']=0.1
    elif change=='rel_tolerance':key['tolerance']['numeric_rel']=0.1
    elif change=='groups':key['groups']={}
    elif change=='kind':key['kind']='other'
    elif change=='outputs':(stage/'tests/outputs.json').write_text('[]')
    elif change=='launcher':(stage/'tests/test.sh').write_text('exit 0\n')
    elif change=='runner':(stage/'tests/run_grader.py').write_text('print(1.0)\n')
    elif change=='package':next((stage/'tests/finance_grader').glob('*.py')).write_text('changed')
    elif change=='extra_file':(stage/'tests/finance_grader/answer.json').write_text('{}')
    elif change=='baseline_answer':
        wb=Workbook();wb.active['B1']=6;wb.save(baseline);wb.close()
    file.write_text(json.dumps(key))
    with pytest.raises((ValueError,SystemExit)):r.grader_content(stage,'0001',source,seg,baseline,root=ROOT)


def test_missing_runtime_evidence_never_inferred_from_handoff_flags(tmp_path):
    stage,source,seg,baseline=make_grader(tmp_path)
    selection=r.Selection('0001',stage,release._tree_hash(stage)[0],{'source_file':r.record(source),'segmentation_generation':{'path':str(seg)},'baseline_inputs':r.record(baseline),'passed':True,'docker_smoke':True,'grader_smoke_score':1.0},tmp_path/'normalization',tmp_path/'naturalization',{}, {})
    before=r.tree(stage);result=r.inspect_readiness(selection,root=ROOT)
    assert result['publication_ready'] is False
    assert result['checks']['grader_content']['checked'] is True
    assert set(r.UNIMPLEMENTED).issubset(result['blockers'])
    assert r.tree(stage)==before


def test_stage_drift_rejected_before_gate_execution(tmp_path):
    stage,source,seg,baseline=make_grader(tmp_path)
    selection=r.Selection('0001',stage,'0'*64,{},tmp_path,tmp_path,{}, {})
    with pytest.raises(ValueError,match='stage hash changed'):r.inspect_readiness(selection,root=ROOT)


def test_unsafe_tree_is_not_an_artifact_hash(tmp_path):
    (tmp_path/'link').symlink_to(ROOT/'README.md')
    with pytest.raises(ValueError,match='symlink'):r.tree(tmp_path)


def test_skill_validator_uses_current_fixed_repository_module():
    module=r.skill_module(ROOT,'naturalize-finance-task-instruction','validate_instruction_rewrite')
    from test_instruction_naturalization import SOURCE,CANDIDATE
    report=module.validate(SOURCE,CANDIDATE)
    assert report['valid'] is True
    with pytest.raises(Exception):module.validate(SOURCE,CANDIDATE.replace('without rounding','rounded to 2 decimals'))


@pytest.mark.parametrize('change',[None,'server','dockerfile','runtime','compose','leaked_eval'])
def test_packaged_transport_matches_current_generator_and_exact_build(tmp_path,change):
    from mcp_env.server_assets import SERVER_PY,SIDECAR_DOCKERFILE
    build=tmp_path/'build';(build/'runtime').mkdir(parents=True)
    (build/'server.py').write_text(SERVER_PY);(build/'Dockerfile').write_text(SIDECAR_DOCKERFILE)
    (build/'runtime/data.json').write_text('{}')
    stage=tmp_path/'stage';sidecar=stage/'environment/mcp-server';sidecar.parent.mkdir(parents=True);shutil.copytree(build,sidecar)
    (stage/'environment/docker-compose.yaml').write_text(builder.COMPOSE_YAML)
    if change=='server':(build/'server.py').write_text('print("pass")')
    elif change=='dockerfile':(build/'Dockerfile').write_text('FROM scratch')
    elif change=='runtime':(sidecar/'runtime/data.json').write_text('[]')
    elif change=='compose':(stage/'environment/docker-compose.yaml').write_text('services: {}')
    elif change=='leaked_eval':(sidecar/'eval.json').write_text('{}')
    if change is None:assert r.mcp_transport_content(stage,build)=={'current_transport_content':True}
    else:
        with pytest.raises(ValueError):r.mcp_transport_content(stage,build)


@pytest.mark.parametrize('change',[None,'missing_root_binding','changed_context','changed_review','extra_context'])
def test_exact_retained_context_bindings_are_required(tmp_path,change):
    run=tmp_path/'run';nat=tmp_path/'nat';run.mkdir();nat.mkdir()
    (run/'normalized.json').write_text('{}');(nat/'state.json').write_text('{}')
    claims=tmp_path/'claims.json';claims.write_text('{}');review=tmp_path/'review.json';review.write_text('{}')
    bindings={str(p):release._tree_hash(p)[0] for p in (run,nat)}
    from scripts.batch.harbor_model_review_provenance import Call
    call=Call(r.record(claims),r.record(review),r.record(claims),r.record(review))
    selection=r.Selection('0001',tmp_path,'0'*64,{},run,nat,r.record(claims),r.record(review),context_trees=bindings,normalization_prepared=r.record(claims),normalization_call=call,naturalization_call=call,naturalization_writer_call=call,naturalization_request=r.record(claims),naturalization_writer_request=r.record(claims))
    if change=='missing_root_binding':bindings.pop(str(nat))
    elif change=='changed_context':(nat/'state.json').write_text('{"passed":true}')
    elif change=='changed_review':review.write_text('{"passed":true}')
    elif change=='extra_context':bindings[str(tmp_path)]='0'*64
    if change is None:assert r.context_bindings(selection)=={'bound_context_trees':2,'dialogue_scope_evidence':[]}
    else:
        with pytest.raises(ValueError):r.context_bindings(selection)
