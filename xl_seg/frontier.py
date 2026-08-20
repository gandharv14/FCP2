"""Stage 6: find the input frontier and rank output candidates.

Inputs fall out of the topology once mirrors are gone: they are the sources of the
condensed DAG that sit inside the cone feeding the outputs. Outputs do not fall
out of the topology, because a workbook has hundreds of sinks and only a handful
of answers, so they are scored and then curated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bands import BandGraph
from .condense import Condensed
from .project import AXIS, NUMERIC

# Terms that name a headline result rather than a step on the way to one.
STRONG_TERMS = (
    "irr", "moic", "npv", "xirr", "equity value", "enterprise value",
    "terminal value", "exit value", "payback", "dscr", "tev", "share price",
    "per share", "roic", "break-even", "breakeven", "money multiple",
    "cash-on-cash", "exit equity", "equity proceeds",
)
WEAK_TERMS = (
    "total revenue", "net income", "net profit", "ebitda", "ebit", "margin",
    "fcf", "free cash flow", "cash flow", "valuation", "return", "multiple",
    "dividend", "total uses", "total sources", "coverage", "net debt",
)
OUTPUT_SHEETS = (
    "dashboard", "output", "summary", "return", "valuation", "dcf",
    "case comparison", "comps",
)
# Reconciliation cells look exactly like outputs -- terminal, named, often on a
# summary sheet -- but they exist to prove the model ties, not to report anything.
CHECK_TERMS = ("check", "tie out", "tie-out", "must be zero", "error", "difference",
               "variance check", "balance check", "control", "audit")
# Constants this common are arithmetic plumbing, not assumptions.
BORING_LITERALS = {0.0, 1.0, -1.0, 2.0, 12.0, 100.0, 365.0, 0.5, 360.0, 4.0, 1000.0,
                   10000.0}


@dataclass
class Candidate:
    comp: str
    band: str
    label: str
    sheet: str
    score: float
    features: dict
    values: list


@dataclass
class Frontier:
    inputs: set = field(default_factory=set)
    outputs: set = field(default_factory=set)
    candidates: list = field(default_factory=list)


def primary_band(cd: Condensed, bg: BandGraph, comp: str) -> str:
    """The most representative band of a component; SCCs get their widest member."""
    members = cd.comp_members[comp]
    if len(members) == 1:
        return members[0]
    return max(members, key=lambda b: (bg.bands[b].width, len(bg.bands[b].label), b))


def _term_hit(label: str, terms) -> bool:
    low = label.lower()
    return any(term in low for term in terms)


def score_outputs(bg: BandGraph, cd: Condensed) -> list[Candidate]:
    max_depth = max(cd.depth.values()) if cd.depth else 1
    main_island = 0
    out: list[Candidate] = []

    for comp, members in cd.comp_members.items():
        band_id = primary_band(cd, bg, comp)
        band = bg.bands[band_id]
        if band.vtype != NUMERIC or band.is_literal:
            continue
        # An output is something the model works out. A component with no
        # predecessors is a given, however prominently it is displayed.
        if not cd.comp_radj.get(comp):
            continue

        is_sink = not cd.comp_adj.get(comp)
        fanin = max((cd.mirror_fanin.get(b, 0) for b in members), default=0)
        presented = set().union(*(cd.presented_on.get(b, set()) for b in members)) if members else set()
        label = band.label
        depth = cd.depth.get(comp, 0)

        # A band that squeezes a multi-period row down to one number is almost
        # always a headline figure rather than another step in the build.
        collapses = band.width == 1 and any(
            bg.bands[p].width >= 3 for b in members for p in cd.radj.get(b, ())
        )

        feats = {
            "sink": is_sink,
            "mirror_fanin": fanin,
            "presented_on": sorted(presented),
            "strong_term": _term_hit(label, STRONG_TERMS),
            "weak_term": _term_hit(label, WEAK_TERMS),
            "output_sheet": any(h in band.sheet.lower() for h in OUTPUT_SHEETS),
            "depth": depth,
            "scalar_collapse": collapses,
            "main_island": cd.island_of.get(comp) == main_island,
            "width": band.width,
            "check_cell": _term_hit(label, CHECK_TERMS),
        }

        score = 0.0
        score += 3.0 if is_sink else 0.0
        score += 0.6 * min(fanin, 5)
        score += 2.0 if presented else 0.0
        score += 2.5 if feats["strong_term"] else 0.0
        score += 0.8 if feats["weak_term"] else 0.0
        score += 1.5 if feats["output_sheet"] else 0.0
        score += 1.5 * (depth / max_depth if max_depth else 0)
        score += 1.5 if collapses else 0.0
        score += 0.5 if feats["main_island"] else 0.0
        score -= 6.0 if feats["check_cell"] else 0.0
        if not label:
            score -= 1.5

        if score <= 0:
            continue
        out.append(
            Candidate(
                comp=comp,
                band=band_id,
                label=label,
                sheet=band.sheet,
                score=round(score, 3),
                features=feats,
                values=[bg.bands[band_id].cells],
            )
        )

    out.sort(key=lambda c: (-c.score, c.band))
    return out


def fallback_outputs(candidates, limit=4):
    """Last-rung picks when neither the scorer nor the adjudicator included anything.

    Guardrails: never a check/reconciliation cell, never unlabelled, one pick per
    distinct label+sheet. Candidates whose label names a result (strong or weak
    term) fill the slots first, so an axis or convention row cannot crowd out a
    margin row that scored marginally lower.
    """
    preferred, other, seen = [], [], set()
    for cand in candidates:
        feats = cand.features
        if feats.get("check_cell") or not cand.label.strip() or cand.score <= 0:
            continue
        key = (cand.label.strip().lower(), cand.sheet)
        if key in seen:
            continue
        seen.add(key)
        if feats.get("strong_term") or feats.get("weak_term"):
            preferred.append(cand)
        else:
            other.append(cand)
    return (preferred + other)[:limit]


def ancestors(cd: Condensed, seeds) -> set:
    seen, stack = set(seeds), list(seeds)
    while stack:
        node = stack.pop()
        for pred in cd.comp_radj.get(node, ()):
            if pred not in seen:
                seen.add(pred)
                stack.append(pred)
    return seen


def descendants(cd: Condensed, seeds) -> set:
    seen, stack = set(seeds), list(seeds)
    while stack:
        node = stack.pop()
        for succ in cd.comp_adj.get(node, ()):
            if succ not in seen:
                seen.add(succ)
                stack.append(succ)
    return seen


def input_frontier(cd: Condensed, outputs: set) -> set:
    """Sources of the condensed DAG that lie inside the cone feeding the outputs.

    Defining inputs this way makes the cone self-closing: every ancestor of an
    output traces back to a source that is itself an ancestor, so no output can
    depend on something outside the frontier.
    """
    cone = ancestors(cd, outputs)
    return {c for c in cone if not cd.comp_radj.get(c)}


def is_notable_literal(value) -> bool:
    """A hardcoded constant that reads as an assumption rather than plumbing.

    Small round integers are offsets, month counts and thresholds. Decimals
    (rates, multiples) and large integers (balances, hardcoded line items) are
    numbers somebody chose, and the model cannot be rebuilt without them.
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    if num != num or num in (float("inf"), float("-inf")):
        return False
    if num in BORING_LITERALS or abs(num) in BORING_LITERALS:
        return False
    return not num.is_integer() or abs(num) >= 100.0
