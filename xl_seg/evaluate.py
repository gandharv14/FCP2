"""Stage 8: recompute the workbook from the input frontier alone.

This is what makes the segmentation falsifiable. Input cells are seeded with their
cached values and nothing else is; every other cell is rebuilt from its parsed AST.
If the recomputed outputs match the workbook, the input set is provably sufficient.

Only 24 distinct functions and 14 operators appear across these four workbooks, so
the surface is small. Anything outside it yields ``Unresolved``, which propagates
and is reported rather than silently coerced to zero.
"""

from __future__ import annotations

import heapq
import math
import re
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta

from .condense import strongly_connected, topo_order
from .model import A1_RE, Graph, a1, col_number, range_members, split_ref
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
        stripped = operand.strip()
        if stripped.endswith("%"):
            # Excel parses criteria like ">0%" numerically.
            target = float(stripped[:-1]) / 100.0
        else:
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


def _solve_rate(exponents, values, guess=0.1, scan=True):
    """Excel-compatible rate solve.

    A cash flow series can cross zero more than once, so bisecting a wide bracket
    is liable to land on a root Excel never reports. Excel runs Newton from the
    guess and returns the root nearest it; bisection is only the fallback.

    ``scan=False`` skips the grid scan when the caller has proven the root is
    unique (a single sign change in the series); RATE-heavy workbooks call this
    hundreds of times and the unconditional scan would dominate the run.
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

    if not scan and newton_root is not None and newton_root > -1.0:
        try:
            if abs(npv(newton_root)) < 1e-7:
                return newton_root
        except (OverflowError, ZeroDivisionError, ValueError):
            pass

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
    # Excel documents that IRR needs at least one positive and one negative
    # flow. Without a sign change the NPV curve never crosses zero -- and on an
    # all-zero series it is identically zero, which the grid scan would happily
    # "solve" at whatever point sits nearest the guess.
    if not values or not any(v > 0 for v in values) or not any(v < 0 for v in values):
        return ExcelError("#NUM!")
    return _solve_rate(list(range(len(values))), values, guess)


def _xirr(values, dates, guess=0.1):
    if len(values) != len(dates) or not values:
        return ExcelError("#NUM!")
    if not any(v > 0 for v in values) or not any(v < 0 for v in values):
        return ExcelError("#NUM!")
    start = dates[0]
    return _solve_rate([(d - start) / 365.0 for d in dates], values, guess)


def _div(a, b):
    return ExcelError("#DIV/0!") if b == 0 else a / b


def _ddb_amount(cost, salvage, life, period, factor):
    """Double-declining depreciation for one whole ``period`` (1-based).

    Mirrors the reference implementation Excel-compatible engines share
    (LibreOffice ``ScGetDDB``): book value declines geometrically at
    ``factor/life`` capped at 100%, and the period's charge never takes the
    book value below salvage or itself goes negative.
    """
    rate = factor / life
    if rate >= 1.0:
        rate = 1.0
        old = cost if period == 1 else 0.0
    else:
        old = cost * (1.0 - rate) ** (period - 1.0)
    new = cost * (1.0 - rate) ** period
    return max(old - (salvage if new < salvage else new), 0.0)


def _inter_vdb(cost, salvage, life, life1, period, factor):
    """VDB core with straight-line switching (LibreOffice ``ScInterVDB``)."""
    vdb = 0.0
    loop_end = int(math.ceil(period - 1e-12))
    remaining = cost - salvage
    switched = False
    linear = 0.0
    for i in range(1, loop_end + 1):
        if not switched:
            ddb = _ddb_amount(cost, salvage, life, float(i), factor)
            denominator = life1 - float(i - 1)
            linear = remaining / denominator if denominator != 0 else math.inf
            if linear > ddb:
                term = linear
                switched = True
            else:
                term = ddb
                remaining -= ddb
        else:
            term = linear
        if i == loop_end:
            term *= period + 1.0 - loop_end
        vdb += term
    return vdb


def _vdb_value(cost, salvage, life, start, end, factor, no_switch):
    if (start < 0.0 or end < start or end > life or cost < 0.0
            or salvage > cost or factor <= 0.0 or life <= 0.0):
        return ExcelError("#NUM!")
    int_start = math.floor(start + 1e-12)
    int_end = math.ceil(end - 1e-12)
    if no_switch:
        vdb = 0.0
        for i in range(int(int_start) + 1, int(int_end) + 1):
            term = _ddb_amount(cost, salvage, life, float(i), factor)
            if i == int_start + 1:
                term *= min(end, int_start + 1.0) - start
            elif i == int_end:
                term *= end + 1.0 - int_end
            vdb += term
        return vdb
    life1 = life
    fractional = abs(start - int_start) > 1e-12 or abs(end - int_end) > 1e-12
    if fractional and factor > 1 and start >= life / 2.0 - 1e-12:
        # Excel's documented quirk: fractional spans starting in the second
        # half of the asset's life re-anchor at midlife.
        part = start - life / 2.0
        start = life / 2.0
        end -= part
        life1 += 1.0
    # Depreciation accumulated before `start` always comes off the cost first;
    # the requested span then runs on the reduced book value over the
    # remaining life.
    cost -= _inter_vdb(cost, salvage, life, life1, start, factor)
    return _inter_vdb(cost, salvage, life, life - start, end - start, factor)


def _fv_value(rate, nper, pmt, pv, ptype):
    """Balance after ``nper`` periods (Excel FV sign convention)."""
    if rate == 0.0:
        return -(pv + pmt * nper)
    growth = (1.0 + rate) ** nper
    return -(pv * growth + pmt * (1.0 + rate * ptype) * (growth - 1.0) / rate)


def _pmt_value(rate, nper, pv, fv, ptype):
    if nper == 0:
        return ExcelError("#DIV/0!")
    if rate == 0.0:
        return -(pv + fv) / nper
    growth = (1.0 + rate) ** nper
    denominator = (growth - 1.0) * (1.0 + rate * ptype)
    if denominator == 0:
        return ExcelError("#DIV/0!")
    return -(pv * growth + fv) * rate / denominator


def _ipmt_value(rate, per, nper, pv, fv, ptype):
    """Interest share of period ``per``: rate on the balance carried into it."""
    payment = _pmt_value(rate, nper, pv, fv, ptype)
    if isinstance(payment, ExcelError):
        return payment
    if per < 1 or per > nper:
        return ExcelError("#NUM!")
    if ptype and per == 1:
        return 0.0
    balance = _fv_value(rate, per - 1, payment, pv, ptype)
    interest = balance * rate
    return interest / (1.0 + rate) if ptype else interest


_RATE_CACHE: dict = {}


def _rate_value(nper, pmt, pv, fv, ptype, guess):
    """RATE as a root of the same NPV equation IRR solves.

    The flow series is ``pv`` now, ``pmt`` each period (shifted one period
    earlier when payments are due at the start), and ``fv`` at the end.
    """
    if nper <= 0:
        return ExcelError("#NUM!")
    key = (nper, pmt, pv, fv, ptype, guess)
    if key in _RATE_CACHE:
        return _RATE_CACHE[key]
    periods = int(nper)
    if periods == nper:
        shift = 1.0 if ptype else 0.0
        exponents = [0.0] + [t - shift for t in range(1, periods + 1)] + [float(periods)]
        values = [pv] + [pmt] * periods + [fv]
        nonzero = [v for v in values if v]
        changes = sum(1 for a, b in zip(nonzero, nonzero[1:]) if a * b < 0)
        result = _solve_rate(exponents, values, guess, scan=changes > 1)
    else:
        # Fractional periods have no discrete flow series; Newton on the
        # closed-form annuity equation, which is what Excel evaluates.
        def balance(rate):
            if rate == 0.0:
                return pv + pmt * nper + fv
            growth = (1.0 + rate) ** nper
            return pv * growth + pmt * (1.0 + rate * ptype) * (growth - 1.0) / rate + fv

        rate = guess
        result = ExcelError("#NUM!")
        for _ in range(100):
            try:
                value = balance(rate)
                step = max(abs(rate), 1e-5) * 1e-6
                derivative = (balance(rate + step) - balance(rate - step)) / (2.0 * step)
            except (OverflowError, ZeroDivisionError, ValueError):
                break
            if abs(derivative) < 1e-14:
                break
            nxt = rate - value / derivative
            if nxt <= -1.0:
                nxt = (rate - 1.0) / 2.0
            if abs(nxt - rate) < 1e-12:
                result = nxt
                break
            rate = nxt
    if len(_RATE_CACHE) > 4096:
        _RATE_CACHE.clear()
    _RATE_CACHE[key] = result
    return result


def _norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_pdf(z):
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _norm_s_inv(p):
    """Acklam's rational approximation, polished with one Halley step."""
    if not 0.0 < p < 1.0:
        return ExcelError("#NUM!")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        z = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        z = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        z = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    error = _norm_cdf(z) - p
    density = _norm_pdf(z)
    if density > 0:
        u = error / density
        z -= u / (1.0 + z * u / 2.0)
    return z


