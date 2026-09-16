"""Prompt-only task revisions; staging/validation never changes the base campaign.

Remote mutations occur only in provision() and launch_corrections(). Activation is
an explicit, separate local action after every staged version has been verified.
"""
from __future__ import annotations
import argparse
import ast
import copy
import re
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import recursion_campaign as c

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from grader.finance_grader.core import grade_discrete
WORK = ROOT / 'reports/recursion/gemini-high-score-audit-20260915/refactor'
IDENTITY_FIELDS = {'id', 'version', 'createdAt', 'updatedAt', 'lockedAt', 'prompt'}
MODELS = ('opus', 'gemini')
TERMINAL = {'completed', 'failed', 'cancelled', 'canceled'}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def revision_dir(work):
    return Path(work) / 'validity-fixes'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same_behavior(base, revised):
    require(revised.get('lockedAt'), 'Corrected version must be locked')
    before = {k: v for k, v in base.items() if k not in IDENTITY_FIELDS}
    after = {k: v for k, v in revised.items() if k not in IDENTITY_FIELDS}
    require(before == after, 'Copied version changed non-prompt behavior')


def literal_arithmetic(formula):
    """Evaluate source constant expressions without arbitrary Python execution."""
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div): return left / right
            if isinstance(node.op, ast.Pow) and abs(left) <= 1e6 and abs(right) <= 12:
                return left ** right
        raise ValueError('Source host is not a supported literal arithmetic expression')
    return visit(ast.parse(formula.lstrip('=').replace('^', '**'), mode='eval').body)


def validate_oracles(evidence, key, source_path):
    require(grade_discrete(evidence['nativeOracle'], key).score == 1, 'Native oracle does not pass')
    scope = evidence.get('independentTargetScope', 'full')
    if scope == 'full':
        require(grade_discrete(evidence['independentOracle'], key).score == 1, 'Independent oracle does not pass')
        return
    require(scope == 'partial_with_source_host_validation', 'Unknown independent validation scope')
    subset = set(evidence['independentOracle'])
    require(bool(subset) and subset <= set(key['targets']), 'Invalid independently checked target subset')
    require(set(evidence['independentlyVerifiedTargets']) == subset, 'Independent target coverage mismatch')
    require(set(evidence['notIndependentlyVerifiedTargets']) == set(key['targets']) - subset,
            'Unverified target coverage is incomplete')
    require(all(evidence['notIndependentlyVerifiedTargets'].values()), 'Missing limitation reason')
    subset_key = {**key, 'targets': {k: key['targets'][k] for k in subset}, 'groups': {}}
    require(grade_discrete(evidence['independentOracle'], subset_key).score == 1, 'Independent subset does not pass')
    hosts = evidence['independentSourceHostOracle']
    require(hosts and set(hosts) == set(evidence['clarifiedSourceHosts']), 'Incomplete clarified-host validation')
    import openpyxl
    source = openpyxl.load_workbook(source_path, data_only=False, read_only=True)
    try:
        for ref, proof in hosts.items():
            sheet, cell = ref.rsplit('!', 1)
            formula = source[sheet.strip("'")][cell].value
            require(formula == proof['sourceFormula'], 'Source-host formula mismatch')
            require(literal_arithmetic(formula) == proof['independentValue'], 'Source-host arithmetic mismatch')
    finally:
        source.close()


