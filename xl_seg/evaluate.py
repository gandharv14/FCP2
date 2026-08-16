"""Stage 8: recompute the workbook from the input frontier alone.

This is what makes the segmentation falsifiable. Input cells are seeded with their
cached values and nothing else is; every other cell is rebuilt from its parsed AST.
If the recomputed outputs match the workbook, the input set is provably sufficient.

Only 24 distinct functions and 14 operators appear across these four workbooks, so
the surface is small. Anything outside it yields ``Unresolved``, which propagates
and is reported rather than silently coerced to zero.
"""

from __future__ import annotations

import math
import re
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from .condense import strongly_connected, topo_order
from .model import Graph, a1, range_members, split_ref
from .project import CellGraph


def _split(cid: str):
    parsed = split_ref(cid)
    return (parsed[0], parsed[1], parsed[2]) if parsed else (cid, 0, 0)

EPOCH = datetime(1899, 12, 30)
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")
CRITERIA_RE = re.compile(r"^\s*(>=|<=|<>|>|<|=)?\s*(.*)$")
A1_RANGE_RE = re.compile(
    r"(?:(?:'(?P<quoted>(?:[^']|'')+)'|(?P<bare>[A-Za-z0-9_ .&-]+))!)?"
    r"\$?(?P<col0>[A-Za-z]{1,3})\$?(?P<row0>\d+)"
    r"\s*:\s*\$?(?P<col1>[A-Za-z]{1,3})\$?(?P<row1>\d+)"
)
OFFSET_BASE_RE = re.compile(
    r"^OFFSET\(\s*(?:(?:'(?P<quoted>(?:[^']|'')+)'|(?P<bare>[A-Za-z0-9_ .&-]+))!)?"
    r"\$?(?P<col>[A-Za-z]{1,3})\$?(?P<row>\d+)",
    re.IGNORECASE,
)
MAX_ITERATIONS = 5000
MAX_SWEEPS = 12
MAX_ACTIVE_PASSES = 12
EXCEL_DEFAULT_ITERATIONS = 100
EXCEL_DEFAULT_CHANGE = 0.001
CONVERGENCE = 1e-12


class Unresolved:
    """A value the evaluator refuses to guess at; poisons anything downstream."""

    __slots__ = ("reason",)

    def __init__(self, reason: str):
        self.reason = reason

    def __repr__(self):
        return f"Unresolved({self.reason})"


class ExcelError:
    __slots__ = ("code",)

    def __init__(self, code: str):
        self.code = code

    def __repr__(self):
        return self.code


class RangeValues(list):
    """Flat range values with enough shape metadata for lookup/array functions."""

    def __init__(self, values, rows=1, cols=None):
        super().__init__(values)
        self.rows = max(int(rows or 1), 1)
        self.cols = max(int(cols or len(values) or 1), 1)


def is_bad(value) -> bool:
    return isinstance(value, (Unresolved, ExcelError))


def to_serial(text: str):
    try:
        stamp = datetime.fromisoformat(text.replace("T", " "))
    except ValueError:
        return None
    return (stamp - EPOCH).days + (stamp - EPOCH).seconds / 86400.0


def literal(raw: str):
    if raw is None or raw == "":
        return None
    if ISO_RE.match(raw):
        serial = to_serial(raw)
        if serial is not None:
            return serial
    try:
        return float(raw)
    except ValueError:
        pass
    if raw in ("TRUE", "FALSE"):
        return raw == "TRUE"
    return raw


def num(value, default=0.0):
    if value is None:
        return default
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if ISO_RE.match(value):
            serial = to_serial(value)
            if serial is not None:
                return serial
        try:
            return float(value)
        except ValueError:
            return default
    return default


def flatten(args):
    for arg in args:
        if isinstance(arg, list):
            yield from flatten(arg)
        else:
            yield arg


def numbers(args):
    return [num(v) for v in flatten(args) if isinstance(v, (int, float)) and not isinstance(v, bool)]


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.upper() == "TRUE"
    return False


def _matches(value, criteria) -> bool:
    if isinstance(criteria, (int, float)) and not isinstance(criteria, bool):
        return isinstance(value, (int, float)) and float(value) == float(criteria)
    text = str(criteria)
    op, operand = CRITERIA_RE.match(text).groups()
    try:
        target = float(operand)
        left = num(value, math.nan)
    except ValueError:
        target = operand.strip().lower()
        left = str(value).strip().lower() if value is not None else ""
    if op in (None, "", "="):
        return left == target
    if isinstance(left, float) and isinstance(target, float):
        if math.isnan(left):
            return False
        return {
            ">": left > target, "<": left < target,
            ">=": left >= target, "<=": left <= target, "<>": left != target,
        }[op]
    return left != target if op == "<>" else False


def _eomonth(start, months):
    base = EPOCH + timedelta(days=num(start))
    year = base.year + (base.month - 1 + int(num(months))) // 12
    month = (base.month - 1 + int(num(months))) % 12 + 1
    nxt = datetime(year + (month == 12), month % 12 + 1, 1)
    return ((nxt - timedelta(days=1)) - EPOCH).days


def _edate(start, months):
    base = EPOCH + timedelta(days=num(start))
    absolute = base.year * 12 + base.month - 1 + int(num(months))
    year, month0 = divmod(absolute, 12)
    month = month0 + 1
    day = min(base.day, monthrange(year, month)[1])
    return (datetime(year, month, day) - EPOCH).days


