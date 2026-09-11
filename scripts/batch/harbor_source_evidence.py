"""Read-only validation of completed trusted source execution and its lineage.

Request expiry limits execution, not later inspection of an immutable result.
The trust anchor is supplied by the root coordinator, never by task evidence.
"""
import json
from pathlib import Path, PurePosixPath
import re
import time
import uuid

from scripts.batch.harbor_admission import require
from scripts.corpus import digest
from scripts.corpus_lineage import validate_generation_lineage
from xl_source_health import inspect_workbook
from xl_source_recalc import POLICY_VERSION


def validate_source_lineage(original, generation, normalizations=(), *,
                            trusted_public_key='/etc/harbor/excel-runner-public.pem'):
    generation = Path(generation)
    # This independently verifies actual transformations, signed request/source/
    # output bindings, completion window, and complete workbook semantic diff.
    lineage = validate_generation_lineage(original, generation, normalizations,
                                          trusted_public_key=trusted_public_key)
    if lineage['mode'] != 'recalculation':
        return lineage
    request = json.loads((generation / 'request.json').read_text())
    result = json.loads((generation / 'result.json').read_text())
    manifest = json.loads((generation / 'generation-manifest.json').read_text())
    candidate = generation / manifest['layout']['source_workbook']
    require(request['policy_version'] == result['policy_version'] == POLICY_VERSION,
            'completed recalculation policy changed')
    require(result['schema_version'] == 'source-recalc-result/v2', 'unsupported result schema')
    require(str(uuid.UUID(request['request_id'])) == request['request_id']
            and result['request_id'] == request['request_id'], 'request identity changed')
    policy = request['policy']
    require(isinstance(policy, dict)
            and set(policy) == {'allow_cache_only_changes', 'require_semantic_equivalence'}
            and all(value is True for value in policy.values()),
            'unsupported recalculation policy constraints')
    source_record = request['source']
    source = Path(source_record['path'])
    require(source.is_absolute() and not source.is_symlink() and source.is_file(),
            'request source must remain an actual regular file')
    size = source.stat().st_size
    require(digest(source) == source_record['sha256'] == request['source_sha256'],
            'request source record changed')
    require(type(source_record['size_bytes']) is int
            and type(request['source_size_bytes']) is int
            and source_record['size_bytes'] == request['source_size_bytes'] == size,
            'request source size changed')
    require(type(request['max_source_size_bytes']) is int
            and 0 <= size <= request['max_source_size_bytes'], 'source exceeds requested size')
    require(type(result['output_size_bytes']) is int
            and result['output_size_bytes'] == candidate.stat().st_size,
            'completed output size changed')
    relative = PurePosixPath(request['destination_relative'])
    destination = Path(result['destination'])
    require(not relative.is_absolute() and relative.parts
            and all(p not in {'.', '..'} for p in relative.parts)
            and str(relative) == request['destination_relative']
            and destination.is_absolute()
            and destination.parts[-len(relative.parts):] == relative.parts
            and destination.resolve() != source.resolve(), 'completed destination changed')
    timestamps = [request['created_at_ns'], result['started_at_ns'],
                  result['completed_at_ns'], request['expires_at_ns']]
    require(all(type(t) is int for t in timestamps)
            and 0 < timestamps[0] <= timestamps[1] <= timestamps[2] <= timestamps[3]
            and 0 < timestamps[3] - timestamps[0] <= 7 * 86400 * 10**9
            and timestamps[2] <= time.time_ns() + 60 * 10**9,
            'completed execution is outside the request window')
    constraints = request['engine_constraints']
    require(constraints.get('require_authoritative') is True
            and constraints.get('require_capability_check') is True
            and constraints.get('allowed_engines') == ['excel-macos']
            and constraints.get('required_engine') == result['engine'] == 'excel-macos',
            'completed engine violates request constraints')
    versions = constraints.get('permitted_versions')
    require(isinstance(versions, list) and bool(versions)
            and all(isinstance(v, str) and re.fullmatch(r'\d+\.\d+(?:\.\d+){0,2}', v)
                    for v in versions)
            and len(set(versions)) == len(versions)
            and result['engine_version'] in versions,
            'completed version violates concrete request constraints')
    evidence = result['engine_evidence']
    signed = evidence['runner_receipt']['signed_payload']
    controls = ('dedicated_session', 'network_disabled', 'macros_disabled',
                'add_ins_disabled', 'link_updates_disabled', 'prompts_suppressed')
    require(all(evidence.get('controls', {}).get(k) is True
                and signed['isolation_controls'].get(k) is True for k in controls),
            'completed isolation controls contradict signed execution')
    health = inspect_workbook(source)
    require(health['route'] in {'pass', 'recalc_candidate', 'restricted_pass',
                                'restricted_recalc_pass'},
            'ordinary recalculation source is outside the current supported profile')
    return dict(lineage, completed_request_constraints=True,
                request_id=request['request_id'], engine_version=result['engine_version'])