def validate_staged(work=WORK):
    work = Path(work)
    campaign = read(work / 'campaign.json')
    rows = read(work / 'final-assets.json')
    require(sha(work / 'final-assets.json') == campaign['assetsSha256'], 'Base assets changed')
    require(rows == campaign['tasks'], 'Campaign/base asset rows differ')
    indexed = {r['taskId']: r for r in rows}
    staged = read(revision_dir(work) / 'manifest.json')
    require(bool(staged), 'Empty revision manifest')
    require(len({r['taskId'] for r in staged}) == len(staged), 'Duplicate revised task')
    for row in staged:
        task = row['taskId']
        require(task in indexed, 'Revision is outside frozen roster')
        base = indexed[task]
        version = read(work / 'problems' / task / 'verified.json')
        require(version.get('lockedAt'), 'Base version is not locked')
        require(version['id'] == row['baseProblemVersionId'], 'Wrong base version')
        for pathkey, hashkey in [('basePromptPath', 'basePromptSha256'),
                                 ('promptPath', 'promptSha256'),
                                 ('answerKeyPath', 'answerKeySha256'),
                                 ('workbookPath', 'workbookSha256'),
                                 ('nativeSourcePath', 'nativeSourceSha256')]:
            require(sha(row[pathkey]) == row[hashkey], f'{task}: changed {pathkey}')
        require(Path(row['basePromptPath']).resolve() == Path(base['promptPath']).resolve(), 'Wrong base prompt')
        require(version['prompt'] == Path(row['basePromptPath']).read_text(), 'Base prompt/version mismatch')
        require(row['promptSha256'] != row['basePromptSha256'], 'Revision makes no prompt change')
        for pathkey, hashkey in [('answerKeyPath', 'answerKeySha256'), ('workbookPath', 'workbookSha256')]:
            require(Path(row[pathkey]).resolve() == Path(base[pathkey]).resolve(), 'Prompt-only revision changed input/key path')
            require(row[hashkey] == base[hashkey], 'Prompt-only revision changed input/key hash')
        require(row.get('additionalAssumptionsPath') == base.get('additionalAssumptionsPath'), 'Assumptions path changed')
        if base.get('additionalAssumptionsPath') and base.get('additionalAssumptionsSha256'):
            require(sha(base['additionalAssumptionsPath']) == base['additionalAssumptionsSha256'], 'Assumptions changed')
        evidence = read(row['validationPath'])
        key = read(row['answerKeyPath'])
        validate_oracles(evidence, key, row['nativeSourcePath'])
        require(evidence['promptChangesOnlyAddedClarification'], 'Validation does not establish prompt-only clarification')
        # Restoring a missing native-source input in the prompt is permitted;
        # newEconomicInputs means invented/source-new economic assumptions.
        require(not evidence['newEconomicInputs'] and not evidence['newTargets'], 'Source economics or targets changed')
        require(evidence['tolerancesUnchanged'] and evidence['targetKeysUnchanged'], 'Grading changed')
        for path in row['sourceEvidencePaths']:
            require(Path(path).is_file(), 'Missing source evidence')
    return rows, staged


def artifact_hashes(row, work):
    paths = [row[k] for k in ('basePromptPath', 'promptPath', 'answerKeyPath',
                              'workbookPath', 'nativeSourcePath', 'validationPath')]
    paths += row['sourceEvidencePaths']
    if row.get('additionalAssumptionsPath'):
        paths.append(row['additionalAssumptionsPath'])
    paths += [str(Path(work) / 'problems' / row['taskId'] / 'verified.json'),
              str(revision_dir(work) / 'versions' / row['taskId'] / 'verified.json')]
    paths += [str(p) for p in (revision_dir(work) / 'versions' / row['taskId']).glob('*.json')]
    return {str(Path(p).resolve()): sha(p) for p in paths}