def _day_count_30_360(start: datetime, end: datetime, european: bool):
    d1, d2 = start.day, end.day
    if european:
        d1, d2 = min(d1, 30), min(d2, 30)
    else:
        start_feb_end = start.month == 2 and d1 == monthrange(start.year, 2)[1]
        end_feb_end = end.month == 2 and d2 == monthrange(end.year, 2)[1]
        if start_feb_end and end_feb_end:
            d2 = 30
        if start_feb_end:
            d1 = 30
        if d2 == 31 and d1 >= 30:
            d2 = 30
        if d1 == 31:
            d1 = 30
    return (end.year - start.year) * 360 + (end.month - start.month) * 30 + (d2 - d1)


def snap(result: float, *operands: float) -> float:
    """Mimic Excel's cancellation rule for sums.

    Excel zeroes a sum whose magnitude is negligible against its operands, so
    ``=SUM(a,-a)`` is exactly 0 and any ``IF(x>0)`` downstream takes the false
    branch. Plain IEEE arithmetic leaves a ~1e-15 residue and flips the branch.
    The threshold stays within a few ulps: workbook 0654 caches a genuine
    -1.04e-5 residue against 2.8e8 operands (3.8e-14 relative), which Excel
    demonstrably does not zero.
    """
    scale = max((abs(x) for x in operands), default=0.0)
    return 0.0 if scale and abs(result) < scale * 1e-15 else result


def _broadcast(operation, left, right):
    """Element-wise binary application in array context (legacy CSE rules).

    Scalars replicate across the other operand's shape; paired ranges align
    positionally, and members beyond the shorter operand read #N/A as Excel's
    array evaluation does.
    """
    ranges = [operand for operand in (left, right) if isinstance(operand, RangeValues)]
    shape = ranges[0]
    length = max(len(operand) for operand in ranges)

    def element(operand, index):
        if isinstance(operand, RangeValues):
            return operand[index] if index < len(operand) else ExcelError("#N/A")
        return operand

    values = []
    for index in range(length):
        a, b = element(left, index), element(right, index)
        if is_bad(a):
            values.append(a)
        elif is_bad(b):
            values.append(b)
        else:
            values.append(operation(a, b))
    rows, cols = shape.rows, shape.cols
    if rows * cols != length:
        rows, cols = 1, length
    return RangeValues(values, rows, cols)


def _numeric(value):
    """Excel arithmetic coercion: text must parse as a number, else #VALUE!."""
    if isinstance(value, str):
        parsed = num(value, math.nan)
        if math.isnan(parsed):
            return ExcelError("#VALUE!")
        return parsed
    return num(value)


def _arith(operation):
    def apply(a, b):
        x = _numeric(a)
        if isinstance(x, ExcelError):
            return x
        y = _numeric(b)
        if isinstance(y, ExcelError):
            return y
        return operation(x, y)
    return apply


def _negate(a):
    x = _numeric(a)
    return x if isinstance(x, ExcelError) else -x


def _percent(a):
    x = _numeric(a)
    return x if isinstance(x, ExcelError) else x / 100.0


