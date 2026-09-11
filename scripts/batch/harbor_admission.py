"""Root admission glue; no model calls, successful lane claims or legacy adoption.

Root constructs AdmissionPolicy from the selected campaign. Agent data cannot
supply authority callbacks, clocks, probe commands, requested IDs or namespaces.
"""
from dataclasses import dataclass
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_tracker as schema
from scripts.corpus import digest, read_inventory, object_hash


class AdmissionError(ValueError):
    pass


class DiskReserveUnavailable(AdmissionError):
    """All other dependency probes passed; only current disk reserve is short."""
    def __init__(self, free_bytes, required_bytes, observation):
        super().__init__('disk reserve unavailable')
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
        self.observation = observation


def require(ok, message):
    if not ok:
        raise AdmissionError(message)


def read(record):
    lifecycle.checked_record(record)
    return schema.strict_json(Path(record['path']).read_bytes())


def record(path):
    path = schema.safe_path(path)
    return lifecycle.checked_record({'path': str(path), 'sha256': digest(path)})


@dataclass(frozen=True)
class DependencyRecipe:
    requirements: tuple  # Exact root-owned path/hash records, including recursive -r files.
    imports: tuple = ('openpyxl', 'yaml')
    max_age_ns: int = 300_000_000_000
    minimum_disk_bytes: int = 8 * 1024**3
    maximum_workbooks: int = 4
    maximum_models: int = 2


