"""Explicit completed-audit validator upgrade; no historical code execution.

The ordinary auditor cache remains unchanged. This adapter supports only the
reviewed addition of three completion guards and multiline label rendering.
It preserves actual invocation identity and independently binds current checks.
"""
from pathlib import Path
import hashlib
import json
import xl_variable_source_audit as audit
from xl_variable_source_audit import (AuditRevalidationRequired, GENERATOR_VERSION,
    PROMPT_VERSION, DEFAULT_MODEL, PROD_ENDPOINT, DEFAULT_PROJECT_ID, SYSTEM_PROMPT,
    _request_config_fingerprint, _canonical_hash, validate_audit_coverage,
    _coverage_dispositions, _attempt_scope_config, _read_bound_json,
    _inherited_batch_counts, _split_chunks, _base_messages, _clean_table,
    validate_row_ids, validate_table, resolve_table, _correction_messages)

SCHEMA = 'completed-audit-validator-upgrade/v1'
POLICY = 'audit-completion-label-lines-and-selected-lookup/v3'
ORIGINAL_SHA256 = '88338cdb4653e1d0d8f12efc61a07afce540ccbcbe991135121fa66b6bcc14a1'
LABEL_RENDERING = (
    b'    label = str(row.get("label") or "").strip()\n',
    b'    label = "<br>".join(str(row.get("label") or "").strip().splitlines())\n',
)
AUDIT_KEYS = ('variable_source_metadata', 'variable_source_inventory', 'variable_source_audit')
GUARDS = (
    b'        if response.get("finish_reason") != "stop":\n            raise ValueError("audit model completion is incomplete")\n',
    b'                    if response.get("finish_reason") != "stop":\n                        raise ValueError("audit model completion is incomplete")\n',
    b'                if finish != "stop":\n                    raise ValueError("audit model completion is incomplete")\n',
)


def require(value, message):
    if not value:raise ValueError(message)


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(binding):
    require(isinstance(binding,dict) and set(binding)=={'path','sha256'},'exact evidence record required')
    path=Path(binding['path'])
    require(path.is_absolute() and path.is_file() and not path.is_symlink(),'regular absolute evidence path required')
    require(digest(path)==binding['sha256'],'altered upgrade evidence')
    return path


def record(path):
    path=Path(path).resolve();return {'path':str(path),'sha256':digest(path)}


def code_identity(original_code):
    original=checked(original_code)
    from xl_audit_inventory_upgrade import BEFORE_SHA256, restore_inventory_lookup, evidence
    require(digest(original) in (ORIGINAL_SHA256,BEFORE_SHA256),'unreviewed original generator')
    current=Path(audit.__file__).resolve();raw=current.read_bytes();restored=restore_inventory_lookup(raw)
    if digest(original)==BEFORE_SHA256:
        require(restored==original.read_bytes(),'original pre-streaming auditor differs')
        return {'policy':POLICY,'original_generator':original_code,'current_validator':record(current),
                'adapter':record(__file__),'inventory_lookup_code':evidence(),'historical_code_executed':False}
    require(restored.count(LABEL_RENDERING[1])==1,'current generator lacks exact reviewed label rendering')
    restored=restored.replace(LABEL_RENDERING[1],LABEL_RENDERING[0],1)
    for guard in GUARDS:
        require(restored.count(guard)==1,'current generator is not the exact reviewed completion upgrade')
        restored=restored.replace(guard,b'',1)
    require(hashlib.sha256(restored).hexdigest()==ORIGINAL_SHA256 and restored==original.read_bytes(),
            'current generator differs beyond reviewed completion guards and label rendering')
    return {'policy':POLICY,'original_generator':original_code,'current_validator':record(current),
            'adapter':record(__file__),'inventory_lookup_code':evidence(),'historical_code_executed':False}