def _solve_rate(exponents, values, guess=0.1):
    """Excel-compatible rate solve.

    A cash flow series can cross zero more than once, so bisecting a wide bracket
    is liable to land on a root Excel never reports. Excel runs Newton from the
    guess and returns the root nearest it; bisection is only the fallback.
    """
    def npv(rate):
        return sum(v * (1.0 + rate) ** -t for v, t in zip(values, exponents))

    def slope(rate):
        return sum(-t * v * (1.0 + rate) ** (-t - 1.0) for v, t in zip(values, exponents))

    rate = guess
    newton_root = None
    for _ in range(100):
        try:
            value, derivative = npv(rate), slope(rate)
        except (OverflowError, ZeroDivisionError, ValueError):
            break
        if abs(derivative) < 1e-14:
            break
        step = value / derivative
        nxt = rate - step
        if nxt <= -1.0:
            nxt = (rate - 1.0) / 2.0
        if abs(nxt - rate) < 1e-12:
            newton_root = nxt
            break
        rate = nxt
    else:
        newton_root = rate

    # Multiple sign changes yield multiple mathematically valid IRRs. Excel
    # selects the root nearest the supplied guess; an unconstrained Newton step
    # can jump over that root and settle on a remote one. Find every bracketed
    # root on a mixed linear/log grid and use the closest.
    points = [-0.9999 + index * 1.9999 / 1000 for index in range(1001)]
    points += [10.0 ** (index / 100.0) - 1.0 for index in range(31, 601)]
    roots = []
    previous_rate, previous_value = None, None
    for candidate in points:
        try:
            value = npv(candidate)
        except (OverflowError, ZeroDivisionError, ValueError):
            previous_rate, previous_value = None, None
            continue
        if abs(value) < 1e-10:
            roots.append(candidate)
        elif previous_value is not None and value * previous_value < 0:
            low, high = previous_rate, candidate
            f_low = previous_value
            for _ in range(200):
                mid = (low + high) / 2.0
                f_mid = npv(mid)
                if abs(f_mid) < 1e-12 or high - low < 1e-14:
                    break
                if f_low * f_mid < 0:
                    high = mid
                else:
                    low, f_low = mid, f_mid
            roots.append((low + high) / 2.0)
        previous_rate, previous_value = candidate, value
    if roots:
        return min(roots, key=lambda root: abs(root - guess))
    return newton_root if newton_root is not None else ExcelError("#NUM!")


def _irr(values, guess=0.1):
    if not values:
        return ExcelError("#NUM!")
    return _solve_rate(list(range(len(values))), values, guess)


def _xirr(values, dates, guess=0.1):
    if len(values) != len(dates) or not values:
        return ExcelError("#NUM!")
    start = dates[0]
    return _solve_rate([(d - start) / 365.0 for d in dates], values, guess)


def _div(a, b):
    return ExcelError("#DIV/0!") if b == 0 else a / b


def snap(result: float, *operands: float) -> float:
    """Mimic Excel's cancellation rule for sums.

    Excel zeroes a sum whose magnitude is negligible against its operands, so
    ``=SUM(a,-a)`` is exactly 0 and any ``IF(x>0)`` downstream takes the false
    branch. Plain IEEE arithmetic leaves a ~1e-15 residue and flips the branch.
    """
    scale = max((abs(x) for x in operands), default=0.0)
    return 0.0 if scale and abs(result) < scale * 1e-13 else result


BINARY = {
    "+": lambda a, b: snap(num(a) + num(b), num(a), num(b)),
    "-": lambda a, b: snap(num(a) - num(b), num(a), num(b)),
    "*": lambda a, b: num(a) * num(b),
    "/": lambda a, b: _div(num(a), num(b)),
    "^": lambda a, b: _power(num(a), num(b)),
    "&": lambda a, b: f"{_text(a)}{_text(b)}",
    "=": lambda a, b: _equal(a, b),
    "<>": lambda a, b: not _equal(a, b),
    ">": lambda a, b: num(a) > num(b),
    "<": lambda a, b: num(a) < num(b),
    ">=": lambda a, b: num(a) >= num(b),
    "<=": lambda a, b: num(a) <= num(b),
}
UNARY = {
    "u-": lambda a: -num(a),
    "u+": lambda a: num(a),
    "%": lambda a: num(a) / 100.0,
}


def _power(a, b):
    try:
        result = a ** b
    except (ValueError, OverflowError, ZeroDivisionError):
        return ExcelError("#NUM!")
    return ExcelError("#NUM!") if isinstance(result, complex) else result


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _equal(a, b):
    if isinstance(a, str) or isinstance(b, str):
        return _text(a).strip().lower() == _text(b).strip().lower()
    return num(a) == num(b)


@dataclass
class EvalResult:
    values: dict
    unresolved: dict
    iterated: list
    coverage: dict


