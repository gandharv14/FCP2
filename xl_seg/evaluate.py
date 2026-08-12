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
MAX_ITERATIONS = 5000
MAX_SWEEPS = 12
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
    def _args(self, node):
        """Positional arguments for an op node.

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
        out = []
        for kind, payload in cached:
            if kind == "none":
                out.append(None)
            elif kind == "scalar":
                out.append(self._eval_node(payload))
            elif kind == "span":
                values = [self._read(cid) for cid in payload]
                coordinates = [_split(cid) for cid in payload]
                rows = len({row for _, row, _ in coordinates})
                cols = len({col for _, _, col in coordinates})
                out.append(RangeValues(values, rows, cols))
            else:
                out.append([self._eval_node(src) for src in payload])
        return out

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
            return literal(node.value if node.value != "" else node.expr)
        if node.kind == "range":
            return self._expand_range(node)
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
        return [self._read(cid) for cid in members]

    def _apply(self, node):
        op = node.op
        if op == "OFFSET":
            return self._offset(node)
        if op == "CELL":
            return self._cell_info(node)
        args = self._args(node)
        if op == "IFERROR":
            value = args[0] if args else Unresolved("iferror-arity")
            if isinstance(value, ExcelError):
                return args[1] if len(args) > 1 else 0.0
            return value
        if op == "IF":
            cond = args[0]
            if is_bad(cond):
                return cond
            chosen = 1 if truthy(cond) else 2
            return args[chosen] if len(args) > chosen else (False if chosen == 2 else True)
        if op in ("IFS", "_XLFN.IFS"):
            for index in range(0, len(args) - 1, 2):
                cond = args[index]
                if is_bad(cond):
                    return cond
                if truthy(cond):
                    return args[index + 1]
            return ExcelError("#N/A")
        bad = next((a for a in flatten(args) if isinstance(a, Unresolved)), None)
        if bad is not None:
            return bad
        err = next((a for a in flatten(args) if isinstance(a, ExcelError)), None)
        if err is not None and op not in (
            "COUNTIF", "COUNTIFS", "SUMIF", "SUMIFS", "AVERAGEIFS"
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
        pool = list(flatten([args[0]]))
        idx = int(num(args[1])) if len(args) > 1 else 1
        return pool[idx - 1] if 1 <= idx <= len(pool) else ExcelError("#REF!")

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

    def _offset(self, node):
        """Resolve a dynamic reference against the grid, then read the target."""
        groups: dict = {}
        for edge in self.graph.in_edges.get(node.id, ()):
            groups.setdefault(edge.arg_index, []).append(edge)
        if 0 not in groups:
            return Unresolved("offset-no-base")
        base_id = groups[0][0].source
        base = self.graph.nodes.get(base_id)
        if base is None or base.row is None:
            return Unresolved("offset-base-not-cell")
        rows = self._eval_node(groups[1][0].source) if 1 in groups else 0
        cols = self._eval_node(groups[2][0].source) if 2 in groups else 0
        if is_bad(rows) or is_bad(cols):
            return rows if is_bad(rows) else cols
        target_row = base.row + int(num(rows))
        target_col = base.col + int(num(cols))
        target = a1(base.sheet, target_row, target_col)
        if target in self.values:
            return self.values[target]
        if target in self.graph.nodes:
            return Unresolved("offset-uncomputed")
        if self.oracle is not None:
            found = self.oracle(base.sheet, target_row, target_col)
            if found is not None:
                return literal(found) if isinstance(found, str) else found
        return None

    # -- driver -----------------------------------------------------------
    def run(self, inputs: set) -> EvalResult:
        """Seed ``inputs`` from cache, rebuild everything else, report the gaps."""
        cells = list(self.cg.info)
        for cid in cells:
            info = self.cg.info[cid]
            if cid in inputs or info.node.kind in ("input", "label") or info.is_literal:
                self.values[cid] = literal(info.node.value)

        groups = strongly_connected(cells, self.cg.adj)
        comp_of, members = {}, {}
        for group in groups:
            key = group[0]
            members[key] = group
            for cell in group:
                comp_of[cell] = key
        comp_adj, comp_radj = {}, {}
        for src, targets in self.cg.adj.items():
            csrc = comp_of.get(src)
            for dst in targets:
                cdst = comp_of.get(dst)
                if csrc is None or cdst is None or csrc == cdst:
                    continue
                comp_adj.setdefault(csrc, set()).add(cdst)
                comp_radj.setdefault(cdst, set()).add(csrc)

        iterated = []
        for comp in topo_order(members, comp_adj, comp_radj):
            group = members[comp]
            # Sorted so a circular block always relaxes in the same order; set
            # iteration order otherwise varies per process and the iteration
            # count stops being reproducible.
            pending = sorted(c for c in group if c not in self.values)
            if not pending:
                continue
            if len(group) == 1:
                self.values[group[0]] = self._eval_cell(group[0])
                continue
            # Circular block: seed at zero and iterate the way Excel does.
            for cell in pending:
                self.values[cell] = 0.0
            for step in range(MAX_ITERATIONS):
                delta = 0.0
                for cell in pending:
                    before = self.values[cell]
                    after = self._eval_cell(cell)
                    self.values[cell] = after
                    if isinstance(before, float) and isinstance(after, float):
                        delta = max(delta, abs(after - before) / max(abs(after), 1.0))
                    else:
                        delta = max(delta, 1.0)
                if delta < CONVERGENCE:
                    break
            iterated.append({"size": len(group), "seed": comp, "iterations": step + 1,
                             "converged": delta < CONVERGENCE})

        # OFFSET targets and unexpanded ranges are resolved from values, so the
        # static edge set never ordered them. Sweep until nothing more resolves.
        order = [c for comp in topo_order(members, comp_adj, comp_radj) for c in members[comp]]
        for _ in range(MAX_SWEEPS):
            stuck = [c for c in order if isinstance(self.values.get(c), Unresolved)]
            if not stuck:
                break
            for cell in stuck:
                self.values[cell] = self._eval_cell(cell)
            if sum(1 for c in stuck if isinstance(self.values.get(c), Unresolved)) == len(stuck):
                break

        for cid, value in self.values.items():
            if isinstance(value, Unresolved):
                self.unresolved[cid] = value.reason

        computed = sum(1 for c in cells if not isinstance(self.values.get(c), Unresolved))
        coverage = {
            "cells": len(cells),
            "computed": computed,
            "unresolved": len(self.unresolved),
            "unknown_ops": dict(self.unknown_ops),
        }
        return EvalResult(self.values, self.unresolved, iterated, coverage)

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