def observe_dependencies(recipe, workspace):
    """Read-only fresh local probes; no provisioning, network or model requests."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    require(0 < recipe.maximum_models <= recipe.maximum_workbooks <= 4 and recipe.maximum_models <= 2, 'invalid concurrency caps')
    checked = {str(schema.safe_path(x['path'])): lifecycle.checked_record(x) for x in recipe.requirements}
    require(bool(checked), 'requirements closure is empty')
    installed = {canonicalize_name(d.metadata['Name']): d.version
                 for d in importlib.metadata.distributions() if d.metadata['Name']}
    for filename in checked:
        for line in Path(filename).read_text().splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if line.startswith('-r '):
                require(str((Path(filename).parent / line[3:].strip()).absolute()) in checked,
                        'requirements include is not bound')
                continue
            require(not line.startswith('-'), 'unsupported requirements directive')
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            version = installed.get(canonicalize_name(requirement.name))
            require(version is not None and requirement.specifier.contains(version),
                    'required distribution missing or incompatible: ' + requirement.name)
    script = 'import importlib,json; print(json.dumps([importlib.import_module(n).__name__ for n in ' + repr(list(recipe.imports)) + ']))'
    imports = subprocess.run([sys.executable, '-I', '-c', script], capture_output=True, text=True, timeout=30, check=True)
    require(json.loads(imports.stdout) == list(recipe.imports), 'required import probe differs')
    docker = shutil.which('docker')
    require(docker is not None, 'docker executable unavailable')
    docker = str(Path(docker).resolve(strict=True))
    daemon = subprocess.run([docker, 'info', '--format', '{{json .}}'], capture_output=True, text=True, timeout=30, check=True)
    info = json.loads(daemon.stdout)
    require(isinstance(info, dict) and info.get('ServerVersion') and not info.get('ServerErrors'), 'docker daemon unavailable')
    require(sys.version_info[:2] == (3, 12), 'pipeline requires Python 3.12')
    observation = dict(executable=str(Path(sys.executable).absolute()), executable_sha256=digest(sys.executable),
                python=list(sys.version_info[:3]), requirements=list(checked.values()),
                installed_manifest=installed, installed_manifest_sha256=object_hash(installed),
                import_probe=list(recipe.imports), docker_executable=record(docker),
                docker_server_version=info['ServerVersion'])
    free = shutil.disk_usage(workspace).free
    if free < recipe.minimum_disk_bytes:
        raise DiskReserveUnavailable(free, recipe.minimum_disk_bytes, observation)
    return observation


def dependency_report(recipe, workspace):
    return dict(schema_version='harbor-dependency-observation/v2', checked_at_ns=time.time_ns(),
                observation=observe_dependencies(recipe, workspace))


def validate_dependency(binding, recipe, workspace):
    report = read(binding)
    require(report.get('schema_version') == 'harbor-dependency-observation/v2', 'fresh semantic dependency observation required')
    age = time.time_ns() - report.get('checked_at_ns', 0)
    require(0 <= age <= recipe.max_age_ns, 'dependency observation expired or future dated')
    require(report.get('observation') == observe_dependencies(recipe, workspace), 'current dependency observation changed')
    return True


PRUNE = {'.git', '.venv', '__pycache__', 'node_modules', 'ast', 'ast_out', 'segments',
         'sources', 'source', 'lineage', 'code', 'snapshots', 'mcp', 'runtime', 'eval',
         'tests', 'site-packages', 'verifier', 'baseline', 'inputs', 'inputs_out',
         'inputs_out_mcp', 'seg_out', 'graph_out', 'level_out'}
MARKERS = {'task.toml', 'task.json', 'task.yaml', 'task.yml', 'release-manifest.json',
           'current-release.json', 'instruction.md'}


def survey_namespaces(roots, known_ids):
    """Re-scan the configured known namespaces; never trust historical absence."""
    excluded, histories, seen = {}, {}, set()
    def identity(path):
        for part in reversed(Path(path).parts):
            found = re.search(r'(?<!\d)(\d{4})(?:[-_.]|$)', part)
            if found and found[1] in known_ids:
                return found[1]
        return None
    def exclude(wb, path):
        require(wb is not None, 'unmapped prior task evidence: ' + str(path))
        excluded.setdefault(wb, []).append(str(path))
    for root in roots:
        root = schema.safe_path(root)
        require(root.is_dir(), 'required history namespace missing')
        for base, dirs, files in os.walk(root, followlinks=False):
            base = Path(base)
            if base in seen:
                dirs[:] = []; continue
            seen.add(base)
            links={n for n in dirs+files if n not in PRUNE and (base/n).is_symlink()}
            for name in links:
                # Compatibility task links are positive prior-identity evidence;
                # never follow them outside the configured namespace.
                exclude(identity(base/name),base/name)
            dirs[:] = sorted(n for n in dirs if n not in PRUNE and n not in links)
            files=[n for n in files if n not in links and n not in PRUNE]
            wb = identity(base)
            markers = MARKERS.intersection(files)
            if markers or re.match(r'^\d{4}[-_]outputs(?:$|[.-])', base.name) or re.match(r'^\d{4}-(?:instruction-naturalization|additional-assumptions)(?:$|[.-])', base.name):
                exclude(wb, base)
            for name in files:
                path = base / name
                if name.endswith('.zip'):
                    # Central directory only. Unreadable archives cannot prove novelty.
                    with zipfile.ZipFile(path) as archive:
                        for member in archive.namelist():
                            if Path(member).name in MARKERS:
                                exclude(identity(member), path)
                if base.name == 'workbooks' and name.endswith('.json'):
                    tracker = schema.strict_json(path.read_bytes())
                    own = tracker.get('workbook_id', path.stem)
                    if own not in known_ids:
                        continue
                    histories.setdefault(own, []).append(record(path))
                    ids, promotion = tracker.get('generation_ids', {}), tracker.get('promotion_ids', {})
                    if ids.get('task') or ids.get('release') or promotion.get('task_generation_id') or promotion.get('release_id') or tracker.get('staged_path'):
                        exclude(own, path)
                if name.endswith('.metadata.json') and wb:
                    metadata = schema.strict_json(path.read_bytes())
                    if metadata.get('workbook') == wb:
                        # Keep whole directories: legacy audit metadata may not bind request files.
                        for item in sorted(base.rglob('*')):
                            if item.is_file():
                                histories.setdefault(wb, []).append(record(item))
    return excluded, {wb: sorted({x['path']: x for x in rows}.values(), key=lambda x:x['path'])
                      for wb, rows in histories.items()}


@dataclass(frozen=True)
class AdmissionPolicy:
    batch: str
    requested_ids: tuple
    tracker_root: Path
    original_inventory: dict
    dependency: dict
    dependencies: DependencyRecipe
    history_roots: tuple
    mode: str = 'new'
    expected_original_count: int = 700
    # Fixed root-approved source options; never read a trust key path from workbook data.
    trusted_public_key: str = '/etc/harbor/excel-runner-public.pem'
    campaign_evidence: tuple = ()


class DispatcherAdmission:
    """Fixed admission operation; does not expose primitive write permissions."""
    def __init__(self, policy):
        self.policy = policy
        self.identity = (policy.batch, policy.requested_ids, str(policy.tracker_root), policy.mode)
        require(policy.mode in {'new', 'historical'}, 'unknown campaign mode')
        require(bool(policy.history_roots), 'known history namespaces must be configured')
        require(bool(policy.batch) and policy.requested_ids and len(set(policy.requested_ids)) == len(policy.requested_ids), 'invalid requested cohort')
        lifecycle.checked_record(policy.original_inventory)
        self.inventory = read_inventory(policy.original_inventory['path'])
        rows = self.inventory['sources']
        require(len(rows) == policy.expected_original_count, 'original denominator changed')
        self.sources = {r['workbook_id']: r for r in rows}
        require(set(policy.requested_ids) <= self.sources.keys(), 'unknown requested identity')
        require(len({r['sha256'] for r in rows}) == len(rows), 'ambiguous duplicate original bytes')
        require(len({r['path'] for r in rows}) == len(rows), 'ambiguous original path')
        schema.safe_path(policy.tracker_root)

    def _source(self, workbook, raw, generation, normalization):
        original = self.sources[workbook]
        source = record(original['path'])
        require(source['sha256'] == original['sha256'] and Path(source['path']).stat().st_size == original['size_bytes'], 'original bytes changed')
        require(Path(source['path']).stem.casefold() == workbook.casefold() and Path(source['path']).suffix.lower() == original['format'].lower(), 'original path identity changed')
        raw = lifecycle.checked_record(raw or source)
        from xl_source_health import inspect_workbook
        health = inspect_workbook(raw['path'])
        require(health['route'] in {'pass','restricted_pass','restricted_recalc_pass'}, 'source remains unsupported or needs recalculation')
        evidence = [source, raw]
        if generation:
            from xl_source_publication import validate_source_generation, validate_ast_provenance, create_ast_provenance
            from scripts.batch.harbor_source_evidence import validate_source_lineage
            bound = lifecycle.checked_record(generation)
            directory = Path(bound['path']).parent
            require(directory.name == read(generation).get('generation_id'), 'generation identity mismatch')
            manifest = validate_source_generation(directory, expected_generation_id=directory.name)
            require(manifest['layout']['workbook_id'] == workbook, 'source generation workbook mismatch')
            published = directory / manifest['layout']['source_workbook']
            require(digest(published) == raw['sha256'], 'candidate differs from published source')
            require(read(record(directory/'health.json')) == health, 'current source health changed')
            args=dict(production=True, workbook_id=workbook, max_range_expand=100, include_constants=True, include_cached_values=True)
            ast=directory/manifest['layout']['ast_directory']
            previous=validate_ast_provenance(published,ast,directory/'ast-provenance.json',builder_args=args)
            current=create_ast_provenance(published,ast,builder_args=args)
            project=lambda b:[{k:v[k] for k in ('name','sha256','size_bytes')} for v in b['builder']['code']]
            require(project(previous)==project(current),'source AST builder changed')
            records=[read(x) for x in normalization]
            for n in records:
                for key in ('plan','manifest','candidate','curation'):
                    if n.get(key):
                        item=record(n[key])
                        if n.get(key+'_sha256'):
                            require(item['sha256']==n[key+'_sha256'],'normalization nested evidence changed')
                        evidence.append(item)
            validate_source_lineage(Path(source['path']),directory,records,trusted_public_key=self.policy.trusted_public_key)
            evidence += [record(x) for x in directory.rglob('*') if x.is_file()] + list(normalization)
        else:
            require(raw == source and health['route'] == 'pass', 'candidate/restricted intake requires complete published lineage')
        return source,raw,health,evidence

    def _check(self, workbook, raw=None, generation=None, normalization=()):
        p=self.policy
        require((p.batch,p.requested_ids,str(p.tracker_root),p.mode)==self.identity,'configured cohort changed')
        require(workbook in p.requested_ids, 'workbook outside exact requested cohort')
        lifecycle.checked_record(p.original_inventory)
        for bound in p.campaign_evidence:
            lifecycle.checked_record(bound)
        require(read_inventory(p.original_inventory['path']) == self.inventory,'original inventory changed')
        validate_dependency(p.dependency,p.dependencies,p.tracker_root.parent)
        excluded,histories=survey_namespaces(p.history_roots,set(self.sources))
        require(workbook not in excluded,'prior task identity requires preserved-task resume, not a fresh tracker')
        history=histories.get(workbook,[])
        # A new tracker must never zero a previous tracker's attempts. Use explicit migration/resume.
        if any(Path(x['path']).parent.name=='workbooks' for x in history):
            from scripts.batch import harbor_pre_audit_history as preaudit
            proofs=[b for b in p.campaign_evidence
                    if (v:=read(b)).get('schema_version')==preaudit.SCHEMA
                    and v.get('workbook')==workbook]
            require(len(proofs)==1,'prior tracker requires explicit lossless resume')
            preaudit.validate(proofs[0],workbook=workbook,
                original=record(self.sources[workbook]['path']),
                history_roots=p.history_roots,known_ids=set(self.sources))
        original,raw,health,evidence=self._source(workbook,raw,generation,normalization)
        return dict(workbook_id=workbook,batch=p.batch,requested_ids=list(p.requested_ids),original_source=original,raw_source=raw,dependency_preflight=p.dependency,intake_evidence=list({x['path']:x for x in [p.original_inventory,*p.campaign_evidence,*evidence,*history]}.values())),history

    def create(self, workbook, *, raw=None, generation=None, normalization=()):
        """Create pending-only tracker. Source/financial/model lanes must still run."""
        request,history=self._check(workbook,raw,generation,normalization)
        path=schema.safe_path(self.policy.tracker_root/(workbook+'.json'))
        def authority(operation,target,before,actual):
            return operation=='create' and target==str(path) and before is None and actual==request
        def intake(actual):
            current,_=self._check(workbook,raw,generation,normalization)
            require(actual==current==request,'admission evidence changed before commit')
            return True
        def lineage(original,candidate,evidence):
            actual,_,_,checked=self._source(workbook,candidate,generation,normalization)
            require(actual==original and all(x in evidence for x in checked),'lineage evidence mismatch')
            return True
        # No caller-held workbook flock: lifecycle owns it and commits all original/history bindings.
        return lifecycle.create_tracker(path,authority=lifecycle.RootAuthority('harbor-orchestrator',900_000_000_000,authority),intake_validator=intake,lineage_validator=lineage,**request)


def validate_prior_audit_budget(history_records, inherited_binding, workbook):
    """Required before first model dispatch; retained metadata alone cannot reset budgets.

    This validates the existing auditor's actual request/response inheritance
    contract, not a new receipt type or an assumed zero-attempt declaration.
    Incomplete legacy history remains an explicit reconciliation blocker.
    """
    from xl_variable_source_audit import _validate_inherited_budget
    files={x['path']:read(x) for x in history_records if x['path'].endswith('.json')}
    metadata=[v for path,v in files.items() if path.endswith('.metadata.json')]
    if not metadata:
        require(inherited_binding is None,'unexpected inheritance without discovered audit history')
        return []
    require(inherited_binding is not None,'prior audit requires actual budget inheritance')
    batches=_validate_inherited_budget(inherited_binding,workbook)
    receipt=read(inherited_binding)
    inherited={row['request']['path']:row['request']['sha256'] for row in receipt['attempts']}
    requests={p:digest(p) for p,v in files.items() if isinstance(v,dict) and isinstance(v.get('messages'),list) and isinstance(v.get('model'),str) and isinstance(v.get('endpoint'),str)}
    require(bool(requests) and requests==inherited,'inherited requests do not cover exact discovered audit history')
    # Legacy metadata records consumed calls without durable requests. Do not
    # manufacture calls or treat disappearance of their journals as fresh budget.
    for path,m in files.items():
        if not path.endswith('.metadata.json'):
            continue
        local_requests=[p for p in requests if Path(path).parent in Path(p).parents]
        require(type(m.get('api_calls')) is int and m['api_calls'] <= len(local_requests), 'legacy audit consumption cannot be reconciled')
    return batches


def _policy_from_general_cohort(manifest, candidate_binding, *, batch, tracker_root,
                                original_inventory, dependency, dependencies,
                                history_roots, expected_original_count):
    """Bind a frozen arbitrary-size cohort without changing workbook lane gates.

    Requested identities are explicit and ordered: changing selection requires a
    new hash-bound proposal, including when replacing an ineligible workbook.
    The target is the selected cohort size, not a promise of qualification.
    """
    ids = manifest.get('requested_workbook_ids')
    require(isinstance(ids, list) and bool(ids) and
            all(isinstance(wb, str) and re.fullmatch(r'\d{4}', wb) for wb in ids),
            'invalid frozen requested identities')
    require(len(set(ids)) == len(ids), 'duplicate requested identity')
    count = manifest.get('target_new_qualified_tasks')
    denominator = manifest.get('denominator_for_original_rebuild')
    require(type(count) is int and count == len(ids), 'campaign objective/count changed')
    require(type(expected_original_count) is int and expected_original_count > 0 and
            type(denominator) is int and denominator == expected_original_count,
            'campaign denominator changed')
    selected = manifest.get('selected_candidates')
    require(isinstance(selected, list) and len(selected) == count,
            'selected count differs from frozen requested cohort')
    lifecycle.checked_record(original_inventory)
    rows = read_inventory(original_inventory['path'])['sources']
    require(len(rows) == denominator, 'original denominator changed')
    originals = {row['workbook_id']: row for row in rows}
    require(set(ids) <= originals.keys(), 'unknown requested identity')
    require(len({row['sha256'] for row in rows}) == len(rows), 'ambiguous duplicate original bytes')
    require(len({row['path'] for row in rows}) == len(rows), 'ambiguous original path')
    require(bool(history_roots), 'known history namespaces must be configured')
    survey_binding = manifest['survey']
    survey = read(survey_binding)
    require(survey.get('unmapped_prior_artifacts') == [], 'unmapped historical task evidence')
    roots = survey.get('scanned_roots')
    require(isinstance(roots, list) and len(roots) == len(set(roots)) and
            set(roots) == {str(schema.safe_path(p)) for p in history_roots},
            'history namespace scope differs from frozen survey')
    require(survey.get('inventory') == original_inventory, 'survey original inventory differs')
    evidence = [candidate_binding, survey_binding]
    for wb, row in zip(ids, selected):
        require(isinstance(row, dict) and row.get('original') == originals[wb],
                'selected original differs from frozen requested identity')
        require(row.get('exclusion_evidence') == [], 'selected prior-task exclusion changed')
        proof=row.get('pre_audit_history')
        if proof is not None:
            from scripts.batch import harbor_pre_audit_history as preaudit
            inherited=preaudit.validate(proof,workbook=wb,
                original=record(originals[wb]['path']),history_roots=history_roots,
                known_ids=set(originals))
            retained=row.get('retained_curation')
            lifecycle.checked_record(retained)
            require(retained['sha256']==inherited['curations'][0]['sha256'],
                    'migration cannot change retained curation')
            evidence.append(proof)
        generated=row.get('generated_curation')
        if generated is not None:
            require(proof is None,'generated curation cannot replace historical curation')
            from scripts.batch import harbor_generated_curation as generated_curation
            adopted=generated_curation.validate(generated,workbook=wb,original=record(originals[wb]['path']),history_roots=history_roots,known_ids=set(originals))
            require(adopted['ledger']==row['ledger'] and adopted['curation']==row['retained_curation'] and adopted['ordered_targets']==row['retained_targets'],'candidate differs from generated curation adoption')
            evidence.append(generated)
        prior = row.get('prior_audit_metadata')
        require(isinstance(prior, list), 'prior audit evidence list required')
        for bound in prior:
            lifecycle.checked_record(bound)
            evidence.append(bound)
    return AdmissionPolicy(batch, tuple(ids), tracker_root, original_inventory,
                           dependency, dependencies, tuple(history_roots),
                           expected_original_count=expected_original_count,
                           campaign_evidence=tuple(evidence))


def policy_from_candidate_manifest(candidate_binding, *, batch, tracker_root,
                                   original_inventory, dependency, dependencies,
                                   history_roots, expected_original_count=700):
    """Bind a versioned frozen selection to its exact proposal and originals.

    The root chooses when to start this campaign after the prior plan. This is
    admission evidence validation, not another authorization or approval layer.
    Replacements require a revised frozen candidate manifest, never a silent ID swap.
    """
    manifest=read(candidate_binding)
    if manifest.get('schema_version') == 'harbor-qualified-task-candidate-proposal/v1':
        return _policy_from_general_cohort(
            manifest, candidate_binding, batch=batch, tracker_root=tracker_root,
            original_inventory=original_inventory, dependency=dependency,
            dependencies=dependencies, history_roots=history_roots,
            expected_original_count=expected_original_count)
    require(manifest.get('schema_version')=='new50-qualified-task-candidate-proposal/v1','unsupported candidate manifest')
    require(manifest.get('target_new_qualified_tasks')==50 and manifest.get('denominator_for_original_rebuild')==expected_original_count,'campaign objective/denominator changed')
    survey=read(manifest['survey'])
    require(not survey.get('unmapped_prior_artifacts'),'unmapped historical task evidence')
    require(set(survey.get('scanned_roots',[]))=={str(schema.safe_path(p)) for p in history_roots},'history namespace scope differs from frozen survey')
    lifecycle.checked_record(original_inventory)
    require(survey['inventory']['path']==original_inventory['path'] and survey['inventory']['sha256']==original_inventory['sha256'],'survey original inventory differs')
    originals={r['workbook_id']:r for r in read_inventory(original_inventory['path'])['sources']}
    selected=manifest.get('selected_candidates',[])
    require(len(selected)==50,'initial campaign requires exact 50 selected originals')
    ids=[]
    campaign_evidence=[candidate_binding,manifest['survey']]
    for row in selected:
        source=row['original'];wb=source['workbook_id']
        require(originals.get(wb)==source and not row.get('exclusion_evidence'),'selected original identity or prior-task exclusion changed')
        require(wb not in ids,'duplicate selected original')
        ids.append(wb)
        for evidence in row.get('prior_audit_metadata',[]):
            lifecycle.checked_record(evidence)
            campaign_evidence.append(evidence)
    policy=AdmissionPolicy(batch,tuple(ids),tracker_root,original_inventory,dependency,dependencies,tuple(history_roots),expected_original_count=expected_original_count,campaign_evidence=tuple(campaign_evidence))
    return policy
