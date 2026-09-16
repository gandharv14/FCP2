"""Validate a retained successful package command before continuing its gates.

No command, model, tracker or source write is performed. A stopped packaging
attempt is not a pass: every current static content check must still succeed.
The caller supplies an explicitly selected producer/validation implementation.
The explicit checkpoint operation writes a new continuation receipt only after
validation; the original environment gate still owns the lane transition.
"""
from dataclasses import replace
from pathlib import Path
import sys

from scripts.batch.harbor_admission import record, read, require
from scripts.batch.harbor_transition_codec import encode


def command_receipt(producer, content, profiles, work, taxonomy):
    work = Path(work)
    selected = record(work/'selection.json')
    descriptor = read(selected)
    require(descriptor == dict(schema_version='harbor-environment-production/v1',
        content=encode(content), profiles=encode(profiles), taxonomy=taxonomy,
        code=producer.code_binding(), mode='plain'),
        'retained package selection differs or requires unsupported MCP recovery')
    require(not (work/'complete.json').exists(),
            'package already completed; select its completion instead')
    require(work != content.stage and work not in content.stage.parents
            and content.stage not in work.parents and content.stage.name == content.workbook+'-outputs',
            'retained package stage identity differs')
    _, arguments = producer.packaging_arguments(content, work, taxonomy)
    intent = record(work/'package.intent.json')
    command = read(intent)
    require(command == dict(argv=[sys.executable, *map(str, arguments)],
        entry=record(arguments[0]), model_calls_permitted=False),
        'retained command is not the exact cached-audit-only package command')
    result = record(work/'package.result.json')
    require(read(result) == dict(returncode=0, log=record(work/'package.log')),
            'package command incomplete, failed or changed; never replay')
    for item in (taxonomy, selected, intent, result):
        producer.journal.checked_bytes(item)
    return dict(selection=selected, intent=intent, result=result,
                log=record(work/'package.log'))


def validate(producer, content, profiles, work, taxonomy):
    """Re-use command output only after full source/profile/content validation."""
    evidence = command_receipt(producer, content, profiles, work, taxonomy)
    producer.lanes.profile_context(profiles, content)
    producer.validate_profiles(profiles)
    require(producer.lanes.normalization_projection(content)['mode']=='plain',
            'retained package mode changed')
    readiness = producer.readiness
    before = readiness.publication._tree_hash(content.stage)
    current = replace(content, stage_sha256=before[0])
    readiness.generation_content(current)
    readiness.normalized_content(current)
    readiness.grader_content(current.stage, current.workbook,
        current.bindings['source_file']['path'],
        Path(current.bindings['segmentation_generation']['path']),
        current.bindings['baseline_inputs']['path'])
    readiness.environment_content(current, 'plain')
    require(readiness.publication._tree_hash(current.stage)==before,
            'retained stage changed during independent static validation')
    require(command_receipt(producer, content, profiles, work, taxonomy)==evidence,
            'retained command evidence changed during validation')
    return current, evidence


def checkpoint(coordinator, refresh, descriptor, prior, *, authority, directory):
    """Materialize an environment checkpoint from one completed package command.

    This runs inside the original step guard and workbook locks. It neither
    packages again nor completes a tracker lane. An interrupted write remains
    single-use and requires explicit reconciliation.
    """
    worker, _, _ = coordinator.selected(descriptor)
    value, lease = coordinator._prior(prior, descriptor)
    declared = read(descriptor)
    require(value['status']=='IMPORTED' and value['step']=='normalization_phase2'
            and declared.get('refresh') is not None,
            'exact imported complete phase2 prefix required')
    W = coordinator.worker
    _, before = W._active(worker, lease)
    prepared, selection = refresh.validate(worker, declared['refresh'], before)
    require(prepared['last']=='normalization_phase2'
            and coordinator.content_codec.encode(selection)==value['selection'],
            'package recovery changed refreshed content')
    policy = read(authority)
    require(set(policy)=={'schema_version','helper','runner','release','original_release',
                          'policy','work','selection','command','result','log','failure'}
            and policy['schema_version']=='fleet-completed-package-selection/v1'
            and policy['helper']==record(__file__), 'closed package recovery authority required')
    work = Path(policy['work'])
    require(work==worker.run/'packaging', 'foreign retained package directory')
    receipt = command_receipt(W.environment, selection.content,
                             selection.public_profiles, work, worker.taxonomy)
    require(receipt=={k:policy['command' if k=='intent' else k]
                     for k in ('selection','intent','result','log')},
            'selected package command receipt changed')
    continuation = Path(declared['refresh']['path']).parent/'continuation'
    directory = Path(directory)
    require(directory==continuation and not (directory/'environment.json').exists()
            and not (directory/'package-completion.json').exists(),
            'package continuation already attempted or foreign')
    current, evidence = validate(W.environment, selection.content,
                                selection.public_profiles, work, worker.taxonomy)
    require(W.environment.readiness.publication._tree_hash(current.stage)[0]
            ==current.stage_sha256, 'package changed before checkpoint')
    directory.mkdir(exist_ok=True,mode=0o700)
    completion = W.keep(directory/'package-completion.json', dict(
        schema_version='harbor-retained-package-completion/v1', authority=authority,
        descriptor=descriptor, checkpoint=prior, refresh=declared['refresh'],
        evidence=evidence, stage_sha256=current.stage_sha256,
        command_repeated=False, model_calls=0, tracker_mutations=0))
    return W.keep(directory/'environment.json', dict(
        selection=coordinator.content_codec.encode(replace(selection,content=current)),
        evidence=[authority,completion,*evidence.values()]))