def _validate_original_coverage(metadata_path, inventory_path, markdown_path, *, coverage_path=None,
                                  model=DEFAULT_MODEL, endpoint=PROD_ENDPOINT, project_id=DEFAULT_PROJECT_ID, generator_sha256=ORIGINAL_SHA256):
    """Validate saved coverage without service calls or file mutations.

    Missing coverage is unproven evidence, never evidence that the workbook is invalid.
    """
    metadata_path, inventory_path, markdown_path = map(Path, (metadata_path, inventory_path, markdown_path))
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("audit metadata is missing or unreadable") from exc
    if not isinstance(metadata, dict) or metadata.get("status") != "complete":
        raise ValueError("audit metadata is not complete")
    expected_config = _request_config_fingerprint(endpoint, model, project_id)
    if (metadata.get("generator_version") != GENERATOR_VERSION or metadata.get("prompt_version") != PROMPT_VERSION
            or metadata.get("generator_sha256") != generator_sha256):
        raise AuditRevalidationRequired("audit generator or prompt version requires revalidation")
    if (metadata.get("model") != model or not isinstance(metadata.get("endpoint"), str) or metadata["endpoint"].rstrip("/") != endpoint.rstrip("/")
            or metadata.get("project_id") != project_id or metadata.get("request_config_sha256") != expected_config
            or metadata.get("system_prompt_sha256") != hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()):
        raise AuditRevalidationRequired("audit request/model/prompt configuration requires revalidation")
    if (not isinstance(metadata.get("coverage_path"), str) or not metadata["coverage_path"]
            or not isinstance(metadata.get("coverage_sha256"), str) or not metadata["coverage_sha256"]):
        raise ValueError("missing bound audit coverage evidence")
    if coverage_path is None:
        suffix = ".metadata.json"
        if not metadata_path.name.endswith(suffix):
            raise ValueError("audit metadata filename cannot resolve coverage")
        coverage_path = metadata_path.with_name(metadata_path.name[:-len(suffix)] + ".coverage.json")
    coverage_path = Path(coverage_path)
    if Path(metadata["coverage_path"]).resolve() != coverage_path.resolve():
        raise ValueError("audit coverage path changed")
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        text = markdown_path.read_text(encoding="utf-8")
        raw = coverage_path.read_bytes()
        coverage = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("audit coverage evidence is missing or unreadable") from exc
    if metadata["coverage_sha256"] != hashlib.sha256(raw).hexdigest():
        raise ValueError("audit coverage hash changed")
    if metadata.get("inventory_sha256") != _canonical_hash(inventory):
        raise ValueError("audit inventory hash changed")
    if metadata.get("markdown_sha256") != hashlib.sha256(markdown_path.read_bytes()).hexdigest():
        raise ValueError("audit text hash changed")
    try:
        faults = validate_audit_coverage(text, inventory, coverage)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("audit coverage structure is invalid") from exc
    if faults:
        raise ValueError("; ".join(faults))
    _validate_original_attempts(metadata, inventory, coverage, endpoint, model, project_id, generator_sha256)
    return {"passed": True, "inventory_rows": len(inventory["variables"]),
            **_coverage_dispositions(coverage),
            "coverage_path": str(coverage_path), "coverage_sha256": metadata["coverage_sha256"]}

def _validate_original_attempts(metadata, inventory, coverage, endpoint, model, project_id, generator_sha256=ORIGINAL_SHA256):
    scope = _attempt_scope_config(_canonical_hash(inventory), endpoint, model, project_id)
    scope["generator_sha256"] = generator_sha256
    if metadata.get("attempt_scope") != scope:
        raise ValueError("audit attempt scope changed")
    policy = _read_bound_json(metadata.get("attempt_policy"))
    if (not isinstance(policy, dict) or policy.get("schema_version") != "audit-attempt-policy/v1"
            or policy.get("scope") != scope or type(policy.get("limit")) is not int or policy["limit"] not in (1, 2)):
        raise ValueError("audit attempt policy is invalid")
    consumed = _inherited_batch_counts(policy.get("inherited_budget"), inventory)
    if not isinstance(policy.get("submitted_counts"), dict) or set(policy["submitted_counts"]) != {str(i) for i in consumed}:
        raise ValueError("audit persisted batch counts are incomplete or unknown")
    for index, ((header, rows), batch) in enumerate(zip(_split_chunks(inventory), coverage["batches"]), 1):
        attempt = batch.get("attempt")
        if (type(attempt) is not int or not 1 <= attempt <= min(policy["limit"], 2 - consumed[index])
                or not isinstance(policy.get("submitted_counts"), dict) or policy["submitted_counts"].get(str(index)) != attempt):
            raise ValueError("audit attempt exceeds semantic budget")
        request, response = _read_bound_json(batch.get("request")), _read_bound_json(batch.get("response"))
        expected = {"endpoint": endpoint.rstrip("/"), "model": model, "project_id": project_id,
                    "scope": scope, "batch": index, "attempt": attempt}
        if not isinstance(request, dict) or any(request.get(k) != v for k, v in expected.items()):
            raise ValueError("audit saved request configuration changed")
        messages = request.get("messages")
        if not isinstance(messages, list) or messages[:2] != _base_messages(header, rows, index, len(coverage["batches"])):
            raise ValueError("audit saved request prompt/inventory changed")
        if (not isinstance(response, dict) or response.get("request_sha256") != batch["request"]["sha256"]
                or not isinstance(response.get("content"), str) or _clean_table(response["content"]) != batch["model_table"]):
            raise ValueError("audit raw table differs from bound model response")
        if response.get("finish_reason") != "stop":
            raise ValueError("audit model completion is incomplete")
        history = batch.get("history")
        if not isinstance(history, list) or len(history) != attempt:
            raise ValueError("audit semantic attempt history is incomplete")
        for number, binding in enumerate(history, 1):
            prior = _read_bound_json(binding)
            if (not isinstance(prior, dict) or prior.get("scope") != scope
                    or prior.get("batch") != index or prior.get("attempt") != number):
                raise ValueError("audit semantic attempt history changed")
        if history[-1] != batch["request"]:
            raise ValueError("audit accepted attempt is not final history entry")
        response_history = batch.get("response_history")
        if not isinstance(response_history, list) or len(response_history) != attempt or response_history[-1] != batch["response"]:
            raise ValueError("audit response history is incomplete")
        prior_responses = []
        for old_request, old_response in zip(history, response_history):
            if old_response is None:
                prior_responses.append(None)
                continue
            item = _read_bound_json(old_response)
            if not isinstance(item, dict) or item.get("request_sha256") != old_request["sha256"] or not isinstance(item.get("content"), str):
                raise ValueError("audit response history binding changed")
            prior_responses.append(item)
        if len(messages) > 2:
            expected_messages = _base_messages(header, rows, index, len(coverage["batches"]))
            for old in prior_responses[:-1]:
                if old is None:
                    raise ValueError("audit correction lacks a received response")
                old_table = _clean_table(old["content"])
                row_ids = {json.loads(row)["row_id"] for row in rows}
                violations = validate_row_ids(old_table, row_ids, {row["row_id"] for row in inventory["variables"]})
                if not violations:
                    violations = validate_table(resolve_table(old_table, {row["row_id"]: row for row in inventory["variables"]}), rows, inventory["metadata"])
                if not violations:
                    raise ValueError("audit correction does not follow a rejected response")
                expected_messages.extend(_correction_messages(old["content"], violations))
            if messages != expected_messages:
                raise ValueError("audit correction instructions changed")


