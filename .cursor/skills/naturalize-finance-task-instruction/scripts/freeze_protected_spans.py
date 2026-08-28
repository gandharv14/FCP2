#!/usr/bin/env python3
"""Freeze validator-protected anchors before a rewrite; restore them after.

freeze  — wraps every phrase that validate_instruction_rewrite.py will demand
          (named example outputs, permission modality, semantic anchors,
          source categories, model type, removed-content terms) AND every
          exact-count token the validator tallies inside the two rewriteable
          regions (numeric tokens, cell references, URLs, inline-code spans,
          found with the validator's own regexes) in immutable markers
          [[Fnn]]...[[/Fnn]], then writes a writer-facing source plus a span
          map. Overlapping or adjacent spans are merged into one marker. The
          rewriter composes prose around the markers — a marker may move
          within its sentence — instead of being trusted to preserve the
          tokens mid-sentence.

restore — replaces every marker pair in the rewriter's output with the
          canonical frozen text from the span map (reinsert), verifies each
          span appears exactly once and no marker residue remains, and writes
          the final candidate for the unchanged deterministic validator.

The anchor list comes from validate_instruction_rewrite.protected_anchor_specs
and the token patterns come from validate_instruction_rewrite.EXACT_TOKEN_CHECKS
(restricted to FROZEN_TOKEN_LABELS), so what gets frozen is exactly what the
validator later checks. The validator itself is not touched: it still runs,
unchanged, on the restored candidate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_instruction_rewrite as vir  # noqa: E402


# Ids are F00, F01, ... and grow past two digits (F100, ...) on token-dense
# sources, so the id pattern accepts two or more digits.
MARKER_PAIR_RE = re.compile(r"\[\[(F\d{2,})\]\](.*?)\[\[/\1\]\]", re.DOTALL)
ANY_MARKER_RE = re.compile(r"\[\[/?F\d{2,}\]\]")


class SpanError(ValueError):
    pass


def region_offsets(source: str) -> tuple[tuple[int, int], tuple[int, int] | None]:
    """Offsets of the two rewriteable regions: the preamble and ## Input."""
    matches = list(vir.HEADING_RE.finditer(source))
    if not matches:
        return (0, len(source)), None
    preamble = (0, matches[0].start())
    input_range = None
    for index, match in enumerate(matches):
        if match.group(1).strip().lower() == "## input":
            end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
            input_range = (match.start(), end)
            break
    return preamble, input_range


def find_span(
    source: str,
    phrase: str,
    ranges: list[tuple[int, int]],
    normalize_ws: bool,
) -> tuple[int, int] | None:
    if normalize_ws:
        pattern = re.compile(
            r"\s+".join(re.escape(token) for token in phrase.split()),
            re.IGNORECASE,
        )
    else:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
    for start, end in ranges:
        match = pattern.search(source, start, end)
        if match:
            return match.start(), match.end()
    return None


def token_intervals(source: str, ranges: list[tuple[int, int]]) -> list[list]:
    """Every validator-counted token (numbers, cell refs, URLs, inline code)
    that lies wholly inside the given rewriteable ranges.

    Matches are taken over the full source with the validator's own patterns —
    the same way validate() counts them — then filtered by containment, so the
    frozen token set can never diverge from what the validator tallies.
    """
    patterns = dict(vir.EXACT_TOKEN_CHECKS)
    intervals: list[list] = []
    for label in vir.FROZEN_TOKEN_LABELS:
        check = "%s preserved exactly" % label
        for match in patterns[label].finditer(source):
            if any(
                start <= match.start() and match.end() <= end
                for start, end in ranges
            ):
                intervals.append([match.start(), match.end(), [check]])
    return intervals


def freeze(source: str) -> tuple[str, dict]:
    if ANY_MARKER_RE.search(source):
        raise SpanError("source already contains [[Fnn]] marker text; refusing to freeze")
    preamble_range, input_range = region_offsets(source)
    region_ranges: dict[str, list[tuple[int, int]]] = {
        "preamble": [preamble_range],
        "input": [input_range] if input_range else [],
        "mutable": [preamble_range] + ([input_range] if input_range else []),
    }
    intervals: list[list] = []
    for spec in vir.protected_anchor_specs(source):
        span = find_span(
            source, spec["phrase"], region_ranges[spec["region"]], spec["normalize_ws"]
        )
        if span is None:
            raise SpanError(
                "cannot locate protected anchor %r in the source %s (check: %s)"
                % (spec["phrase"], vir.REGION_LABELS[spec["region"]], spec["check"])
            )
        intervals.append([span[0], span[1], [spec["check"]]])

    intervals.extend(token_intervals(source, region_ranges["mutable"]))

    intervals.sort(key=lambda item: (item[0], item[1]))
    merged: list[list] = []
    for start, end, span_checks in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2].extend(span_checks)
        else:
            merged.append([start, end, list(span_checks)])

    spans: dict[str, dict] = {}
    pieces: list[str] = []
    cursor = 0
    for index, (start, end, span_checks) in enumerate(merged):
        span_id = "F%02d" % index
        text = source[start:end]
        spans[span_id] = {"text": text, "checks": list(dict.fromkeys(span_checks))}
        pieces.append(source[cursor:start])
        pieces.append("[[%s]]%s[[/%s]]" % (span_id, text, span_id))
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces), spans