def provision(work=WORK):
    """Copy/update/lock sequentially; receipt guards block uncertain mutation retries."""
    work = Path(work)
    _, staged = validate_staged(work)
    require(not (revision_dir(work) / 'activation.json').exists(), 'Already activated; use a new revision batch')
    for row in staged:
        task = row['taskId']
        base = read(work / 'problems' / task / 'verified.json')
        remote = c.cli('problem-versions', 'get', '--problem-version-id', base['id'])
        require(remote == base, 'Remote base version differs from frozen verified version')
        folder = revision_dir(work) / 'versions' / task
        created = c.mutation(folder, 'create', ['problem-versions', 'create', '--problem-id', base['problemId'],
                                              '--copy-from-version-id', base['id']])
        vid = created['id']
        payload = {'prompt': Path(row['promptPath']).read_text()}
        request = folder / 'update.request.json'
        if request.exists():
            require(read(request) == payload, 'Previously provisioned prompt differs; use a new batch')
        c.mutation(folder, 'update', ['problem-versions', 'update', '--problem-version-id', vid], payload)
        c.mutation(folder, 'lock', ['problem-versions', 'lock', '--problem-version-id', vid])
        verified = c.cli('problem-versions', 'get', '--problem-version-id', vid)
        same_behavior(base, verified)
        require(verified['prompt'] == payload['prompt'], 'Corrected prompt mismatch')
        require(vid != base['id'], 'Correction must create a new immutable version')
        c.save(folder / 'verified.json', verified)
    return {'status': 'provisioned_not_activated', 'tasks': [r['taskId'] for r in staged]}


def activate(work=WORK, *, confirmed=False):
    require(confirmed, 'Activation requires explicit confirmation by the caller')
    work = Path(work)
    _, staged = validate_staged(work)
    destination = revision_dir(work) / 'activation.json'
    require(not destination.exists(), 'Activation is immutable; refusing overwrite')
    activated = []
    for row in staged:
        task = row['taskId']
        base = read(work / 'problems' / task / 'verified.json')
        verified_path = revision_dir(work) / 'versions' / task / 'verified.json'
        revised = read(verified_path)
        same_behavior(base, revised)
        require(revised['id'] != base['id'], 'Correction reused base version')
        require(revised['prompt'] == Path(row['promptPath']).read_text(), 'Corrected prompt mismatch')
        activated.append({**row, 'newProblemVersionId': revised['id'], 'verifiedPath': str(verified_path.resolve()),
                          'artifactHashes': artifact_hashes(row, work)})
    manifest = {'schemaVersion': 1, 'status': 'ready', 'activatedAt': datetime.now(timezone.utc).isoformat(),
                'baseAssetsSha256': sha(work / 'final-assets.json'),
                'stagedManifestSha256': sha(revision_dir(work) / 'manifest.json'),
                'replacementPolicy': 'all_three_attempts_both_models', 'revisions': activated}
    frozen_paths = [work / 'runtime/overlay-manifest.json']
    for model in MODELS:
        frozen_paths += [work / 'final-images' / (model + '-image.json'),
                         work / 'final-images/configs' / model / 'verified.json']
    manifest['frozenRuntimeArtifacts'] = {str(p.resolve()): sha(p) for p in frozen_paths if p.exists()}
    c.save(destination, manifest)
    return manifest


def activation(work=WORK):
    work = Path(work)
    if (work / 'content-amendments/0002/activation.json').exists():
        import recursion_hardening_amendment as amendment
        return amendment.resolved_activation(work)
    if (work / 'content-successor/activation.json').exists():
        import recursion_hardening_content as content
        return content.activation(work)
    path = revision_dir(work) / 'activation.json'
    if not path.exists():
        return None
    data = read(path)
    require(data['status'] == 'ready', 'Revision activation is not ready')
    require(data['baseAssetsSha256'] == sha(work / 'final-assets.json'), 'Activated base assets changed')
    require(data['stagedManifestSha256'] == sha(revision_dir(work) / 'manifest.json'), 'Activated staged manifest changed')
    for artifact, digest in data['frozenRuntimeArtifacts'].items():
        require(sha(artifact) == digest, 'Frozen runtime/configuration artifact changed')
    require(len({r['taskId'] for r in data['revisions']}) == len(data['revisions']), 'Duplicate activated task')
    for row in data['revisions']:
        for artifact, digest in row['artifactHashes'].items():
            require(sha(artifact) == digest, 'Activated revision artifact changed: ' + artifact)
        base = read(work / 'problems' / row['taskId'] / 'verified.json')
        corrected = read(row['verifiedPath'])
        same_behavior(base, corrected)
        require(corrected['id'] == row['newProblemVersionId'], 'Activated version ID mismatch')
        require(corrected['prompt'] == Path(row['promptPath']).read_text(), 'Activated prompt mismatch')
    return data