class Evaluator:
    def __init__(self, graph: Graph, cg: CellGraph, oracle=None):
        self.graph = graph
        self.cg = cg
        self.oracle = oracle
        self.values: dict = {}
        self.unresolved: dict = {}
        self.unknown_ops: dict = defaultdict(int)
        self._arg_cache: dict = {}

    # -- AST walking ------------------------------------------------------
    def _arg_specs(self, node):
        """Cached positional argument specifications for an op node.

        Slots are indexed by ``arg_index`` rather than packed, because the parser
        emits no edge for an operand that points at an empty cell. Packing would
        silently slide ``=J3+1`` into a one-argument addition.
        """
        cached = self._arg_cache.get(node.id)
        if cached is None:
            grouped: dict = {}
            for edge in self.graph.in_edges.get(node.id, ()):
                grouped.setdefault(edge.arg_index, []).append(edge)
            declared = int(node.arity) if str(node.arity).isdigit() else 0
            size = max([declared] + [k + 1 for k in grouped]) if (grouped or declared) else 0
            cached = [self._spec(grouped.get(i)) for i in range(size)]
            self._arg_cache[node.id] = cached
        return cached

    def _arg_value(self, spec):
        kind, payload = spec
        if kind == "none":
            return None
        if kind == "scalar":
            return self._eval_node(payload)
        if kind == "span":
            values = [self._read(cid) for cid in payload]
            coordinates = [_split(cid) for cid in payload]
            rows = len({row for _, row, _ in coordinates})
            cols = len({col for _, _, col in coordinates})
            return RangeValues(values, rows, cols)
        return [self._eval_node(src) for src in payload]

    def _arg(self, node, index):
        specs = self._arg_specs(node)
        return self._arg_value(specs[index]) if index < len(specs) else None

    def _args(self, node):
        return [self._arg_value(spec) for spec in self._arg_specs(node)]

    def _spec(self, group):
        """Decide once how an argument slot should be read.

        Range arguments are re-derived from their A1 span rather than from the
        edges, because the parser emits no edge for an empty cell. Reading them
        edge-wise silently shortens the list, which slides every later element
        onto the wrong position in paired-range functions like XIRR.
        """
        if not group:
            return ("none", None)
        span = group[0].via_range
        if span:
            sheet = self.graph.nodes[group[0].source].sheet
            members = range_members(sheet, span.split("!")[-1].replace("$", ""))
            if members:
                return ("span", members)
        if len(group) == 1:
            return ("scalar", group[0].source)
        return ("many", [e.source for e in group])

    def _read(self, cid: str):
        if cid in self.values:
            return self.values[cid]
        if cid in self.graph.nodes:
            return Unresolved("range-uncomputed")
        if self.oracle is not None:
            raw = self.oracle(*_split(cid))
            return literal(raw) if isinstance(raw, str) else raw
        return None

    def _eval_node(self, node_id):
        node = self.graph.nodes.get(node_id)
        if node is None:
            return Unresolved("missing-node")
        if node.kind == "const":
            if node.op == "text" and node.value == "":
                return ""
            return literal(node.value if node.value != "" else node.expr)
        if node.kind == "range":
            return self._expand_range(node)
        if node.kind == "name":
            return ExcelError("#NAME?")
        if node.kind == "external":
            return Unresolved("external-reference")
        if node.is_cell:
            if node_id in self.values:
                return self.values[node_id]
            return Unresolved("uncomputed-precedent")
        return self._apply(node)

    def _expand_range(self, node):
        """A range node the parser kept whole; read its cells one by one."""
        members = range_members(node.sheet, node.coordinate or "")
        if not members:
            return Unresolved("opaque-range")
        coordinates = [_split(cid) for cid in members]
        rows = len({row for _, row, _ in coordinates})
        cols = len({col for _, _, col in coordinates})
        return RangeValues([self._read(cid) for cid in members], rows, cols)

    def _apply(self, node):
        op = node.op
        if op == "OFFSET":
            return self._offset(node)
        if op == "CELL":
            return self._cell_info(node)
        if op == "INDIRECT":
            return self._indirect(node)
        if op == "ROW":
            return self._row(node)
        if op == "COUNTA":
            return self._counta(node)
        if op == "IFERROR":
            if not self._arg_specs(node):
                return Unresolved("iferror-arity")
            value = self._arg(node, 0)
            if isinstance(value, ExcelError):
                fallback = self._arg(node, 1)
                return 0.0 if fallback is None else fallback
            return value
        if op == "IF":
            cond = self._arg(node, 0)
            if is_bad(cond):
                return cond
            chosen = 1 if truthy(cond) else 2
            value = self._arg(node, chosen)
            return value if value is not None else (False if chosen == 2 else True)
        if op == "CHOOSE":
            index = self._arg(node, 0)
            if index is None:
                return Unresolved("choose-arity")
            if is_bad(index):
                return index
            selected = int(num(index))
            if not 1 <= selected < len(self._arg_specs(node)):
                return ExcelError("#VALUE!")
            return self._arg(node, selected)
        if op in ("IFS", "_XLFN.IFS"):
            specs = self._arg_specs(node)
            for index in range(0, len(specs) - 1, 2):
                cond = self._arg(node, index)
                if is_bad(cond):
                    return cond
                if truthy(cond):
                    return self._arg(node, index + 1)
            return ExcelError("#N/A")
        args = self._args(node)
        selective = (
            "VLOOKUP", "HLOOKUP", "XLOOKUP", "_XLFN.XLOOKUP", "MATCH", "INDEX"
        )
        bad = next((a for a in flatten(args) if isinstance(a, Unresolved)), None)
        if bad is not None and op not in selective:
            return bad
        err = next((a for a in flatten(args) if isinstance(a, ExcelError)), None)
        if err is not None and op not in (
            "COUNTIF", "COUNTIFS", "SUMIF", "SUMIFS", "AVERAGEIFS", *selective
        ):
            return err
        if node.op_kind in ("infix",) and op in BINARY:
            return BINARY[op](args[0], args[1]) if len(args) > 1 else Unresolved("arity")
        if node.op_kind in ("prefix", "postfix") and op in UNARY:
            if not args:
                return Unresolved("arity")
            if isinstance(args[0], RangeValues):
                return RangeValues(
                    [UNARY[op](value) for value in args[0]],
                    args[0].rows,
                    args[0].cols,
                )
            return UNARY[op](args[0])
        handler = getattr(self, f"_fn_{op.replace('.', '_').lower()}", None)
        if handler is None:
            self.unknown_ops[op] += 1
            return Unresolved(f"unsupported:{op}")
        return handler(args)

    # -- functions --------------------------------------------------------
    def _fn_sum(self, args):
        vals = numbers(args)
        return snap(sum(vals), *vals)

    def _fn_average(self, args):
        vals = numbers(args)
        return sum(vals) / len(vals) if vals else ExcelError("#DIV/0!")

    def _fn_count(self, args):
        return float(len(numbers(args)))

    def _fn_min(self, args):
        vals = numbers(args)
        return min(vals) if vals else 0.0

    def _fn_max(self, args):
        vals = numbers(args)
        return max(vals) if vals else 0.0

    def _fn_median(self, args):
        vals = sorted(numbers(args))
        if not vals:
            return ExcelError("#NUM!")
        mid = len(vals) // 2
        return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2

    def _fn_round(self, args):
        digits = int(num(args[1])) if len(args) > 1 else 0
        factor = 10.0 ** digits
        return math.floor(abs(num(args[0])) * factor + 0.5) / factor * (1 if num(args[0]) >= 0 else -1)

    def _fn_roundup(self, args):
        value = num(args[0])
        digits = int(num(args[1])) if len(args) > 1 else 0
        factor = 10.0 ** digits
        return math.ceil(abs(value) * factor - 1e-12) / factor * (
            1 if value >= 0 else -1
        )

    def _fn_rounddown(self, args):
        value = num(args[0])
        digits = int(num(args[1])) if len(args) > 1 else 0
        factor = 10.0 ** digits
        return math.floor(abs(value) * factor + 1e-12) / factor * (
            1 if value >= 0 else -1
        )

    def _fn_mround(self, args):
        value = num(args[0])
        multiple = num(args[1]) if len(args) > 1 else 0.0
        if multiple == 0:
            return 0.0
        if value * multiple < 0:
            return ExcelError("#NUM!")
        rounded = math.floor(abs(value / multiple) + 0.5 + 1e-12)
        return math.copysign(rounded * abs(multiple), value)

    def _fn_abs(self, args):
        return abs(num(args[0]))

    def _fn_not(self, args):
        return not truthy(args[0])

    def _fn_and(self, args):
        return all(truthy(value) for value in flatten(args) if value is not None)

    def _fn_or(self, args):
        return any(truthy(value) for value in flatten(args) if value is not None)

    def _fn_year(self, args):
        return float((EPOCH + timedelta(days=num(args[0]))).year)

    def _fn_month(self, args):
        return float((EPOCH + timedelta(days=num(args[0]))).month)

    def _fn_day(self, args):
        return float((EPOCH + timedelta(days=num(args[0]))).day)

    def _fn_date(self, args):
        year = int(num(args[0]))
        month = int(num(args[1]))
        day = int(num(args[2]))
        if 0 <= year < 1900:
            year += 1900
        absolute_month = year * 12 + month - 1
        normalized_year, month0 = divmod(absolute_month, 12)
        try:
            stamp = datetime(normalized_year, month0 + 1, 1) + timedelta(days=day - 1)
        except (OverflowError, ValueError):
            return ExcelError("#NUM!")
        return float((stamp - EPOCH).days)

    def _fn_eomonth(self, args):
        return float(_eomonth(args[0], args[1]))

    def _fn_edate(self, args):
        return float(_edate(args[0], args[1]))

    def _fn_days(self, args):
        return num(args[0]) - num(args[1])

    _fn__xlfn_days = _fn_days

    def _fn_today(self, args):
        return float((datetime.today() - EPOCH).days)

    def _fn_choose(self, args):
        idx = int(num(args[0]))
        return args[idx] if 1 <= idx < len(args) else ExcelError("#VALUE!")

    def _fn_index(self, args):
        source = args[0]
        pool = list(flatten([source]))
        row = int(num(args[1])) if len(args) > 1 else 1
        if not isinstance(source, RangeValues):
            return pool[row - 1] if 1 <= row <= len(pool) else ExcelError("#REF!")
        col = int(num(args[2])) if len(args) > 2 and args[2] is not None else 1
        if row == 0 and col == 0:
            return source
        if row == 0:
            if not 1 <= col <= source.cols:
                return ExcelError("#REF!")
            values = [pool[index] for index in range(col - 1, len(pool), source.cols)]
            return RangeValues(values, len(values), 1)
        if col == 0:
            if not 1 <= row <= source.rows:
                return ExcelError("#REF!")
            start = (row - 1) * source.cols
            return RangeValues(pool[start:start + source.cols], 1, source.cols)
        index = (row - 1) * source.cols + col - 1
        if not (1 <= row <= source.rows and 1 <= col <= source.cols and index < len(pool)):
            return ExcelError("#REF!")
        return pool[index]

    def _fn_countif(self, args):
        pool = list(flatten([args[0]]))
        return float(sum(1 for v in pool if _matches(v, args[1])))

    def _fn_sumif(self, args):
        pool = list(flatten([args[0]]))
        target = list(flatten([args[2]])) if len(args) > 2 else pool
        total = 0.0
        for i, value in enumerate(pool):
            if _matches(value, args[1]) and i < len(target):
                total += num(target[i])
        return total

    @staticmethod
    def _criteria_matches(args, start):
        ranges = []
        for index in range(start, len(args) - 1, 2):
            pool = list(flatten([args[index]]))
            ranges.append((pool, args[index + 1]))
        width = min((len(pool) for pool, _ in ranges), default=0)
        return [
            all(_matches(pool[index], criterion) for pool, criterion in ranges)
            for index in range(width)
        ]

    def _fn_sumifs(self, args):
        target = list(flatten([args[0]])) if args else []
        matches = self._criteria_matches(args, 1)
        vals = [
            num(target[index]) for index, matched in enumerate(matches)
            if matched and index < len(target)
        ]
        return snap(sum(vals), *vals)

    def _fn_averageifs(self, args):
        target = list(flatten([args[0]])) if args else []
        matches = self._criteria_matches(args, 1)
        vals = [
            num(target[index]) for index, matched in enumerate(matches)
            if matched and index < len(target)
            and isinstance(target[index], (int, float))
            and not isinstance(target[index], bool)
        ]
        return sum(vals) / len(vals) if vals else ExcelError("#DIV/0!")

    def _fn_countifs(self, args):
        return float(sum(self._criteria_matches(args, 0)))

    def _fn_npv(self, args):
        rate = num(args[0]) if args else 0.0
        vals = numbers(args[1:])
        if rate == -1.0:
            return ExcelError("#DIV/0!")
        try:
            return sum(value / (1.0 + rate) ** period
                       for period, value in enumerate(vals, 1))
        except (OverflowError, ZeroDivisionError, ValueError):
            return ExcelError("#NUM!")

    def _fn_rri(self, args):
        periods = num(args[0])
        present = num(args[1])
        future = num(args[2])
        if periods == 0 or present == 0 or future / present < 0:
            return ExcelError("#NUM!")
        return _power(future / present, 1.0 / periods) - 1.0

    _fn__xlfn_rri = _fn_rri

    def _fn_sumproduct(self, args):
        arrays = [list(flatten([arg])) for arg in args]
        if not arrays:
            return 0.0
        width = min(len(array) for array in arrays)
        vals = [
            math.prod(num(array[index]) for array in arrays)
            for index in range(width)
        ]
        return snap(sum(vals), *vals)

    def _fn_forecast(self, args):
        x = num(args[0])
        known_y = numbers([args[1]]) if len(args) > 1 else []
        known_x = numbers([args[2]]) if len(args) > 2 else []
        if not known_y or len(known_y) != len(known_x):
            return ExcelError("#N/A")
        mean_x = sum(known_x) / len(known_x)
        mean_y = sum(known_y) / len(known_y)
        denominator = sum((value - mean_x) ** 2 for value in known_x)
        if denominator == 0:
            return ExcelError("#DIV/0!")
        slope = sum(
            (x_value - mean_x) * (y_value - mean_y)
            for x_value, y_value in zip(known_x, known_y)
        ) / denominator
        return mean_y + slope * (x - mean_x)

    _fn_forecast_linear = _fn_forecast
    _fn__xlfn_forecast_linear = _fn_forecast

    def _fn_len(self, args):
        return float(len(_text(args[0])))

    def _fn_upper(self, args):
        return _text(args[0]).upper()

    def _fn_proper(self, args):
        return _text(args[0]).title()

    def _fn_text(self, args):
        value = args[0]
        pattern = _text(args[1]).strip() if len(args) > 1 else ""
        lower = pattern.lower()
        if any(token in lower for token in ("yy", "mm", "dd")):
            stamp = EPOCH + timedelta(days=num(value))
            replacements = (
                ("yyyy", f"{stamp.year:04d}"), ("yy", f"{stamp.year % 100:02d}"),
                ("mmmm", stamp.strftime("%B")), ("mmm", stamp.strftime("%b")),
                ("mm", f"{stamp.month:02d}"), ("dd", f"{stamp.day:02d}"),
            )
            text = pattern
            for token, rendered in replacements:
                text = re.sub(token, rendered, text, flags=re.IGNORECASE)
            return text
        percent = "%" in pattern
        scaled = num(value) * (100.0 if percent else 1.0)
        decimals = len(pattern.split(".", 1)[1].split("%", 1)[0]) if "." in pattern else 0
        grouping = "," in pattern.split(".", 1)[0]
        rendered = f"{scaled:,.{decimals}f}" if grouping else f"{scaled:.{decimals}f}"
        return rendered + ("%" if percent else "")

    def _fn_right(self, args):
        count = int(num(args[1])) if len(args) > 1 else 1
        return _text(args[0])[-count:] if count else ""

    def _fn_mid(self, args):
        start = int(num(args[1]))
        count = int(num(args[2]))
        return _text(args[0])[start - 1: start - 1 + count]

    def _fn_find(self, args):
        pos = _text(args[1]).find(_text(args[0]))
        return float(pos + 1) if pos >= 0 else ExcelError("#VALUE!")

    def _fn_xlookup(self, args):
        keys = list(flatten([args[1]]))
        pool = list(flatten([args[2]])) if len(args) > 2 else keys
        for i, key in enumerate(keys):
            if _equal(key, args[0]) and i < len(pool):
                return pool[i]
        return args[3] if len(args) > 3 and args[3] is not None else ExcelError("#N/A")

    _fn__xlfn_xlookup = _fn_xlookup

    def _fn_match(self, args):
        lookup = args[0]
        pool = list(flatten([args[1]])) if len(args) > 1 else []
        mode = int(num(args[2], 1.0)) if len(args) > 2 and args[2] is not None else 1
        if mode == 0:
            for index, value in enumerate(pool):
                if _equal(value, lookup):
                    return float(index + 1)
            return ExcelError("#N/A")
        candidates = [
            (index, value) for index, value in enumerate(pool)
            if isinstance(value, (int, float))
            and ((mode > 0 and value <= num(lookup))
                 or (mode < 0 and value >= num(lookup)))
        ]
        if not candidates:
            return ExcelError("#N/A")
        selected = max(candidates, key=lambda pair: pair[1]) if mode > 0 else \
            min(candidates, key=lambda pair: pair[1])
        return float(selected[0] + 1)

    def _fn_vlookup(self, args):
        lookup = args[0]
        table = args[1] if len(args) > 1 else []
        values = list(flatten([table]))
        column = int(num(args[2])) if len(args) > 2 else 1
        exact = len(args) > 3 and not truthy(args[3])
        cols = table.cols if isinstance(table, RangeValues) else max(column, 2)
        if column < 1 or column > cols:
            return ExcelError("#REF!")
        rows = [values[index:index + cols] for index in range(0, len(values), cols)]
        selected = None
        for row in rows:
            if len(row) < column:
                continue
            if _equal(row[0], lookup):
                return row[column - 1]
            if not exact and isinstance(row[0], (int, float)) and row[0] <= num(lookup):
                selected = row
        return selected[column - 1] if selected is not None else ExcelError("#N/A")

    def _fn_hlookup(self, args):
        lookup = args[0]
        table = args[1] if len(args) > 1 else []
        values = list(flatten([table]))
        row = int(num(args[2])) if len(args) > 2 else 1
        exact = len(args) > 3 and not truthy(args[3])
        cols = table.cols if isinstance(table, RangeValues) else len(values)
        rows = table.rows if isinstance(table, RangeValues) else 1
        if row < 1 or row > rows or cols < 1:
            return ExcelError("#REF!")
        header = values[:cols]
        selected = None
        for index, value in enumerate(header):
            if _equal(value, lookup):
                selected = index
                break
            if not exact and isinstance(value, (int, float)) and value <= num(lookup):
                selected = index
        if selected is None:
            return ExcelError("#N/A")
        index = (row - 1) * cols + selected
        return values[index] if index < len(values) else ExcelError("#REF!")

    def _fn_transpose(self, args):
        source = args[0] if args else []
        values = list(flatten([source]))
        if not isinstance(source, RangeValues):
            return RangeValues(values, len(values), 1)
        matrix = [
            values[index:index + source.cols]
            for index in range(0, len(values), source.cols)
        ]
        transposed = [
            matrix[row][col]
            for col in range(source.cols)
            for row in range(min(source.rows, len(matrix)))
        ]
        return RangeValues(transposed, source.cols, source.rows)

    def _fn__xlfn_vstack(self, args):
        values = []
        cols = 1
        rows = 0
        for arg in args:
            chunk = list(flatten([arg]))
            values.extend(chunk)
            if isinstance(arg, RangeValues):
                cols = max(cols, arg.cols)
                rows += arg.rows
            else:
                rows += max(len(chunk), 1)
        return RangeValues(values, rows, cols)

    def _fn_irr(self, args):
        return _irr(numbers([args[0]]))

    def _fn_xirr(self, args):
        # Blanks are kept as zeros on both sides rather than dropped. A blank date
        # is serial 0, which discounts its amount over a century -- negligible on
        # its own, but so is everything else once the other flows are discounted
        # that far, and Excel's answer depends on it.
        flows = list(flatten([args[0]]))
        dates = list(flatten([args[1]]))
        if not flows or not dates:
            return ExcelError("#NUM!")
        pairs = [(num(v, 0.0), num(d, 0.0)) for v, d in zip(flows, dates)]
        return _xirr([p[0] for p in pairs], [p[1] for p in pairs])

    def _cell_info(self, node):
        """Only ``CELL("filename", ...)`` appears here, feeding sheet-title cells."""
        args = self._args(node)
        kind = _text(args[0]).strip().lower() if args else ""
        if kind != "filename":
            return Unresolved("unsupported:CELL")
        target = node.sheet
        for edge in self.graph.in_edges.get(node.id, ()):
            ref = self.graph.nodes.get(edge.source)
            if ref is not None and ref.is_cell:
                target = ref.sheet
                break
        return f"[{self.graph.wb}.xlsx]{target}"

    def _indirect(self, node):
        """Return the statically resolved target attached by the AST builder."""
        args = self._args(node)
        declared = int(node.arity) if str(node.arity).isdigit() else 1
        if len(args) > declared:
            return args[declared]
        return ExcelError("#REF!")

    def _row(self, node):
        """Excel ROW, using the referenced range or the formula owner's row."""
        incoming = sorted(self.graph.in_edges.get(node.id, ()), key=lambda edge: edge.arg_index)
        if incoming:
            source = self.graph.nodes.get(incoming[0].source)
            if source is not None and source.row is not None:
                return float(source.row)
        owner = self.graph.nodes.get(node.owner)
        return float(owner.row) if owner is not None and owner.row is not None else ExcelError("#REF!")

    def _counta(self, node):
        """Count nonblank values, recovering a range omitted by older AST graphs."""
        args = self._args(node)
        values = list(flatten(args))
        if not values or all(value is None for value in values):
            match = A1_RANGE_RE.search(node.expr or "")
            if match:
                sheet = (match.group("quoted") or match.group("bare") or node.sheet)
                sheet = sheet.replace("''", "'").strip()
                span = (
                    f"{match.group('col0')}{match.group('row0')}:"
                    f"{match.group('col1')}{match.group('row1')}"
                )
                values = [self._read(cid) for cid in range_members(sheet, span)]
        bad = next((value for value in values if isinstance(value, Unresolved)), None)
        if bad is not None:
            return bad
        return float(sum(value is not None and value != "" for value in values))

    def _offset(self, node):
        """Resolve a dynamic reference, including optional range dimensions."""
        targets = self._offset_targets(node)
        if is_bad(targets):
            return targets
        values = [self._read(target) for target in targets]
        groups = {
            edge.arg_index for edge in self.graph.in_edges.get(node.id, ())
        }
        height = int(num(self._arg(node, 3), 1.0)) if 3 in groups else 1
        width = int(num(self._arg(node, 4), 1.0)) if 4 in groups else 1
        if height == 1 and width == 1:
            return values[0]
        return RangeValues(values, height, width)

    def _offset_targets(self, node):
        """Resolve the cell ids addressed by OFFSET without reading their values."""
        groups: dict = {}
        for edge in self.graph.in_edges.get(node.id, ()):
            groups.setdefault(edge.arg_index, []).append(edge)
        if 0 in groups:
            base_id = groups[0][0].source
            base = self.graph.nodes.get(base_id)
            if base is None or base.row is None:
                return Unresolved("offset-base-not-cell")
            base_sheet, base_row, base_col = base.sheet, base.row, base.col
        else:
            # Older ASTs omitted an OFFSET base when that anchor cell was blank.
            # The coordinate is still a reference origin, not a value dependency.
            match = OFFSET_BASE_RE.match(node.expr or "")
            if not match:
                return Unresolved("offset-no-base")
            base_sheet = match.group("quoted") or match.group("bare") or node.sheet
            base_sheet = base_sheet.replace("''", "'").strip()
            parsed = _split(f"{base_sheet}!{match.group('col')}{match.group('row')}")
            if parsed is None:
                return Unresolved("offset-base-not-cell")
            base_sheet, base_row, base_col = parsed
        rows = self._arg(node, 1) if 1 in groups else 0
        cols = self._arg(node, 2) if 2 in groups else 0
        if is_bad(rows) or is_bad(cols):
            return rows if is_bad(rows) else cols
        target_row = base_row + int(num(rows))
        target_col = base_col + int(num(cols))
        height = self._arg(node, 3) if 3 in groups else 1
        width = self._arg(node, 4) if 4 in groups else 1
        if is_bad(height) or is_bad(width):
            return height if is_bad(height) else width
        height, width = int(num(height, 1.0)), int(num(width, 1.0))
        if height < 1 or width < 1 or target_row < 1 or target_col < 1:
            return ExcelError("#REF!")
        return [
            a1(base_sheet, row, col)
            for row in range(target_row, target_row + height)
            for col in range(target_col, target_col + width)
        ]

    def _active_node_sources(self, node_id, seen=None):
        """Cell precedents actually evaluated under the current selector values."""
        seen = set() if seen is None else seen
        if node_id in seen:
            return set()
        seen.add(node_id)
        node = self.graph.nodes.get(node_id)
        if node is None:
            return set()
        if node.is_cell:
            return {node.id}
        if node.kind == "range":
            return set(range_members(node.sheet, node.coordinate or ""))
        if node.kind != "op":
            return set()

        specs = self._arg_specs(node)
        indices = list(range(len(specs)))
        if node.op == "OFFSET":
            # The first argument supplies an address origin, not a value. The
            # resolved target below is the actual value dependency.
            indices = list(range(1, len(specs)))
        elif node.op == "IF":
            cond = self._arg(node, 0)
            indices = [0] if is_bad(cond) else [0, 1 if truthy(cond) else 2]
        elif node.op == "CHOOSE":
            selected = self._arg(node, 0)
            index = int(num(selected)) if not is_bad(selected) and selected is not None else -1
            indices = [0] + ([index] if 1 <= index < len(specs) else [])
        elif node.op in ("IFS", "_XLFN.IFS"):
            indices = []
            for index in range(0, len(specs) - 1, 2):
                indices.append(index)
                cond = self._arg(node, index)
                if not is_bad(cond) and truthy(cond):
                    indices.append(index + 1)
                    break
                if is_bad(cond):
                    break
        elif node.op == "IFERROR":
            indices = [0]
            if isinstance(self._arg(node, 0), ExcelError) and len(specs) > 1:
                indices.append(1)

        sources = set()
        for index in indices:
            if index >= len(specs):
                continue
            kind, payload = specs[index]
            if kind == "span":
                sources.update(payload)
            elif kind == "scalar":
                sources.update(self._active_node_sources(payload, seen))
            elif kind == "many":
                for source in payload:
                    sources.update(self._active_node_sources(source, seen))
        if node.op == "OFFSET":
            targets = self._offset_targets(node)
            if isinstance(targets, list):
                sources.update(targets)
        return sources

    def _active_graph(self, cells):
        """Build source-to-consumer edges after pruning inactive formula branches."""
        known = set(cells)
        adj: dict[str, set] = {}
        radj: dict[str, set] = {}
        for target in cells:
            root = self.graph.root_of(target)
            if root is None:
                continue
            for source in self._active_node_sources(root):
                if source not in known:
                    continue
                adj.setdefault(source, set()).add(target)
                radj.setdefault(target, set()).add(source)
        return adj, radj

    # -- driver -----------------------------------------------------------
    def run(self, inputs: set) -> EvalResult:
        """Seed ``inputs`` from cache, rebuild everything else, report the gaps."""
        cells = list(self.cg.info)
        for cid in cells:
            info = self.cg.info[cid]
            if cid in inputs or info.node.kind in ("input", "label") or info.is_literal:
                self.values[cid] = literal(info.node.value)
        seeded = set(self.values)
        # Excel initializes every iterative formula at zero. Doing this globally
        # also makes selector evaluation deterministic before the first active
        # dependency graph is built.
        for cid in cells:
            if cid not in seeded:
                self.values[cid] = 0.0
        indeterminate: set[str] = set()

        iterate = bool(getattr(self.oracle, "iterate", True))
        iteration_limit = getattr(self.oracle, "iterate_count", None)
        iteration_limit = iteration_limit or EXCEL_DEFAULT_ITERATIONS
        iteration_delta = getattr(self.oracle, "iterate_delta", None)
        iteration_delta = iteration_delta or EXCEL_DEFAULT_CHANGE

        last_signature = None
        latest_cycles = {}
        indeterminate_diagnostics = {}
        graph_passes = 0
        for graph_pass in range(MAX_ACTIVE_PASSES):
            graph_passes = graph_pass + 1
            before_pass = dict(self.values)
            cycle_candidates = []
            disabled_cycle_candidates = []
            current_cycles = dict(indeterminate_diagnostics)
            active_adj, active_radj = self._active_graph(cells)
            signature = frozenset(
                (source, target)
                for source, targets in active_adj.items()
                for target in targets
            )
            groups = strongly_connected(cells, active_adj)
            comp_of, members = {}, {}
            for group in groups:
                key = group[0]
                members[key] = group
                for cell in group:
                    comp_of[cell] = key
            comp_adj, comp_radj = {}, {}
            for source, targets in active_adj.items():
                csrc = comp_of.get(source)
                for target in targets:
                    cdst = comp_of.get(target)
                    if csrc is None or cdst is None or csrc == cdst:
                        continue
                    comp_adj.setdefault(csrc, set()).add(cdst)
                    comp_radj.setdefault(cdst, set()).add(csrc)

            for comp in topo_order(members, comp_adj, comp_radj):
                group = members[comp]
                pending = sorted(
                    cell for cell in group
                    if cell not in seeded and cell not in indeterminate
                )
                if not pending:
                    continue
                cyclic = len(group) > 1 or any(
                    cell in active_adj.get(cell, ()) for cell in group
                )
                if not cyclic:
                    self.values[pending[0]] = self._eval_cell(pending[0])
                    continue
                if not iterate:
                    # A zero-valued selector can temporarily expose an otherwise
                    # inactive self-reference. Give the active graph a single
                    # lazy sweep to settle before declaring a real cycle.
                    diagnostic = self._iterate_group(pending, 1, iteration_delta)
                    diagnostic.update({
                        "size": len(group), "seed": comp,
                        "reason": "iteration-disabled-provisional",
                        "graph_pass": graph_pass + 1,
                    })
                    disabled_cycle_candidates.append(
                        (tuple(sorted(group)), pending, diagnostic)
                    )
                else:
                    diagnostic = self._iterate_group(
                        pending, iteration_limit, iteration_delta
                    )
                    diagnostic["unique"] = True
                    diagnostic.update({
                        "size": len(group), "seed": comp,
                        "graph_pass": graph_pass + 1,
                    })
                    cycle_candidates.append((tuple(sorted(group)), pending, diagnostic))
                current_cycles[tuple(sorted(group))] = diagnostic

            max_change = max(
                (self._value_change(before_pass[cell], self.values[cell]) for cell in cells),
                default=0.0,
            )
            latest_cycles = current_cycles
            if signature == last_signature and max_change <= iteration_delta:
                found_indeterminate = False
                for group_key, pending, diagnostic in disabled_cycle_candidates:
                    diagnostic.update({
                        "iterations": 0,
                        "converged": False,
                        "max_change": None,
                        "reason": "iteration-disabled",
                    })
                    latest_cycles[group_key] = diagnostic
                    indeterminate_diagnostics[group_key] = diagnostic
                    indeterminate.update(pending)
                    for cell in pending:
                        self.values[cell] = Unresolved("circular-reference")
                    found_indeterminate = True
                for group_key, pending, diagnostic in cycle_candidates:
                    alternative = self._alternate_fixed_point(
                        pending, diagnostic, iteration_limit, iteration_delta
                    )
                    if alternative is None:
                        continue
                    canonical, alternate = alternative
                    diagnostic.update({
                        "unique": False,
                        "reason": "non-unique-fixed-point",
                        "canonical_fixed_point": canonical,
                        "alternate_fixed_point": alternate,
                    })
                    latest_cycles[group_key] = diagnostic
                    indeterminate_diagnostics[group_key] = diagnostic
                    indeterminate.update(pending)
                    for cell in pending:
                        self.values[cell] = Unresolved(
                            "non-unique-circular-reference"
                        )
                    found_indeterminate = True
                if found_indeterminate:
                    continue
                break
            last_signature = signature

        cycle_diagnostics = list(latest_cycles.values())
        iterated = [
            diagnostic for diagnostic in cycle_diagnostics
            if diagnostic["size"] > 1
            or diagnostic["iterations"] > 1
            or not diagnostic["converged"]
            or not diagnostic.get("unique", True)
            or diagnostic.get("errors")
            or diagnostic.get("unresolved")
        ]

        for cid, value in self.values.items():
            if isinstance(value, Unresolved):
                self.unresolved[cid] = value.reason

        computed = sum(1 for c in cells if not isinstance(self.values.get(c), Unresolved))
        errors = sum(isinstance(self.values.get(c), ExcelError) for c in cells)
        coverage = {
            "cells": len(cells),
            "computed": computed,
            "unresolved": len(self.unresolved),
            "errors": errors,
            "unknown_ops": dict(self.unknown_ops),
            "active_graph_passes": graph_passes,
            "iteration_enabled": iterate,
            "iteration_limit": iteration_limit,
            "iteration_delta": iteration_delta,
            "active_cycles": len(cycle_diagnostics),
            "stable_self_cycles": sum(
                diagnostic["size"] == 1 and diagnostic["converged"]
                and diagnostic.get("unique", True)
                for diagnostic in cycle_diagnostics
            ),
        }
        return EvalResult(self.values, self.unresolved, iterated, coverage)

    @staticmethod
    def _value_change(before, after):
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            if math.isfinite(float(before)) and math.isfinite(float(after)):
                return abs(float(after) - float(before))
        return 0.0 if type(before) is type(after) and repr(before) == repr(after) else math.inf

    def _iterate_group(self, pending, iteration_limit, iteration_delta):
        max_change = math.inf
        for step in range(iteration_limit):
            max_change = 0.0
            for cell in pending:
                before = self.values[cell]
                after = self._eval_cell(cell)
                self.values[cell] = after
                max_change = max(max_change, self._value_change(before, after))
            if max_change <= iteration_delta:
                break
        return {
            "iterations": step + 1,
            "converged": max_change <= iteration_delta,
            "max_change": None if math.isinf(max_change) else max_change,
            "errors": sum(isinstance(self.values[cell], ExcelError) for cell in pending),
            "unresolved": sum(isinstance(self.values[cell], Unresolved) for cell in pending),
        }

    def _alternate_fixed_point(
        self, pending, canonical_diagnostic, iteration_limit, iteration_delta
    ):
        """Prove non-uniqueness when a second numeric fixed point is reachable.

        A zero-seeded circular calculation can appear to converge even when the
        recurrence merely preserves its initial state (for example ``x = x``).
        Re-run the same active SCC from a unit seed. If both runs converge and
        produce distinct numeric solutions, frontier inputs do not determine the
        workbook's persisted value, so strict verification must remain unresolved.
        """
        if (
            not canonical_diagnostic["converged"]
            or canonical_diagnostic["errors"]
            or canonical_diagnostic["unresolved"]
        ):
            return None
        canonical = {cell: self.values[cell] for cell in pending}
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in canonical.values()
        ):
            return None

        seed = 1.0
        if all(abs(float(value) - seed) <= iteration_delta for value in canonical.values()):
            seed = -1.0
        for cell in pending:
            self.values[cell] = seed
        alternate_diagnostic = self._iterate_group(
            pending, iteration_limit, iteration_delta
        )
        alternate = {cell: self.values[cell] for cell in pending}
        self.values.update(canonical)

        if (
            not alternate_diagnostic["converged"]
            or alternate_diagnostic["errors"]
            or alternate_diagnostic["unresolved"]
            or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in alternate.values()
            )
        ):
            return None
        distinct = any(
            self._value_change(canonical[cell], alternate[cell]) > iteration_delta
            for cell in pending
        )
        if not distinct:
            return None
        return (
            {cell: canonical[cell] for cell in pending[:20]},
            {cell: alternate[cell] for cell in pending[:20]},
        )

    def _stabilize(self, order, seeded):
        """Bounded fixed-point sweeps for dependencies hidden by dynamic references."""
        for _ in range(MAX_SWEEPS):
            changed = 0
            # The static order is optimal for known edges. The reverse pass
            # quickly carries values across dynamic edges whose direction was
            # unavailable to the graph builder.
            for sweep in (order, reversed(order)):
                for cell in sweep:
                    if cell in seeded:
                        continue
                    before = self.values.get(cell)
                    after = self._eval_cell(cell)
                    self.values[cell] = after
                    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                        scale = max(abs(float(before)), abs(float(after)), 1.0)
                        changed += abs(float(after) - float(before)) > CONVERGENCE * scale
                    else:
                        changed += type(before) is not type(after) or repr(before) != repr(after)
            if not changed:
                break

    def _eval_cell(self, cid: str):
        info = self.cg.info[cid]
        root = self.graph.root_of(cid)
        if root is not None:
            value = self._eval_node(root)
            # Dynamic-array formulas store the anchor's first element in the
            # owning cell; spill cells are represented separately in the grid.
            if isinstance(value, RangeValues):
                return value[0] if value else None
            return value
        if info.empty_ref:
            return 0.0
        return Unresolved("no-ast-root")