def restore(marked: str, spans: dict[str, dict]) -> tuple[str, list[str], list[str]]:
    errors: list[str] = []
    drift: list[str] = []
    counts: dict[str, int] = {}

    def substitute(match: re.Match[str]) -> str:
        span_id = match.group(1)
        counts[span_id] = counts.get(span_id, 0) + 1
        if span_id not in spans:
            errors.append(
                "unknown frozen span id %s in the rewrite output; the span map"
                " defines only: %s" % (span_id, ", ".join(sorted(spans)))
            )
            return match.group(0)
        canonical = spans[span_id]["text"]
        if match.group(2) != canonical:
            drift.append(
                "span %s was edited inside its markers (found %r); canonical"
                " text %r was reinserted (protects: %s)"
                % (
                    span_id,
                    match.group(2)[:120],
                    canonical[:120],
                    "; ".join(spans[span_id]["checks"]),
                )
            )
        return canonical

    restored = MARKER_PAIR_RE.sub(substitute, marked)

    for span_id in sorted(spans):
        seen = counts.get(span_id, 0)
        if seen == 0:
            errors.append(
                "frozen span %s is missing from the rewrite output | expected the"
                " literal block [[%s]]%s[[/%s]] to appear exactly once (protects:"
                " %s)"
                % (
                    span_id,
                    span_id,
                    spans[span_id]["text"],
                    span_id,
                    "; ".join(spans[span_id]["checks"]),
                )
            )
        elif seen > 1:
            errors.append(
                "frozen span %s appears %d times in the rewrite output; expected"
                " the literal block [[%s]]%s[[/%s]] exactly once (protects: %s)"
                % (
                    span_id,
                    seen,
                    span_id,
                    spans[span_id]["text"],
                    span_id,
                    "; ".join(spans[span_id]["checks"]),
                )
            )

    residue = sorted(set(ANY_MARKER_RE.findall(restored)))
    if residue:
        errors.append(
            "unpaired or malformed frozen-span markers remain after restore: %s"
            % ", ".join(residue[:8])
        )
    return restored, errors, drift


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".freeze.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def write_report(path: str, payload: dict) -> None:
    if path:
        atomic_write(Path(path), json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    freeze_cmd = sub.add_parser("freeze", help="wrap protected anchors in immutable markers")
    freeze_cmd.add_argument("source", type=Path)
    freeze_cmd.add_argument("--writer-source", required=True, type=Path)
    freeze_cmd.add_argument("--span-map", required=True, type=Path)

    restore_cmd = sub.add_parser("restore", help="reinsert canonical spans and strip markers")
    restore_cmd.add_argument("marked", type=Path)
    restore_cmd.add_argument("--span-map", required=True, type=Path)
    restore_cmd.add_argument("--output", required=True, type=Path)
    restore_cmd.add_argument("--report", default="")

    args = parser.parse_args(argv)

    if args.cmd == "freeze":
        source = args.source.read_text(encoding="utf-8")
        try:
            writer_source, spans = freeze(source)
        except SpanError as exc:
            print("FAIL:", exc, file=sys.stderr)
            return 1
        atomic_write(args.writer_source, writer_source)
        atomic_write(
            args.span_map,
            json.dumps(
                {"source_sha256": vir.sha256_text(source), "spans": spans},
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        print("froze %d span(s) -> %s" % (len(spans), args.writer_source))
        return 0

    marked = args.marked.read_text(encoding="utf-8")
    span_map = json.loads(args.span_map.read_text(encoding="utf-8"))
    restored, errors, drift = restore(marked, span_map.get("spans") or {})
    report = {
        "restored": not errors,
        "spans": len(span_map.get("spans") or {}),
        "drift": drift,
        "errors": errors,
    }
    write_report(args.report, report)
    for note in drift:
        print("note:", note)
    if errors:
        for error in errors:
            print("FAIL:", error, file=sys.stderr)
        return 1
    atomic_write(args.output, restored)
    print("restored %d span(s) -> %s" % (report["spans"], args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