def active_assets(base_rows, work=WORK):
    """Only two fields are overlaid; caller-owned rows are never mutated."""
    require(base_rows == read(Path(work) / 'final-assets.json'), 'Expected frozen base rows')
    result = copy.deepcopy(base_rows)
    data = activation(work)
    if data:
        revised = {r['taskId']: r for r in data['revisions']}
        require(set(revised) <= {r['taskId'] for r in result}, 'Activated task outside roster')
        for row in result:
            if row['taskId'] in revised:
                fields = ('promptPath', 'promptSha256', 'workbookPath', 'workbookSha256') if data.get('kind') == 'content_successor' else ('promptPath', 'promptSha256')
                for key in fields:
                    row[key] = revised[row['taskId']][key]
    return result


def version_for(task_id, work=WORK):
    data = activation(work)
    if data:
        for row in data['revisions']:
            if row['taskId'] == task_id:
                return read(row['verifiedPath'])
    return read(Path(work) / 'problems' / task_id / 'verified.json')


def versions_for(base_rows, work=WORK):
    """Validate activation once for a whole launch/collection pass."""
    require(base_rows == read(Path(work) / 'final-assets.json'), 'Expected frozen base rows')
    data = activation(work)
    corrected = {r['taskId']: r['verifiedPath'] for r in data['revisions']} if data else {}
    require(set(corrected) <= {r['taskId'] for r in base_rows}, 'Activated task outside roster')
    return {row['taskId']: read(corrected.get(row['taskId'],
                Path(work) / 'problems' / row['taskId'] / 'verified.json')) for row in base_rows}


def active_configs(work=WORK):
    data = activation(work)
    if data and data.get('kind') == 'content_successor':
        return {m: read(data['configs'][m]['successorPath']) for m in MODELS}
    return {m: read(Path(work) / 'final-images/configs' / m / 'verified.json') for m in MODELS}


def known_configs(work=WORK):
    mapping = {}
    def add(config_id, model):
        require(config_id not in mapping or mapping[config_id] == model, 'Configuration assigned to multiple models')
        mapping[config_id] = model
    for model in MODELS:
        add(read(Path(work) / 'final-images/configs' / model / 'verified.json')['id'], model)
    legacy = c.REPORTS / 'opus-max-20260915/verified.json'
    if legacy.exists(): add(read(legacy)['id'], 'opus')
    if (Path(work) / 'content-successor/plan.json').exists():
        import recursion_hardening_content as content
        for model, spec in content.planned_configs(work).items():
            add(spec['baseId'], model)
            add(spec['successorId'], model)
    if (Path(work) / 'content-amendments/0002/plan.json').exists():
        import recursion_hardening_amendment as amendment
        for model, spec in amendment.validate_plan(work)['configs'].items():
            add(spec['baseId'], model)
            add(spec['successorId'], model)
    return mapping


def historical_versions(work=WORK, *, context=None):
    context = run_binding_context(work) if context is None else context
    indexed = {}
    for task, versions in context['bindings'].items():
        for version in versions:
            require(version not in indexed or indexed[version] == task, 'Version belongs to multiple tasks')
            indexed[version] = task
    return indexed


def run_binding_context(work=WORK):
    """Build once per collection, then pass context= to each binding check."""
    work = Path(work)
    if (work / 'content-amendments/0002/activation.json').exists():
        import recursion_hardening_amendment as amendment
        context = amendment.run_binding_context(work)
        context['knownConfigs'] = known_configs(work)
        return context
    data = activation(work)
    base_configs = {read(work / 'final-images/configs' / m / 'verified.json')['id'] for m in MODELS}
    new_configs = {x['successorId'] for x in data['configs'].values()} if data and data.get('kind') == 'content_successor' else set()
    revised = {x['taskId']: x for x in data['revisions']} if data else {}
    bindings = {}
    for row in read(work / 'final-assets.json'):
        task = row['taskId']
        original = read(work / 'problems' / task / 'verified.json')['id']
        correction = revised.get(task)
        if correction and correction.get('assetRepaired'):
            versions = {vid: set(base_configs) for vid in correction['oldVersionIds']}
            versions[correction['newProblemVersionId']] = set(new_configs)
        else:
            versions = {original: base_configs | new_configs}
            if correction:
                for vid in correction.get('oldVersionIds', []): versions[vid] = base_configs | new_configs
                versions[correction['newProblemVersionId']] = base_configs | new_configs
        bindings[task] = versions
    return {'bindings': bindings, 'knownConfigs': known_configs(work)}


