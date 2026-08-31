"""Deterministic restriction-cone certificates for restricted source generations."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import diagnostics, evaluate, model


SCHEMA_VERSION = "restriction-cone-certificate/v1"
POLICY_PROFILE_SCHEMAS = {
    "source-restriction-profile/v2",
    "source-restriction-profile/v3",
}
MAX_DYNAMIC_TARGET_CELLS = 10_000
MAX_EXCEL_ROW = 1_048_576
MAX_EXCEL_COLUMN = 16_384
SEGMENTATION_BINDING_KEYS = frozenset({
    "source_generation",
    "source_restriction_evidence",
    "source_restriction_profile",
    "source_inventory_approval",
    "source_recalc_signals",
    "restriction_cone_certificate",
})
_A1_TARGET_RE = re.compile(
    r"(?i)^(?:(?:'(?P<quoted>(?:[^']|'')+)'|"
    r"(?P<bare>[A-Z_\\][A-Z0-9_. &\\-]*))!)?"
    r"\$?(?P<col0>[A-Z]{1,3})\$?(?P<row0>[1-9][0-9]*)"
    r"(?::\$?(?P<col1>[A-Z]{1,3})\$?(?P<row1>[1-9][0-9]*))?$"
)


class RestrictionConeError(ValueError):
    """The restricted source cannot be proven safe for the selected outputs."""


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def object_hash(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def certificate_bytes(certificate: dict) -> bytes:
    return canonical_bytes(certificate)


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RestrictionConeError(f"cannot read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise RestrictionConeError(f"{description} must be a JSON object")
    return value


def _unsigned_hash(value: dict, field: str) -> str:
    unsigned = dict(value)
    unsigned.pop(field, None)
    return object_hash(unsigned)


def base_segmentation_fingerprints(fingerprints: dict) -> dict:
    if not isinstance(fingerprints, dict):
        raise RestrictionConeError("segmentation fingerprints must be an object")
    return {
        key: value
        for key, value in fingerprints.items()
        if key not in SEGMENTATION_BINDING_KEYS
    }


def _proof_cone(outputs: list[str], runtime_radj: dict) -> set[str]:
    cone = set(outputs)
    stack = list(outputs)
    while stack:
        target = stack.pop()
        sources = runtime_radj.get(target)
        if not isinstance(sources, list):
            sources = []
        for source in sources:
            if not isinstance(source, str):
                raise RestrictionConeError("runtime proof contains a non-string cell")
            if source not in cone:
                cone.add(source)
                stack.append(source)
    return cone


def _immutable_cells(profile: dict) -> dict[str, object]:
    declared = profile.get("immutable_cells", [])
    if not isinstance(declared, list):
        raise RestrictionConeError("restriction profile immutable_cells must be a list")
    result = {}
    for item in declared:
        if not isinstance(item, dict):
            raise RestrictionConeError("immutable cell declaration must be an object")
        coordinate = item.get("coordinate", item.get("cell"))
        if not isinstance(coordinate, str) or model.split_ref(coordinate) is None:
            raise RestrictionConeError("immutable cell has an invalid coordinate")
        if coordinate in result or "value" not in item:
            raise RestrictionConeError("immutable cell declarations are incomplete or duplicate")
        result[coordinate] = item["value"]
    return result


def _arg_sources(graph: model.Graph, op_node: model.Node, index: int) -> list[str]:
    return sorted({
        edge.source
        for edge in graph.in_edges.get(op_node.id, ())
        if edge.arg_index == index
    })


def _static_scalar(
    graph: model.Graph,
    node_id: str,
    immutable: dict[str, object],
    *,
    seen: set[str] | None = None,
) -> tuple[object, list[dict]]:
    seen = set() if seen is None else set(seen)
    if node_id in seen:
        raise RestrictionConeError("dynamic argument contains an AST cycle")
    seen.add(node_id)
    node = graph.nodes.get(node_id)
    if node is None:
        raise RestrictionConeError("dynamic argument AST node is missing")
    if node.kind == "const":
        if isinstance(node.value, str) and node.value.casefold() in {"true", "false"}:
            value = node.value.casefold() == "true"
        else:
            value = evaluate.literal(node.value)
        return value, [{
            "kind": "literal",
            "ast_node": node.id,
            "value": value,
            "binding_sha256": object_hash({
                "ast_node": node.id,
                "value": value,
            }),
        }]
    if node.is_cell:
        if node.kind == "formula":
            raise RestrictionConeError(
                f"dynamic argument depends on formula cell {node.id}"
            )
        if node.id not in immutable:
            raise RestrictionConeError(
                f"dynamic argument depends on task-editable cell {node.id}"
            )
        observed = evaluate.literal(node.value)
        declared = immutable[node.id]
        if observed != declared:
            raise RestrictionConeError(
                f"immutable cell value disagrees with AST: {node.id}"
            )
        return observed, [{
            "kind": "immutable_cell",
            "coordinate": node.id,
            "value": declared,
            "binding_sha256": object_hash({
                "coordinate": node.id,
                "value": declared,
            }),
        }]
    if node.kind != "op":
        raise RestrictionConeError("dynamic argument depends on a non-scalar AST node")
    arity = int(node.arity) if str(node.arity).isdigit() else 0
    arguments = []
    records = []
    for index in range(arity):
        sources = _arg_sources(graph, node, index)
        if len(sources) != 1:
            raise RestrictionConeError(
                "dynamic argument operation has omitted or non-scalar inputs"
            )
        value, bindings = _static_scalar(
            graph, sources[0], immutable, seen=seen
        )
        arguments.append(value)
        records.extend(bindings)
    op = node.op.upper()
    try:
        if op == "+":
            value = +float(arguments[0]) if len(arguments) == 1 else (
                float(arguments[0]) + float(arguments[1])
            )
        elif op == "-":
            value = -float(arguments[0]) if len(arguments) == 1 else (
                float(arguments[0]) - float(arguments[1])
            )
        elif op == "*":
            value = float(arguments[0]) * float(arguments[1])
        elif op == "/":
            value = float(arguments[0]) / float(arguments[1])
        elif op == "^":
            value = float(arguments[0]) ** float(arguments[1])
        elif op == "&":
            value = "".join(str(item) for item in arguments)
        else:
            raise RestrictionConeError(
                f"dynamic argument operation is not immutable-safe: {node.op}"
            )
    except (IndexError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise RestrictionConeError("dynamic argument cannot be computed safely") from exc
    unique = {
        object_hash(record): record
        for record in records
    }
    return value, [unique[key] for key in sorted(unique)]


def _integer(value: object, description: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (int, float)) or not float(value).is_integer():
        raise RestrictionConeError(f"{description} is not an integer")
    return int(value)


def _bounded_rectangle(
    sheet: str,
    start_row: int,
    start_col: int,
    height: int,
    width: int,
) -> list[str]:
    if (
        start_row < 1
        or start_col < 1
        or height < 1
        or width < 1
        or start_row + height - 1 > MAX_EXCEL_ROW
        or start_col + width - 1 > MAX_EXCEL_COLUMN
    ):
        raise RestrictionConeError("dynamic target is empty or out of bounds")
    count = height * width
    if count > MAX_DYNAMIC_TARGET_CELLS:
        raise RestrictionConeError(
            f"dynamic target exceeds hard cap of {MAX_DYNAMIC_TARGET_CELLS} cells"
        )
    return [
        model.a1(sheet, row, col)
        for row in range(start_row, start_row + height)
        for col in range(start_col, start_col + width)
    ]


def _offset_targets(
    graph: model.Graph,
    op_node: model.Node,
    immutable: dict[str, object],
) -> tuple[list[str], list[dict]]:
    base_sources = _arg_sources(graph, op_node, 0)
    if len(base_sources) != 1:
        raise RestrictionConeError("OFFSET base is omitted or not a single AST cell")
    base = graph.nodes.get(base_sources[0])
    if base is None or not base.is_cell or base.row is None or base.col is None:
        raise RestrictionConeError("OFFSET base is not an internal AST cell")
    base_dependency = {
        "kind": "static_cell_reference",
        "coordinate": base.id,
        "binding_sha256": object_hash({
            "kind": "static_cell_reference",
            "coordinate": base.id,
        }),
    }
    records = [{
        "argument_index": 0,
        "argument": "base",
        "value": base.id,
        "dependencies": [base_dependency],
        "binding_sha256": object_hash({
            "argument_index": 0,
            "value": base.id,
            "dependencies": [base_dependency],
        }),
    }]

    def scalar(
        index: int,
        default: object,
        description: str,
        *,
        required: bool = False,
    ) -> object:
        sources = _arg_sources(graph, op_node, index)
        if not sources:
            if required:
                raise RestrictionConeError(
                    f"OFFSET {description} argument is omitted"
                )
            default_dependency = {
                "kind": "implicit_default",
                "value": default,
                "binding_sha256": object_hash({
                    "argument_index": index,
                    "value": default,
                    "kind": "implicit_default",
                }),
            }
            records.append({
                "argument_index": index,
                "argument": description,
                "value": default,
                "dependencies": [default_dependency],
                "binding_sha256": object_hash({
                    "argument_index": index,
                    "value": default,
                    "dependencies": [default_dependency],
                }),
            })
            return default
        if len(sources) != 1:
            raise RestrictionConeError(f"OFFSET {description} is not scalar")
        value, bindings = _static_scalar(graph, sources[0], immutable)
        records.append({
            "argument_index": index,
            "argument": description,
            "value": value,
            "dependencies": bindings,
            "binding_sha256": object_hash({
                "argument_index": index,
                "value": value,
                "dependencies": bindings,
            }),
        })
        return value

    rows = _integer(
        scalar(1, 0, "rows", required=True), "OFFSET rows"
    )
    columns = _integer(
        scalar(2, 0, "columns", required=True), "OFFSET columns"
    )
    height = _integer(scalar(3, 1, "height"), "OFFSET height")
    width = _integer(scalar(4, 1, "width"), "OFFSET width")
    targets = _bounded_rectangle(
        base.sheet,
        base.row + rows,
        base.col + columns,
        height,
        width,
    )
    return targets, records


def _indirect_targets(
    graph: model.Graph,
    op_node: model.Node,
    immutable: dict[str, object],
) -> tuple[list[str], list[dict]]:
    address_sources = _arg_sources(graph, op_node, 0)
    if len(address_sources) != 1:
        raise RestrictionConeError("INDIRECT address is omitted or not scalar")
    address, address_bindings = _static_scalar(
        graph, address_sources[0], immutable
    )
    records = [{
        "argument_index": 0,
        "argument": "address",
        "value": address,
        "dependencies": address_bindings,
        "binding_sha256": object_hash({
            "argument_index": 0,
            "value": address,
            "dependencies": address_bindings,
        }),
    }]
    a1_sources = _arg_sources(graph, op_node, 1)
    if a1_sources:
        if len(a1_sources) != 1:
            raise RestrictionConeError("INDIRECT A1-mode argument is not scalar")
        a1_mode, bindings = _static_scalar(graph, a1_sources[0], immutable)
        records.append({
            "argument_index": 1,
            "argument": "a1_mode",
            "value": a1_mode,
            "dependencies": bindings,
            "binding_sha256": object_hash({
                "argument_index": 1,
                "value": a1_mode,
                "dependencies": bindings,
            }),
        })
        if not bool(a1_mode):
            raise RestrictionConeError("R1C1 INDIRECT is not permitted")
    else:
        dependency = {
            "kind": "implicit_default",
            "value": True,
            "binding_sha256": object_hash({
                "argument_index": 1,
                "value": True,
                "kind": "implicit_default",
            }),
        }
        records.append({
            "argument_index": 1,
            "argument": "a1_mode",
            "value": True,
            "dependencies": [dependency],
            "binding_sha256": object_hash({
                "argument_index": 1,
                "value": True,
                "dependencies": [dependency],
            }),
        })
    if not isinstance(address, str) or "[" in address or "]" in address:
        raise RestrictionConeError("INDIRECT address is external or non-text")
    match = _A1_TARGET_RE.fullmatch(address.strip())
    if match is None:
        raise RestrictionConeError("INDIRECT address is not an internal A1 reference")
    sheet = (match.group("quoted") or match.group("bare") or op_node.sheet)
    sheet = sheet.replace("''", "'").strip()
    row0 = int(match.group("row0"))
    col0 = model.col_number(match.group("col0"))
    row1 = int(match.group("row1") or row0)
    col1 = model.col_number(match.group("col1") or match.group("col0"))
    targets = _bounded_rectangle(
        sheet,
        min(row0, row1),
        min(col0, col1),
        abs(row1 - row0) + 1,
        abs(col1 - col0) + 1,
    )
    return targets, records


def _call_expression(formula: str, offset: object, function: str) -> tuple[str, int]:
    if not isinstance(offset, int) or offset < 0 or offset >= len(formula):
        raise RestrictionConeError("restriction event has an invalid token offset")
    match = re.match(
        rf"(?i)(?:_xlfn\.)?{re.escape(function)}\s*\(",
        formula[offset:],
    )
    if match is None:
        raise RestrictionConeError(
            "restriction event token does not identify its formula operation"
        )
    open_index = offset + match.end() - 1
    depth = 0
    in_string = False
    index = open_index
    while index < len(formula):
        character = formula[index]
        if character == '"':
            if in_string and index + 1 < len(formula) and formula[index + 1] == '"':
                index += 2
                continue
            in_string = not in_string
        elif not in_string:
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    return formula[offset:index + 1], index + 1
        index += 1
    raise RestrictionConeError("restriction event formula operation is unbalanced")


def _canonical_call(expression: str) -> str:
    try:
        from xl_ast_graph import parse_formula

        return parse_formula("=" + expression).render()
    except Exception as exc:
        raise RestrictionConeError(
            "restriction event formula operation cannot be parsed"
        ) from exc


def _ast_ordinal(host: str, node_id: str) -> int | None:
    match = re.fullmatch(re.escape(host) + r"#([0-9]+):.+", node_id)
    return int(match.group(1)) if match else None


def _match_event_nodes(graph: model.Graph, events: list[dict]) -> list[list[str]]:
    assignments: list[list[str] | None] = [None] * len(events)
    by_host_function: dict[tuple[str, str], list[int]] = {}
    for index, event in enumerate(events):
        if event.get("event") == "volatile_function":
            function = str(event.get("function", "")).upper()
            key = (str(event.get("location", "")), function)
            by_host_function.setdefault(key, []).append(index)
        elif event.get("event") == "false_external_detection":
            location = event.get("location")
            node = graph.nodes.get(location)
            if node is None or not node.is_cell:
                raise RestrictionConeError(
                    "false-external event has no matching worksheet AST host"
                )
            assignments[index] = [node.id]
        else:
            raise RestrictionConeError(
                f"restriction event remains closed: {event.get('event')}"
            )
    used_operations = set()
    for (host, function), indices in sorted(by_host_function.items()):
        host_node = graph.nodes.get(host)
        if host_node is None or host_node.kind != "formula":
            raise RestrictionConeError(
                f"restriction event has no formula AST host: {host}"
            )
        event_calls = []
        event_formula = (
            host_node.formula[1:]
            if host_node.formula.startswith("=")
            else host_node.formula
        )
        for index in indices:
            expression, end = _call_expression(
                event_formula,
                events[index].get("token_offset"),
                function,
            )
            event_calls.append((end, index, _canonical_call(expression)))
        if len({events[index].get("token_offset") for index in indices}) != len(
            indices
        ):
            raise RestrictionConeError(
                f"restriction event mapping is ambiguous for {host} {function}"
            )
        nodes = [
            node
            for node in graph.nodes.values()
            if (
                node.kind == "op"
                and node.owner == host
                and node.op.upper().removeprefix("_XLFN.") == function
            )
        ]
        if len(nodes) != len(indices):
            raise RestrictionConeError(
                f"restriction event/AST coverage mismatch for {host} {function}"
            )
        ordinals = [_ast_ordinal(host, node.id) for node in nodes]
        if len(nodes) > 1 and (
            any(ordinal is None for ordinal in ordinals)
            or len(set(ordinals)) != len(ordinals)
        ):
            raise RestrictionConeError(
                f"restriction event mapping is ambiguous for {host} {function}"
            )
        ordered_nodes = (
            [nodes[0]]
            if len(nodes) == 1
            else [
                node
                for _, node in sorted(
                    zip(ordinals, nodes), key=lambda item: item[0]
                )
            ]
        )
        for (_, index, canonical), node in zip(
            sorted(event_calls), ordered_nodes
        ):
            if not node.expr or node.expr != canonical:
                raise RestrictionConeError(
                    f"restriction event does not match exact AST operation "
                    f"for {host} {function}"
                )
            if node.id in used_operations:
                raise RestrictionConeError(
                    "restriction events reuse the same AST operation"
                )
            used_operations.add(node.id)
            assignments[index] = [node.id]
    if any(item is None for item in assignments):
        raise RestrictionConeError("restriction event coverage is incomplete")
    restricted_operations = {
        node.id
        for node in graph.nodes.values()
        if (
            node.kind == "op"
            and node.op.upper().removeprefix("_XLFN.")
            in {"OFFSET", "INDIRECT", "TODAY", "NOW", "CELL"}
        )
    }
    unmatched_operations = sorted(restricted_operations - used_operations)
    if unmatched_operations:
        raise RestrictionConeError(
            "restricted volatile AST operations lack one-to-one health-ledger "
            f"events: {unmatched_operations[:3]}"
        )
    return [list(item or ()) for item in assignments]


def _strict_verification_passes(verification: dict, proof: dict) -> bool:
    closure = proof.get("closure") if isinstance(proof, dict) else {}
    return (
        verification.get("status") == "pass"
        and verification.get("disposition") == "pass"
        and verification.get("blocking_reasons") == []
        and verification.get("skipped") is False
        and verification.get("passed") is True
        and verification.get("provenance", {}).get("proof", {}).get("strict") is True
        and verification.get("counts", {}).get("cache_reads", {}).get("proof") == 0
        and isinstance(closure, dict)
        and closure.get("stabilized") is True
        and closure.get("targets_stable") is True
    )


def build_certificate(
    *,
    source_generation_dir: str | Path,
    graph: model.Graph,
    proof: dict,
    verification: dict,
    ordered_outputs: list[str],
    segmentation_fingerprints: dict,
) -> dict:
    """Build and independently check one complete source-event certificate."""
    from xl_source_publication import validate_source_generation

    source_dir = Path(source_generation_dir)
    try:
        source_manifest = validate_source_generation(source_dir)
    except ValueError as exc:
        raise RestrictionConeError(f"source generation is invalid: {exc}") from exc
    if source_manifest.get("schema_version") != "source-generation/v2":
        raise RestrictionConeError("restricted certificates require a v2 source generation")
    health = _read_json(source_dir / "health.json", "source health")
    result = _read_json(source_dir / "result.json", "restriction evidence")
    if health.get("route") not in {
        "restricted_pass",
        "restricted_recalc_pass",
    }:
        raise RestrictionConeError("source generation is not restricted")
    if not _strict_verification_passes(verification, proof):
        raise RestrictionConeError("strict segmentation verification is not fully passing")
    if graph.integrity_errors or any(
        available is not True for available in graph.capabilities.values()
    ):
        raise RestrictionConeError("AST v2 capabilities or integrity checks failed")
    events = health.get("restriction_events")
    if not isinstance(events, list) or not events:
        raise RestrictionConeError("restricted source has no complete event ledger")
    if health.get("restriction_events_sha256") != object_hash(events):
        raise RestrictionConeError("restriction event ledger hash is invalid")
    if result.get("restriction", {}).get("restriction_events") != events:
        raise RestrictionConeError("restriction result and health event ledgers disagree")
    profile = health.get("restriction_profile")
    if (
        not isinstance(profile, dict)
        or profile.get("schema_version") not in POLICY_PROFILE_SCHEMAS
        or result.get("restriction", {}).get("profile") != profile
    ):
        raise RestrictionConeError("restriction policy profile is absent or mismatched")
    immutable = _immutable_cells(profile)
    outputs = list(ordered_outputs)
    if len(outputs) != len(set(outputs)) or any(
        output not in graph.nodes for output in outputs
    ):
        raise RestrictionConeError("ordered output identity is invalid")
    if verification.get("ordered_output_cells") != outputs:
        raise RestrictionConeError(
            "verification and certificate ordered outputs disagree"
        )
    runtime_radj = proof.get("runtime_radj")
    if not isinstance(runtime_radj, dict):
        raise RestrictionConeError("runtime proof graph is absent")
    cone = _proof_cone(outputs, runtime_radj)
    matches = _match_event_nodes(graph, events)
    runtime_targets_map = proof.get("resolved_operation_targets")
    if not isinstance(runtime_targets_map, dict):
        raise RestrictionConeError(
            "per-operation runtime resolved_targets ledger is absent"
        )
    dynamic_details = {}
    for index, (event, ast_nodes) in enumerate(zip(events, matches)):
        function = str(event.get("function", "")).upper()
        location = str(event.get("location", ""))
        if (
            event.get("event") != "volatile_function"
            or function not in {"OFFSET", "INDIRECT"}
            or location not in cone
        ):
            continue
        op_node = graph.nodes[ast_nodes[0]]
        if function == "OFFSET":
            targets, arguments = _offset_targets(graph, op_node, immutable)
        else:
            targets, arguments = _indirect_targets(graph, op_node, immutable)
        raw_runtime = runtime_targets_map.get(op_node.id)
        if not isinstance(raw_runtime, list) or not raw_runtime:
            raise RestrictionConeError(
                f"runtime targets were omitted for operation {op_node.id}"
            )
        if (
            any(not isinstance(target, str) for target in raw_runtime)
            or len(raw_runtime) != len(set(raw_runtime))
            or set(targets) != set(raw_runtime)
        ):
            raise RestrictionConeError(
                f"static/runtime targets differ for operation {op_node.id}"
            )
        missing = [
            target for target in targets
            if target not in graph.nodes or not graph.nodes[target].is_cell
        ]
        if missing:
            raise RestrictionConeError(
                f"dynamic targets are absent from AST: {missing[:3]}"
            )
        omitted_formulas = [
            target for target in targets
            if (
                graph.nodes[target].kind == "formula"
                and (
                    graph.nodes[target].parse_status not in {"", "ok"}
                    or graph.root_of(target) is None
                )
            )
        ]
        if omitted_formulas:
            raise RestrictionConeError(
                f"dynamic target formulas are omitted from AST: "
                f"{omitted_formulas[:3]}"
            )
        if not set(targets) <= cone:
            raise RestrictionConeError("runtime closure omitted a dynamic target")
        immutable_proofs = {
            object_hash(dependency): dependency
            for argument in arguments
            for dependency in argument.get("dependencies", [])
            if dependency.get("kind") == "immutable_cell"
        }
        dynamic_details[index] = {
            "static_targets": list(targets),
            "runtime_targets": list(raw_runtime),
            "arguments": arguments,
            "argument_dependencies": [
                {
                    "argument_index": argument["argument_index"],
                    "argument": argument["argument"],
                    "dependencies": argument["dependencies"],
                    "binding_sha256": object_hash({
                        "argument_index": argument["argument_index"],
                        "argument": argument["argument"],
                        "dependencies": argument["dependencies"],
                    }),
                }
                for argument in arguments
            ],
            "immutable_cell_proofs": [
                immutable_proofs[key] for key in sorted(immutable_proofs)
            ],
            "stability_equality_check": {
                "required": True,
                "runtime_operation": op_node.id,
                "closure_targets_stable": (
                    proof.get("closure", {}).get("targets_stable") is True
                ),
                "static_runtime_equal": set(targets) == set(raw_runtime),
                "static_targets_sha256": object_hash(sorted(targets)),
                "runtime_targets_sha256": object_hash(sorted(raw_runtime)),
            },
        }

    event_records = []
    seen_ids = set()
    for index, (event, ast_nodes) in enumerate(zip(events, matches)):
        event_hash = object_hash(event)
        event_id = object_hash({"event_index": index, "event_sha256": event_hash})
        if event_id in seen_ids:
            raise RestrictionConeError("restriction event coverage is duplicate")
        seen_ids.add(event_id)
        location = str(event.get("location", ""))
        scope = str(event.get("scope", ""))
        in_cone = location in cone if scope == "worksheet" else False
        static_targets: list[str] = []
        runtime_targets: list[str] = []
        immutable_arguments: list[dict] = []
        argument_dependencies: list[dict] = []
        immutable_cell_proofs: list[dict] = []
        stability_equality_check = {
            "required": False,
            "runtime_operation": None,
            "closure_targets_stable": (
                proof.get("closure", {}).get("targets_stable") is True
            ),
            "static_runtime_equal": None,
            "static_targets_sha256": object_hash([]),
            "runtime_targets_sha256": object_hash([]),
        }
        resolution_status = "confirmed"
        disposition = "confirmed_false_external_detection"
        function = str(event.get("function", "")).upper()
        if event.get("event") == "volatile_function":
            if function in {"TODAY", "NOW", "CELL"}:
                if in_cone:
                    raise RestrictionConeError(
                        f"{function} is inside the selected output cone at {location}"
                    )
                resolution_status = "outside_output_cone"
                disposition = "allowed_only_outside_output_cone"
            elif function in {"OFFSET", "INDIRECT"}:
                if not in_cone:
                    resolution_status = "outside_output_cone"
                    disposition = "dynamic_reference_outside_output_cone"
                else:
                    details = dynamic_details[index]
                    static_targets = details["static_targets"]
                    runtime_targets = details["runtime_targets"]
                    immutable_arguments = details["arguments"]
                    argument_dependencies = details["argument_dependencies"]
                    immutable_cell_proofs = details["immutable_cell_proofs"]
                    stability_equality_check = details[
                        "stability_equality_check"
                    ]
                    resolution_status = "bounded_static_runtime_equal"
                    disposition = "certified_internal_dynamic_reference"
            else:
                raise RestrictionConeError(
                    f"volatile function remains closed: {function}"
                )
        record = {
            "event_id": event_id,
            "event_sha256": event_hash,
            "event_index": index,
            "event": event,
            "host_location": location,
            "scope": scope,
            "package_part": location if scope == "package" else None,
            "matching_ast_nodes": ast_nodes,
            "ast_operation_node": (
                ast_nodes[0]
                if event.get("event") == "volatile_function"
                else None
            ),
            "in_output_cone": in_cone,
            "resolution_status": resolution_status,
            "runtime_targets": runtime_targets,
            "static_targets": static_targets,
            "immutable_dynamic_arguments": immutable_arguments,
            "dynamic_argument_dependencies": argument_dependencies,
            "immutable_cell_proofs": immutable_cell_proofs,
            "stability_equality_check": stability_equality_check,
            "disposition": disposition,
        }
        record["record_sha256"] = object_hash(record)
        event_records.append(record)

    base_fingerprints = base_segmentation_fingerprints(segmentation_fingerprints)
    source_bindings = source_manifest.get("bindings") or {}
    certificate = {
        "schema_version": SCHEMA_VERSION,
        "source_generation": {
            "generation_id": source_manifest.get("generation_id"),
            "manifest_sha256": diagnostics.fingerprint_file(
                source_dir / "generation-manifest.json"
            )["sha256"],
            "source_sha256": source_bindings.get("source_sha256"),
            "health_artifact_sha256": source_bindings.get("health_sha256"),
            "health_report_sha256": source_bindings.get("health_report_sha256"),
            "inventory_approval_sha256": source_bindings.get(
                "inventory_approval_sha256"
            ),
            "restriction_evidence_sha256": source_bindings.get(
                "restriction_evidence_sha256"
            ),
            "restriction_events_sha256": source_bindings.get(
                "restriction_events_sha256"
            ),
            "recalc_signals_sha256": source_bindings.get(
                "recalc_signals_sha256"
            ),
            "policy_version": health.get("policy_version"),
            "restriction_profile": profile,
            "restriction_profile_sha256": object_hash(profile),
        },
        "segmentation": {
            "evidence_fingerprints": base_fingerprints,
            "evidence_fingerprints_sha256": object_hash(base_fingerprints),
            "ordered_outputs": outputs,
            "ordered_outputs_fingerprint": diagnostics.fingerprint_values(outputs),
            "runtime_proof_sha256": object_hash(proof),
            "runtime_closure_sha256": object_hash(proof.get("closure")),
            "runtime_cone_cells": sorted(cone),
            "runtime_cone_sha256": object_hash(sorted(cone)),
        },
        "events": event_records,
        "event_count": len(event_records),
        "event_coverage_sha256": object_hash([
            {
                "event_id": record["event_id"],
                "event_sha256": record["event_sha256"],
                "record_sha256": record["record_sha256"],
            }
            for record in event_records
        ]),
        "dynamic_target_hard_cap": MAX_DYNAMIC_TARGET_CELLS,
    }
    certificate["certificate_sha256"] = _unsigned_hash(
        certificate, "certificate_sha256"
    )
    return certificate


def validate_certificate(
    certificate: dict | str | Path,
    *,
    source_generation_dir: str | Path,
    segmentation_artifact: dict | str | Path,
) -> dict:
    """Rebuild a certificate from exact source and segmentation bytes."""
    supplied = (
        certificate
        if isinstance(certificate, dict)
        else _read_json(Path(certificate), "restriction cone certificate")
    )
    if not isinstance(supplied, dict):
        raise RestrictionConeError("restriction cone certificate must be an object")
    if supplied.get("schema_version") != SCHEMA_VERSION:
        raise RestrictionConeError("unsupported restriction cone certificate schema")
    if supplied.get("certificate_sha256") != _unsigned_hash(
        supplied, "certificate_sha256"
    ):
        raise RestrictionConeError("restriction cone certificate self-hash is invalid")
    segments = (
        segmentation_artifact
        if isinstance(segmentation_artifact, dict)
        else _read_json(Path(segmentation_artifact), "segments artifact")
    )
    verification = segments.get("verification")
    proof = segments.get("proof")
    if not isinstance(verification, dict) or not isinstance(proof, dict):
        raise RestrictionConeError("segments artifact lacks verification or runtime proof")
    source_dir = Path(source_generation_dir)
    manifest = _read_json(
        source_dir / "generation-manifest.json", "source generation manifest"
    )
    layout = manifest.get("layout") or {}
    ast_relative = layout.get("ast_directory")
    if not isinstance(ast_relative, str):
        raise RestrictionConeError("source generation AST layout is invalid")
    graph = model.load(source_dir / ast_relative, str(layout.get("workbook_id", "")))
    expected = build_certificate(
        source_generation_dir=source_dir,
        graph=graph,
        proof=proof,
        verification=verification,
        ordered_outputs=supplied.get("segmentation", {}).get(
            "ordered_outputs", []
        ),
        segmentation_fingerprints=verification.get("fingerprints") or {},
    )
    if supplied != expected:
        raise RestrictionConeError(
            "restriction cone certificate does not match exact source and segmentation"
        )
    events = supplied.get("events")
    source_events = _read_json(source_dir / "health.json", "source health").get(
        "restriction_events"
    )
    if (
        not isinstance(events, list)
        or not isinstance(source_events, list)
        or len(events) != len(source_events)
        or [record.get("event") for record in events] != source_events
        or len({record.get("event_id") for record in events}) != len(events)
    ):
        raise RestrictionConeError("restriction event coverage is missing or duplicate")
    return supplied