BINARY = {
    "+": _arith(lambda x, y: snap(x + y, x, y)),
    "-": _arith(lambda x, y: snap(x - y, x, y)),
    "*": _arith(lambda x, y: x * y),
    "/": _arith(_div),
    "^": _arith(lambda x, y: _power(x, y)),
    "&": lambda a, b: f"{_text(a)}{_text(b)}",
    "=": lambda a, b: _equal(a, b),
    "<>": lambda a, b: not _equal(a, b),
    ">": lambda a, b: num(a) > num(b),
    "<": lambda a, b: num(a) < num(b),
    ">=": lambda a, b: num(a) >= num(b),
    "<=": lambda a, b: num(a) <= num(b),
}
UNARY = {
    "u-": _negate,
    # Excel's unary plus is a no-op that preserves its operand, including
    # text: =+IFERROR(x, "n.a.") must surface "n.a.", not 0.
    "u+": lambda a: a if a is not None else 0.0,
    "%": _percent,
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
    # target cell -> source cells actually read on the final evaluation pass,
    # including dynamic references (OFFSET) invisible to the static graph.
    runtime_radj: dict = dataclass_field(default_factory=dict)
    # cell id served by the workbook cached-value oracle -> the formula cells
    # that consumed it. Every entry is a value the evaluator could not derive
    # and copied from the golden workbook instead.
    oracle_reads: dict = dataclass_field(default_factory=dict)


class Evaluator:
    def __init__(self, graph: Graph, cg: CellGraph, oracle=None):
        self.graph = graph
        self.cg = cg
        self.oracle = oracle
        self.values: dict = {}
        self.unresolved: dict = {}
        self.unknown_ops: dict = defaultdict(int)
        self._arg_cache: dict = {}
        self.oracle_reads: dict = {}
        self._current_cell = None

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
            value = literal(raw) if isinstance(raw, str) else raw
            if value is not None:
                # A populated cell the graph never recorded: its value comes
                # straight from the golden workbook's cache, not from any
                # rebuild. Record who consumed it so verification can refuse
                # to count outputs fed this way.
                self.oracle_reads.setdefault(cid, set()).add(self._current_cell)
            return value
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
        if op == "COLUMN":
            return self._column(node)
        if op == "COUNTA":
            return self._counta(node)
        if op == "isect":
            return self._reference_join(node)
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
            if value is not None:
                return value
            # Excel: an omitted value_if_true yields 0, an omitted
            # value_if_false yields FALSE.
            return False if chosen == 2 else 0.0
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
        if op in selective:
            # Lookups tolerate bad values inside the searched range -- only the
            # matched row matters -- but a bad key/index/mode must poison the
            # result. Letting it fall through to num() would silently search
            # for 0 and fabricate a match Excel never produces.
            scalar_slots = {
                "VLOOKUP": (0, 2, 3), "HLOOKUP": (0, 2, 3),
                "XLOOKUP": (0,), "_XLFN.XLOOKUP": (0,),
                "MATCH": (0, 2), "INDEX": (1, 2),
            }[op]
            for slot in scalar_slots:
                if slot < len(args) and is_bad(args[slot]):
                    return args[slot]
        bad = next((a for a in flatten(args) if isinstance(a, Unresolved)), None)
        if bad is not None and op not in selective:
            return bad
        err = next((a for a in flatten(args) if isinstance(a, ExcelError)), None)
        if err is not None and op not in (
            "COUNTIF", "COUNTIFS", "SUMIF", "SUMIFS", "AVERAGEIFS", "AVERAGEIF",
            "MAXIFS", "_XLFN.MAXIFS", "MINIFS", "_XLFN.MINIFS",
            "ISERR", "ISERROR", "ISNA", "ISNUMBER", "IFNA", "_XLFN.IFNA",
            *selective
        ):
            return err
        if op == "{}":
            # Array constants; the parser flattens rows, so only 1-D literals
            # (the ones that actually appear) can be reshaped faithfully.
            if ";" in (node.expr or ""):
                return Unresolved("2d-array-constant")
            values = list(flatten(args))
            return RangeValues(values, 1, len(values))
        if node.op_kind in ("infix",) and op in BINARY:
            if len(args) < 2:
                return Unresolved("arity")
            left, right = args[0], args[1]
            if isinstance(left, RangeValues) or isinstance(right, RangeValues):
                return _broadcast(BINARY[op], left, right)
            return BINARY[op](left, right)
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
        # Excel stores post-2007 functions with a future-function prefix
        # (``_xlfn.NORM.DIST``), which the tokenizer keeps verbatim. Try the
        # bare name first, but keep the raw lookup as fallback: handlers like
        # ``_fn__xlfn_vstack`` exist only under the prefixed spelling.
        bare = op
        for prefix in ("_XLFN.", "_XLWS."):
            if bare.startswith(prefix):
                bare = bare[len(prefix):]
        handler = getattr(self, f"_fn_{bare.replace('.', '_').lower()}", None)
        if handler is None and bare != op:
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
        explicit_col = len(args) > 2 and args[2] is not None
        if not explicit_col and source.rows == 1 and row >= 1:
            # Excel treats a lone index into a single-row vector as the
            # position along the row, not a row number.
            return pool[row - 1] if row <= len(pool) else ExcelError("#REF!")
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

    def _fn_vdb(self, args):
        if len(args) < 5:
            return ExcelError("#VALUE!")
        cost, salvage, life, start, end = (num(a) for a in args[:5])
        factor = num(args[5]) if len(args) > 5 and args[5] is not None else 2.0
        no_switch = truthy(args[6]) if len(args) > 6 and args[6] is not None else False
        return _vdb_value(cost, salvage, life, start, end, factor, no_switch)

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

    def _fn_xnpv(self, args):
        rate = num(args[0])
        flows = list(flatten([args[1]])) if len(args) > 1 else []
        dates = list(flatten([args[2]])) if len(args) > 2 else []
        if not flows or len(flows) != len(dates) or rate <= -1.0:
            return ExcelError("#NUM!")
        start = num(dates[0], 0.0)
        try:
            return sum(
                num(value, 0.0) * (1.0 + rate) ** (-(num(date, 0.0) - start) / 365.0)
                for value, date in zip(flows, dates)
            )
        except (OverflowError, ZeroDivisionError, ValueError):
            return ExcelError("#NUM!")

    def _fn_mirr(self, args):
        flows = [
            num(v) for v in flatten([args[0]])
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        finance_rate = num(args[1]) if len(args) > 1 else 0.0
        reinvest_rate = num(args[2]) if len(args) > 2 else 0.0
        n = len(flows)
        npv_positive = sum(
            v / (1.0 + reinvest_rate) ** i for i, v in enumerate(flows) if v > 0
        )
        npv_negative = sum(
            v / (1.0 + finance_rate) ** i for i, v in enumerate(flows) if v < 0
        )
        if n < 2 or npv_positive == 0 or npv_negative == 0:
            return ExcelError("#DIV/0!")
        try:
            ratio = npv_positive / -npv_negative
            return ratio ** (1.0 / (n - 1)) * (1.0 + reinvest_rate) - 1.0
        except (OverflowError, ZeroDivisionError, ValueError):
            return ExcelError("#NUM!")

    def _fn_pmt(self, args):
        fv = num(args[3]) if len(args) > 3 else 0.0
        ptype = 1 if len(args) > 4 and truthy(args[4]) else 0
        return _pmt_value(num(args[0]), num(args[1]), num(args[2]), fv, ptype)

    def _fn_ipmt(self, args):
        fv = num(args[4]) if len(args) > 4 else 0.0
        ptype = 1 if len(args) > 5 and truthy(args[5]) else 0
        return _ipmt_value(
            num(args[0]), int(num(args[1])), num(args[2]), num(args[3]), fv, ptype)

    def _fn_ppmt(self, args):
        fv = num(args[4]) if len(args) > 4 else 0.0
        ptype = 1 if len(args) > 5 and truthy(args[5]) else 0
        rate, per = num(args[0]), int(num(args[1]))
        nper, pv = num(args[2]), num(args[3])
        payment = _pmt_value(rate, nper, pv, fv, ptype)
        interest = _ipmt_value(rate, per, nper, pv, fv, ptype)
        if isinstance(payment, ExcelError):
            return payment
        if isinstance(interest, ExcelError):
            return interest
        return payment - interest

    def _fn_pv(self, args):
        rate, nper, pmt = num(args[0]), num(args[1]), num(args[2])
        fv = num(args[3]) if len(args) > 3 else 0.0
        ptype = 1 if len(args) > 4 and truthy(args[4]) else 0
        if rate == 0.0:
            return -(fv + pmt * nper)
        try:
            growth = (1.0 + rate) ** nper
        except (OverflowError, ZeroDivisionError, ValueError):
            return ExcelError("#NUM!")
        return -(fv + pmt * (1.0 + rate * ptype) * (growth - 1.0) / rate) / growth

    def _fn_fv(self, args):
        rate, nper, pmt = num(args[0]), num(args[1]), num(args[2])
        pv = num(args[3]) if len(args) > 3 else 0.0
        ptype = 1 if len(args) > 4 and truthy(args[4]) else 0
        try:
            return _fv_value(rate, nper, pmt, pv, ptype)
        except (OverflowError, ZeroDivisionError, ValueError):
            return ExcelError("#NUM!")

    def _fn_nper(self, args):
        rate, pmt, pv = num(args[0]), num(args[1]), num(args[2])
        fv = num(args[3]) if len(args) > 3 else 0.0
        ptype = 1 if len(args) > 4 and truthy(args[4]) else 0
        if rate == 0.0:
            return ExcelError("#DIV/0!") if pmt == 0 else -(pv + fv) / pmt
        adjusted = pmt * (1.0 + rate * ptype) / rate
        denominator = adjusted + pv
        if denominator == 0:
            return ExcelError("#DIV/0!")
        ratio = (adjusted - fv) / denominator
        if ratio <= 0 or rate <= -1.0:
            return ExcelError("#NUM!")
        return math.log(ratio) / math.log(1.0 + rate)

    def _fn_rate(self, args):
        nper, pmt, pv = num(args[0]), num(args[1]), num(args[2])
        fv = num(args[3]) if len(args) > 3 else 0.0
        ptype = 1 if len(args) > 4 and truthy(args[4]) else 0
        guess = num(args[5], 0.1) if len(args) > 5 else 0.1
        return _rate_value(nper, pmt, pv, fv, ptype, guess)

    def _fn_cumipmt(self, args):
        rate, nper, pv = num(args[0]), num(args[1]), num(args[2])
        start, end = int(num(args[3])), int(num(args[4]))
        ptype = 1 if len(args) > 5 and truthy(args[5]) else 0
        if rate <= 0 or nper <= 0 or pv <= 0 or start < 1 or end < start or end > nper:
            return ExcelError("#NUM!")
        total = 0.0
        for per in range(start, end + 1):
            interest = _ipmt_value(rate, per, nper, pv, 0.0, ptype)
            if isinstance(interest, ExcelError):
                return interest
            total += interest
        return total

    def _fn_cumprinc(self, args):
        rate, nper, pv = num(args[0]), num(args[1]), num(args[2])
        start, end = int(num(args[3])), int(num(args[4]))
        ptype = 1 if len(args) > 5 and truthy(args[5]) else 0
        if rate <= 0 or nper <= 0 or pv <= 0 or start < 1 or end < start or end > nper:
            return ExcelError("#NUM!")
        payment = _pmt_value(rate, nper, pv, 0.0, ptype)
        if isinstance(payment, ExcelError):
            return payment
        total = 0.0
        for per in range(start, end + 1):
            interest = _ipmt_value(rate, per, nper, pv, 0.0, ptype)
            if isinstance(interest, ExcelError):
                return interest
            total += payment - interest
        return total

    def _fn_mod(self, args):
        divisor = num(args[1]) if len(args) > 1 else 0.0
        if divisor == 0:
            return ExcelError("#DIV/0!")
        return num(args[0]) % divisor

    def _fn_networkdays(self, args):
        start, end = int(num(args[0])), int(num(args[1]))
        holidays = set()
        if len(args) > 2 and args[2] is not None:
            for value in flatten([args[2]]):
                serial = num(value, math.nan)
                if not math.isnan(serial):
                    holidays.add(int(serial))
        sign = 1
        if start > end:
            start, end, sign = end, start, -1
        count = sum(
            1 for serial in range(start, end + 1)
            if (EPOCH + timedelta(days=serial)).weekday() < 5
            and serial not in holidays
        )
        return float(sign * count)

    def _fn_yearfrac(self, args):
        start_serial = num(args[0])
        end_serial = num(args[1]) if len(args) > 1 else 0.0
        basis = int(num(args[2])) if len(args) > 2 and args[2] is not None else 0
        if start_serial > end_serial:
            start_serial, end_serial = end_serial, start_serial
        start = EPOCH + timedelta(days=int(start_serial))
        end = EPOCH + timedelta(days=int(end_serial))
        actual_days = int(end_serial) - int(start_serial)
        if basis == 0:
            return _day_count_30_360(start, end, european=False) / 360.0
        if basis == 4:
            return _day_count_30_360(start, end, european=True) / 360.0
        if basis == 2:
            return actual_days / 360.0
        if basis == 3:
            return actual_days / 365.0
        if basis == 1:
            def leap(year):
                return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            if start.year == end.year:
                denominator = 366.0 if leap(start.year) else 365.0
            elif end <= start.replace(year=start.year + 1, day=min(start.day, 28)) \
                    or (end - start).days <= 366:
                feb29_hit = any(
                    leap(year)
                    and start <= datetime(year, 2, 29) <= end
                    for year in (start.year, end.year)
                )
                denominator = 366.0 if feb29_hit else 365.0
            else:
                years = range(start.year, end.year + 1)
                denominator = sum(366.0 if leap(y) else 365.0 for y in years) / len(years)
            return actual_days / denominator
        return ExcelError("#NUM!")

    def _fn_norm_dist(self, args):
        x, mean, sd = num(args[0]), num(args[1]), num(args[2])
        if sd <= 0:
            return ExcelError("#NUM!")
        cumulative = truthy(args[3]) if len(args) > 3 else True
        z = (x - mean) / sd
        return _norm_cdf(z) if cumulative else _norm_pdf(z) / sd

    _fn_normdist = _fn_norm_dist

    def _fn_norm_s_dist(self, args):
        # Legacy NORMSDIST takes no flag and is always cumulative; NORM.S.DIST
        # passes one, so a missing second argument defaults to cumulative.
        z = num(args[0])
        cumulative = truthy(args[1]) if len(args) > 1 and args[1] is not None else True
        return _norm_cdf(z) if cumulative else _norm_pdf(z)

    _fn_normsdist = _fn_norm_s_dist

    def _fn_norm_inv(self, args):
        p, mean, sd = num(args[0]), num(args[1]), num(args[2])
        if sd <= 0:
            return ExcelError("#NUM!")
        z = _norm_s_inv(p)
        return z if isinstance(z, ExcelError) else mean + sd * z

    _fn_norminv = _fn_norm_inv

    def _fn_norm_s_inv(self, args):
        return _norm_s_inv(num(args[0]))

    _fn_normsinv = _fn_norm_s_inv

    def _fn_product(self, args):
        vals = numbers(args)
        return math.prod(vals) if vals else 0.0

    def _fn_power(self, args):
        return _power(num(args[0]), num(args[1]) if len(args) > 1 else 0.0)

    def _fn_exp(self, args):
        try:
            return math.exp(num(args[0]))
        except OverflowError:
            return ExcelError("#NUM!")

    def _fn_ln(self, args):
        value = num(args[0])
        return math.log(value) if value > 0 else ExcelError("#NUM!")

    def _fn_concatenate(self, args):
        return "".join(_text(value) for value in flatten(args))

    # -- census-driven long tail -------------------------------------------
    def _fn_averageif(self, args):
        pool = list(flatten([args[0]]))
        target = list(flatten([args[2]])) if len(args) > 2 and args[2] is not None else pool
        vals = [
            num(target[i]) for i, value in enumerate(pool)
            if _matches(value, args[1]) and i < len(target)
            and isinstance(target[i], (int, float)) and not isinstance(target[i], bool)
        ]
        return sum(vals) / len(vals) if vals else ExcelError("#DIV/0!")

    def _fn_maxifs(self, args):
        target = list(flatten([args[0]])) if args else []
        matches = self._criteria_matches(args, 1)
        vals = [
            num(target[i]) for i, matched in enumerate(matches)
            if matched and i < len(target)
            and isinstance(target[i], (int, float)) and not isinstance(target[i], bool)
        ]
        return max(vals) if vals else 0.0

    def _fn_minifs(self, args):
        target = list(flatten([args[0]])) if args else []
        matches = self._criteria_matches(args, 1)
        vals = [
            num(target[i]) for i, matched in enumerate(matches)
            if matched and i < len(target)
            and isinstance(target[i], (int, float)) and not isinstance(target[i], bool)
        ]
        return min(vals) if vals else 0.0

    def _fn_days360(self, args):
        start = EPOCH + timedelta(days=int(num(args[0])))
        end = EPOCH + timedelta(days=int(num(args[1] if len(args) > 1 else None)))
        european = len(args) > 2 and truthy(args[2])
        return float(_day_count_30_360(start, end, european))

    def _fn_lookup(self, args):
        lookup = args[0]
        table = args[1] if len(args) > 1 else []
        if isinstance(table, RangeValues) and table.rows > 1 and table.cols > 1:
            # Array form: search the longer edge, return the opposite edge.
            values = list(table)
            if table.cols >= table.rows:
                keys = values[: table.cols]
                results = values[(table.rows - 1) * table.cols:]
            else:
                keys = values[:: table.cols]
                results = values[table.cols - 1:: table.cols]
        else:
            keys = list(flatten([table]))
            results = list(flatten([args[2]])) if len(args) > 2 and args[2] is not None else keys
        selected = None
        for index, key in enumerate(keys):
            if _equal(key, lookup):
                selected = index
                break
            if isinstance(key, (int, float)) and not isinstance(key, bool) \
                    and key <= num(lookup):
                selected = index
        if selected is None or selected >= len(results):
            return ExcelError("#N/A")
        return results[selected]

    def _fn_subtotal(self, args):
        # Hidden-row variants (1xx) computed over everything: the graph has no
        # visibility data, and models that hide rows fail verification honestly.
        code = int(num(args[0])) % 100
        rest = args[1:]
        if code == 1:
            return self._fn_average(rest)
        if code == 2:
            return self._fn_count(rest)
        if code == 3:
            values = list(flatten(rest))
            return float(sum(v is not None and v != "" for v in values))
        if code == 4:
            return self._fn_max(rest)
        if code == 5:
            return self._fn_min(rest)
        if code == 6:
            return self._fn_product(rest)
        if code == 9:
            return self._fn_sum(rest)
        return Unresolved(f"unsupported:SUBTOTAL({code})")

    def _fn_int(self, args):
        return float(math.floor(num(args[0])))

    def _fn_n(self, args):
        value = self._scalar(args[0] if args else None)
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        return 0.0

    def _fn_now(self, args):
        delta = datetime.now() - EPOCH
        return delta.days + delta.seconds / 86400.0

    def _fn_workday(self, args):
        serial = int(num(args[0]))
        remaining = int(num(args[1] if len(args) > 1 else None))
        holidays = set()
        if len(args) > 2 and args[2] is not None:
            for value in flatten([args[2]]):
                day = num(value, math.nan)
                if not math.isnan(day):
                    holidays.add(int(day))
        step = 1 if remaining >= 0 else -1
        remaining = abs(remaining)
        while remaining:
            serial += step
            if (EPOCH + timedelta(days=serial)).weekday() < 5 and serial not in holidays:
                remaining -= 1
        return float(serial)

    def _fn_weekday(self, args):
        if isinstance(args[0], RangeValues):
            # Array context, e.g. SUMPRODUCT(--(WEEKDAY(ROW(...), 2) < 6)).
            source = args[0]
            values = [self._fn_weekday([value] + list(args[1:]))
                      for value in source]
            return RangeValues(values, source.rows, source.cols)
        monday0 = (EPOCH + timedelta(days=int(num(args[0])))).weekday()
        mode = int(num(args[1], 1.0)) if len(args) > 1 and args[1] is not None else 1
        if mode == 1:
            return float((monday0 + 1) % 7 + 1)
        if mode == 2:
            return float(monday0 + 1)
        if mode == 3:
            return float(monday0)
        return ExcelError("#NUM!")

    def _fn_datedif(self, args):
        start = EPOCH + timedelta(days=int(num(args[0])))
        end = EPOCH + timedelta(days=int(num(args[1] if len(args) > 1 else None)))
        unit = _text(args[2] if len(args) > 2 else "").strip().upper()
        if end < start:
            return ExcelError("#NUM!")
        months = (end.year - start.year) * 12 + end.month - start.month
        if end.day < start.day:
            months -= 1
        if unit == "D":
            return float((end - start).days)
        if unit == "Y":
            return float(months // 12)
        if unit == "M":
            return float(months)
        if unit == "YM":
            return float(months % 12)
        if unit == "MD":
            anchor = _edate((start - EPOCH).days, months)
            return float((end - EPOCH).days - anchor)
        if unit == "YD":
            anchor = _edate((start - EPOCH).days, (months // 12) * 12)
            return float((end - EPOCH).days - anchor)
        return ExcelError("#NUM!")

    def _fn_rows(self, args):
        source = args[0] if args else None
        return float(source.rows) if isinstance(source, RangeValues) else 1.0

    def _fn_columns(self, args):
        source = args[0] if args else None
        return float(source.cols) if isinstance(source, RangeValues) else 1.0

    def _fn_countblank(self, args):
        values = list(flatten(args))
        return float(sum(v is None or v == "" for v in values))

    def _fn_mina(self, args):
        vals = [num(v) for v in flatten(args) if v is not None]
        return min(vals) if vals else 0.0

    def _fn_maxa(self, args):
        vals = [num(v) for v in flatten(args) if v is not None]
        return max(vals) if vals else 0.0

    def _fn_small(self, args):
        vals = sorted(numbers([args[0]]))
        k = int(num(args[1])) if len(args) > 1 else 1
        return vals[k - 1] if 1 <= k <= len(vals) else ExcelError("#NUM!")

    def _fn_large(self, args):
        vals = sorted(numbers([args[0]]), reverse=True)
        k = int(num(args[1])) if len(args) > 1 else 1
        return vals[k - 1] if 1 <= k <= len(vals) else ExcelError("#NUM!")

    def _fn_quartile(self, args):
        vals = sorted(numbers([args[0]]))
        quart = int(num(args[1])) if len(args) > 1 else 0
        if not vals or not 0 <= quart <= 4:
            return ExcelError("#NUM!")
        position = quart / 4.0 * (len(vals) - 1)
        low = int(math.floor(position))
        frac = position - low
        if low + 1 < len(vals):
            return vals[low] + frac * (vals[low + 1] - vals[low])
        return vals[low]

    _fn_quartile_inc = _fn_quartile

    def _fn_gcd(self, args):
        vals = [int(num(v)) for v in flatten(args)
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not vals or any(v < 0 for v in vals):
            return ExcelError("#NUM!")
        out = 0
        for v in vals:
            out = math.gcd(out, v)
        return float(out)

    def _fn_slope(self, args):
        known_y = numbers([args[0]]) if args else []
        known_x = numbers([args[1]]) if len(args) > 1 else []
        if not known_y or len(known_y) != len(known_x):
            return ExcelError("#N/A")
        mean_x = sum(known_x) / len(known_x)
        mean_y = sum(known_y) / len(known_y)
        denominator = sum((x - mean_x) ** 2 for x in known_x)
        if denominator == 0:
            return ExcelError("#DIV/0!")
        return sum((x - mean_x) * (y - mean_y)
                   for x, y in zip(known_x, known_y)) / denominator

    def _fn_rsq(self, args):
        known_y = numbers([args[0]]) if args else []
        known_x = numbers([args[1]]) if len(args) > 1 else []
        if not known_y or len(known_y) != len(known_x):
            return ExcelError("#N/A")
        mean_x = sum(known_x) / len(known_x)
        mean_y = sum(known_y) / len(known_y)
        var_x = sum((x - mean_x) ** 2 for x in known_x)
        var_y = sum((y - mean_y) ** 2 for y in known_y)
        if var_x == 0 or var_y == 0:
            return ExcelError("#DIV/0!")
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(known_x, known_y))
        return cov * cov / (var_x * var_y)

    def _fn_avedev(self, args):
        vals = numbers(args)
        if not vals:
            return ExcelError("#NUM!")
        mean = sum(vals) / len(vals)
        return sum(abs(v - mean) for v in vals) / len(vals)

    def _fn_trim(self, args):
        return re.sub(r" +", " ", _text(args[0])).strip(" ")

    def _fn_left(self, args):
        count = int(num(args[1])) if len(args) > 1 and args[1] is not None else 1
        return _text(args[0])[:count] if count >= 0 else ExcelError("#VALUE!")

    def _fn_lower(self, args):
        return _text(args[0]).lower()

    def _fn_substitute(self, args):
        text = _text(args[0])
        old = _text(args[1] if len(args) > 1 else "")
        new = _text(args[2] if len(args) > 2 else "")
        if not old:
            return text
        if len(args) > 3 and args[3] is not None:
            instance = int(num(args[3]))
            if instance < 1:
                return ExcelError("#VALUE!")
            parts = text.split(old)
            if instance >= len(parts):
                return text
            return old.join(parts[:instance]) + new + old.join(parts[instance:])
        return text.replace(old, new)

    def _fn_search(self, args):
        start = int(num(args[2], 1.0)) if len(args) > 2 and args[2] is not None else 1
        haystack = _text(args[1] if len(args) > 1 else "").lower()
        position = haystack.find(_text(args[0]).lower(), max(start - 1, 0))
        return float(position + 1) if position >= 0 else ExcelError("#VALUE!")

    def _fn_fixed(self, args):
        decimals = int(num(args[1], 2.0)) if len(args) > 1 and args[1] is not None else 2
        no_commas = len(args) > 2 and truthy(args[2])
        value = round(num(args[0]), decimals)
        rendered = f"{value:.{max(decimals, 0)}f}" if no_commas else \
            f"{value:,.{max(decimals, 0)}f}"
        return rendered

    def _fn_textjoin(self, args):
        delimiter = _text(args[0] if args else "")
        ignore_empty = truthy(args[1]) if len(args) > 1 else True
        parts = [_text(v) for v in flatten(args[2:])]
        if ignore_empty:
            parts = [p for p in parts if p != ""]
        return delimiter.join(parts)

    def _fn_concat(self, args):
        return self._fn_concatenate(args)

    def _fn_ifna(self, args):
        value = args[0] if args else None
        if isinstance(value, ExcelError) and value.code == "#N/A":
            fallback = args[1] if len(args) > 1 else None
            return 0.0 if fallback is None else fallback
        return value

    @staticmethod
    def _scalar(value):
        """Implicit intersection for predicates handed a range."""
        if isinstance(value, RangeValues):
            return value[0] if value else None
        return value

    def _fn_isnumber(self, args):
        value = self._scalar(args[0] if args else None)
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _fn_isblank(self, args):
        return self._scalar(args[0] if args else None) is None

    def _fn_iserr(self, args):
        value = self._scalar(args[0] if args else None)
        return isinstance(value, ExcelError) and value.code != "#N/A"

    def _fn_iserror(self, args):
        return isinstance(self._scalar(args[0] if args else None), ExcelError)

    def _fn_isna(self, args):
        value = self._scalar(args[0] if args else None)
        return isinstance(value, ExcelError) and value.code == "#N/A"

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

    _REF_ARG_RE = re.compile(
        r"^\s*[A-Za-z_.]+\(\s*"
        r"(?:(?:'[^']+'|[A-Za-z0-9_ .&-]+)!)?\$?([A-Za-z]{1,3})\$?(\d{1,7})")

    def _reference_axis(self, node, axis):
        """Coordinates a ROW()/COLUMN() argument covers, or None.

        Handles plain references, spans, and statically resolved INDIRECT
        targets; multi-member references return the sorted distinct axis
        values (Excel's array form).
        """
        specs = self._arg_specs(node)
        if not specs:
            return None
        spec = specs[0]
        if spec[0] == "span":
            coordinates = [_split(member) for member in spec[1]]
            return sorted({c[axis] for c in coordinates if c is not None})
        if spec[0] == "scalar":
            source = self.graph.nodes.get(spec[1])
            if source is None:
                return None
            if source.is_cell:
                value = source.row if axis == 1 else source.col
                return [value] if value is not None else None
            if source.op == "INDIRECT":
                declared = int(source.arity) if str(source.arity).isdigit() else 1
                inner = self._arg_specs(source)
                if len(inner) > declared:
                    target = inner[declared]
                    if target[0] == "span":
                        coordinates = [_split(member) for member in target[1]]
                        return sorted({c[axis] for c in coordinates if c is not None})
                    if target[0] == "scalar":
                        cell = self.graph.nodes.get(target[1])
                        if cell is not None and cell.is_cell:
                            value = cell.row if axis == 1 else cell.col
                            return [value] if value is not None else None
                # No statically resolved target: the reference text may still
                # be computable, e.g. ROW(INDIRECT(T2&":"&T3)) where the dates
                # concatenate into a "46023:46053" whole-row span.
                if inner:
                    text = self._arg_value(inner[0])
                    if isinstance(text, str) and axis == 1:
                        match = re.match(
                            r"^\s*(\d{1,7})(?:\.0+)?\s*:\s*(\d{1,7})(?:\.0+)?\s*$",
                            text,
                        )
                        if match:
                            low, high = int(match.group(1)), int(match.group(2))
                            if low <= high and high - low < 10000:
                                return list(range(low, high + 1))
        return None

    def _row(self, node):
        """Excel ROW: scalar for a cell, array for a range, owner row bare."""
        values = self._reference_axis(node, 1)
        if values:
            if len(values) == 1:
                return float(values[0])
            return RangeValues([float(v) for v in values], len(values), 1)
        # An argument pointing at an empty cell has no edge; the literal
        # reference is still in the expression text.
        if str(node.arity) not in ("", "0"):
            match = self._REF_ARG_RE.match(node.expr or "")
            if match:
                return float(int(match.group(2)))
        owner = self.graph.nodes.get(node.owner)
        return float(owner.row) if owner is not None and owner.row is not None else ExcelError("#REF!")

    def _column(self, node):
        """Excel COLUMN: scalar for a cell, array for a range, owner column bare."""
        values = self._reference_axis(node, 2)
        if values:
            if len(values) == 1:
                return float(values[0])
            return RangeValues([float(v) for v in values], 1, len(values))
        if str(node.arity) not in ("", "0"):
            match = self._REF_ARG_RE.match(node.expr or "")
            if match:
                return float(col_number(match.group(1)))
        owner = self.graph.nodes.get(node.owner)
        return float(owner.col) if owner is not None and owner.col is not None else ExcelError("#REF!")

    def _reference_join(self, node):
        """Reference-form colon, e.g. ``SUM(INDEX(...):INDEX(...))``.

        The parser renders a colon between computed references as nested
        ``isect`` nodes. When every leaf operand is an INDEX over a real span,
        resolve each INDEX to its member cell and read the rectangle between
        them. Anything else stays unresolved, as before.
        """
        leaves = []

        def collect(nid):
            source = self.graph.nodes.get(nid)
            if source is not None and source.op == "isect":
                for edge in sorted(self.graph.in_edges.get(nid, ()),
                                   key=lambda e: e.arg_index):
                    collect(edge.source)
            else:
                leaves.append(nid)

        collect(node.id)
        corner_cells = []
        real_leaves = 0
        for leaf in leaves:
            source = self.graph.nodes.get(leaf)
            if source is not None and source.kind == "name" and \
                    source.label in ("", ":"):
                # Placeholder produced by the parser for the colon itself.
                continue
            real_leaves += 1
            if source is None or source.op != "INDEX":
                return Unresolved("unsupported:isect")
            members = self._index_reference(source)
            if not isinstance(members, list):
                return members if is_bad(members) else Unresolved("unsupported:isect")
            corner_cells.extend(members)
        if real_leaves != 2 or not corner_cells:
            return Unresolved("unsupported:isect")
        splits = [_split(corner) for corner in corner_cells]
        if any(s is None for s in splits) or len({s[0] for s in splits}) != 1:
            return Unresolved("unsupported:isect")
        sheet = splits[0][0]
        row_lo, row_hi = min(s[1] for s in splits), max(s[1] for s in splits)
        col_lo, col_hi = min(s[2] for s in splits), max(s[2] for s in splits)
        members = [
            a1(sheet, row, col)
            for row in range(row_lo, row_hi + 1)
            for col in range(col_lo, col_hi + 1)
        ]
        values = [self._read(cid) for cid in members]
        return RangeValues(values, row_hi - row_lo + 1, col_hi - col_lo + 1)

    def _index_reference(self, node):
        """Member cell ids an INDEX call refers to, for reference-form use.

        Returns a one-element list for an ordinary lookup, the whole member
        list for Excel's documented ``INDEX(range, 0)`` whole-range form, or
        an error/Unresolved marker.
        """
        specs = self._arg_specs(node)
        if not specs or specs[0][0] != "span":
            return Unresolved("unsupported:isect")
        members = specs[0][1]
        coordinates = [_split(cid) for cid in members]
        rows = len({row for _, row, _ in coordinates})
        cols = len({col for _, _, col in coordinates})
        first = self._arg(node, 1) if len(specs) > 1 else 1.0
        if is_bad(first):
            return first
        position = int(num(first))
        if position == 0:
            return list(members)
        second = self._arg(node, 2) if len(specs) > 2 else None
        if second is not None:
            if is_bad(second):
                return second
            row_index, col_index = position, int(num(second))
        elif rows == 1:
            row_index, col_index = 1, position
        else:
            row_index, col_index = position, 1
        index = (row_index - 1) * cols + col_index - 1
        if not (1 <= row_index <= rows and 1 <= col_index <= cols
                and 0 <= index < len(members)):
            return ExcelError("#REF!")
        return [members[index]]

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
        # Excel's COUNTA counts every non-empty cell, including formulas that
        # display as "" -- only genuinely empty cells (None here) are skipped.
        return float(sum(value is not None for value in values))

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
        if height == 0 or width == 0:
            return ExcelError("#REF!")
        # Excel allows negative height/width: the range anchors at the target
        # cell and extends upward/leftward, e.g. SUM(OFFSET(AF85,0,0,1,-3)).
        if height < 0:
            target_row, height = target_row + height + 1, -height
        if width < 0:
            target_col, width = target_col + width + 1, -width
        if target_row < 1 or target_col < 1:
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
            self._current_cell = target
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
                ordered = self._cycle_order(group, active_adj)
                pending = [
                    cell for cell in ordered
                    if cell not in seeded and cell not in indeterminate
                ]
                if not pending:
                    continue
                cyclic = len(group) > 1 or any(
                    cell in active_adj.get(cell, ()) for cell in group
                )
                if not cyclic:
                    self.values[pending[0]] = self._eval_cell(pending[0])
                    continue
                # A workbook whose calculation settings disable iteration but
                # whose cached values are a converged circular solution can
                # only have been saved by a session that did iterate (Excel
                # writes zeros otherwise). Recompute the cycle either way and
                # let verification's cached-value comparison be the judge; the
                # diagnostic reason records the setting mismatch.
                diagnostic = self._iterate_group(
                    pending, iteration_limit, iteration_delta
                )
                diagnostic["unique"] = True
                diagnostic.update({
                    "size": len(group), "seed": comp,
                    "graph_pass": graph_pass + 1,
                })
                if not iterate:
                    diagnostic["reason"] = "iteration-disabled-recomputed"
                cycle_candidates.append((tuple(sorted(group)), pending, diagnostic))
                current_cycles[tuple(sorted(group))] = diagnostic

            max_change = max(
                (self._value_change(before_pass[cell], self.values[cell]) for cell in cells),
                default=0.0,
            )
            latest_cycles = current_cycles
            if signature == last_signature and max_change <= iteration_delta:
                found_indeterminate = False
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
        # The final pass's active graph carries the dependencies actually read,
        # including runtime-resolved dynamic references (OFFSET targets) that
        # the static edge list cannot see. Verification walks the output cone
        # over these edges as well as the static ones.
        runtime_radj = {
            target: set(sources) for target, sources in active_radj.items()
        }
        return EvalResult(
            self.values, self.unresolved, iterated, coverage,
            runtime_radj=runtime_radj, oracle_reads=self.oracle_reads,
        )

    @staticmethod
    def _cycle_order(group, adjacency):
        """Dependency-ordered sweep for a (possibly cyclic) group.

        Excel updates a circular block in calculation-chain order, so a
        division sees its in-cycle denominator's fresh value within the same
        sweep. An alphabetical sweep can evaluate the division first, produce
        a transient #DIV/0!, and let the error stick across iterations.
        Kahn's algorithm over the intra-group edges, breaking ties and true
        cycles deterministically by name.
        """
        members = set(group)
        incoming = {cell: 0 for cell in group}
        forward = {cell: [] for cell in group}
        for source in group:
            for target in adjacency.get(source, ()):
                if target in members and target != source:
                    forward[source].append(target)
                    incoming[target] += 1
        ready = [cell for cell, degree in incoming.items() if degree == 0]
        heapq.heapify(ready)
        remaining = {cell for cell, degree in incoming.items() if degree > 0}
        order = []
        while ready or remaining:
            if not ready:
                # A genuine cycle: break it at the lexicographically smallest
                # member so repeated runs sweep identically.
                cut = min(remaining)
                remaining.discard(cut)
                incoming[cut] = 0
                heapq.heappush(ready, cut)
                continue
            cell = heapq.heappop(ready)
            order.append(cell)
            for target in forward[cell]:
                if target in remaining:
                    incoming[target] -= 1
                    if incoming[target] <= 0:
                        remaining.discard(target)
                        heapq.heappush(ready, target)
        return order

    @staticmethod
    def _value_change(before, after):
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            if math.isfinite(float(before)) and math.isfinite(float(after)):
                return abs(float(after) - float(before))
        return 0.0 if type(before) is type(after) and repr(before) == repr(after) else math.inf

    def _iterate_group(self, pending, iteration_limit, iteration_delta):
        max_change = math.inf
        refine_delta = min(iteration_delta, 1e-11)
        converged_step = None
        for step in range(iteration_limit):
            max_change = 0.0
            for cell in pending:
                before = self.values[cell]
                after = self._eval_cell(cell)
                self.values[cell] = after
                max_change = max(max_change, self._value_change(before, after))
            if max_change <= iteration_delta and converged_step is None:
                converged_step = step
            # Excel stops at the workbook's delta, but every subsequent
            # recalculation of a saved file iterates again from the converged
            # state, so cached values sit essentially at the fixed point.
            # Keep polishing (bounded) until the change is negligible, not
            # merely below the workbook threshold.
            if max_change <= refine_delta:
                break
            if converged_step is not None and step - converged_step >= 200:
                break
        return {
            "iterations": (converged_step if converged_step is not None else step) + 1,
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
        self._current_cell = cid
        info = self.cg.info[cid]
        root = self.graph.root_of(cid)
        if root is not None:
            value = self._eval_node(root)
            # Dynamic-array anchors store the first element in the owning
            # cell; members of a multi-cell CSE span each take their own
            # positional element of the shared result.
            if isinstance(value, RangeValues):
                return self._array_element(info.node, value)
            return value
        if info.empty_ref:
            return 0.0
        return Unresolved("no-ast-root")

    def _array_element(self, node, value):
        """Pick this cell's element from a multi-cell array-formula result.

        Excel's CSE semantics: the member at offset (dr, dc) inside the
        entered span reads result[dr][dc], with single-row/single-column
        results broadcast across the span and out-of-range members #N/A.
        Cells without a recorded span keep the anchor's first element.
        """
        if not value:
            return None
        span = getattr(node, "array_span", "")
        if not span or node.row is None or node.col is None:
            return value[0]
        start = span.split(":", 1)[0].replace("$", "")
        matched = A1_RE.match(start)
        if not matched:
            return value[0]
        offset_row = node.row - int(matched.group(2))
        offset_col = node.col - col_number(matched.group(1))
        row = offset_row if value.rows > 1 else 0
        col = offset_col if value.cols > 1 else 0
        if row < 0 or col < 0 or row >= value.rows or col >= value.cols:
            return ExcelError("#N/A")
        index = row * value.cols + col
        if index >= len(value):
            return ExcelError("#N/A")
        element = value[index]
        # An empty source cell inside an array result renders as 0 in Excel.
        return 0.0 if element is None else element


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
    if want is None:
        # The workbook cached no value, so there is no ground truth to compare
        # against. Calling a recomputed 0/blank a "match" here would verify
        # nothing (and Python's False == 0.0 made even FALSE pass); report the
        # cell as unverifiable instead of inflating the match count.
        return "unverifiable", None
    if isinstance(want, str) or isinstance(actual, str):
        return ("match" if _text(want).strip() == _text(actual).strip() else "mismatch"), None
    got, ref = num(actual, math.nan), num(want, math.nan)
    if math.isnan(got) or math.isnan(ref):
        return "unresolved", None
    diff = abs(got - ref)
    scale = max(abs(ref), 1.0)
    return ("match" if diff / scale <= tolerance else "mismatch"), diff