def validate_run_binding(task_id, version_id, config_id, work=WORK, *, context=None):
    context = run_binding_context(work) if context is None else context
    require(config_id in context['knownConfigs'], 'Unknown run configuration')
    versions = context['bindings'].get(task_id, {})
    require(version_id in versions, 'Undeclared task version')
    require(config_id in versions[version_id], 'Wrong workbook image for task/version')
    return True


def prepare_content_successor(workbook_manifest_path, runtime_manifest_path, work=WORK):
    import recursion_hardening_content as content
    return content.prepare_content_successor(workbook_manifest_path, runtime_manifest_path, work)


def provision_content_successor(work=WORK):
    import recursion_hardening_content as content
    return content.provision_content_successor(work)


def activate_content_successor(work=WORK, *, confirmed=False):
    import recursion_hardening_content as content
    return content.activate_content_successor(work, confirmed=confirmed)


def solver_is_terminal(evaluation, solver, matrix=None):
    """Partial jobs release reservations only with an authoritative end proof."""
    if solver.get('jobStatus') in TERMINAL:
        return True
    if solver.get('jobStatus') != 'partial':
        return False
    if evaluation.get('completedAt'):
        return True
    if not matrix or matrix.get('evaluationId') != evaluation.get('id'):
        return False
    expected = [p['problemVersionId'] for p in evaluation.get('problems', [])]
    count = evaluation.get('nAttemptsPerProblem')
    if not expected or len(set(expected)) != len(expected) or not isinstance(count, int) or count < 1:
        return False
    cells = [x for x in matrix.get('cells', []) if x.get('solverId') == solver.get('id')]
    if len(cells) != len(expected) or {x.get('problemVersionId') for x in cells} != set(expected):
        return False
    run_ids = []
    for cell in cells:
        attempts = cell.get('attempts', [])
        if len(attempts) != count or {a.get('attemptNumber') for a in attempts} != set(range(1, count + 1)):
            return False
        if any(a.get('status') not in TERMINAL or not a.get('problemRunId') for a in attempts):
            return False
        run_ids += [a['problemRunId'] for a in attempts]
    return len(set(run_ids)) == len(run_ids)


def reviewed_external_capacity_configs(work=WORK):
    """Reviewed identities only for reservations; never accepted as campaign solvers."""
    path = Path(work) / 'content-amendments/0002/reviewed-capacity-configs.json'
    if not path.exists():
        return {}, None
    receipt = read(path)
    require(receipt.get('schemaVersion') == 1 and receipt.get('status') == 'reviewed'
            and receipt.get('scope') == 'capacity_reservations_only', 'Invalid external capacity review')
    require(receipt.get('evidenceHashes'), 'External capacity evidence required')
    for source, digest in receipt['evidenceHashes'].items():
        require(sha(Path(source)) == digest, 'External capacity evidence changed')
    configs = {}
    for item in receipt['configs']:
        source = Path(item['configPath'])
        require(receipt['evidenceHashes'].get(str(source)) == item['configSha256']
                and sha(source) == item['configSha256'], 'External config hash mismatch')
        config = read(source); cid = item['runConfigVersionId']; body = config['config']
        require(cid not in configs and cid == config['id'], 'Duplicate/wrong external config identity')
        require(config.get('lockedAt') and config.get('verifiedAt'), 'External config must be locked and verified')
        # This bounded review establishes only these exact Opus max identities.
        require(item['model'] == 'opus' and body.get('args') == '--model claude-opus-5 --effort max'
                and body.get('envVars', {}).get('ANTHROPIC_MODEL') == 'claude-opus-5'
                and body.get('envVars', {}).get('CLAUDE_CODE_EFFORT_LEVEL') == 'max', 'External model identity differs')
        repository = 'us-central1-docker.pkg.dev/lb-ml-dev/fde-runners/runner-fcp-claude-code-opus-5'
        require(re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', body.get('harnessImageUrl', '')),
                'External image is outside reviewed repository')
        configs[cid] = config
    require(configs, 'Empty external capacity review')
    return configs, {'path': str(path.resolve()), 'sha256': sha(path)}


