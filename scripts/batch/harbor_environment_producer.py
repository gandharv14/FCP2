"""Deterministic post-profile emission and cached-audit-only Harbor packaging.

Run inside the root's bounded workbook worker. This module grants no lane PASS,
creates no tracker or release, and never invokes a model or a container. Retain
failed attempt directories; a partial command is not a validated checkpoint.
"""
from dataclasses import replace
from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import uuid

from scripts.batch import harbor_model_executor as journal
from scripts.batch import harbor_normalization_producer as normalization
from scripts.batch import harbor_readiness as readiness
from scripts.batch import harbor_completed_lanes as lanes
from scripts.batch.harbor_admission import record, read, require
from scripts.batch.harbor_profile_readiness import validate_profiles
from scripts.batch.harbor_transition_codec import encode

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ('normalized.json', 'exclusions.json', 'normalization_report.json')


def code_binding():
    return record(Path(__file__))


def wire_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)+'\n').encode()


def finish_normalization(content, profiles, output, *, fault_hook=lambda _: None):
    """Snapshot phase1 before the fixed current generator's catalog insertion.

    Interrupted replacement may resume only when every current file is exactly
    its captured phase1 bytes or its independently regenerated phase2 bytes.
    The frozen phase1, original atomic count and review journals never change.
    """
    output=journal.safe(output);run=journal.safe(content.normalization_run)
    require(output != run and run not in output.parents and output not in run.parents,
            'phase2 journal and normalization run must be disjoint')
    lanes.profile_context(profiles,content)
    validation=validate_profiles(profiles)
    require(validation['confidence']=='high','source profile is not independently accepted')
    b=content.bindings
    for key in ('normalization_draft','normalized_phase1','source_file','baseline_inputs'):
        record_=b[key];journal.checked_bytes(record_)
    inspection = content
    if output.exists():
        # A prior replacement may already contain phase2 bytes. Recheck the
        # current review and frozen phase1 projection, then constrain each byte
        # below to the separately retained old/new sets before resuming writes.
        present = copy.deepcopy(b)
        for key,name in [('normalized_final','normalized.json'),('normalization_report','normalization_report.json')]:
            present[key] = record(run/name)
        inspection = replace(content,bindings=present)
    lanes.normalization_projection(inspection,phase1=True)
    draft,rows=normalization.gen.analyse(content.workbook,b['source_file']['path'],b['source_file']['sha256'],runs_root=run.parent,inputs_root=Path(b['baseline_inputs']['path']).parent)
    pair=readiness.publication.resolve_build_context(content.workbook,source_root=Path(b['source_generation']['path']).parent.parent,source_generation_id=b['source_generation']['id'],segmentation_root=Path(b['segmentation_generation']['path']).parent.parent,segmentation_generation_id=b['segmentation_generation']['id'])
    script,expected=normalization.expected_outputs(content.workbook,draft,rows,run,pair['bindings'])
    again_script,again=normalization.expected_outputs(content.workbook,draft,rows,run,pair['bindings'])
    require(script==again_script and expected==again,'phase2 emission is nondeterministic')
    require((run/f'normalize_{content.workbook}.py').read_text()==script,'normalizer no longer matches current generator')
    after={name:wire_bytes(expected[run/name]) for name in OUTPUTS}
    frozen={key:b[key] for key in ('normalized_phase1','normalization_draft','source_file','baseline_inputs','curation')}
    descriptor=dict(schema_version='harbor-normalization-phase2/v1',workbook=content.workbook,code=code_binding(),
        frozen=frozen,profiles=encode(profiles),pipeline_bindings=pair['bindings'],
        expected={name:journal.sha(raw) for name,raw in after.items()})
    if not output.exists():
        output.mkdir(parents=True,mode=0o700)
        for name in OUTPUTS:journal.retain(output/name,(run/name).read_bytes())
        journal.save(output/'selection.json',descriptor)
    else:
        require(read(record(output/'selection.json'))==descriptor,'phase2 selection changed or preparation incomplete')
    for name in OUTPUTS:
        old=journal.checked_bytes(record(output/name));current=(run/name).read_bytes()
        # The baseline snapshot itself is tied to the exact phase1 gate binding.
        key={'normalized.json':'normalized_final','exclusions.json':'normalization_exclusions','normalization_report.json':'normalization_report'}[name]
        require(journal.sha(old)==b[key]['sha256'],'phase1 snapshot differs from selected evidence')
        require(current in (old,after[name]),'phase2 encountered changed/unknown intermediate output')
        if current != after[name]:
            temp=run/('.'+name+'.'+str(uuid.uuid4())+'.tmp')
            journal.retain(temp,after[name]);os.replace(temp,run/name);journal.sync_dir(run)
        fault_hook(name)
    for binding in frozen.values():journal.checked_bytes(binding)
    bindings=copy.deepcopy(b)
    for key,name in [('normalized_final','normalized.json'),('normalization_exclusions','exclusions.json'),('normalization_report','normalization_report.json')]:bindings[key]=record(run/name)
    bindings.update(source_profiles=profiles.profiles,source_profile_mapping=profiles.mapping)
    result=replace(content,bindings=bindings)
    lanes.normalization_projection(result)
    receipt=dict(schema_version='harbor-normalization-phase2-complete/v1',selection=record(output/'selection.json'),outputs={name:record(run/name) for name in OUTPUTS},lane_approval=False,model_calls=0)
    normalization.keep(output/'complete.json',receipt)
    return result,record(output/'complete.json')