def workbook_oracle(path):
    """Cached-value lookup for grid cells the AST graph never recorded."""
    try:
        import openpyxl
    except ImportError:
        return None
    try:
        book = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception:
        return None
    cache: dict = {}

    def lookup(sheet, row, col):
        if sheet not in cache:
            if sheet not in book.sheetnames:
                cache[sheet] = {}
            else:
                grid = {}
                for line in book[sheet].iter_rows():
                    for cell in line:
                        if cell.value is not None:
                            grid[(cell.row, cell.column)] = cell.value
                cache[sheet] = grid
        value = cache[sheet].get((row, col))
        if isinstance(value, datetime):
            return (value - EPOCH).days
        return value

    calculation = getattr(book, "calculation", None)
    lookup.iterate = bool(getattr(calculation, "iterate", False))
    lookup.iterate_count = getattr(calculation, "iterateCount", None)
    lookup.iterate_delta = getattr(calculation, "iterateDelta", None)
    return lookup


def compare(expected: str, actual, tolerance=1e-6):
    """Relative comparison against the workbook's cached value."""
    if isinstance(actual, Unresolved):
        return "unresolved", None
    want = literal(expected)
    if isinstance(want, str) or isinstance(actual, str):
        return ("match" if _text(want).strip() == _text(actual).strip() else "mismatch"), None
    if want is None and actual in (None, 0.0):
        return "match", 0.0
    got, ref = num(actual, math.nan), num(want, math.nan)
    if math.isnan(got) or math.isnan(ref):
        return "unresolved", None
    diff = abs(got - ref)
    scale = max(abs(ref), 1.0)
    return ("match" if diff / scale <= tolerance else "mismatch"), diff