def propose_upgrade(bindings, *, original_code, model=DEFAULT_MODEL, endpoint=PROD_ENDPOINT, project_id=DEFAULT_PROJECT_ID):
    """Pure proposal; input identities are selected by the owning root."""
    identity=code_identity(original_code)
    selected={k:bindings[k] for k in AUDIT_KEYS}
    paths=[checked(selected[k]) for k in AUDIT_KEYS]
    result=_validate_original_coverage(*paths,model=model,endpoint=endpoint,project_id=project_id,
        generator_sha256=identity['original_generator']['sha256'])
    inventory_context={}
    from xl_audit_inventory_upgrade import BEFORE_SHA256
    if identity['original_generator']['sha256']==BEFORE_SHA256:
        # Completed current scopes may use this proof only when the full live
        # source/segmentation/baseline inventory independently reconstructs.
        from scripts.qualified_publication import _validate_current_audit_context
        inventory_context=_validate_current_audit_context(bindings,*paths[:2])
    metadata=json.loads(paths[0].read_text());inventory=json.loads(paths[1].read_text())
    coverage_path=Path(result['coverage_path']);coverage=json.loads(coverage_path.read_text())
    files={str(p):digest(p) for p in paths};files.update(inventory_context)
    files[str(coverage_path.resolve())]=digest(coverage_path)
    def collect(value):
        if isinstance(value,dict):
            if set(value)=={'path','sha256'}:
                p=checked(value);files[str(p.resolve())]=digest(p)
            for child in value.values():collect(child)
        elif isinstance(value,list):
            for child in value:collect(child)
    collect(metadata);collect(coverage)
    policy=audit._read_bound_json(metadata['attempt_policy']);collect(policy)
    if policy.get('inherited_budget'):collect(audit._read_bound_json(policy['inherited_budget']))
    inherited=audit._inherited_batch_counts(policy.get('inherited_budget'),inventory)
    counts={'limit':policy['limit'],'submitted_counts':policy['submitted_counts'],
            'inherited_batch_counts':{str(k):v for k,v in inherited.items()}}
    for key in ('original_generator','current_validator','adapter','inventory_lookup_code'):
        collect(identity[key])
    return {'schema_version':SCHEMA,'policy':POLICY,'identity':identity,'audit_bindings':selected,
        'config':{'model':model,'endpoint':endpoint,'project_id':project_id},
        'coverage':result,'retained_consumption':counts,
        'accepted_completions':[{'request':b['request'],'response':b['response'],'finish_reason':'stop'} for b in coverage['batches']],
        'checked_files':dict(sorted(files.items())),'model_reexecuted':False,'semantic_attempts_added':0,
        'source_approval':False}


def validate_upgrade(receipt_binding, *, bindings, original_code, model=DEFAULT_MODEL, endpoint=PROD_ENDPOINT, project_id=DEFAULT_PROJECT_ID):
    """Recompute using root-selected code and artifacts; never trust PASS fields."""
    path=checked(receipt_binding);receipt=json.loads(path.read_text())
    from xl_audit_inventory_lineage import AUDIT_SCHEMA, validate_renderer
    if receipt.get('schema_version')==AUDIT_SCHEMA:
        return validate_renderer(receipt_binding,bindings=bindings,original_code=original_code,model=model,endpoint=endpoint,project_id=project_id)
    from xl_audit_renderer_recovery import SCHEMA as RENDERER_SCHEMA, validate_receipt
    if receipt.get('schema_version')==RENDERER_SCHEMA:
        return validate_receipt(receipt_binding,bindings=bindings,original_code=original_code,model=model,endpoint=endpoint,project_id=project_id)
    expected=propose_upgrade(bindings,original_code=original_code,model=model,endpoint=endpoint,project_id=project_id)
    require(receipt==expected,'completed audit upgrade receipt is stale or altered')
    return {**expected['checked_files'],str(path.resolve()):digest(path)}