def fresh_capacity(work=WORK):
    """Read every nonterminal evaluation; unknown active configurations fail closed."""
    work = Path(work)
    mapping = known_configs(work)
    external, external_review = reviewed_external_capacity_configs(work)
    require(not (set(mapping) & set(external)), 'External capacity identity overlaps campaign configuration')
    external_checked = set()
    import recursion_tail_capacity as tail
    tail_reviews, tail_review_pin = tail.reviewed_evaluations(work, mapping)
    tail_bounds = []
    items, cursors = [], set()
    cursor = None
    while True:
        args = ['evaluations', 'list', '--limit', '100', '--sort-by', 'createdAt', '--sort-direction', 'desc']
        if cursor:
            args += ['--cursor', cursor]
        page = c.cli(*args)
        items += page['items']
        cursor = page.get('nextCursor')
        if not cursor:
            break
        require(cursor not in cursors, 'Repeated evaluation cursor')
        cursors.add(cursor)
    require(len({x['id'] for x in items}) == len(items), 'Duplicate evaluation listing')
    reserved = dict.fromkeys(MODELS, 0)
    for item in items:
        if item['status'] in TERMINAL:
            continue
        detail = c.cli('evaluations', 'get', '--evaluation-id', item['id'])
        detail_read_at = datetime.now(timezone.utc).isoformat()
        matrix = None
        is_reviewed_tail = item['id'] in tail_reviews
        if is_reviewed_tail or (not detail.get('completedAt') and any(s.get('jobStatus') == 'partial' for s in detail['solvers'])):
            matrix = c.cli('evaluations', 'get-results', '--evaluation-id', item['id'])
        matrix_read_at = datetime.now(timezone.utc).isoformat() if matrix is not None else None
        remaining = tail.remaining_slots(detail, matrix, tail_reviews[item['id']], TERMINAL) if is_reviewed_tail else None
        for solver in detail['solvers']:
            if solver_is_terminal(detail, solver, matrix):
                continue
            config_id = solver['runConfigVersionId']
            if config_id in external and config_id not in external_checked:
                actual = c.cli('run-config-versions', 'get', '--run-config-version-id', config_id)
                reviewed = external[config_id]
                require(actual.get('id') == config_id and actual.get('lockedAt') and actual.get('verifiedAt')
                        and actual.get('config') == reviewed['config'], 'Active external configuration drifted')
                external_checked.add(config_id)
            require(config_id in mapping or config_id in external_checked, 'Unknown active solver; reconcile capacity')
            amount = solver['metadata']['concurrency']
            require(type(amount) is int and 0 < amount <= 75, 'Invalid reservation')
            if remaining is not None:
                bounded = min(amount, remaining[solver['id']])
                tail_bounds.append({'evaluationId': item['id'], 'solverId': solver['id'],
                    'runConfigVersionId': config_id, 'configuredConcurrency': amount,
                    'remainingLogicalSlots': remaining[solver['id']], 'reservedConcurrency': bounded,
                    'detailReadAt': detail_read_at, 'matrixReadAt': matrix_read_at,
                    'detailSha256': hashlib.sha256(json.dumps(detail, sort_keys=True).encode()).hexdigest(),
                    'matrixSha256': hashlib.sha256(json.dumps(matrix, sort_keys=True).encode()).hexdigest()})
                amount = bounded
            reserved[mapping[config_id] if config_id in mapping else 'opus'] += amount
    require(all((datetime.now(timezone.utc) - datetime.fromisoformat(b['detailReadAt'])).total_seconds() < 300
                for b in tail_bounds), 'Stale tail capacity read')
    limit = c.CONFIG['concurrencyPerModel']
    available = {m: max(0, limit - reserved[m]) for m in MODELS}
    over = {m: max(0, reserved[m] - limit) for m in MODELS}
    return {'checkedAt': datetime.now(timezone.utc).isoformat(), 'modelLimit': limit,
            'reservedConcurrencyByModel': reserved, 'availableConcurrencyByModel': available,
            'overReservationByModel': over, 'externalCapacityReview': external_review,
            'activeReviewedExternalConfigIds': sorted(external_checked),
            'tailCapacityReview': tail_review_pin, 'tailReservationBounds': tail_bounds}