def command(work,label,argv):
    """One fresh fixed CLI invocation; partial writes are retained, never replayed."""
    require(not (work/(label+'.intent.json')).exists(),'command already attempted; inspect retained result before a fresh attempt')
    require(sys.version_info[:2]==(3,12),'pipeline requires persistent Python3.12')
    argv=[str(x) for x in argv]
    require(Path(argv[0]).parent==ROOT,'only fixed installed pipeline entry points may run')
    journal.save(work/(label+'.intent.json'),{'argv':[sys.executable,*argv],'entry':record(argv[0]),'model_calls_permitted':False})
    environment=dict(os.environ);environment['PYTHONDONTWRITEBYTECODE']='1'
    with (work/(label+'.log')).open('xb') as log:
        result=subprocess.run([sys.executable,*argv],cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=900)
        log.flush();os.fsync(log.fileno())
    journal.save(work/(label+'.result.json'),dict(returncode=result.returncode,log=record(work/(label+'.log'))))
    require(result.returncode==0,'packaging command failed: '+label)


def packaging_arguments(content, work, taxonomy):
    """Closed CLI arguments always pin the source pair and require cached audits."""
    b=content.bindings;wb=content.workbook
    source=Path(b['source_generation']['path']);seg=Path(b['segmentation_generation']['path'])
    common=['--source',Path(b['source_file']['path']).parent,'--segmentation-mode','strict',
        '--source-generation-root',source.parents[2],'--source-generation-id',source.name,
        '--segmentation-generation-id',seg.name]
    args=[ROOT/'xl_output_task.py',wb,*common,'--seg-root',seg.parents[2],
        '--inputs-root',work/'inputs' if content.mcp_build_a else Path(b['baseline_inputs']['path']).parent,
        '--variable-source-audit-inputs-root',Path(b['baseline_inputs']['path']).parent,
        '--variable-source-audit-root',Path(b['variable_source_audit']['path']).parent.parent,
        '--variable-source-audit-model',content.model_policy.model,'--project-id',content.model_policy.project_id,
        '--normalization-root',content.normalization_run.parent,'--taxonomy',taxonomy['path'],
        '--require-cached-variable-source-audit','--no-naturalize','-o',content.stage.parent]
    if content.mcp_build_a:args += ['--mcp',content.mcp_build_a]
    if b.get('audit_equivalence'):args += ['--audit-equivalence-receipt',b['audit_equivalence']['path']]
    elif b.get('audit_validator_upgrade'):
        require(b.get('audit_upgrade_original_code') is not None,'direct audit upgrade lacks original code')
        for key in ('audit_validator_upgrade','audit_upgrade_original_code'):
            journal.checked_bytes(b[key])
        args += ['--audit-validator-upgrade-receipt',b['audit_validator_upgrade']['path'],
                 '--audit-upgrade-original-code',b['audit_upgrade_original_code']['path']]
    return common,args


def package(content,profiles,work,taxonomy):
    """Build once into an isolated stage, then run current static owning checks."""
    work=journal.safe(work);stage=journal.safe(content.stage)
    require(work != stage and work not in stage.parents and stage not in work.parents,
            'packaging journal and task stage must be disjoint')
    require(stage.name==content.workbook+'-outputs','stage workbook identity differs')
    require(not work.exists() and not stage.exists(),'fresh packaging attempt/stage required; preserve previous artifacts')
    journal.checked_bytes(taxonomy)
    lanes.profile_context(profiles,content);validate_profiles(profiles)
    mode=lanes.normalization_projection(content)['mode']
    content=replace(content,mcp_build_a=work/'build-a' if mode=='mcp' else None,mcp_build_b=work/'build-b' if mode=='mcp' else None)
    descriptor=dict(schema_version='harbor-environment-production/v1',content=encode(content),profiles=encode(profiles),taxonomy=taxonomy,code=code_binding(),mode=mode)
    work.mkdir(parents=True,mode=0o700);journal.save(work/'selection.json',descriptor)
    if mode=='mcp':
        for label,directory in [('build-a',content.mcp_build_a),('build-b',content.mcp_build_b)]:
            command(work,label,[ROOT/'xl_variable_mcp.py','build',content.bindings['normalized_final']['path'],directory,'--workbook',content.workbook,'--source',Path(content.bindings['source_file']['path']).parent])
        require(readiness.tree(content.mcp_build_a)==readiness.tree(content.mcp_build_b),'MCP build is not deterministic')
    common,args=packaging_arguments(content,work,taxonomy)
    if mode=='mcp':
        seg=Path(content.bindings['segmentation_generation']['path'])
        command(work,'mask',[ROOT/'xl_input_mask.py',content.workbook,*common,'--seg-dir',seg.parents[2],'-o',work/'inputs','--mask-cells',content.mcp_build_a/'mask_cells.json'])
    command(work,'package',args)
    content=replace(content,stage_sha256=readiness.publication._tree_hash(stage)[0])
    readiness.generation_content(content)
    readiness.normalized_content(content)
    readiness.grader_content(stage,content.workbook,content.bindings['source_file']['path'],Path(content.bindings['segmentation_generation']['path']),content.bindings['baseline_inputs']['path'])
    readiness.environment_content(content,mode)
    journal.checked_bytes(taxonomy)
    journal.save(work/'complete.json',dict(schema_version='harbor-environment-production-complete/v1',selection=record(work/'selection.json'),stage_sha256=content.stage_sha256,mode=mode,model_calls=0,container_jobs=0,lane_approval=False))
    return content,record(work/'complete.json')
