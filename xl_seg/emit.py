"""Stage 9: write the artifacts.

``curation.toml`` is the hinge of the whole pipeline. Scoring writes it, a human
or the LLM adjudicator edits it, and every later stage reads only from it -- so
hand-editing and machine adjudication are the same interface and re-running is
idempotent.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .frontier import primary_band
from .partition import INPUT, MIDDLE, OUTPUT, SCAFFOLD

SLUG_RE = re.compile(r"[^a-z0-9]+")
TOML_ENTRY_RE = re.compile(r"\[\[output\]\]")
# The band table is the readable derivation and is always complete; the cell table
# is an exact expansion of it and gets abridged here. lineage.json keeps the rest.
MD_CELL_ROWS = 150


def slug(text: str, fallback: str) -> str:
    out = SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return out[:60] or SLUG_RE.sub("-", fallback.lower()).strip("-")[:60]


def _toml_str(value: str) -> str:
    # TOML basic strings cannot carry literal control characters, and workbook
    # row labels routinely contain embedded newlines or tabs.
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return '"' + escaped + '"'


def write_candidates(out_dir: Path, candidates, bg) -> None:
    path = out_dir / "output_candidates.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "rank", "band", "sheet", "label", "score", "sink", "mirror_fanin",
            "presented_on", "strong_term", "weak_term", "output_sheet", "depth",
            "scalar_collapse", "main_island", "check_cell", "width", "cells",
        ])
        for rank, cand in enumerate(candidates, 1):
            feats = cand.features
            writer.writerow([
                rank, cand.band, cand.sheet, cand.label, cand.score,
                feats["sink"], feats["mirror_fanin"], ";".join(feats["presented_on"]),
                feats["strong_term"], feats["weak_term"], feats["output_sheet"],
                feats["depth"], feats["scalar_collapse"], feats["main_island"],
                feats["check_cell"], feats["width"], len(bg.bands[cand.band].cells),
            ])


def write_curation(out_dir: Path, wb: str, candidates, threshold: float, top: int) -> Path:
    path = out_dir / "curation.toml"
    lines = [
        f"# Output curation for workbook {wb}.",
        "# Flip `include` and edit `name`, then re-run xl_segment.py to regenerate",
        "# the segmentation and lineage from your choices. Scores are advisory.",
        f"# Auto-included at score >= {threshold}; top {top} candidates listed.",
        "",
    ]
    for cand in candidates[:top]:
        feats = cand.features
        note = (
            f"sink={feats['sink']} mirror_fanin={feats['mirror_fanin']} "
            f"strong_term={feats['strong_term']} scalar_collapse={feats['scalar_collapse']} "
            f"depth={feats['depth']}"
        )
        lines += [
            "[[output]]",
            f"band = {_toml_str(cand.band)}",
            f"sheet = {_toml_str(cand.sheet)}",
            f"label = {_toml_str(cand.label)}",
            f"score = {cand.score}",
            f"include = {'true' if cand.score >= threshold else 'false'}",
            f"name = {_toml_str(cand.label or cand.band)}",
            f"# {note}",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def read_curation(path: Path) -> list[dict]:
    """Minimal reader for the subset of TOML this file uses."""
    if not path.exists():
        return []
    entries, current = [], None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#") or not line:
            continue
        if TOML_ENTRY_RE.match(line):
            current = {}
            entries.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        # The LLM adjudicator preserves the heuristic decision as an inline
        # comment (for example ``false  # heuristic: true``). Strip comments
        # from unquoted scalars so those booleans remain booleans instead of
        # truthy strings. Quoted labels may legitimately contain ``#``.
        if not value.startswith('"'):
            value = value.split("#", 1)[0].rstrip()
        if value.startswith('"'):
            current[key] = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif value in ("true", "false"):
            current[key] = value == "true"
        else:
            try:
                current[key] = float(value)
            except ValueError:
                current[key] = value
    return entries


def write_bands(out_dir: Path, bg, cd, part) -> None:
    with open(out_dir / "bands.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "band", "bucket", "subclass", "sheet", "row", "col_lo", "col_hi",
            "width", "kind", "vtype", "label", "depth", "island", "mirror_fanin",
            "component", "pattern", "cells",
        ])
        for bid, band in sorted(bg.bands.items()):
            comp = cd.comp_of.get(bid)
            bucket = part.bucket.get(comp, SCAFFOLD) if comp else SCAFFOLD
            sub = part.subclass.get(comp, "") if comp else "presentation"
            writer.writerow([
                bid, bucket, sub, band.sheet, band.row, band.col_lo, band.col_hi,
                band.width, band.kind, band.vtype, band.label,
                cd.depth.get(comp, ""), cd.island_of.get(comp, ""),
                cd.mirror_fanin.get(bid, 0), comp or "", band.pattern,
                len(band.cells),
            ])


def write_segments(out_dir: Path, wb, bg, cd, part, inputs, outputs, literals, verify) -> dict:
    def bands_of(comps):
        return sorted(b for c in comps for b in cd.comp_members[c])

    def describe(comps):
        out = []
        for comp in comps:
            band_id = primary_band(cd, bg, comp)
            band = bg.bands[band_id]
            out.append({
                "band": band_id, "sheet": band.sheet, "label": band.label,
                "width": band.width, "kind": band.kind, "vtype": band.vtype,
                "depth": cd.depth.get(comp, 0), "cells": band.cells,
            })
        return sorted(out, key=lambda d: (d["sheet"], d["band"]))

    by_bucket = {name: [] for name in (INPUT, MIDDLE, OUTPUT, SCAFFOLD)}
    for comp, bucket in part.bucket.items():
        by_bucket[bucket].append(comp)

    payload = {
        "workbook": wb,
        "counts": {
            "cells": sum(len(b.cells) for b in bg.bands.values()),
            "bands": len(bg.bands),
            "mirror_bands": len(cd.mirrors),
            "components": len(cd.comp_members),
            **part.counts,
        },
        "islands": {str(k): v for k, v in sorted(cd.island_sizes.items())[:10]},
        "inputs": describe(sorted(inputs)),
        "outputs": describe(sorted(outputs)),
        "middle": bands_of(sorted(by_bucket[MIDDLE])),
        "scaffolding": {
            name: bands_of(sorted(c for c in by_bucket[SCAFFOLD] if part.subclass.get(c) == name))
            for name in ("unused_input", "dead", "detached")
        },
        "presentation_bands": sorted(cd.mirrors),
        "embedded_literals": literals[:200],
        "verification": verify,
        "unfed_components": sorted(part.unfed),
    }
    (out_dir / "segments.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _fmt(value):
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return f"{value:,.12g}"
    return "" if value is None else str(value)


def write_lineage(out_dir: Path, wb: str, traces, values) -> None:
    lineage_dir = out_dir / "lineage"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    for old in lineage_dir.glob("*.md"):
        old.unlink()

    # Labels repeat -- a model can hold several distinct "Enterprise value" lines --
    # so the band reference disambiguates rather than one trace clobbering another.
    taken: set = set()
    index = []
    for trace in traces:
        name = slug(trace.label, trace.output)
        if name in taken:
            name = f"{name}--{slug(trace.output, trace.output)}"
        taken.add(name)
        path = lineage_dir / f"{name}.md"
        lines = [
            f"# {trace.label or trace.output}",
            "",
            f"- Workbook: `{wb}`",
            f"- Output band: `{trace.output}` on sheet `{trace.sheet}`",
            f"- Derivation: {trace.stats['band_steps']} line items "
            f"({trace.stats['inputs_used']} inputs, {trace.stats['middle_steps']} intermediate)",
            f"- Longest dependency chain: {trace.stats['max_depth']}",
            "",
            "## Values",
            "",
            "| cell | recomputed | workbook |",
            "| --- | --- | --- |",
        ]
        for cid, detail in trace.cell_traces.items():
            final = detail["steps"][-1] if detail["steps"] else {}
            lines.append(f"| `{cid}` | {_fmt(values.get(cid))} | {final.get('cached', '')} |")

        lines += [
            "",
            "## Derivation (line-item level)",
            "",
            "| # | bucket | line item | sheet | span | formula | depends on |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for step in trace.band_steps:
            deps = ", ".join(f"`{d}`" for d in step.inputs[:4])
            if len(step.inputs) > 4:
                deps += f" +{len(step.inputs) - 4}"
            formula = step.formula.replace("|", "\\|")[:70]
            lines.append(
                f"| {step.order} | {step.bucket} | {step.label[:44]} | {step.sheet} | "
                f"`{step.node}` | `{formula}` | {deps or '-'} |"
            )

        lines += ["", "## Derivation (cell level)", ""]
        for cid, detail in trace.cell_traces.items():
            shown = detail["steps"][:MD_CELL_ROWS]
            elided = len(detail["steps"]) - len(shown)
            note = f"{detail['total_ancestors']} ancestor cells"
            if detail["truncated"]:
                note += f", {len(detail['steps'])} traced (budget dropped {detail['truncated']})"
            if elided:
                note += f"; first {len(shown)} shown here, all of them in `lineage.json`"
            lines += [
                f"### `{cid}`",
                "",
                note,
                "",
                "| # | role | cell | label | formula | value |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for step in shown:
                formula = (step["formula"] or step["kind"]).replace("|", "\\|")[:64]
                lines.append(
                    f"| {step['order']} | {step['role']} | `{step['cell']}` | "
                    f"{step['label'][:36]} | `{formula}` | {_fmt(step['value'])} |"
                )
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        index.append({
            "output": trace.output, "label": trace.label, "sheet": trace.sheet,
            "file": f"lineage/{name}.md", **trace.stats,
        })

    payload = {
        "workbook": wb,
        "outputs": index,
        "traces": [
            {
                "output": t.output,
                "label": t.label,
                "band_steps": [vars(s) for s in t.band_steps],
                "cell_traces": t.cell_traces,
            }
            for t in traces
        ],
    }
    (out_dir / "lineage.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