def correction_payload(work=WORK):
    """All activated tasks already dispatched on a prior version, for both models."""
    work = Path(work)
    data = activation(work)
    require(data is not None, 'No activated revisions')
    dispatched = {}
    for response in [*work.glob('evaluation-*.response.json'), *work.glob('correction-*.response.json')]:
        request = response.with_name(response.name.replace('.response.json', '.request.json'))
        for problem in read(request)['problems']:
            dispatched.setdefault(problem['problemId'], set()).add(problem['problemVersionId'])
    selected = []
    accumulated = {x['taskId']: x for x in data['revisions']}
    cohort = data.get('amendmentDelta', data['revisions'])
    for delta in cohort:
        row = accumulated[delta['taskId']]
        version = read(row['verifiedPath'])
        prior = dispatched.get(version['problemId'], set())
        old_versions = set(row.get('oldVersionIds', [row['baseProblemVersionId']]))
        require(not (prior - old_versions - {row['newProblemVersionId']}), 'Unexpected previously dispatched version')
        if data.get('amendmentId'):
            require(prior & old_versions, 'Amendment cohort was not fully dispatched on predecessor versions')
        if prior & old_versions:
            selected.append(version)
    require(selected, 'No previously dispatched tasks need correction')
    # Fixed before amendment0002 dispatch: scheduling parallelism only, not attempt count.
    concurrency_limit = 30 if data.get('amendmentId') == '0002' else (50 if data.get('kind') == 'content_successor' else 10)
    per = min(concurrency_limit, len(selected) * 3)
    configs = active_configs(work)
    for cfg in configs.values():
        require(cfg.get('lockedAt') and cfg.get('verifiedAt'), 'Solver configuration must be locked and verified')
    return {'name': 'FCP task-validity corrections — both models — pass@3',
            'description': 'Source-convention and/or workbook-disclosure corrections. All three attempts replaced equally for both models; original results retained separately.',
            'metadata': {'schemaVersion': 1, 'attemptsPerProblem': 3, 'successThreshold': 1,
                         'retryPolicy': {'maxInfraRetries': 2}},
            'tags': ['fcp-audit-hardening-20260915', 'prompt-validity-corrections'],
            'solvers': [{'displayName': 'Opus 5 thinking max' if m == 'opus' else 'Gemini 3.8 Flash',
                         'runConfigVersionId': configs[m]['id'], 'concurrency': per} for m in MODELS],
            'problems': [{'problemId': v['problemId'], 'problemVersionId': v['id'], 'environmentId': v['environmentId']} for v in selected]}


def launch_corrections(work=WORK, *, confirmed=False, models=None):
    require(confirmed, 'Correction dispatch requires explicit confirmation by the caller')
    work = Path(work)
    require(read(work / 'task-validity-review.json')['status'] == 'complete', 'Validity review is incomplete')
    payload = correction_payload(work)
    data = activation(work)
    prefix = data.get('dispatchPrefix') or ('correction-content-1' if data.get('kind') == 'content_successor' else 'correction-1')
    requested = set(MODELS if models is None else models)
    require(requested and requested <= set(MODELS), 'Invalid correction model selection')
    # An ambiguous mutation blocks dispatch for either model until reconciled.
    for intent in work.glob(prefix + '*.intent.json'):
        require(intent.with_name(intent.name.replace('.intent.json', '.response.json')).exists(),
                'Reconcile uncertain correction dispatch before launching another model')
    mapping = known_configs(work)
    canonical_problems = {(p['problemId'], p['problemVersionId'], p['environmentId']) for p in payload['problems']}
    new_ids = {p['problemVersionId'] for p in payload['problems']}
    dispatched = set()
    prior_receipts = []
    for receipt in [*work.glob('evaluation-*.response.json'), *work.glob('correction-*.response.json')]:
        request = read(receipt.with_name(receipt.name.replace('.response.json', '.request.json')))
        if not new_ids & {p['problemVersionId'] for p in request['problems']}:
            continue
        require({(p['problemId'], p['problemVersionId'], p['environmentId']) for p in request['problems']} == canonical_problems,
                'Correction task/version cohort differs from an earlier dispatch')
        require(request['metadata']['attemptsPerProblem'] == 3, 'Prior correction attempt count differs')
        receipt_models = []
        for solver in request['solvers']:
            require(solver['runConfigVersionId'] in mapping, 'Unknown prior correction configuration')
            model = mapping[solver['runConfigVersionId']]
            if data.get('amendmentId'):
                expected_solver = next(s for s in payload['solvers'] if mapping[s['runConfigVersionId']] == model)
                require(solver['runConfigVersionId'] == expected_solver['runConfigVersionId'], 'Amendment counterpart configuration changed')
                require(solver.get('concurrency') == expected_solver['concurrency'], 'Amendment counterpart concurrency changed')
            receipt_models.append(model)
        require(len(set(receipt_models)) == len(receipt_models), 'Duplicate model in correction receipt')
        require(not dispatched & set(receipt_models), 'Model already has duplicate correction dispatches')
        dispatched.update(receipt_models)
        prior_receipts.append(receipt)
    remaining = requested - dispatched
    if not remaining:
        return {'status': 'already_dispatched', 'models': sorted(requested), 'receipts': [str(p) for p in prior_receipts]}
    capacity = fresh_capacity(work)
    require((datetime.now(timezone.utc) - datetime.fromisoformat(capacity['checkedAt'])).total_seconds() < 300, 'Stale capacity')
    per = payload['solvers'][0]['concurrency']
    available = {m for m in remaining if capacity['availableConcurrencyByModel'][m] >= per}
    require(available, 'Insufficient model capacity')
    if models is not None:
        require(available == remaining, 'Insufficient capacity for explicitly requested model set')
    selected = available
    payload = {**payload, 'solvers': [s for s in payload['solvers'] if mapping[s['runConfigVersionId']] in selected]}
    name = prefix if selected == set(MODELS) else prefix + '-' + next(iter(selected))
    require(not (work / (name + '.response.json')).exists(), 'Correction receipt name already used')
    c.save(work / (name + '.capacity.json'), capacity)
    return c.mutation(work, name, ['evaluations', 'bulk-create'], payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'provision', 'activate', 'launch-corrections'])
    parser.add_argument('--work', type=Path, default=WORK)
    parser.add_argument('--confirm', action='store_true', help='Explicitly activate or dispatch the prepared revision batch')
    args = parser.parse_args()
    if args.action == 'validate':
        _, staged = validate_staged(args.work)
        result = {'status': 'validated', 'tasks': [r['taskId'] for r in staged]}
    elif args.action == 'provision':
        result = provision(args.work)
    elif args.action == 'activate':
        result = activate(args.work, confirmed=args.confirm)
    else:
        result = launch_corrections(args.work, confirmed=args.confirm)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
