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
import os
import re
import stat
import time
import zipfile
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta
from functools import lru_cache
from heapq import heappop, heappush
from pathlib import Path
from xml.etree import ElementTree

from .condense import strongly_connected, topo_order
from .model import A1_RE, Graph, a1, col_number, range_members, split_ref
from .project import CellGraph
from . import active_graph as compact_graph


def _split(cid: str):
    parsed = split_ref(cid)
    return (parsed[0], parsed[1], parsed[2]) if parsed else (cid, 0, 0)

EPOCH = datetime(1899, 12, 30)
EXCEL_EPOCH = datetime(1899, 12, 31)
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")
CRITERIA_RE = re.compile(r"^\s*(>=|<=|<>|>|<|=)?\s*(.*)$")
A1_RANGE_RE = re.compile(
    r"(?:(?:'(?P<quoted>(?:[^']|'')+)'|(?P<bare>[A-Za-z0-9_ .&-]+))!)?"
    r"\$?(?P<col0>[A-Za-z]{1,3})\$?(?P<row0>\d+)"
    r"\s*:\s*\$?(?P<col1>[A-Za-z]{1,3})\$?(?P<row1>\d+)"
)
A1_REFERENCE_RE = re.compile(
    r"^\s*(?:(?:'(?P<quoted>(?:[^']|'')+)'|(?P<bare>[A-Za-z0-9_ .&-]+))!)?"
    r"\$?(?P<col0>[A-Za-z]{1,3})\$?(?P<row0>\d+)"
    r"(?:\s*:\s*\$?(?P<col1>[A-Za-z]{1,3})\$?(?P<row1>\d+))?\s*$"
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
MAX_WORKBOOK_ITERATIONS = 10_000
MAX_ITERATION_DELTA = 1.0
CONVERGENCE = 1e-12

# Only operations whose active source walk is a pure union of their argument
# sources belong here. Unknown and newly implemented operations deliberately
# default to uncacheable. Dynamic selectors and references (IF/IFS/CHOOSE,
# IFERROR, SUMIF/SUMIFS, lookups, OFFSET, INDIRECT) are intentionally absent.
SOURCE_WALK_PURE_OPS = frozenset({
    "+", "-", "*", "/", "^", "&", "=", "<>", ">", "<", ">=", "<=",
    "u-", "u+", "%", "isect", " ",
    "ABS", "AND", "AVERAGE", "AVERAGEIF", "AVERAGEIFS", "CONCAT",
    "CONCATENATE", "COUNT", "COUNTA", "COUNTBLANK", "COUNTIF", "COUNTIFS",
    "CUMIPMT", "CUMPRINC", "DATE", "DATEDIF", "DAY", "DAYS", "DAYS360",
    "EDATE", "EOMONTH", "EXP", "FIXED", "FV", "GCD", "INT", "IPMT",
    "IRR", "ISBLANK", "ISERR", "ISERROR", "ISNA", "ISNUMBER", "LARGE",
    "LEFT", "LEN", "LN", "LOWER", "MAX", "MAXA", "MAXIFS", "MEDIAN",
    "MID", "MIN", "MINA", "MINIFS", "MIRR", "MOD", "MONTH", "MROUND",
    "N", "NETWORKDAYS", "NORM.DIST", "NORM.INV", "NORM.S.DIST",
    "NORM.S.INV", "NOW", "NPER", "NPV", "NUMBERVALUE",
    "PMT", "POWER", "PPMT", "PRODUCT", "PROPER", "PV", "QUARTILE",
    "RANK", "RANK.EQ", "RATE", "RIGHT", "ROUNDDOWN", "ROUNDUP", "ROUND",
    "ROWS", "RRI", "RSQ", "SEARCH", "SLOPE", "SMALL", "SUBSTITUTE",
    "SUBTOTAL", "SUM", "SUMPRODUCT", "TEXT", "TEXTJOIN", "TODAY", "TRANSPOSE",
    "TRIM", "UPPER", "VSTACK", "WEEKDAY", "WORKDAY", "XIRR", "XNPV",
    "YEAR", "YEARFRAC",
})


def _bare_operation(operation: str) -> str:
    bare = str(operation).upper()
    for prefix in ("_XLFN.", "_XLWS."):
        if bare.startswith(prefix):
            bare = bare[len(prefix):]
    return bare


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

    def __init__(self, values, rows=1, cols=None, refs=None):
        super().__init__(values)
        self.rows = max(int(rows or 1), 1)
        self.cols = max(int(cols or len(values) or 1), 1)
        self.refs = tuple(refs) if refs is not None and len(refs) == len(self) else None


NATIVE_ERRORS = frozenset({"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A"})


def is_bad(value) -> bool:
    return isinstance(value, (Unresolved, ExcelError))


@lru_cache(maxsize=8192)
def _datetime_to_serial(stamp: datetime) -> float:
    """Convert a real datetime to Excel's 1900-system serial."""
    delta = stamp - EXCEL_EPOCH
    serial = delta.days + delta.seconds / 86400.0 + delta.microseconds / 86400000000.0
    if stamp >= datetime(1900, 3, 1):
        serial += 1.0
    return serial


@lru_cache(maxsize=8192)
def _excel_date_parts(serial: int) -> tuple[int, int, int]:
    """Return Excel Y/M/D fields, including its fictitious serial-60 date."""
    serial = int(serial)
    if serial == 60:
        return 1900, 2, 29
    offset = serial if serial < 60 else serial - 1
    stamp = EXCEL_EPOCH + timedelta(days=offset)
    return stamp.year, stamp.month, stamp.day


def _excel_month_days(year: int, month: int) -> int:
    return 29 if (year, month) == (1900, 2) else monthrange(year, month)[1]


def _excel_date_serial(year: int, month: int, day: int) -> float:
    """Excel DATE normalization expressed in the same serial coordinate system."""
    absolute_month = year * 12 + month - 1
    normalized_year, month0 = divmod(absolute_month, 12)
    try:
        first = datetime(normalized_year, month0 + 1, 1)
    except (OverflowError, ValueError):
        raise ValueError("date outside supported range")
    return _datetime_to_serial(first) + day - 1


@lru_cache(maxsize=8192)
def to_serial(text: str):
    try:
        stamp = datetime.fromisoformat(text.replace("T", " "))
    except ValueError:
        return None
    return _datetime_to_serial(stamp)


def literal(raw: str):
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        return raw
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


def primitive_value(node):
    """Decode supplied primitive cells using the AST's original Python type."""
    if node.value_type == "str":
        return node.value
    if node.value_type == "bool":
        raw = str(node.value).strip().lower()
        if raw in {"true", "1"}:
            return True
        if raw in {"false", "0"}:
            return False
        return Unresolved("invalid-boolean-primitive")
    return literal(node.value)


def num(value, default=0.0):
    # A proven one-cell result (e.g. INDEX(row=0) of a one-row range)
    # has the same numeric value as its only cell. Never guess an implicit
    # intersection for a general list or a multi-cell/malformed range.
    if isinstance(value, RangeValues) and value.rows == value.cols == len(value) == 1:
        value = value[0]
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


def _date_number(value):
    """Supported date/offset coercion without turning invalid text into zero."""
    if isinstance(value, RangeValues) and value.rows == value.cols == len(value) == 1:
        value = value[0]
    if is_bad(value):
        return value
    serial = num(value, math.nan) if value is not None else 0.0
    if math.isnan(serial):
        return ExcelError("#VALUE!")
    if not math.isfinite(serial):
        return ExcelError("#NUM!")
    return serial


def _eomonth(start, months):
    year, month, _ = _excel_date_parts(int(num(start)))
    absolute = year * 12 + month - 1 + int(num(months))
    target_year, month0 = divmod(absolute, 12)
    target_month = month0 + 1
    return _excel_date_serial(
        target_year, target_month, _excel_month_days(target_year, target_month)
    )


def _edate(start, months):
    base_year, base_month, base_day = _excel_date_parts(int(num(start)))
    absolute = base_year * 12 + base_month - 1 + int(num(months))
    year, month0 = divmod(absolute, 12)
    month = month0 + 1
    day = min(base_day, _excel_month_days(year, month))
    return _excel_date_serial(year, month, day)


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
    # With no periodic payment the equation has a closed-form solution.
    # Newton from Excel's default guess can miss roots extremely close to -1,
    # while the broad fallback scan intentionally stops at -0.9999.
    if pmt == 0 and pv != 0:
        ratio = -fv / pv
        if ratio > 0:
            result = ratio ** (1.0 / nper) - 1.0
            _RATE_CACHE[key] = result
            return result
        if fv == 0:
            # Excel's Newton solver returns a near--1 approximation even
            # though the zero-future-value equation has no finite root.
            rate = guess
            for _ in range(100):
                growth = (1.0 + rate) ** nper
                derivative = nper * (1.0 + rate) ** (nper - 1.0)
                if derivative == 0:
                    break
                nxt = rate - growth / derivative
                if nxt <= -1.0:
                    nxt = (rate - 1.0) / 2.0
                if abs(nxt - rate) < 1e-7:
                    rate = nxt
                    break
                rate = nxt
            _RATE_CACHE[key] = rate
            return rate
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


def _unary_plus(value):
    """Excel's reference-style unary plus keeps nonnumeric text as text."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return num(value)


UNARY = {
    "u-": lambda a: -num(a),
    "u+": _unary_plus,
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


def _binary_value(op, left, right):
    """Apply one scalar operator without swallowing spreadsheet errors."""
    for error_type in (Unresolved, ExcelError):
        if isinstance(left, error_type):
            return left
        if isinstance(right, error_type):
            return right
    if op in {"+", "-", "*", "/", "^"}:
        # Formula-produced empty strings are text, unlike an empty cell None.
        # Keep numeric text coercion, but never silently turn other text into 0.
        for value in (left, right):
            if isinstance(value, str) and math.isnan(num(value, math.nan)):
                return ExcelError("#VALUE!")
    return BINARY[op](left, right)


def _binary(op, left, right):
    """Excel array arithmetic: scalar broadcast or exact-shape elementwise."""
    left_range = isinstance(left, RangeValues)
    right_range = isinstance(right, RangeValues)
    if not left_range and not right_range:
        return _binary_value(op, left, right)
    if left_range and right_range:
        if (
            (left.rows, left.cols) != (right.rows, right.cols)
            or len(left) != len(right)
        ):
            return ExcelError("#VALUE!")
        values = [
            _binary_value(op, left_value, right_value)
            for left_value, right_value in zip(left, right)
        ]
        return RangeValues(values, left.rows, left.cols)
    source = left if left_range else right
    scalar = right if left_range else left
    values = [
        _binary_value(op, value, scalar) if left_range
        else _binary_value(op, scalar, value)
        for value in source
    ]
    return RangeValues(values, source.rows, source.cols)


def _intersection(left, right):
    """Return the overlapping cells of two reference-valued ranges."""
    for error_type in (Unresolved, ExcelError):
        if isinstance(left, error_type):
            return left
        if isinstance(right, error_type):
            return right
    if not isinstance(left, RangeValues) or not isinstance(right, RangeValues):
        return ExcelError("#NULL!")
    if left.refs is None or right.refs is None:
        return ExcelError("#NULL!")
    right_refs = set(right.refs)
    selected = [
        (value, ref) for value, ref in zip(left, left.refs) if ref in right_refs
    ]
    if not selected:
        return ExcelError("#NULL!")
    coordinates = [split_ref(ref) for _, ref in selected]
    valid = [coordinate for coordinate in coordinates if coordinate is not None]
    rows = len({(sheet, row) for sheet, row, _ in valid}) or 1
    cols = len({(sheet, col) for sheet, _, col in valid}) or len(selected)
    return RangeValues(
        [value for value, _ in selected],
        rows,
        cols,
        refs=[ref for _, ref in selected],
    )


@dataclass
class EvalResult:
    values: dict
    unresolved: dict
    iterated: list
    coverage: dict
    # target cell -> source cells actually read on the final evaluation pass,
    # including dynamic references (OFFSET) invisible to the static graph.
    runtime_radj: dict = dataclass_field(default_factory=dict)
    # Address-only origins used to resolve dynamic references.  These are
    # deliberately separate from value precedents so OFFSET(A1, ...) does not
    # make A1 a proof input merely because its coordinate anchors the address.
    runtime_address_radj: dict = dataclass_field(default_factory=dict)
    # Dynamic/selector targets chosen on the final pass, sorted for stable
    # diagnostics and proof-closure signatures.
    resolved_targets: dict = dataclass_field(default_factory=dict)
    # The same targets keyed by the exact AST operation that resolved them.
    # Formula-level targets above are retained for dependency diagnostics, but
    # cannot prove two dynamic calls in one formula independently.
    resolved_operation_targets: dict = dataclass_field(default_factory=dict)
    # cell id served by the workbook cached-value oracle -> the formula cells
    # that consumed it. Every entry is a value the evaluator could not derive
    # and copied from the golden workbook instead.
    oracle_reads: dict = dataclass_field(default_factory=dict)
    oracle_accesses: dict = dataclass_field(default_factory=dict)
    # Every grid read attempted by a formula, including reads which produced no
    # value.  This is diagnostic provenance, not a new frontier-closure policy.
    read_attempts: dict = dataclass_field(default_factory=dict)
    scalar_primitive_reads: dict = dataclass_field(default_factory=dict)
    missing_reads: dict = dataclass_field(default_factory=dict)
    seed_provenance: dict = dataclass_field(default_factory=dict)
    seeded_cells: set = dataclass_field(default_factory=set)
    # Complete formula/input scope authorized for this run. ``values`` also
    # contains cached initialization state outside a strict proof scope, so it
    # cannot by itself establish that a cell was executed.
    evaluated_cells: set = dataclass_field(default_factory=set)
    cycle_membership: dict = dataclass_field(default_factory=dict)
    # Verification-only state.  These are intentionally not emitted directly:
    # diagnostics carry bounded member samples while this complete mapping lets
    # the post-proof verifier reason about every member without truncation.
    cycle_groups: dict = dataclass_field(default_factory=dict)
    endpoint_equation_checker: object = None
    strict_proof: bool = False
    # Process-local performance data. It is returned to the caller but is not
    # part of proof, coverage, verification, or published generation identity.
    benchmark: dict = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class CalculationMetadata:
    """Calculation settings obtained without opening the expected-value cache."""

    available: bool
    iterate: bool | None = None
    iterate_count: int | None = None
    iterate_delta: float | None = None
    source: str = ""
    reason: str = ""
    iterate_origin: str = "unknown"
    iterate_count_origin: str = "unknown"
    iterate_delta_origin: str = "unknown"
    raw_calc_pr: dict = dataclass_field(default_factory=dict)
    calc_mode: str | None = None
    full_calc_on_load: bool | None = None
    force_full_calc: bool | None = None
    calc_mode_origin: str = "unknown"
    full_calc_on_load_origin: str = "unknown"
    force_full_calc_origin: str = "unknown"

    @property
    def calcMode(self):
        """OOXML spelling retained for metadata consumers."""
        return self.calc_mode

    @property
    def fullCalcOnLoad(self):
        """OOXML spelling retained for metadata consumers."""
        return self.full_calc_on_load

    @property
    def forceFullCalc(self):
        """OOXML spelling retained for metadata consumers."""
        return self.force_full_calc


class Evaluator:
    def __init__(
        self,
        graph: Graph,
        cg: CellGraph,
        oracle=None,
        *,
        strict_proof: bool = False,
        calculation=None,
        proof_outputs=None,
        run_probes: bool = True,
        proof_scope=None,
        optimize: bool = True,
        clock=None,
    ):
        self.graph = graph
        self.cg = cg
        self.oracle = oracle
        self.strict_proof = strict_proof
        self.calculation = calculation if calculation is not None else oracle
        self.proof_outputs = set(proof_outputs) if proof_outputs is not None else None
        self.run_probes = run_probes
        self.proof_scope = set(proof_scope) if proof_scope is not None else None
        self.optimize = bool(optimize)
        self.clock = clock if clock is not None else datetime.now
        self.values: dict = {}
        self.unresolved: dict = {}
        self.unknown_ops: dict = defaultdict(int)
        self._arg_cache: dict = {}
        self._source_pool = compact_graph.Pool()
        self._span_shape_cache: dict[tuple[str, ...], tuple[int, int]] = {}
        self._span_shape_cache_members = 0
        self._subtotal_provenance: dict[str, bool] = {}
        self.oracle_reads = compact_graph.ReadRelation(self._source_pool)
        self.oracle_accesses = compact_graph.ReadRelation(self._source_pool)
        self.read_attempts = compact_graph.ReadRelation(self._source_pool)
        self.scalar_primitive_reads: dict = {}
        self.missing_reads = compact_graph.ReadRelation(self._source_pool)
        self.runtime_address_radj: dict = {}
        self._resolved_targets: dict = {}
        self._resolved_operation_targets: dict = {}
        self._selective_sources: dict = {}
        self._current_cell = None
        self._proof_endpoint_values: dict = {}
        self._proof_active_radj: dict = {}
        self._proof_resolved_targets: dict = {}
        self._proof_resolved_operation_targets: dict = {}
        self._static_source_cache: dict[str, frozenset[str]] = {}
        self._source_walk_purity_cache: dict[str, bool] = {}
        self._component_plan_cache: dict[tuple, tuple] = {}
        self._benchmark = {
            "active_graph": {
                "calls": 0,
                "seconds": 0.0,
                "cells_scanned": 0,
                "reused_initial_graphs": 0,
                "static_source_cache_hits": 0,
                "static_source_cache_misses": 0,
            },
            "component_plan": {
                "calls": 0,
                "seconds": 0.0,
                "cache_hits": 0,
                "cache_misses": 0,
            },
            "acyclic_evaluation": {"calls": 0, "seconds": 0.0},
            "run_seconds": 0.0,
            "optimized": self.optimize,
        }

    # -- AST walking ------------------------------------------------------
    def _legacy_average_arithmetic(self, node):
        """Narrow saved-IIE context; intrinsic array functions never opt in.

        A normal saved formula has IIE semantics. Modern Excel saves a formula
        whose AE behavior can differ as an array, even for a one-cell result.
        Source acceptance still requires its normal trusted proof; this helper
        does not infer dialect from cached values or workbook age.
        """
        owner = self.graph.nodes.get(node.owner)
        if owner is None or owner.kind != "formula":
            return False
        if any(getattr(owner, field, False) for field in
               ("array_formula", "array_ref", "array_span", "array_anchor")):
            return False
        root_id = self.graph.root_of(owner.id)
        root = self.graph.nodes.get(root_id)
        if root is None or root.op != "AVERAGE" or root.op_kind != "func":
            return False
        # Do not cross an intrinsic array-evaluation island or an array
        # constant. This first implementation proves only a simple arithmetic
        # tree of cell/range references and scalar constants below AVERAGE.
        pending = [edge.source for edge in self.graph.in_edges.get(root_id, ())]
        checked = set()
        while pending:
            source_id = pending.pop()
            if source_id in checked:
                continue
            checked.add(source_id)
            source = self.graph.nodes.get(source_id)
            if source is None:
                return False
            if source.is_cell or source.kind in {"const", "range"}:
                continue
            if not ((source.op_kind == "infix" and source.op in {"+", "-", "*", "/", "^"})
                    or (source.op_kind in {"prefix", "postfix"} and source.op in UNARY)):
                return False
            pending.extend(edge.source for edge in self.graph.in_edges.get(source_id, ()))
        current = node
        seen = set()
        while current.id != root_id:
            if current.id in seen or current.owner != owner.id:
                return False
            seen.add(current.id)
            if not ((current.op_kind == "infix" and current.op in {"+", "-", "*", "/", "^"})
                    or (current.op_kind in {"prefix", "postfix"} and current.op in UNARY)):
                return False
            parents = self.graph.out_edges.get(current.id, ())
            if len(parents) != 1:
                return False
            current = self.graph.nodes.get(parents[0].target)
            if current is None:
                return False
        return bool(seen)

    def _legacy_static_operand(self, node, index):
        """Return (handled, selected ref/error) without reading range values."""
        kind, payload = self._arg_specs(node)[index]
        if kind == "span":
            refs = list(payload)
        elif kind == "scalar":
            source = self.graph.nodes.get(payload)
            if source is None or source.kind != "range":
                return False, None
            if source.range_truncated:
                return True, Unresolved("whole-column-range-exceeds-bound")
            refs = range_members(source.sheet, source.coordinate or "")
        else:
            return False, None
        coordinates = [split_ref(ref) for ref in refs]
        if not refs or any(item is None for item in coordinates):
            return True, Unresolved("implicit-intersection-invalid-reference")
        sheets = {item[0] for item in coordinates}
        rows = {item[1] for item in coordinates}
        cols = {item[2] for item in coordinates}
        if len(sheets) != 1 or (len(rows) != 1 and len(cols) != 1):
            return True, Unresolved("implicit-intersection-shape-unproved")
        owner = self.graph.nodes[node.owner]
        if owner.row is None or owner.col is None:
            return True, Unresolved("implicit-intersection-owner-unproved")
        selected = [ref for ref, (_, row, col) in zip(refs, coordinates)
                    if len(refs) == 1 or (len(rows) == 1 and col == owner.col)
                    or (len(cols) == 1 and row == owner.row)]
        if len(selected) != 1:
            return True, ExcelError("#VALUE!")
        return True, selected[0]

    def _legacy_arithmetic_args(self, node):
        values = []
        for index, spec in enumerate(self._arg_specs(node)):
            handled, selected = self._legacy_static_operand(node, index)
            values.append(self._read(selected) if isinstance(selected, str)
                          else selected if handled else self._arg_value(spec))
        return values

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
            values = self._read_span(payload)
            # Only coordinate metadata is reusable. Read each value above on
            # every call so changed inputs and proof read accounting stay live.
            key = tuple(payload) if self.optimize else None
            shape = self._span_shape_cache.get(key) if key is not None else None
            if shape is None:
                coordinates = [_split(cid) for cid in payload]
                shape = (len({row for _, row, _ in coordinates}),
                         len({col for _, _, col in coordinates}))
                if (
                    key is not None
                    and len(self._span_shape_cache) < 32768
                    and self._span_shape_cache_members + len(key) <= 1000000
                ):
                    self._span_shape_cache[key] = shape
                    self._span_shape_cache_members += len(key)
            return RangeValues(values, *shape, refs=payload)
        return [self._eval_node(src) for src in payload]

    def _arg(self, node, index):
        specs = self._arg_specs(node)
        return self._arg_value(specs[index]) if index < len(specs) else None

    def _args(self, node):
        return [self._arg_value(spec) for spec in self._arg_specs(node)]

    def _reference_arg(self, node, index, *, read=True):
        """Read an argument while retaining any grid coordinates it represents."""
        specs = self._arg_specs(node)
        if index >= len(specs):
            return RangeValues([], 1, 1)
        spec = specs[index]
        kind, payload = spec
        if not read:
            if kind == "span":
                coordinates = [split_ref(ref) for ref in payload]
                rows = len({item[1] for item in coordinates if item is not None}) or 1
                cols = len({item[2] for item in coordinates if item is not None}) or 1
                return RangeValues([None] * len(payload), rows, cols, refs=payload)
            if kind == "scalar":
                source = self.graph.nodes.get(payload)
                if source is not None and source.kind == "range":
                    refs = range_members(source.sheet, source.coordinate or "")
                    return RangeValues(
                        [None] * len(refs),
                        len({split_ref(ref)[1] for ref in refs}) or 1,
                        len({split_ref(ref)[2] for ref in refs}) or 1,
                        refs=refs,
                    )
                if source is not None and source.is_cell:
                    return RangeValues([None], 1, 1, refs=[source.id])
            if kind == "many":
                refs = [
                    source_id for source_id in payload
                    if self.graph.nodes.get(source_id) is not None
                    and self.graph.nodes[source_id].is_cell
                ]
                if len(refs) == len(payload):
                    coordinates = [split_ref(ref) for ref in refs]
                    rows = len({item[1] for item in coordinates if item is not None}) or 1
                    cols = len({item[2] for item in coordinates if item is not None}) or 1
                    return RangeValues([None] * len(refs), rows, cols, refs=refs)
        value = self._arg_value(spec)
        if isinstance(value, RangeValues):
            return value
        if kind == "scalar":
            source = self.graph.nodes.get(payload)
            if source is not None and source.is_cell:
                return RangeValues([value], 1, 1, refs=[source.id])
        if kind == "many":
            refs = []
            for source_id in payload:
                source = self.graph.nodes.get(source_id)
                if source is None or not source.is_cell:
                    refs = []
                    break
                refs.append(source.id)
            if refs:
                coordinates = [split_ref(ref) for ref in refs]
                rows = len({item[1] for item in coordinates if item is not None}) or 1
                cols = len({item[2] for item in coordinates if item is not None}) or 1
                return RangeValues(list(flatten([value])), rows, cols, refs=refs)
        values = list(flatten([value]))
        return RangeValues(values, 1, max(len(values), 1))

    @staticmethod
    def _reference_anchor(source):
        parsed = [item for ref in (source.refs or ()) if (item := split_ref(ref))]
        if not parsed:
            return None
        sheet = parsed[0][0]
        same_sheet = [item for item in parsed if item[0] == sheet]
        return sheet, min(item[1] for item in same_sheet), min(item[2] for item in same_sheet)

    def _aligned_reference(self, source, rows, cols, *, read=True):
        """Resize a reference from its absolute top-left cell, as Excel does."""
        rows, cols = max(int(rows), 1), max(int(cols), 1)
        anchor = self._reference_anchor(source)
        if anchor is None:
            values = list(source)
            size = rows * cols
            return RangeValues(
                values[:size] + [None] * max(size - len(values), 0), rows, cols
            )
        sheet, top, left = anchor
        cached = dict(zip(source.refs or (), source))
        refs = [
            a1(sheet, top + row, left + col)
            for row in range(rows)
            for col in range(cols)
        ]
        values = [
            cached[ref] if ref in cached else (self._read(ref) if read else None)
            for ref in refs
        ]
        return RangeValues(values, rows, cols, refs=refs)

    @staticmethod
    def _bad_in(values):
        return next((value for value in flatten(values) if is_bad(value)), None)

    def _conditional_layout(self, node):
        """Return condition values and aligned summand coordinates."""
        is_sumifs = node.op == "SUMIFS"
        target_index = 0 if is_sumifs else 2
        first_range = 1 if is_sumifs else 0
        criterion_pairs = []

        if is_sumifs:
            target = self._reference_arg(node, 0, read=False)
            rows, cols = target.rows, target.cols
        else:
            first = self._reference_arg(node, 0)
            rows, cols = first.rows, first.cols
            target = self._reference_arg(node, target_index, read=False) \
                if target_index < len(self._arg_specs(node)) else first

        for index in range(first_range, len(self._arg_specs(node)) - 1, 2):
            raw_source = self._reference_arg(node, index)
            if is_sumifs and (
                raw_source.rows != rows
                or raw_source.cols != cols
                or len(raw_source) != rows * cols
            ):
                return target, rows, cols, criterion_pairs, [], ExcelError("#VALUE!")
            source = self._aligned_reference(raw_source, rows, cols)
            criterion_pairs.append((source, self._arg(node, index + 1), index + 1))

        if not is_sumifs:
            criterion_pairs = [
                (self._aligned_reference(first, rows, cols), self._arg(node, 1), 1)
            ]
        bad = self._bad_in([
            item
            for source, criterion, _ in criterion_pairs
            for item in (source, criterion)
        ])
        if bad is not None:
            return target, rows, cols, criterion_pairs, [], bad
        matches = [
            all(_matches(source[offset], criterion) for source, criterion, _ in criterion_pairs)
            for offset in range(rows * cols)
        ]
        return target, rows, cols, criterion_pairs, matches, None

    def _conditional_sum(self, node):
        target, rows, cols, _, matches, bad = self._conditional_layout(node)
        if bad is not None:
            return bad
        anchor = self._reference_anchor(target)
        cached = dict(zip(target.refs or (), target))
        values = list(target)
        selected = []
        for offset, matched in enumerate(matches):
            if not matched:
                continue
            row, col = divmod(offset, cols)
            if anchor is not None:
                sheet, top, left = anchor
                ref = a1(sheet, top + row, left + col)
                selected.append(
                    self._read(ref) if self.graph is not None else cached.get(ref)
                )
            elif offset < len(values):
                selected.append(values[offset])
        bad = self._bad_in(selected)
        if bad is not None:
            return bad
        nums = [num(value) for value in selected]
        return snap(sum(nums), *nums)

    def _conditional_sources(self, node):
        target, rows, cols, criterion_pairs, matches, _ = self._conditional_layout(node)
        sources = set()
        for source, _, criterion_index in criterion_pairs:
            sources.update(source.refs or ())
            spec = self._arg_specs(node)[criterion_index]
            sources.update(self._active_spec_sources(spec))

        anchor = self._reference_anchor(target)
        if anchor is not None:
            sheet, top, left = anchor
            for offset, matched in enumerate(matches):
                if matched:
                    row, col = divmod(offset, cols)
                    sources.add(a1(sheet, top + row, left + col))
        elif target.refs:
            sources.update(
                ref for ref, matched in zip(target.refs, matches) if matched
            )
        return sources

    def _active_spec_sources(self, spec, seen=None):
        kind, payload = spec
        if kind == "span":
            return self._source_pool.sources(payload)
        if kind == "scalar":
            return self._active_node_sources(payload, seen)
        if kind == "many":
            sources = compact_graph.Builder(self._source_pool)
            for source in payload:
                sources.update(self._active_node_sources(source, seen))
            return sources.freeze()
        return set()

    def _has_subtotal_provenance(self, node_id, seen=None):
        """Whether this cell's own formula contains SUBTOTAL or AGGREGATE."""
        cached = self._subtotal_provenance.get(node_id)
        if cached is not None:
            return cached
        seen = set() if seen is None else seen
        if node_id in seen:
            return False
        seen.add(node_id)
        node = self.graph.nodes.get(node_id)
        if node is None:
            return False
        if node.is_cell:
            root = self.graph.root_of(node.id)
            root_node = self.graph.nodes.get(root)
            # An ordinary link to a subtotal is a supplied reference here;
            # it is not a nested SUBTOTAL expression in this cell's formula.
            result = (root_node is not None and not root_node.is_cell
                      and self._has_subtotal_provenance(root, seen))
        elif node.kind == "op":
            bare = node.op.upper()
            for prefix in ("_XLFN.", "_XLWS."):
                if bare.startswith(prefix):
                    bare = bare[len(prefix):]
            result = bare in {"SUBTOTAL", "AGGREGATE"} or any(
                self.graph.nodes.get(edge.source) is not None
                and not self.graph.nodes[edge.source].is_cell
                and self._has_subtotal_provenance(edge.source, seen)
                for edge in self.graph.in_edges.get(node.id, ())
            )
        else:
            result = False
        self._subtotal_provenance[node_id] = result
        return result

    def _subtotal(self, node):
        code_value = self._arg(node, 0)
        if is_bad(code_value):
            return code_value
        code = int(num(code_value)) % 100
        kept = []
        for index in range(1, len(self._arg_specs(node))):
            source = self._reference_arg(node, index, read=False)
            if source.refs:
                kept.extend(
                    self._read(ref) for ref in source.refs
                    if not self._has_subtotal_provenance(ref)
                )
            else:
                kept.extend(source)
        bad = self._bad_in(kept)
        if bad is not None:
            return bad
        if code == 1:
            return self._fn_average(kept)
        if code == 2:
            return self._fn_count(kept)
        if code == 3:
            return float(sum(value is not None and value != "" for value in kept))
        if code == 4:
            return self._fn_max(kept)
        if code == 5:
            return self._fn_min(kept)
        if code == 6:
            return self._fn_product(kept)
        if code == 9:
            return self._fn_sum(kept)
        return Unresolved(f"unsupported:SUBTOTAL({code})")

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
            coordinate = span.split("!")[-1].replace("$", "")
            members = self._source_pool.span((sheet, coordinate), lambda: range_members(sheet, coordinate))
            if members:
                return ("span", members)
        if len(group) == 1:
            return ("scalar", group[0].source)
        return ("many", [e.source for e in group])

    def _record_read(self, name, cid, journal):
        if journal is None:
            getattr(self, name).setdefault(cid, set()).add(self._current_cell)
        else:
            journal.setdefault(name, set()).add(cid)

    def _read_span(self, members):
        journal = {}
        consumer = self._current_cell
        try:
            return [self._read(cid, _journal=journal) for cid in members]
        finally:
            for name, visited in journal.items():
                getattr(self, name).add_group(visited, consumer)

    def _read(self, cid: str, *, _journal=None):
        self._record_read("read_attempts", cid, _journal)
        if cid in self.values:
            return self.values[cid]
        if cid in self.graph.nodes:
            return Unresolved("range-uncomputed")
        if self.strict_proof:
            # Proof mode may record the absence, but it cannot ask the golden
            # workbook for a saved answer.  Comparison cache access happens
            # only after ``run`` has returned.
            self._record_read("missing_reads", cid, _journal)
            return None
        if self.oracle is not None:
            self._record_read("oracle_accesses", cid, _journal)
            raw = self.oracle(*_split(cid))
            value = literal(raw) if isinstance(raw, str) else raw
            if value is not None:
                # A populated cell the graph never recorded: its value comes
                # straight from the golden workbook's cache, not from any
                # rebuild. Record who consumed it so verification can refuse
                # to count outputs fed this way.
                self._record_read("oracle_reads", cid, _journal)
            return value
        return None

    def _eval_node(self, node_id):
        node = self.graph.nodes.get(node_id)
        if node is None:
            return Unresolved("missing-node")
        if node.kind == "const":
            if node.op == "error":
                value = node.value if node.value != "" else node.expr
                return ExcelError(value) if value in NATIVE_ERRORS else Unresolved("unknown-error-literal")
            if node.op == "text":
                return node.value
            if node.op == "logical":
                raw = str(node.value).upper()
                return raw == "TRUE" if raw in {"TRUE", "FALSE"} else Unresolved("invalid-logical-constant")
            return literal(node.value if node.value != "" else node.expr)
        if node.kind == "range":
            return self._expand_range(node)
        if node.kind == "name":
            # Older/current graphs may misparse an unsupported reference
            # constructor as a name between whitespace intersections. A bare
            # colon cannot be a defined name; IFERROR must not hide this gap.
            if node.label == ":":
                return Unresolved("unsupported-reference-constructor")
            return ExcelError("#NAME?")
        if node.kind == "external":
            return Unresolved("external-reference")
        if node.is_cell:
            # Preserve primitive scalar reads during native iteration, not
            # merely the dependency branch selected at the final endpoint.
            if node.kind in {"input", "label"} and self._current_cell is not None:
                self.read_attempts.setdefault(node_id, set()).add(self._current_cell)
                self.scalar_primitive_reads.setdefault(node_id, set()).add(self._current_cell)
            if node_id in self.values:
                return self.values[node_id]
            return Unresolved("uncomputed-precedent")
        return self._apply(node)

    def _expand_range(self, node):
        """A range node the parser kept whole; read its cells one by one."""
        if getattr(node, "range_truncated", False):
            return Unresolved("whole-column-range-exceeds-bound")
        members = self._source_pool.span((node.sheet, node.coordinate or ""), lambda: range_members(node.sheet, node.coordinate or ""))
        if not members:
            return Unresolved("opaque-range")
        coordinates = [_split(cid) for cid in members]
        rows = len({row for _, row, _ in coordinates})
        cols = len({col for _, _, col in coordinates})
        return RangeValues(
            self._read_span(members), rows, cols, refs=members
        )

    def _apply(self, node):
        op = node.op
        if op == ":":
            return self._reference_range(node)
        if op == "OFFSET":
            return self._offset(node)
        if op == "SUBTOTAL":
            return self._subtotal(node)
        if op in ("SUMIF", "SUMIFS"):
            return self._conditional_sum(node)
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
        if op in {"HLOOKUP", "VLOOKUP"}:
            plan = self._lookup_reference_plan(node)
            if plan is not None:
                lookup_args, sources, selected = plan
                if selected is not None:
                    table = lookup_args[1]
                    table[table.refs.index(selected)] = self._read(selected)
                self._selective_sources[node.id] = sources
                handler = self._fn_hlookup if op == "HLOOKUP" else self._fn_vlookup
                return handler(lookup_args)
        args = (self._legacy_arithmetic_args(node)
                if self._legacy_average_arithmetic(node) else self._args(node))
        if node.op_kind == "infix":
            if op in BINARY:
                return _binary(op, args[0], args[1]) if len(args) > 1 \
                    else Unresolved("arity")
            if op in ("isect", " "):
                return _intersection(args[0], args[1]) if len(args) > 1 \
                    else Unresolved("arity")
        selective = (
            "VLOOKUP", "HLOOKUP", "XLOOKUP", "_XLFN.XLOOKUP",
            "LOOKUP", "MATCH", "INDEX",
        )
        if op in selective:
            # Lookups tolerate bad values inside the searched range -- only the
            # matched row matters -- but a bad key/index/mode must poison the
            # result. Letting it fall through to num() would silently search
            # for 0 and fabricate a match Excel never produces.
            scalar_slots = {
                "VLOOKUP": (0, 2, 3), "HLOOKUP": (0, 2, 3),
                "XLOOKUP": (0,), "_XLFN.XLOOKUP": (0,),
                "LOOKUP": (0,),
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
            values = list(flatten(args))
            rows = (node.expr or "").count(";") + 1
            if not values or len(values) % rows:
                return Unresolved("array-constant-shape")
            return RangeValues(values, rows, len(values) // rows)
        if node.op_kind in ("prefix", "postfix") and op in UNARY:
            if not args:
                return Unresolved("arity")
            if isinstance(args[0], RangeValues):
                return RangeValues(
                    [UNARY[op](value) for value in args[0]],
                    args[0].rows,
                    args[0].cols,
                    refs=args[0].refs,
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
        value = handler(args)
        if op in selective:
            self._selective_sources[node.id] = self._selective_dependencies(
                node, args, value
            )
        return value

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
        # A workbook's "-" date sentinel must reach its surrounding IFERROR.
        serial = _date_number(args[0])
        if is_bad(serial):
            return serial
        try:
            return float(_excel_date_parts(int(serial))[0])
        except (OverflowError, ValueError):
            return ExcelError("#NUM!")

    def _fn_month(self, args):
        return float(_excel_date_parts(int(num(args[0])))[1])

    def _fn_day(self, args):
        return float(_excel_date_parts(int(num(args[0])))[2])

    def _fn_date(self, args):
        year = int(num(args[0]))
        month = int(num(args[1]))
        day = int(num(args[2]))
        if 0 <= year < 1900:
            year += 1900
        try:
            return float(_excel_date_serial(year, month, day))
        except (OverflowError, ValueError):
            return ExcelError("#NUM!")

    def _fn_eomonth(self, args):
        return self._date_month_shift(args, _eomonth)

    def _fn_edate(self, args):
        return self._date_month_shift(args, _edate)

    @staticmethod
    def _date_month_shift(args, operation):
        values = [_date_number(value) for value in args[:2]]
        for value in values:
            if is_bad(value):
                return value
        try:
            return float(operation(*values))
        except (OverflowError, ValueError):
            return ExcelError("#NUM!")

    def _fn_numbervalue(self, args):
        if not args:
            return ExcelError("#VALUE!")
        text = _text(args[0]).strip().replace("\u00a0", " ")
        decimal = _text(args[1]) if len(args) > 1 and args[1] is not None else "."
        group = _text(args[2]) if len(args) > 2 and args[2] is not None else ","
        if (
            len(decimal) > 1 or len(group) > 1
            or (decimal and group and decimal == group)
        ):
            return ExcelError("#VALUE!")
        percent = 0
        while text.endswith("%"):
            percent += 1
            text = text[:-1].rstrip()
        if "%" in text:
            return ExcelError("#VALUE!")
        text = text.replace(" ", "")
        if decimal:
            if text.count(decimal) > 1:
                return ExcelError("#VALUE!")
            integer, separator, fraction = text.partition(decimal)
        else:
            integer, separator, fraction = text, "", ""
        if group and group in fraction:
            return ExcelError("#VALUE!")
        if group:
            integer = integer.replace(group, "")
        normalized = integer + (("." + fraction) if separator else "")
        try:
            return float(normalized) / (100.0 ** percent)
        except ValueError:
            return ExcelError("#VALUE!")

    def _fn_rank(self, args):
        if len(args) < 2:
            return ExcelError("#N/A")
        number = args[0]
        if not isinstance(number, (int, float)) or isinstance(number, bool):
            return ExcelError("#VALUE!")
        values = numbers([args[1]])
        if not values:
            return ExcelError("#N/A")
        ascending = len(args) > 2 and num(args[2]) != 0
        if ascending:
            return float(1 + sum(value < float(number) for value in values))
        return float(1 + sum(value > float(number) for value in values))

    _fn_rank_eq = _fn_rank

    def _fn_days(self, args):
        return num(args[0]) - num(args[1])

    _fn__xlfn_days = _fn_days

    def _fn_today(self, args):
        return float(math.floor(_datetime_to_serial(self.clock())))

    def _fn_choose(self, args):
        idx = int(num(args[0]))
        return args[idx] if 1 <= idx < len(args) else ExcelError("#VALUE!")

    def _fn_index(self, args):
        source = args[0]
        pool = list(flatten([source]))
        if not isinstance(source, RangeValues):
            row = int(num(args[1])) if len(args) > 1 else 1
            return pool[row - 1] if 1 <= row <= len(pool) else ExcelError("#REF!")
        selector = int(num(args[1])) if len(args) > 1 else 1
        has_col = len(args) > 2 and args[2] is not None
        if not has_col and source.rows == 1:
            row, col = 1, selector
        else:
            row = selector
            col = int(num(args[2])) if has_col else 1
        if row == 0 and col == 0:
            return source
        if row == 0:
            if not 1 <= col <= source.cols:
                return ExcelError("#REF!")
            indices = list(range(col - 1, len(pool), source.cols))
            refs = [source.refs[index] for index in indices] \
                if source.refs is not None else None
            return RangeValues(
                [pool[index] for index in indices], len(indices), 1, refs=refs
            )
        if col == 0:
            if not 1 <= row <= source.rows:
                return ExcelError("#REF!")
            start = (row - 1) * source.cols
            indices = list(range(start, min(start + source.cols, len(pool))))
            refs = [source.refs[index] for index in indices] \
                if source.refs is not None else None
            return RangeValues(
                [pool[index] for index in indices], 1, source.cols, refs=refs
            )
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
        guess = num(args[1], 0.1) if len(args) > 1 else 0.1
        return _irr(numbers([args[0]]), guess)

    def _fn_xirr(self, args):
        # Blanks are kept as zeros on both sides rather than dropped. A blank date
        # is serial 0, which discounts its amount over a century -- negligible on
        # its own, but so is everything else once the other flows are discounted
        # that far, and Excel's answer depends on it.
        flows = list(flatten([args[0]]))
        dates = list(flatten([args[1]]))
        if not flows or len(flows) != len(dates):
            return ExcelError("#NUM!")
        pairs = [(num(v, 0.0), num(d, 0.0)) for v, d in zip(flows, dates)]
        guess = num(args[2], 0.1) if len(args) > 2 else 0.1
        return _xirr([p[0] for p in pairs], [p[1] for p in pairs], guess)

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

    @staticmethod
    def _elementary_number(value):
        """Numeric scalar coercion without silently turning invalid text into zero."""
        if isinstance(value, RangeValues):
            if value.rows != 1 or value.cols != 1 or len(value) != 1:
                return Unresolved("elementary-function-array-shape")
            value = value[0]
        if is_bad(value):
            return value
        if isinstance(value, (list, tuple)):
            return Unresolved("elementary-function-array-shape")
        if value is None:
            return 0.0
        if not isinstance(value, (str, int, float, bool)):
            return ExcelError("#VALUE!")
        try:
            result = float(value)
        except (TypeError, ValueError):
            return ExcelError("#VALUE!")
        except OverflowError:
            return ExcelError("#NUM!")
        return result if math.isfinite(result) else ExcelError("#NUM!")

    def _fn_sqrt(self, args):
        if len(args) != 1:
            return Unresolved("sqrt-arity")
        value = self._elementary_number(args[0])
        if is_bad(value):
            return value
        return math.sqrt(value) if value >= 0 else ExcelError("#NUM!")

    def _fn_sln(self, args):
        if len(args) != 3:
            return Unresolved("sln-arity")
        values = [self._elementary_number(value) for value in args]
        bad = next((value for value in values if is_bad(value)), None)
        if bad is not None:
            return bad
        cost, salvage, life = values
        if life == 0:
            return ExcelError("#DIV/0!")
        result = (cost - salvage) / life
        return result if math.isfinite(result) else ExcelError("#NUM!")

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
        return _datetime_to_serial(self.clock())

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

    def _record_resolved_targets(self, node, targets):
        if node.kind != "op" or not node.owner:
            return
        owner = self._current_cell or node.owner
        self._resolved_targets.setdefault(owner, set()).update(targets)
        self._resolved_operation_targets.setdefault(node.id, set()).update(targets)

    def _indirect_targets(self, node):
        """Resolve an A1-style INDIRECT string without consulting saved values."""
        reference = self._arg(node, 0)
        if is_bad(reference):
            return reference
        if not isinstance(reference, str):
            return ExcelError("#REF!")
        declared = int(node.arity) if str(node.arity).isdigit() else 1
        if declared > 1:
            a1_style = self._arg(node, 1)
            if a1_style is not None and not truthy(a1_style):
                return Unresolved("unsupported:INDIRECT-R1C1")
        match = A1_REFERENCE_RE.match(reference)
        if not match:
            # Legacy/static ASTs may already carry a resolved named reference
            # in an extra argument slot. Retain that path without reading its
            # value merely to discover its coordinates.
            attached = self._reference_arg(node, declared, read=False)
            if attached.refs:
                return list(attached.refs)
            return ExcelError("#REF!")
        sheet = match.group("quoted") or match.group("bare") or node.sheet
        sheet = sheet.replace("''", "'").strip()
        start = f"{match.group('col0')}{match.group('row0')}"
        end_col, end_row = match.group("col1"), match.group("row1")
        if end_col is None:
            return [f"{sheet}!{start}"]
        return range_members(sheet, f"{start}:{end_col}{end_row}")

    def _indirect(self, node):
        """Resolve and read the reference text computed by INDIRECT."""
        targets = self._indirect_targets(node)
        if is_bad(targets):
            return targets
        self._record_resolved_targets(node, targets)
        values = [self._read(target) for target in targets]
        if len(targets) == 1:
            return values[0]
        coordinates = [_split(target) for target in targets]
        rows = len({row for _, row, _ in coordinates}) or 1
        cols = len({col for _, _, col in coordinates}) or 1
        return RangeValues(values, rows, cols, refs=targets)

    def _column(self, node):
        """Static reference coordinates, never the referenced cell's contents."""
        specs = self._arg_specs(node)
        owner = self.graph.nodes.get(node.owner)
        current = self.graph.nodes.get(self._current_cell)
        if (owner is not None and current is not None and owner.array_anchor
                and current.array_anchor == owner.array_anchor):
            owner = current
        if not specs:
            return (float(owner.col) if owner is not None and owner.col is not None
                    else ExcelError("#REF!"))
        if len(specs) != 1:
            return Unresolved("column-arity")
        kind, payload = specs[0]
        if kind == "scalar":
            source = self.graph.nodes.get(payload)
            if source is None:
                return Unresolved("column-missing-reference")
            if source.is_cell:
                return float(source.col) if source.col is not None else ExcelError("#REF!")
            if source.kind == "range":
                match = re.fullmatch(r"([A-Za-z]{1,3})[1-9][0-9]*:([A-Za-z]{1,3})[1-9][0-9]*", source.coordinate or "")
                if not match:
                    return Unresolved("column-range-reference")
                left, right = sorted(col_number(value) for value in match.groups())
                columns = list(range(left, right + 1))
            elif source.kind == "const":
                value = self._eval_node(source.id)
                return value if is_bad(value) else ExcelError("#VALUE!")
            else:
                return Unresolved("column-dynamic-reference")
        elif kind in ("span", "many"):
            coordinates = [split_ref(value) for value in payload]
            if not coordinates or any(value is None for value in coordinates):
                return Unresolved("column-reference-shape")
            sheets = {value[0] for value in coordinates}
            rows = {value[1] for value in coordinates}
            cols = {value[2] for value in coordinates}
            if (len(sheets) != 1 or len(set(coordinates)) != len(rows) * len(cols)
                    or len(rows) != max(rows) - min(rows) + 1
                    or len(cols) != max(cols) - min(cols) + 1):
                return Unresolved("column-multiple-areas")
            columns = sorted(cols)
        else:
            return Unresolved("column-missing-reference")
        if not columns or any(not 1 <= value <= 16384 for value in columns):
            return ExcelError("#REF!")
        if len(columns) == 1:
            return float(columns[0])
        if owner is not None and (owner.array_formula or owner.array_ref or owner.array_span):
            return RangeValues([float(value) for value in columns], 1, len(columns))
        if owner is not None and self.graph.root_of(owner.id) == node.id:
            return float(columns[0])
        # Nested array consumers require an established evaluation context;
        # don't assume legacy intersection or dynamic array evaluation.
        return Unresolved("column-array-context")

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
        # Excel's COUNTA counts every non-empty cell, including formulas that
        # display as "" -- only genuinely empty cells (None here) are skipped.
        return float(sum(value is not None for value in values))

    def _index_reference_endpoint(self, node):
        """Bounded single-area reference form; do not read the referenced values.

        INDEX's scalar value handler intentionally remains unchanged. A colon
        needs addresses, including zero-index whole-row/column references.
        Multiple areas, calculated reference sources and array selectors are
        outside this proof and remain unresolved, even under IFERROR.
        """
        if node.kind != "op" or node.op != "INDEX" or str(node.arity) not in {"2", "3"}:
            return Unresolved("index-range-endpoint")
        specs = self._arg_specs(node)
        if len(specs) != int(node.arity) or node.range_truncated:
            return Unresolved("index-range-arguments")
        kind, payload = specs[0]
        if kind == "scalar":
            source = self.graph.nodes.get(payload)
            if source is None or source.range_truncated or not (source.is_cell or source.kind == "range"):
                return Unresolved("index-range-static-reference")
            if source.kind == "range":
                match = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)(?::\$?([A-Za-z]{1,3})\$?([1-9][0-9]*))?", source.coordinate or "")
                if match is None:
                    return Unresolved("index-range-coordinate")
                c0, r0 = col_number(match[1]), int(match[2])
                c1, r1 = col_number(match[3] or match[1]), int(match[4] or match[2])
                if (not 1 <= r0 <= r1 <= 1048576 or not 1 <= c0 <= c1 <= 16384
                        or (r1-r0+1)*(c1-c0+1) > 10000):
                    return Unresolved("index-range-bounds")
                refs = [a1(source.sheet, r, c) for r in range(r0, r1+1) for c in range(c0, c1+1)]
            else:
                refs = [source.id]
        elif kind == "span" and payload and len(payload) <= 10000:
            refs = list(payload)
            if any(self.graph.nodes.get(ref) is not None and self.graph.nodes[ref].range_truncated for ref in refs):
                return Unresolved("index-range-truncated")
        else:
            return Unresolved("index-range-static-reference")
        coordinates = [split_ref(ref) for ref in refs]
        if not coordinates or any(item is None or not 1 <= item[1] <= 1048576
                                  or not 1 <= item[2] <= 16384 for item in coordinates):
            return Unresolved("index-range-coordinate")
        sheet, row0, col0 = coordinates[0]
        rows, cols = len({item[1] for item in coordinates}), len({item[2] for item in coordinates})
        if (len(refs) != rows*cols
                or refs != [a1(sheet, row0+r, col0+c) for r in range(rows) for c in range(cols)]):
            return Unresolved("index-range-shape")
        origins = [self.graph.nodes.get(edge.source) for edge in self.graph.in_edges.get(node.id, ()) if edge.arg_index == 0]
        if not origins or any(source is None or source.sheet != sheet or not (source.is_cell or source.kind == "range") for source in origins):
            return Unresolved("index-range-internal-reference")
        selectors = []
        for index in range(1, len(specs)):
            value = self._arg(node, index)
            if is_bad(value):
                return value
            if type(value) not in {int, float} or not math.isfinite(value) or value != int(value):
                return Unresolved("index-range-scalar-integer-selector")
            selectors.append(int(value))
        row, col = (1, selectors[0]) if len(selectors) == 1 and rows == 1 else (selectors[0], selectors[1] if len(selectors) == 2 else 1)
        if not 0 <= row <= rows or not 0 <= col <= cols:
            return ExcelError("#REF!")
        selected = [refs[r*cols+c] for r in (range(rows) if row == 0 else [row-1])
                    for c in (range(cols) if col == 0 else [col-1])]
        self._record_resolved_targets(node, selected)
        return selected

    def _index_range_endpoints(self, node):
        specs = self._arg_specs(node)
        if len(specs) != 2 or any(kind != "scalar" for kind, _ in specs):
            return None
        ends = [self.graph.nodes.get(payload) for _, payload in specs]
        return ends if all(end is not None and end.kind == "op" and end.op == "INDEX" for end in ends) else None

    def _reference_range_targets(self, node):
        endpoints = self._index_range_endpoints(node)
        if endpoints is None:
            return self._static_offset_range_targets(node)
        left, right = [self._index_reference_endpoint(end) for end in endpoints]
        # Unproved semantics must not be hidden by a caught error in the other
        # endpoint. Ordinary Excel errors propagate only after both are proved.
        for value in (left, right):
            if isinstance(value, Unresolved):
                return value
        for value in (left, right):
            if isinstance(value, ExcelError):
                return value
        points = [split_ref(ref) for ref in left + right]
        if len({point[0] for point in points}) != 1:
            return Unresolved("index-range-cross-sheet")
        sheet = points[0][0]
        r0, r1 = min(p[1] for p in points), max(p[1] for p in points)
        c0, c1 = min(p[2] for p in points), max(p[2] for p in points)
        if (r1-r0+1)*(c1-c0+1) > 10000:
            return Unresolved("index-range-hard-cap")
        targets = [a1(sheet, r, c) for r in range(r0, r1+1) for c in range(c0, c1+1)]
        self._record_resolved_targets(node, targets)
        return targets

    def _static_offset_range_targets(self, node):
        """Only the parser's signed integer literal OFFSET endpoint is supported.

        Preserve the OFFSET operation for occurrence-specific policy evidence.
        Resolve both coordinates without reading any workbook value or cache.
        """
        groups = {}
        for edge in self.graph.in_edges.get(node.id, ()):
            groups.setdefault(edge.arg_index, []).append(edge.source)
        if set(groups) != {0, 1} or any(len(v) != 1 for v in groups.values()):
            return Unresolved("static-offset-range-shape")
        start = self.graph.nodes.get(groups[0][0])
        offset = self.graph.nodes.get(groups[1][0])
        if (start is None or not start.is_cell or offset is None
                or offset.kind != "op" or offset.op != "OFFSET"
                or str(offset.arity) != "3"):
            return Unresolved("static-offset-range-endpoint")
        arguments = {}
        for edge in self.graph.in_edges.get(offset.id, ()):
            arguments.setdefault(edge.arg_index, []).append(edge.source)
        if set(arguments) != {0, 1, 2} or any(len(v) != 1 for v in arguments.values()):
            return Unresolved("static-offset-range-arguments")
        base = self.graph.nodes.get(arguments[0][0])
        if (base is None or not base.is_cell or start.sheet != node.sheet
                or base.sheet != node.sheet):
            return Unresolved("static-offset-range-sheet")
        offsets = []
        for index in (1, 2):
            literal_node = self.graph.nodes.get(arguments[index][0])
            sign = 1
            if literal_node is not None and literal_node.kind == "op" and literal_node.op in {"u+", "u-"}:
                unary_edges = list(self.graph.in_edges.get(literal_node.id, ()))
                if str(literal_node.arity) != "1" or len(unary_edges) != 1 or unary_edges[0].arg_index != 0:
                    return Unresolved("static-offset-range-unary")
                sign = -1 if literal_node.op == "u-" else 1
                literal_node = self.graph.nodes.get(unary_edges[0].source)
            if (literal_node is None or literal_node.kind != "const" or literal_node.op != "number"
                    or self.graph.in_edges.get(literal_node.id)):
                return Unresolved("static-offset-range-literal")
            value = literal(literal_node.value)
            if (type(value) not in {int, float} or not math.isfinite(value)
                    or abs(value) > 1048576 or value != int(value)):
                return Unresolved("static-offset-range-integer")
            offsets.append(sign * int(value))
        if any(type(value) is not int for value in (start.row, start.col, base.row, base.col)):
            return Unresolved("static-offset-range-coordinate")
        end_row, end_col = base.row + offsets[0], base.col + offsets[1]
        if not all(1 <= row <= 1048576 and 1 <= col <= 16384
                   for row, col in [(start.row, start.col), (base.row, base.col), (end_row, end_col)]):
            return Unresolved("static-offset-range-bounds")
        height, width = abs(start.row - end_row) + 1, abs(start.col - end_col) + 1
        if height * width > 10_000:
            return Unresolved("static-offset-range-hard-cap")
        targets = [a1(node.sheet, row, col)
                   for row in range(min(start.row, end_row), max(start.row, end_row) + 1)
                   for col in range(min(start.col, end_col), max(start.col, end_col) + 1)]
        self._record_resolved_targets(offset, [a1(node.sheet, end_row, end_col)])
        self._record_resolved_targets(node, targets)
        return targets

    def _reference_range(self, node):
        targets = self._reference_range_targets(node)
        if is_bad(targets):
            return targets
        coordinates = [_split(target) for target in targets]
        return RangeValues([self._read(target) for target in targets],
                           len({row for _, row, _ in coordinates}),
                           len({col for _, _, col in coordinates}), refs=targets)

    def _offset(self, node):
        """Resolve a dynamic reference, including optional range dimensions."""
        targets = self._offset_targets(node)
        if is_bad(targets):
            return targets
        self._record_resolved_targets(node, targets)
        values = [self._read(target) for target in targets]
        groups = {
            edge.arg_index for edge in self.graph.in_edges.get(node.id, ())
        }
        height = int(num(self._arg(node, 3), 1.0)) if 3 in groups else 1
        width = int(num(self._arg(node, 4), 1.0)) if 4 in groups else 1
        if height == 1 and width == 1:
            return values[0]
        return RangeValues(values, height, width, refs=targets)

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
            base_id = a1(base_sheet, base_row, base_col)
        if self._current_cell is not None:
            self.runtime_address_radj.setdefault(
                self._current_cell, set()
            ).add(base_id)
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

    @staticmethod
    def _refs(value):
        return list(value.refs or ()) if isinstance(value, RangeValues) else []

    def _lookup_reference_plan(self, node):
        """Read a static table's search vector, never its unselected result cells.

        Reuse the existing lookup dependency selector and value handler. Unknown
        keys/indices keep the whole table frontier until they are reconstructed.
        This is dependency discovery, not a new matching or error policy.
        """
        specs = self._arg_specs(node)
        if len(specs) not in {3, 4}:
            return None
        if node.range_truncated:
            return None
        kind, payload = specs[1]
        if kind == "scalar":
            table_node = self.graph.nodes.get(payload)
            if (table_node is None or table_node.range_truncated
                    or not (table_node.is_cell or table_node.kind == "range")):
                return None
            if table_node.kind == "range":
                # range_members allocates eagerly: establish cardinality and
                # worksheet bounds before _reference_arg can expand anything.
                match = re.fullmatch(
                    r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)(?::\$?([A-Za-z]{1,3})\$?([1-9][0-9]*))?",
                    table_node.coordinate or "")
                if match is None:
                    return None
                col0, row0 = col_number(match[1]), int(match[2])
                col1, row1 = col_number(match[3] or match[1]), int(match[4] or match[2])
                if (not 1 <= row0 <= row1 <= 1048576 or not 1 <= col0 <= col1 <= 16384
                        or (row1 - row0 + 1) * (col1 - col0 + 1) > 10000):
                    return None
            else:
                ref = split_ref(table_node.id)
                if (ref is None or not 1 <= ref[1] <= 1048576 or not 1 <= ref[2] <= 16384):
                    return None
        elif kind == "span":
            if not payload or len(payload) > 10000:
                return None
            coordinates = []
            for source_id in payload:
                source = self.graph.nodes.get(source_id)
                if source is not None and source.range_truncated:
                    return None
                ref = split_ref(source_id)
                if ref is None or not 1 <= ref[1] <= 1048576 or not 1 <= ref[2] <= 16384:
                    return None
                coordinates.append(ref)
            sheet, first_row, first_col = coordinates[0]
            rows = len({ref[1] for ref in coordinates})
            cols = len({ref[2] for ref in coordinates})
            if (len(payload) != rows * cols or list(payload) != [a1(sheet, first_row + r, first_col + c)
                    for r in range(rows) for c in range(cols)]):
                return None
        else:
            return None
        table = self._reference_arg(node, 1, read=False)
        refs = self._refs(table)
        if not refs or len(refs) != table.rows * table.cols or len(refs) > 10000:
            return None
        coordinates = [split_ref(ref) for ref in refs]
        if any(item is None for item in coordinates):
            return None
        sheet, row, col = coordinates[0]
        if refs != [a1(sheet, row + r, col + c)
                    for r in range(table.rows) for c in range(table.cols)]:
            return None
        key, index = self._arg(node, 0), self._arg(node, 2)
        mode = self._arg(node, 3) if len(specs) == 4 else True
        if any(is_bad(value) or isinstance(value, (RangeValues, list))
               for value in (key, index, mode)):
            return None
        dimension = table.rows if node.op == "HLOOKUP" else table.cols
        if (type(index) not in {int, float} or not math.isfinite(index)
                or int(index) != index or not 1 <= index <= dimension):
            return None
        search_positions = (range(table.cols) if node.op == "HLOOKUP"
                            else range(0, len(refs), table.cols))
        values = [None] * len(refs)
        search_refs = []
        journal = {}
        consumer = self._current_cell
        try:
            for position in search_positions:
                search_refs.append(refs[position])
                values[position] = self._read(refs[position], _journal=journal)
                if is_bad(values[position]):
                    return None
        finally:
            for name, visited in journal.items():
                getattr(self, name).add_group(visited, consumer)
        table = RangeValues(values, table.rows, table.cols, refs=refs)
        args = [key, table, index, mode]
        sources, selected = self._selective_dependencies(node, args, None, return_selected=True)
        # Complete search-vector coverage is retained even if an exact match
        # occurs early. Key, index and mode's transitive inputs remain present.
        sources = compact_graph.Builder(self._source_pool, sources)
        sources.update(self._source_pool.sources(search_refs))
        if len(selected) > 1:
            return None
        return args, sources.freeze(), selected[0] if selected else None

    def _selective_dependencies(self, node, args, result, *, return_selected=False):
        """Value precedents used by a selector, excluding unselected result cells."""
        op = node.op
        specs = self._arg_specs(node)
        sources = compact_graph.Builder(self._source_pool)

        def add_slot(index):
            if index < len(specs):
                sources.update(self._active_spec_sources(specs[index]))

        selected_refs = []
        if op == "INDEX":
            add_slot(1)
            add_slot(2)
            source = args[0] if args else RangeValues([])
            refs = self._refs(source)
            if refs and isinstance(source, RangeValues):
                row = int(num(args[1])) if len(args) > 1 else 1
                has_col = len(args) > 2 and args[2] is not None
                if not has_col and source.rows == 1:
                    row, col = 1, row
                else:
                    col = int(num(args[2])) if has_col else 1
                if row == 0 and col == 0:
                    selected_refs = refs
                elif row == 0 and 1 <= col <= source.cols:
                    selected_refs = refs[col - 1::source.cols]
                elif col == 0 and 1 <= row <= source.rows:
                    start = (row - 1) * source.cols
                    selected_refs = refs[start:start + source.cols]
                elif 1 <= row <= source.rows and 1 <= col <= source.cols:
                    index = (row - 1) * source.cols + col - 1
                    if index < len(refs):
                        selected_refs = [refs[index]]
            elif specs:
                add_slot(0)
        elif op == "MATCH":
            add_slot(0)
            add_slot(2)
            if len(args) > 1:
                refs = self._refs(args[1])
                sources.update(refs)
                if not refs:
                    add_slot(1)
        elif op in ("XLOOKUP", "_XLFN.XLOOKUP"):
            add_slot(0)
            keys = list(flatten([args[1]])) if len(args) > 1 else []
            key_refs = self._refs(args[1]) if len(args) > 1 else []
            sources.update(key_refs)
            if not key_refs:
                add_slot(1)
            selected = next(
                (index for index, key in enumerate(keys) if _equal(key, args[0])),
                None,
            )
            if selected is not None and len(args) > 2:
                result_refs = self._refs(args[2])
                if selected < len(result_refs):
                    selected_refs = [result_refs[selected]]
                elif not result_refs:
                    add_slot(2)
            elif selected is None:
                add_slot(3)
            add_slot(4)
            add_slot(5)
        elif op == "VLOOKUP":
            add_slot(0)
            add_slot(2)
            add_slot(3)
            table = args[1] if len(args) > 1 else RangeValues([])
            refs = self._refs(table)
            values = list(flatten([table]))
            cols = table.cols if isinstance(table, RangeValues) else 1
            column = int(num(args[2])) if len(args) > 2 else 1
            exact = len(args) > 3 and not truthy(args[3])
            selected = None
            for offset in range(0, len(values), max(cols, 1)):
                if offset < len(refs):
                    sources.add(refs[offset])
                key = values[offset]
                if _equal(key, args[0]):
                    selected = offset
                    break
                if not exact and isinstance(key, (int, float)) and key <= num(args[0]):
                    selected = offset
            if selected is not None:
                target = selected + column - 1
                if target < len(refs):
                    selected_refs = [refs[target]]
            if not refs:
                add_slot(1)
        elif op == "HLOOKUP":
            add_slot(0)
            add_slot(2)
            add_slot(3)
            table = args[1] if len(args) > 1 else RangeValues([])
            refs = self._refs(table)
            values = list(flatten([table]))
            cols = table.cols if isinstance(table, RangeValues) else len(values)
            row = int(num(args[2])) if len(args) > 2 else 1
            exact = len(args) > 3 and not truthy(args[3])
            selected = None
            for index, key in enumerate(values[:cols]):
                if index < len(refs):
                    sources.add(refs[index])
                if _equal(key, args[0]):
                    selected = index
                    break
                if not exact and isinstance(key, (int, float)) and key <= num(args[0]):
                    selected = index
            target = (row - 1) * cols + selected if selected is not None else -1
            if 0 <= target < len(refs):
                selected_refs = [refs[target]]
            if not refs:
                add_slot(1)
        elif op == "LOOKUP":
            add_slot(0)
            table = args[1] if len(args) > 1 else RangeValues([])
            refs = self._refs(table)
            sources.update(refs)
            if not refs:
                add_slot(1)
            if len(args) > 2 and args[2] is not None:
                result_refs = self._refs(args[2])
                if isinstance(result, RangeValues):
                    selected_refs = list(result.refs or ())
                elif result_refs:
                    values = list(flatten([table]))
                    selected = None
                    for index, key in enumerate(values):
                        if _equal(key, args[0]):
                            selected = index
                            break
                        if isinstance(key, (int, float)) and key <= num(args[0]):
                            selected = index
                    if selected is not None and selected < len(result_refs):
                        selected_refs = [result_refs[selected]]
                else:
                    add_slot(2)

        sources.update(selected_refs)
        self._record_resolved_targets(node, selected_refs)
        return (sources.freeze(), selected_refs) if return_selected else sources.freeze()

    def _active_node_sources(self, node_id, seen=None):
        """Cell precedents actually evaluated under the current selector values."""
        root_call = seen is None
        if root_call and self.optimize and self._source_walk_is_pure(node_id):
            cached = self._static_source_cache.get(node_id)
            if cached is not None:
                self._benchmark["active_graph"]["static_source_cache_hits"] += 1
                return cached
            self._benchmark["active_graph"]["static_source_cache_misses"] += 1

        seen = set() if root_call else seen
        if node_id in seen:
            return set()
        seen.add(node_id)
        node = self.graph.nodes.get(node_id)
        if node is None:
            return set()
        if node.is_cell:
            return {node.id}
        if node.kind == "range":
            return self._source_pool.sources(self._source_pool.span((node.sheet, node.coordinate or ""), lambda: range_members(node.sheet, node.coordinate or "")))
        if node.kind != "op":
            return set()

        if node.op == "COLUMN" and not is_bad(self._column(node)):
            # A proved coordinate result never consumes the anchor's value.
            return set()

        if node.op == ":":
            targets = self._reference_range_targets(node)
            sources = compact_graph.Builder(self._source_pool, targets if isinstance(targets, list) else ())
            endpoints = self._index_range_endpoints(node)
            if endpoints is not None:
                for endpoint in endpoints:
                    specs = self._arg_specs(endpoint)
                    for spec in specs[1:]:
                        sources.update(self._active_spec_sources(spec, seen))
                    # Before selectors can be reconstructed retain the full
                    # conservative table frontier; narrow only after proof.
                    if isinstance(targets, Unresolved) and specs:
                        sources.update(self._active_spec_sources(specs[0], seen))
            return sources.freeze()

        if self._legacy_average_arithmetic(node):
            sources = compact_graph.Builder(self._source_pool)
            for index, spec in enumerate(self._arg_specs(node)):
                handled, selected = self._legacy_static_operand(node, index)
                if isinstance(selected, str):
                    sources.add(selected)
                elif not handled:
                    sources.update(self._active_spec_sources(spec, seen))
            return sources.freeze()

        if node.op == "SUBTOTAL":
            # Nested subtotals are excluded by Excel. Their values and their
            # dependency edges must both be excluded, or ignored totals can
            # manufacture active cycles in the reconstruction proof.
            specs = self._arg_specs(node)
            sources = compact_graph.Builder(self._source_pool, self._active_spec_sources(specs[0], seen) if specs else ())
            for index in range(1, len(specs)):
                reference = self._reference_arg(node, index, read=False)
                if reference.refs:
                    sources.update(ref for ref in reference.refs
                                   if not self._has_subtotal_provenance(ref))
                else:
                    sources.update(self._active_spec_sources(specs[index], seen))
            return sources.freeze()

        if node.op in ("SUMIF", "SUMIFS"):
            return self._conditional_sources(node)
        if node.op in {"HLOOKUP", "VLOOKUP"}:
            plan = self._lookup_reference_plan(node)
            if plan is not None:
                return set(plan[1])
            # Do not reuse a selection from older key/index/mode values.
            self._selective_sources.pop(node.id, None)
        if node.op in (
            "VLOOKUP", "HLOOKUP", "XLOOKUP", "_XLFN.XLOOKUP",
            "LOOKUP", "MATCH", "INDEX",
        ):
            selected = self._selective_sources.get(node.id)
            if selected is not None:
                return set(selected)

        specs = self._arg_specs(node)
        indices = list(range(len(specs)))
        if node.op == "OFFSET":
            # The first argument supplies an address origin, not a value. The
            # resolved target below is the actual value dependency.
            indices = list(range(1, len(specs)))
        elif node.op == "IF":
            cond = self._arg(node, 0)
            # Discovery must over-approximate an unresolved selector. Keeping
            # only the condition creates a self-sustaining under-closed cone:
            # neither branch is admitted, so the condition can never become
            # computable. Once the selector has a value this remains exactly
            # branch-specific.
            indices = (
                list(range(len(specs)))
                if is_bad(cond) or cond is None
                else [0, 1 if truthy(cond) else 2]
            )
        elif node.op == "CHOOSE":
            selected = self._arg(node, 0)
            if is_bad(selected) or selected is None:
                indices = list(range(len(specs)))
            else:
                index = int(num(selected))
                indices = [0] + ([index] if 1 <= index < len(specs) else [])
        elif node.op in ("IFS", "_XLFN.IFS"):
            indices = []
            for index in range(0, len(specs) - 1, 2):
                indices.append(index)
                cond = self._arg(node, index)
                if is_bad(cond) or cond is None:
                    # Every remaining condition/value pair can still become
                    # active after proof closure supplies the missing selector.
                    indices.extend(range(index + 1, len(specs)))
                    break
                if truthy(cond):
                    indices.append(index + 1)
                    break
        elif node.op == "IFERROR":
            indices = [0]
            if isinstance(self._arg(node, 0), ExcelError) and len(specs) > 1:
                indices.append(1)

        sources = compact_graph.Builder(self._source_pool)
        for index in indices:
            if index >= len(specs):
                continue
            kind, payload = specs[index]
            if kind == "span":
                sources.update(self._source_pool.sources(payload))
            elif kind == "scalar":
                sources.update(self._active_node_sources(payload, seen))
            elif kind == "many":
                for source in payload:
                    sources.update(self._active_node_sources(source, seen))
        if node.op == "OFFSET":
            targets = self._offset_targets(node)
            if isinstance(targets, list):
                sources.update(targets)
                self._record_resolved_targets(node, targets)
        elif node.op == "INDIRECT":
            targets = self._indirect_targets(node)
            if isinstance(targets, list):
                sources.update(targets)
                self._record_resolved_targets(node, targets)
        if (
            root_call
            and self.optimize
            and self._source_walk_purity_cache.get(node_id) is True
        ):
            self._static_source_cache[node_id] = sources.freeze()
        return sources.freeze()

    def _source_walk_is_pure(self, node_id, visiting=None):
        """Prove that an AST subtree's active sources cannot depend on values."""
        cached = self._source_walk_purity_cache.get(node_id)
        if cached is not None:
            return cached
        visiting = set() if visiting is None else visiting
        if node_id in visiting:
            self._source_walk_purity_cache[node_id] = False
            return False
        node = self.graph.nodes.get(node_id)
        if node is None:
            self._source_walk_purity_cache[node_id] = False
            return False
        if node.is_cell or node.kind in {"const", "range"}:
            self._source_walk_purity_cache[node_id] = True
            return True
        if (
            node.kind != "op"
            or _bare_operation(node.op) not in SOURCE_WALK_PURE_OPS
        ):
            self._source_walk_purity_cache[node_id] = False
            return False
        branch = visiting | {node_id}
        pure = all(
            self._source_walk_is_pure(edge.source, branch)
            for edge in self.graph.in_edges.get(node_id, ())
        )
        self._source_walk_purity_cache[node_id] = pure
        return pure

    def _active_graph(self, cells):
        """Build source-to-consumer edges after pruning inactive formula branches."""
        started = time.perf_counter()
        cell_count = 0
        radj = {}
        try:
            for target in cells:
                cell_count += 1
                root = self.graph.root_of(target)
                if root is None:
                    continue
                self._current_cell = target
                radj[target] = self._source_pool.sources(self._active_node_sources(root))
            return compact_graph.transpose(radj, self._source_pool), compact_graph.Rows(radj)
        finally:
            metric = self._benchmark["active_graph"]
            metric["calls"] += 1
            metric["cells_scanned"] += cell_count
            metric["seconds"] += time.perf_counter() - started

    @staticmethod
    def _cell_key(cell):
        sheet, row, col = _split(cell)
        return str(sheet), int(row), int(col), str(cell)

    def _component_plan(self, cells, active_adj):
        """Return deterministic SCC and topological metadata for an active graph."""
        started = time.perf_counter()
        metric = self._benchmark["component_plan"]
        metric["calls"] += 1
        ordered_cells = tuple(sorted(cells, key=self._cell_key))
        cache_key = None
        if self.optimize:
            cache_key = (ordered_cells, self._graph_signature(active_adj))
            cached = self._component_plan_cache.get(cache_key)
            if cached is not None:
                metric["cache_hits"] += 1
                metric["seconds"] += time.perf_counter() - started
                return cached
            metric["cache_misses"] += 1
        ordered_adj = compact_graph.OrderedRows(active_adj, self._cell_key)
        groups = strongly_connected(ordered_cells, ordered_adj)
        groups = [tuple(sorted(group, key=self._cell_key)) for group in groups]
        groups.sort(key=lambda group: self._cell_key(group[0]))
        comp_of = {
            cell: index for index, group in enumerate(groups) for cell in group
        }
        comp_adj, indegree = compact_graph.components(
            active_adj, comp_of, len(groups), self._source_pool
        )
        ordered_components = compact_graph.OrderedRows(
            comp_adj, lambda item: self._cell_key(groups[item][0])
        )
        ready = []
        for index, count in indegree.items():
            if count == 0:
                heappush(ready, (self._cell_key(groups[index][0]), index))
        topo = []
        while ready:
            _, index = heappop(ready)
            topo.append(index)
            for target in ordered_components[index]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    heappush(
                        ready,
                        (self._cell_key(groups[target][0]), target),
                    )
        cyclic = {
            index
            for index, group in enumerate(groups)
            if len(group) > 1
            or any(cell in active_adj.get(cell, ()) for cell in group)
        }
        result = (groups, topo, cyclic)
        if cache_key is not None:
            self._component_plan_cache[cache_key] = result
        metric["seconds"] += time.perf_counter() - started
        return result

    def _cycle_independent_values(self, cells, seeded):
        """Prove closed static formulas depend only on fixed supplied inputs.

        Reuse is local to the workbook-iteration phase after initial topology
        stabilization. It uses reconstructed evaluator values, never caches
        from the original workbook. Unknown/dynamic/array expressions decline.
        """
        if not self.optimize or not self.strict_proof or self.oracle is not None:
            return frozenset()
        allowed = frozenset({
            "+", "-", "*", "/", "^", "&", "u+", "u-", "%",
            "=", "<>", ">", "<", ">=", "<=", "SUM", "AVERAGE",
            "MIN", "MAX", "ABS", "ROUND", "ROUNDUP", "ROUNDDOWN",
            "DATE", "EDATE", "EOMONTH", "YEAR", "MONTH", "DAY",
            "IFERROR", "SUMIFS",
        })
        scope = set(cells)
        memo = {}
        visiting = set()
        walked = [0]

        def rectangle(spec):
            kind, payload = spec
            if kind == "span":
                refs = payload
            elif kind == "scalar":
                node = self.graph.nodes.get(payload)
                if node is None or node.range_truncated:
                    return None
                if node.is_cell:
                    refs = (node.id,)
                elif node.kind == "range":
                    match = re.fullmatch(
                        r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)(?::\$?([A-Za-z]{1,3})\$?([1-9][0-9]*))?",
                        node.coordinate or "")
                    if match is None:
                        return None
                    c0, r0 = col_number(match[1]), int(match[2])
                    c1, r1 = col_number(match[3] or match[1]), int(match[4] or match[2])
                    if (not 1 <= r0 <= r1 <= 1048576 or not 1 <= c0 <= c1 <= 16384
                            or (r1-r0+1)*(c1-c0+1) > 10000):
                        return None
                    refs = tuple(a1(node.sheet, r, c) for r in range(r0,r1+1)
                                 for c in range(c0,c1+1))
                else:
                    return None
            else:
                return None
            if not refs or len(refs) > 10000:
                return None
            coordinates = [split_ref(ref) for ref in refs]
            if any(x is None or not 1 <= x[1] <= 1048576 or not 1 <= x[2] <= 16384
                   for x in coordinates):
                return None
            sheet, top, left = coordinates[0]
            rows = len({x[1] for x in coordinates})
            cols = len({x[2] for x in coordinates})
            if list(refs) != [a1(sheet,top+r,left+c) for r in range(rows) for c in range(cols)]:
                return None
            return tuple(refs), rows, cols

        def spec_safe(spec):
            kind, payload = spec
            if kind == "none":
                return True
            if kind == "span":
                layout = rectangle(spec)
                return layout is not None and all(visit(ref) for ref in layout[0])
            if kind == "scalar":
                return visit(payload)
            return False

        def visit(node_id):
            if node_id in memo:
                return memo[node_id]
            if node_id in visiting or len(visiting) >= 128 or walked[0] >= 50000:
                return False
            walked[0] += 1
            node = self.graph.nodes.get(node_id)
            if node is None:
                # A named absent static cell is a blank in strict proof mode;
                # unknown AST identifiers are not assumed to be blank cells.
                ref = split_ref(node_id)
                return bool(ref and 1 <= ref[1] <= 1048576 and 1 <= ref[2] <= 16384)
            if node.range_truncated:
                return False
            visiting.add(node_id)
            try:
                if node.is_cell:
                    if node.id not in scope:
                        safe = False
                    elif node.kind in {"input", "label"}:
                        safe = node.id in seeded
                    elif (node.kind != "formula" or any(getattr(node, key, False)
                            for key in ("array_formula", "array_anchor", "array_ref", "array_span"))):
                        safe = False
                    else:
                        root = self.graph.root_of(node.id)
                        safe = root is not None and visit(root)
                elif node.kind == "const":
                    safe = True
                elif node.kind == "range":
                    layout = rectangle(("scalar", node.id))
                    safe = layout is not None and all(visit(ref) for ref in layout[0])
                elif node.kind == "op" and node.op in allowed:
                    specs = self._arg_specs(node)
                    safe = True
                    if node.op == "SUMIFS":
                        # Prove the complete static sum/criterion rectangles,
                        # including unselected summands and selector inputs.
                        layouts = [rectangle(specs[i]) for i in [0,*range(1,len(specs),2)]] if specs else []
                        safe = (len(specs) >= 3 and len(specs)%2 == 1
                                and all(layout is not None for layout in layouts)
                                and all(layout[1:] == layouts[0][1:] for layout in layouts))
                    safe = safe and all(spec_safe(spec) for spec in specs)
                else:
                    safe = False
            finally:
                visiting.remove(node_id)
            memo[node_id] = safe
            return safe

        return frozenset(cell for cell in cells
            if self.graph.nodes[cell].kind == "formula" and cell not in seeded
            and type(self.values.get(cell)) in (bool, int, float, str)
            and not (isinstance(self.values.get(cell), float) and not math.isfinite(self.values[cell]))
            and visit(cell))

    def _evaluate_acyclic(self, groups, topo, cyclic, evaluable, seeded, reusable=frozenset()):
        started = time.perf_counter()
        try:
            for index in topo:
                if index in cyclic:
                    continue
                for cell in groups[index]:
                    if cell in evaluable and cell not in seeded and cell not in reusable:
                        self.values[cell] = self._eval_cell(cell)
        finally:
            metric = self._benchmark["acyclic_evaluation"]
            metric["calls"] += 1
            metric["seconds"] += time.perf_counter() - started

    @staticmethod
    def _graph_signature(active_adj):
        return compact_graph.Signature(active_adj)

    def _target_signature(self):
        return tuple(
            (target, tuple(sorted(sources)))
            for target, sources in sorted(
                self._resolved_operation_targets.items()
            )
        )

    def _equation_residual(self, members, state=None):
        """Evaluate every equation against one simultaneous endpoint state."""
        saved = {cell: self.values.get(cell) for cell in members}
        if state is not None:
            self.values.update(state)
        changes = []
        errors = []
        unresolved = []
        try:
            for cell in members:
                before = self.values.get(cell)
                after = self._eval_cell(cell)
                if isinstance(after, ExcelError):
                    errors.append({"cell": cell, "value": repr(after)})
                if isinstance(after, Unresolved):
                    unresolved.append({
                        "cell": cell,
                        "value": repr(after),
                        "reason": after.reason,
                    })
                changes.append(self._value_change(before, after))
        finally:
            self.values.update(saved)
        if any(math.isinf(change) for change in changes):
            residual = None
        else:
            residual = max(changes, default=0.0)
        return residual, errors, unresolved

    @staticmethod
    def _bounded_history(history, limit=20):
        if len(history) <= limit:
            return list(history)
        half = limit // 2
        return list(history[:half]) + list(history[-half:])

    def _endpoint_equation_check(self, members, state):
        """Check a cached/recomputed endpoint against the stabilized equations."""
        members = tuple(members)
        if not members or set(members) - set(self._proof_endpoint_values):
            return {"safe": False, "reason": "missing_proof_endpoint_state"}
        if set(members) - set(state):
            return {"safe": False, "reason": "incomplete_cycle_endpoint"}
        if not all(
            isinstance(state[cell], (int, float))
            and not isinstance(state[cell], bool)
            and math.isfinite(float(state[cell]))
            for cell in members
        ):
            return {"safe": False, "reason": "non_numeric_cycle_endpoint"}

        checker = Evaluator(
            self.graph,
            self.cg,
            strict_proof=True,
            calculation=self.calculation,
            proof_outputs=self.proof_outputs,
            proof_scope=self.proof_scope,
        )
        checker.values = dict(self._proof_endpoint_values)
        checker.values.update(state)
        computed = {}
        for cell in members:
            computed[cell] = checker._eval_cell(cell)
        active_adj, active_radj = checker._active_graph(self.cg.info)
        del active_adj

        expected_equations, actual_equations = compact_graph.endpoint_equations(
            {cell: self._proof_active_radj.get(cell, ()) for cell in members},
            {cell: active_radj.get(cell, ()) for cell in members},
        )
        if actual_equations != expected_equations:
            return {
                "safe": False,
                "reason": "cycle_endpoint_equations_changed",
                "expected_equations": expected_equations,
                "actual_equations": actual_equations,
            }
        expected_targets = {
            cell: tuple(sorted(self._proof_resolved_targets.get(cell, ())))
            for cell in members
        }
        actual_targets = {
            cell: tuple(sorted(checker._resolved_targets.get(cell, ())))
            for cell in members
        }
        if actual_targets != expected_targets:
            return {
                "safe": False,
                "reason": "cycle_endpoint_targets_changed",
                "expected_targets": expected_targets,
                "actual_targets": actual_targets,
            }
        expected_operation_targets = {
            operation: tuple(sorted(targets))
            for operation, targets in sorted(
                self._proof_resolved_operation_targets.items()
            )
            if self.graph.nodes.get(operation) is not None
            and self.graph.nodes[operation].owner in members
        }
        actual_operation_targets = {
            operation: tuple(sorted(targets))
            for operation, targets in sorted(
                checker._resolved_operation_targets.items()
            )
            if self.graph.nodes.get(operation) is not None
            and self.graph.nodes[operation].owner in members
        }
        if actual_operation_targets != expected_operation_targets:
            return {
                "safe": False,
                "reason": "cycle_endpoint_operation_targets_changed",
                "expected_operation_targets": expected_operation_targets,
                "actual_operation_targets": actual_operation_targets,
            }

        changes = [
            self._value_change(state[cell], computed[cell]) for cell in members
        ]
        if any(math.isinf(change) for change in changes):
            return {
                "safe": False,
                "reason": "cycle_endpoint_error_or_unresolved",
            }
        return {
            "safe": True,
            "reason": None,
            "residual": max(changes, default=0.0),
            "equations": actual_equations,
        }

    def _affine_cycle_certificate(self, members):
        """Certify a bounded affine SCC ``x = A*x + b``.

        The accepted expression subset is deliberately narrow: scalar
        arithmetic, SUM/AVERAGE, and selectors whose conditions are constants
        with respect to the SCC.  The induced infinity norm ``||A||∞ < 1`` is
        a sound contraction bound, not an empirical convergence guess.
        """
        members = tuple(members)
        if not members:
            return {"certified": False, "reason": "empty_cycle"}
        position = {cell: index for index, cell in enumerate(members)}
        dimension = len(members)
        if not all(
            isinstance(self.values.get(cell), (int, float))
            and not isinstance(self.values.get(cell), bool)
            and math.isfinite(float(self.values[cell]))
            for cell in members
        ):
            return {
                "certified": False,
                "reason": "non_numeric_affine_endpoint",
            }
        branch_guards = []
        constant_error_branches = []
        equation_condition_proofs = []
        equation_inline_mode = False
        unsupported_nodes = []

        def reject(node_id, reason):
            if len(unsupported_nodes) < 20:
                node = self.graph.nodes.get(node_id)
                unsupported_nodes.append({
                    "node": node_id,
                    "kind": getattr(node, "kind", None),
                    "op": getattr(node, "op", None),
                    "reason": reason,
                })
            return None

        def constant(value):
            zeros = [0.0] * dimension
            return (zeros, float(value), list(zeros))

        def combine(left, right, sign=1.0):
            return (
                [
                    left[0][index] + sign * right[0][index]
                    for index in range(dimension)
                ],
                left[1] + sign * right[1],
                [
                    left[2][index] + right[2][index]
                    for index in range(dimension)
                ],
            )

        def scale(value, factor):
            return (
                [coefficient * factor for coefficient in value[0]],
                value[1] * factor,
                [bound * abs(factor) for bound in value[2]],
            )

        def is_constant(value):
            if equation_inline_mode:
                return all(coefficient == 0.0 for coefficient in value[0]) and all(bound == 0.0 for bound in value[2])
            return all(abs(coefficient) <= CONVERGENCE for coefficient in value[0])

        def equation_constant(value, seen):
            """Unfold finitely many member equations, never a circular assumption.

            Used only for a selector on an exact fixed-point constraint such as
            a fully repaid balance. Each chosen piecewise branch still adds its
            independent guard. No approximate-zero coefficient becomes zero.
            """
            nonlocal equation_inline_mode
            original_mode = equation_inline_mode
            equation_inline_mode = True
            expanded = []
            try:
                while True:
                    if any(bound != abs(coefficient) for bound, coefficient in zip(value[2], value[0])):
                        return None
                    remaining = [index for index, coefficient in enumerate(value[0]) if coefficient != 0.0]
                    if not remaining:
                        return value, expanded
                    index = remaining[0]
                    member = members[index]
                    if member in expanded or len(expanded) >= dimension:
                        return None
                    root = self.graph.root_of(member)
                    if root is None:
                        return None
                    replacement = derive(root, seen | set(expanded))
                    if replacement is None:
                        return None
                    expanded.append(member)
                    factor = value[0][index]
                    coefficients = list(value[0]); coefficients[index] = 0.0
                    remainder = (coefficients, value[1], [abs(c) for c in coefficients])
                    value = combine(remainder, scale(replacement, factor))
            finally:
                equation_inline_mode = original_mode

        def derive_spec(spec, seen, *, sum_blank_as_zero=False):
            kind, payload = spec
            if kind == "none":
                return []
            if kind == "span":
                sources = payload
            elif kind == "many":
                sources = payload
            elif kind == "scalar":
                source = self.graph.nodes.get(payload)
                if source is not None and source.kind == "range":
                    sources = range_members(
                        source.sheet, source.coordinate or ""
                    )
                else:
                    sources = (payload,)
            else:
                return None
            values = [
                (
                    constant(0.0)
                    if sum_blank_as_zero
                    and (
                        source not in self.graph.nodes
                        or self.values.get(source) in (None, "")
                    )
                    else derive(source, seen)
                )
                for source in sources
            ]
            return None if any(value is None for value in values) else values

        def derive_condition(spec, seen):
            kind, payload = spec
            node = self.graph.nodes.get(payload) if kind == "scalar" else None
            if node is not None and node.kind == "op" and node.op in {"AND", "OR", "NOT"}:
                if node.id in seen:
                    return None
                arguments = self._arg_specs(node)
                if not arguments or (node.op == "NOT" and len(arguments) != 1):
                    return None
                conditions = [derive_condition(arg, seen | {node.id}) for arg in arguments]
                # Excel evaluates all Boolean arguments. An error, unknown
                # scalar, or unguarded branch cannot be hidden by another arg.
                if any(value is None for value in conditions):
                    return None
                selected = (all(conditions) if node.op == "AND" else
                            any(conditions) if node.op == "OR" else not conditions[0])
                actual = self._eval_node(node.id)
                return selected if not is_bad(actual) and truthy(actual) == selected else None
            if node is not None and node.kind == "op" and node.op in {
                "=", "<>", "<", "<=", ">", ">=",
            }:
                comparison_specs = self._arg_specs(node)
                if len(comparison_specs) != 2:
                    return None
                sides = [
                    derive_spec(item, seen | {node.id})
                    for item in comparison_specs
                ]
                if any(side is None or len(side) != 1 for side in sides):
                    return None
                left, right = sides[0][0], sides[1][0]
                left_value = sum(
                    left[0][index] * float(self.values[members[index]])
                    for index in range(dimension)
                ) + left[1]
                right_value = sum(
                    right[0][index] * float(self.values[members[index]])
                    for index in range(dimension)
                ) + right[1]
                gap = abs(left_value - right_value)
                sensitivity = sum(
                    abs(left[0][index] - right[0][index])
                    for index in range(dimension)
                )
                threshold = 0.0 if equation_inline_mode else CONVERGENCE
                if gap <= CONVERGENCE and sensitivity > threshold:
                    if equation_inline_mode:
                        return None
                    start_guards, start_errors = len(branch_guards), len(unsupported_nodes)
                    expanded_left = equation_constant(left, seen | {node.id})
                    expanded_right = equation_constant(right, seen | {node.id})
                    if expanded_left is None or expanded_right is None:
                        del branch_guards[start_guards:]
                        del unsupported_nodes[start_errors:]
                        return None
                    lvalue, lmembers = expanded_left
                    rvalue, rmembers = expanded_right
                    a, b = lvalue[1], rvalue[1]
                    condition = {"=": a == b, "<>": a != b, "<": a < b,
                                 "<=": a <= b, ">": a > b, ">=": a >= b}[node.op]
                    if condition != truthy(self._eval_node(node.id)):
                        # A proof about an equilibrium cannot select another
                        # branch than the actual native finite-stop endpoint.
                        del branch_guards[start_guards:]
                        del unsupported_nodes[start_errors:]
                        return None
                    equation_condition_proofs.append({
                        "node": node.id, "operator": node.op,
                        "members": sorted(set(lmembers + rmembers)),
                        "left_constant": a, "right_constant": b,
                        "selected": condition,
                    })
                    return condition
                if sensitivity > threshold:
                    branch_guards.append((left, right, gap))
                return truthy(self._eval_node(node.id))
            values = derive_spec(spec, seen)
            if values is None or len(values) != 1 or not is_constant(values[0]):
                return None
            return truthy(values[0][1])

        def fixed_native_error(spec, seen):
            """An explicit native error dominates supported scalar arithmetic.

            Other operands must still have a valid affine derivation. A domain
            error observed only at this endpoint, an unknown operation, or a
            cycle-dependent division error is never a constant-error proof.
            """
            kind, node_id = spec
            if kind != "scalar" or node_id in seen:
                return None
            node = self.graph.nodes.get(node_id)
            if node is None or node.is_cell:
                return None
            if node.kind == "const" and node.op == "error":
                value = node.value if node.value != "" else node.expr
                return [node_id] if value in NATIVE_ERRORS else None
            if node.kind != "op" or node.op not in {"+", "-", "*", "/", "^", "u+", "u-"}:
                return None
            specs = self._arg_specs(node)
            expected = 1 if node.op in {"u+", "u-"} else 2
            if len(specs) != expected or any(item[0] != "scalar" for item in specs):
                return None
            errors = [fixed_native_error(item, seen | {node_id}) for item in specs]
            if not any(errors):
                return None
            for item, error in zip(specs, errors):
                if error is None:
                    value = derive_spec(item, seen | {node_id})
                    if value is None or len(value) != 1:
                        return None
            return sorted({leaf for group in errors if group for leaf in group})

        def derive(node_id, seen):
            if node_id in seen:
                return reject(node_id, "recursive_ast_path")
            node = self.graph.nodes.get(node_id)
            if node is None:
                return reject(node_id, "missing_node")
            if node.is_cell:
                if node.id in position:
                    coefficients = [0.0] * dimension
                    coefficients[position[node.id]] = 1.0
                    return coefficients, 0.0, [abs(value) for value in coefficients]
                value = self.values.get(node.id)
                if (
                    isinstance(value, (int, float))
                    and math.isfinite(float(value))
                ):
                    return constant(value)
                return reject(node_id, "non_numeric_external_cell")
            if node.kind == "const":
                value = literal(node.value if node.value != "" else node.expr)
                if (
                    isinstance(value, (int, float))
                    and math.isfinite(float(value))
                ):
                    return constant(value)
                return reject(node_id, "non_numeric_constant")
            if node.kind != "op":
                return reject(node_id, "unsupported_node_kind")
            specs = self._arg_specs(node)
            if node.op == "IFERROR" and len(specs) == 2:
                errors = fixed_native_error(specs[0], seen | {node_id})
                if errors:
                    fallback = derive_spec(specs[1], seen | {node_id})
                    if fallback is not None and len(fallback) == 1:
                        constant_error_branches.append({"node": node_id, "native_error_nodes": errors,
                                                        "fallback_spec": specs[1]})
                        return fallback[0]
                    return reject(node_id, "unsupported_constant_error_fallback")
            if node.op == "IF" and len(specs) >= 2:
                condition = derive_condition(specs[0], seen | {node_id})
                if condition is None:
                    return reject(node_id, "non_constant_if_selector")
                branch = 1 if condition else 2
                if branch >= len(specs):
                    return constant(0.0)
                selected = derive_spec(specs[branch], seen | {node_id})
                return selected[0] if selected is not None and len(selected) == 1 else None
            if node.op == "CHOOSE" and specs:
                selectors = derive_spec(specs[0], seen | {node_id})
                if (
                    selectors is None
                    or len(selectors) != 1
                    or not is_constant(selectors[0])
                ):
                    return reject(node_id, "non_constant_choose_selector")
                branch = int(num(selectors[0][1]))
                if not 1 <= branch < len(specs):
                    return None
                selected = derive_spec(specs[branch], seen | {node_id})
                return selected[0] if selected is not None and len(selected) == 1 else None
            if node.op in ("IFS", "_XLFN.IFS"):
                for index in range(0, len(specs) - 1, 2):
                    condition = derive_condition(
                        specs[index], seen | {node_id}
                    )
                    if condition is None:
                        return reject(node_id, "non_constant_ifs_selector")
                    if condition:
                        selected = derive_spec(
                            specs[index + 1], seen | {node_id}
                        )
                        return (
                            selected[0]
                            if selected is not None and len(selected) == 1
                            else None
                        )
                return None
            grouped_args = [
                derive_spec(
                    spec,
                    seen | {node_id},
                    # Blank cells have no AST node. Excel SUM treats blank
                    # references as zero, so their absence is affine rather
                    # than an unsupported dependency.
                    sum_blank_as_zero=node.op == "SUM",
                )
                for spec in specs
            ]
            if any(arg is None for arg in grouped_args):
                return reject(node_id, "unsupported_argument")
            args = [value for group in grouped_args for value in group]
            if node.op in ("u+", "u-") and len(args) == 1:
                return scale(args[0], -1.0 if node.op == "u-" else 1.0)
            if node.op in ("SUM", "AVERAGE"):
                total = constant(0.0)
                for arg in args:
                    total = combine(total, arg)
                if node.op == "AVERAGE" and args:
                    total = scale(total, 1.0 / len(args))
                return total
            if node.op == "PRODUCT":
                total = constant(1.0)
                for arg in args:
                    total_constant = is_constant(total)
                    arg_constant = is_constant(arg)
                    if not total_constant and not arg_constant:
                        return reject(node_id, "bilinear_product")
                    if total_constant:
                        total = scale(arg, total[1])
                    else:
                        total = scale(total, arg[1])
                return total
            if node.op in ("MIN", "MAX") and args:
                endpoint = [
                    sum(
                        value[0][index] * float(self.values[members[index]])
                        for index in range(dimension)
                    ) + value[1]
                    for value in args
                ]
                selected_index = (
                    min(range(len(args)), key=endpoint.__getitem__)
                    if node.op == "MIN"
                    else max(range(len(args)), key=endpoint.__getitem__)
                )
                selected = args[selected_index]
                tied = []
                for index, other in enumerate(args):
                    if index == selected_index:
                        continue
                    gap = abs(endpoint[selected_index] - endpoint[index])
                    sensitivity = sum(
                        abs(selected[0][member_index] - other[0][member_index])
                        for member_index in range(dimension)
                    )
                    constant_gap = abs(selected[1] - other[1])
                    if (
                        gap <= CONVERGENCE
                        and (
                            sensitivity > CONVERGENCE
                            or constant_gap > CONVERGENCE
                        )
                    ):
                        tied.append(other)
                        continue
                    if sensitivity > CONVERGENCE or constant_gap > CONVERGENCE:
                        branch_guards.append((selected, other, gap))
                if tied:
                    selected = (
                        selected[0],
                        selected[1],
                        [
                            max(value[2][member_index] for value in [selected, *tied])
                            for member_index in range(dimension)
                        ],
                    )
                return selected
            if any(len(group) != 1 for group in grouped_args):
                return reject(node_id, "non_scalar_arithmetic_argument")
            if args and all(is_constant(arg) for arg in args):
                actual = self._eval_node(node_id)
                if (
                    isinstance(actual, (int, float))
                    and math.isfinite(float(actual))
                ):
                    return constant(actual)
            if len(args) != 2:
                return reject(node_id, "unsupported_operation_arity")
            left, right = args
            if node.op == "+":
                return combine(left, right)
            if node.op == "-":
                return combine(left, right, -1.0)
            if node.op == "*":
                left_constant = is_constant(left)
                right_constant = is_constant(right)
                if not left_constant and not right_constant:
                    return reject(node_id, "bilinear_product")
                if left_constant:
                    return scale(right, left[1])
                return scale(left, right[1])
            if (
                node.op == "/"
                and is_constant(right)
                and abs(right[1]) > CONVERGENCE
            ):
                return scale(left, 1.0 / right[1])
            return reject(node_id, "unsupported_operation")

        matrix = []
        derivative_bounds = []
        constants = []
        for variable in members:
            root = self.graph.root_of(variable)
            if root is None:
                return {"certified": False, "reason": "missing_formula_root"}
            affine = derive(root, set())
            if affine is None:
                return {
                    "certified": False,
                    "reason": "unsupported_affine_structure",
                    "unsupported_nodes": unsupported_nodes,
                }
            matrix.append(affine[0])
            derivative_bounds.append(affine[2])
            constants.append(affine[1])
        if not all(
            math.isfinite(value)
            for row in matrix for value in row
        ) or not all(math.isfinite(value) for value in constants):
            return {"certified": False, "reason": "non_finite_affine_terms"}

        def matrix_multiply(left, right):
            return [
                [
                    sum(left[row][k] * right[k][col] for k in range(dimension))
                    for col in range(dimension)
                ]
                for row in range(dimension)
            ]

        def matrix_norm(value):
            return max(
                sum(abs(coefficient) for coefficient in row)
                for row in value
            )

        identity = [
            [1.0 if row == col else 0.0 for col in range(dimension)]
            for row in range(dimension)
        ]
        power = identity
        prefix_norm_sum = 0.0
        contraction_bound = None
        contraction_power = None
        inverse_norm_bound = None
        for exponent in range(1, min(2 * dimension, 100) + 1):
            prefix_norm_sum += matrix_norm(power)
            power = matrix_multiply(power, derivative_bounds)
            candidate = matrix_norm(power)
            if candidate < 1.0:
                contraction_bound = candidate
                contraction_power = exponent
                inverse_norm_bound = prefix_norm_sum / (1.0 - candidate)
                break
        contractive = contraction_power is not None
        if not contractive:
            return {
                "certified": False,
                "reason": "affine_system_not_bounded_contractive",
                "kind": "affine_system",
                "dimension": dimension,
                "contraction_bound": matrix_norm(derivative_bounds),
                "contractive": False,
            }

        # Banach's theorem gives a unique fixed point and the useful endpoint
        # error bound ||x-x*||∞ <= residual / (1-||A||∞).
        residual = self._equation_residual(members)[0]
        if equation_condition_proofs and residual is not None:
            # A constrained selector need not describe the map off its member
            # equations. Bound distance using the selected affine system too,
            # rather than borrowing only the original nonlinear-map residual.
            affine_residual = max(abs(sum(matrix[row][col] * float(self.values[members[col]])
                                             for col in range(dimension)) + constants[row]
                                      - float(self.values[members[row]]))
                                  for row in range(dimension))
            residual = max(residual, affine_residual)
        endpoint_error_bound = (
            None
            if residual is None
            else residual * inverse_norm_bound
        )
        if endpoint_error_bound is None:
            return {
                "certified": False,
                "reason": "missing_affine_endpoint_residual",
            }
        for selected, other, gap in branch_guards:
            guard_sensitivity = sum(
                abs(selected[0][index] - other[0][index])
                for index in range(dimension)
            )
            if gap <= guard_sensitivity * endpoint_error_bound:
                return {
                    "certified": False,
                    "reason": "unstable_piecewise_affine_branch",
                    "kind": "piecewise_affine_system",
                    "dimension": dimension,
                    "branch_gap": gap,
                    "branch_error_bound": (
                        guard_sensitivity * endpoint_error_bound
                    ),
                }
        if dimension == 1:
            coefficient = matrix[0][0]
            denominator = 1.0 - coefficient
            return {
                "certified": True,
                **({"constant_error_branches": constant_error_branches} if constant_error_branches else {}),
                **({"equation_condition_proofs": equation_condition_proofs} if equation_condition_proofs else {}),
                "kind": "scalar_affine",
                "coefficient": coefficient,
                "constant": constants[0],
                "fixed_point": constants[0] / denominator,
                "contractive": True,
                "contraction_bound": contraction_bound,
                "contraction_power": contraction_power,
                "endpoint_error_bound": endpoint_error_bound,
            }
        return {
            "certified": True,
                **({"constant_error_branches": constant_error_branches} if constant_error_branches else {}),
                **({"equation_condition_proofs": equation_condition_proofs} if equation_condition_proofs else {}),
            "kind": "affine_system",
            "dimension": dimension,
            "contractive": True,
            "contraction_bound": contraction_bound,
            "contraction_power": contraction_power,
            "inverse_norm_bound": inverse_norm_bound,
            "endpoint_error_bound": endpoint_error_bound,
        }

    # -- driver -----------------------------------------------------------
    def run(self, inputs: set) -> EvalResult:
        """Seed primitive graph values, rebuild formulas, and report the gaps."""
        run_started = time.perf_counter()
        cells = [
            cell for cell in self.cg.info
            if self.proof_scope is None or cell in self.proof_scope
        ]
        seed_categories = {
            "requested_frontier": set(),
            "primitive_input": set(),
            "primitive_text": set(),
            "label": set(),
            "formula_literal": set(),
            "formula_cache_rejected": set(),
            "requested_ineligible_rejected": set(),
            "implicit_seed_rejected": set(),
        }
        for cid in cells:
            info = self.cg.info[cid]
            requested = cid in inputs
            formula_cache_seed = info.node.kind == "formula" and (
                requested or info.is_literal
            )
            if self.strict_proof:
                if formula_cache_seed:
                    # Formula ``node.value`` is a saved workbook answer. Even
                    # when old frontier logic asks for it, strict proof must
                    # evaluate its AST (or fail) instead of seeding that answer.
                    seed_categories["formula_cache_rejected"].add(cid)
                if requested and info.node.kind in ("input", "label"):
                    self.values[cid] = primitive_value(info.node)
                    seed_categories["requested_frontier"].add(cid)
                    seed_categories[
                        "primitive_input" if info.node.kind == "input"
                        else "primitive_text"
                    ].add(cid)
                elif requested:
                    seed_categories["requested_ineligible_rejected"].add(cid)
                elif info.node.kind in ("input", "label") or info.is_literal:
                    seed_categories["implicit_seed_rejected"].add(cid)
                continue
            if requested or info.node.kind in ("input", "label") or info.is_literal:
                self.values[cid] = primitive_value(info.node)
                if requested:
                    seed_categories["requested_frontier"].add(cid)
                if info.node.kind == "input":
                    seed_categories["primitive_input"].add(cid)
                if info.node.kind == "label":
                    seed_categories["label"].add(cid)
                if info.is_literal:
                    seed_categories["formula_literal"].add(cid)
        seeded = set(self.values)
        # Excel initializes every iterative formula at zero. Doing this globally
        # for formulas also makes selector evaluation deterministic before the
        # first active dependency graph is built. Strict proof must not apply
        # that initialization to primitive cells: an unauthorized workbook
        # value is unknown, not zero.
        evaluable = {
            cid for cid in cells if self.cg.info[cid].node.kind == "formula"
        }
        for cid in cells:
            if cid not in seeded:
                self.values[cid] = (
                    0.0 if cid in evaluable
                    else Unresolved("unauthorized-primitive")
                    if self.strict_proof
                    else 0.0
                )
        indeterminate: set[str] = set()

        settings = self.calculation
        iterate_setting = getattr(settings, "iterate", None)
        # Preserve the evaluator's historical unknown-metadata behavior while
        # retaining enough provenance for verification to fail closed.
        iterate = True if iterate_setting is None else bool(iterate_setting)
        raw_limit = getattr(settings, "iterate_count", None)
        iteration_limit = (
            int(raw_limit)
            if isinstance(raw_limit, (int, float)) and int(raw_limit) > 0
            else EXCEL_DEFAULT_ITERATIONS
        )
        raw_delta = getattr(settings, "iterate_delta", None)
        iteration_delta = (
            float(raw_delta)
            if isinstance(raw_delta, (int, float)) and float(raw_delta) >= 0
            else EXCEL_DEFAULT_CHANGE
        )

        # First stabilize selectors, dynamic targets, and the active topology.
        # Cyclic components are deliberately not advanced in this phase: their
        # workbook iteration budget starts only after topology discovery.
        previous_graph = None
        previous_targets = None
        topology_stable = False
        targets_stable = False
        topology_history = []
        graph_passes = 0
        active_adj, active_radj = {}, {}
        groups, topo, cyclic = [], [], set()
        for graph_pass in range(1, MAX_ACTIVE_PASSES + 1):
            graph_passes = graph_pass
            if graph_pass == 1 or not self.optimize:
                self._resolved_targets = {}
                self._resolved_operation_targets = {}
                self.runtime_address_radj = {}
                active_adj, active_radj = self._active_graph(cells)
            else:
                # The previous pass's post-evaluation graph was built from the
                # exact values present here. No value, scope, or target-ledger
                # mutation occurs between these points.
                self._benchmark["active_graph"]["reused_initial_graphs"] += 1
            groups, topo, cyclic = self._component_plan(cells, active_adj)
            self._evaluate_acyclic(groups, topo, cyclic, evaluable, seeded)

            # Rebuild after the acyclic sweep so branch and address choices made
            # by newly computed selectors are what stabilization compares.
            self._resolved_targets = {}
            self._resolved_operation_targets = {}
            self.runtime_address_radj = {}
            active_adj, active_radj = self._active_graph(cells)
            graph_signature = self._graph_signature(active_adj)
            target_signature = self._target_signature()
            graph_same = graph_signature == previous_graph
            targets_same = target_signature == previous_targets
            topology_history.append({
                "pass": graph_pass,
                "edge_count": len(graph_signature),
                "resolved_target_count": sum(
                    len(sources) for _, sources in target_signature
                ),
                "topology_unchanged": graph_same,
                "targets_unchanged": targets_same,
            })
            topology_stable = graph_same
            targets_stable = targets_same
            if topology_stable and targets_stable:
                break
            previous_graph = graph_signature
            previous_targets = target_signature

        stabilized_graph_signature = self._graph_signature(active_adj)
        stabilized_target_signature = self._target_signature()
        groups, topo, cyclic = self._component_plan(cells, active_adj)
        self._evaluate_acyclic(groups, topo, cyclic, evaluable, seeded)

        if self.proof_outputs is None:
            relevant_cone = set(cells)
        else:
            relevant_cone = set(self.proof_outputs)
            stack = list(self.proof_outputs)
            while stack:
                target = stack.pop()
                for source in active_radj.get(target, ()):
                    if source not in relevant_cone:
                        relevant_cone.add(source)
                        stack.append(source)

        cycle_specs = []
        for index in sorted(cyclic, key=lambda item: self._cell_key(groups[item][0])):
            complete_members = tuple(groups[index])
            pending = tuple(
                cell for cell in complete_members
                if cell in evaluable and cell not in seeded
            )
            if not pending:
                continue
            cycle_specs.append({
                "members": complete_members,
                "pending": pending,
                "output_relevant": bool(set(complete_members) & relevant_cone),
                "history": [],
            })

        relevant_specs = [
            spec for spec in cycle_specs if spec["output_relevant"]
        ]
        reusable_values = self._cycle_independent_values(cells, seeded) if iterate and relevant_specs else frozenset()
        workbook_iterations = 0
        if iterate and relevant_specs:
            for workbook_iteration in range(1, iteration_limit + 1):
                workbook_iterations = workbook_iteration
                changes = {spec["members"]: 0.0 for spec in relevant_specs}
                for spec in relevant_specs:
                    for cell in spec["pending"]:
                        before = self.values[cell]
                        after = self._eval_cell(cell)
                        self.values[cell] = after
                        changes[spec["members"]] = max(
                            changes[spec["members"]],
                            self._value_change(before, after),
                        )
                # Recompute deterministic acyclic dependants between workbook
                # iterations without granting any SCC an extra advance.
                self._evaluate_acyclic(groups, topo, cyclic, evaluable, seeded, reusable_values)
                for spec in relevant_specs:
                    spec["history"].append(changes[spec["members"]])
                if all(
                    not math.isinf(changes[spec["members"]])
                    and changes[spec["members"]] <= iteration_delta
                    for spec in relevant_specs
                ):
                    break

        # The final graph must be the same graph whose cycles were iterated.
        self._resolved_targets = {}
        self._resolved_operation_targets = {}
        self.runtime_address_radj = {}
        final_active_adj, final_active_radj = self._active_graph(cells)
        final_graph_signature = self._graph_signature(final_active_adj)
        final_target_signature = self._target_signature()
        pre_iteration_topology_stable = topology_stable
        pre_iteration_targets_stable = targets_stable

        # Iteration may legitimately move an IF/MAX/lookup selector away from
        # its zero-initialized branch. Certify the graph at the converged values
        # instead of requiring it to equal that pre-iteration graph. Continue
        # coordinated value/topology sweeps until both values and active
        # dependencies stop changing, or the workbook's own iteration budget
        # is exhausted. Oscillating selectors therefore remain fail-closed.
        joint_topology_stable = (
            final_graph_signature == stabilized_graph_signature
        )
        joint_targets_stable = (
            final_target_signature == stabilized_target_signature
        )
        joint_values_stable = all(
            spec["history"]
            and not math.isinf(spec["history"][-1])
            and spec["history"][-1] <= iteration_delta
            for spec in relevant_specs
        )
        while (
            iterate
            and relevant_specs
            and not (
                joint_topology_stable
                and joint_targets_stable
                and joint_values_stable
            )
            and workbook_iterations < iteration_limit
        ):
            reference_graph = final_graph_signature
            reference_targets = final_target_signature
            groups, topo, cyclic = self._component_plan(
                cells, final_active_adj
            )
            changes = {spec["members"]: 0.0 for spec in relevant_specs}
            for spec in relevant_specs:
                for cell in spec["pending"]:
                    before = self.values[cell]
                    after = self._eval_cell(cell)
                    self.values[cell] = after
                    changes[spec["members"]] = max(
                        changes[spec["members"]],
                        self._value_change(before, after),
                    )
            self._evaluate_acyclic(
                groups, topo, cyclic, evaluable, seeded, reusable_values
            )
            workbook_iterations += 1
            for spec in relevant_specs:
                spec["history"].append(changes[spec["members"]])

            self._resolved_targets = {}
            self._resolved_operation_targets = {}
            self.runtime_address_radj = {}
            final_active_adj, final_active_radj = self._active_graph(cells)
            final_graph_signature = self._graph_signature(final_active_adj)
            final_target_signature = self._target_signature()
            joint_topology_stable = (
                final_graph_signature == reference_graph
            )
            joint_targets_stable = (
                final_target_signature == reference_targets
            )
            joint_values_stable = all(
                not math.isinf(changes[spec["members"]])
                and changes[spec["members"]] <= iteration_delta
                for spec in relevant_specs
            )

        canonical_resolved_targets = {
            target: set(sources) for target, sources in self._resolved_targets.items()
        }
        canonical_resolved_operation_targets = {
            operation: set(targets)
            for operation, targets in self._resolved_operation_targets.items()
        }
        canonical_runtime_address_radj = {
            target: set(sources)
            for target, sources in self.runtime_address_radj.items()
        }
        topology_stable = (
            pre_iteration_topology_stable and joint_topology_stable
        )
        targets_stable = (
            pre_iteration_targets_stable and joint_targets_stable
        )

        native_scalar_primitive_reads = {source: set(consumers)
                                         for source, consumers in self.scalar_primitive_reads.items()}
        cycle_diagnostics = []
        diagnostics_by_members = {}
        for spec in cycle_specs:
            members = spec["members"]
            pending = spec["pending"]
            output_relevant = spec["output_relevant"]
            residual, residual_errors, residual_unresolved = (
                self._equation_residual(pending)
                if output_relevant and iterate
                else (None, [], [])
            )
            current_errors = [
                {"cell": cell, "value": repr(self.values[cell]), "phase": "state"}
                for cell in pending if isinstance(self.values[cell], ExcelError)
            ]
            current_unresolved = [
                {
                    "cell": cell,
                    "value": repr(self.values[cell]),
                    "reason": self.values[cell].reason,
                    "phase": "state",
                }
                for cell in pending if isinstance(self.values[cell], Unresolved)
            ]
            equation_errors = [
                {**record, "phase": "equation"} for record in residual_errors
            ]
            equation_unresolved = [
                {**record, "phase": "equation"} for record in residual_unresolved
            ]
            error_records = current_errors + equation_errors
            unresolved_records = current_unresolved + equation_unresolved
            error_cells = sorted({record["cell"] for record in error_records})
            unresolved_cells = sorted({
                record["cell"] for record in unresolved_records
            })
            final_change = (
                spec["history"][-1] if spec["history"] else None
            )
            converged = (
                bool(iterate)
                and output_relevant
                and final_change is not None
                and not math.isinf(final_change)
                and final_change <= iteration_delta
                and residual is not None
                and residual <= iteration_delta
                and not error_records
                and not unresolved_records
            )
            certification = self._affine_cycle_certificate(pending)
            if not certification.get("certified") and len(pending) <= 128:
                from .cycle_regimes import certify as certify_finite_regime
                finite_regime = certify_finite_regime(self, pending)
                if finite_regime.get("certified"):
                    certification = finite_regime
            certified = bool(
                certification.get("certified")
                and certification.get("contractive")
            )
            diagnostic = {
                "size": len(members),
                "member_count": len(members),
                "members": list(members[:20]),
                "members_sample": list(members[:20]),
                "members_sample_count": min(len(members), 20),
                "seed": members[0],
                "graph_pass": graph_passes,
                "iterations": workbook_iterations if output_relevant and iterate else 0,
                "workbook_iterations": (
                    workbook_iterations if output_relevant and iterate else 0
                ),
                "iteration_enabled": bool(iterate),
                "iteration_limit": iteration_limit,
                "iteration_delta": iteration_delta,
                "evaluated": bool(output_relevant and iterate),
                "output_relevant": output_relevant,
                "converged": converged,
                "certified": certified,
                "certification": certification,
                "max_change": (
                    None if final_change is None or math.isinf(final_change)
                    else final_change
                ),
                "max_change_history": self._bounded_history([
                    None if math.isinf(change) else change
                    for change in spec["history"]
                ]),
                "max_change_history_complete_count": len(spec["history"]),
                "residual": residual,
                "errors": len(error_records),
                "error_members": error_cells[:20],
                "error_member_count": len(error_cells),
                "error_values": error_records[:20],
                "error_value_count": len(error_records),
                "unresolved": len(unresolved_records),
                "unresolved_members": unresolved_cells[:20],
                "unresolved_member_count": len(unresolved_cells),
                "unresolved_values": unresolved_records[:20],
                "unresolved_value_count": len(unresolved_records),
                "budget_exhausted": bool(
                    output_relevant
                    and iterate
                    and workbook_iterations >= iteration_limit
                    and not converged
                ),
                "topology_stable": topology_stable,
                "targets_stable": targets_stable,
                "uniqueness": (
                    "not_disproven" if converged and topology_stable and targets_stable
                    else "unknown"
                ),
                # Compatibility key: agreement never becomes ``True``.
                "unique": None,
                "reason": (
                    "detached-cycle"
                    if not output_relevant
                    else "iteration-disabled"
                    if not iterate
                    else "active-topology-not-stable"
                    if not topology_stable
                    else "runtime-targets-not-stable"
                    if not targets_stable
                    else "iteration-budget-exhausted"
                    if workbook_iterations >= iteration_limit and not converged
                    else "non-converged"
                    if not converged
                    else None
                ),
            }
            cycle_diagnostics.append(diagnostic)
            diagnostics_by_members[members] = diagnostic

        # A multi-start probe can exhibit a second fixed point, but agreement is
        # only "not_disproven".  All active SCCs share the probe's workbook
        # iteration counter and deterministic advance order as well.
        def strict_fixed_point_tolerance(spec, values):
            scale = max(
                [
                    abs(float(values[cell]))
                    for cell in spec["pending"]
                    if isinstance(values[cell], (int, float))
                    and not isinstance(values[cell], bool)
                    and math.isfinite(float(values[cell]))
                ]
                + [1.0]
            )
            return CONVERGENCE * scale

        probe_specs = [
            spec for spec in relevant_specs
            if diagnostics_by_members[spec["members"]]["converged"]
            and topology_stable
            and targets_stable
            and diagnostics_by_members[spec["members"]]["residual"]
            <= strict_fixed_point_tolerance(spec, self.values)
        ]
        if probe_specs and self.run_probes:
            canonical_values = dict(self.values)
            for spec in probe_specs:
                canonical = {
                    cell: canonical_values[cell] for cell in spec["pending"]
                }
                seed_value = 1.0
                if all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and abs(float(value) - seed_value) <= iteration_delta
                    for value in canonical.values()
                ):
                    seed_value = -1.0
                for cell in spec["pending"]:
                    self.values[cell] = seed_value

            probe_changes = {spec["members"]: math.inf for spec in probe_specs}
            probe_iterations = 0
            for probe_iteration in range(1, iteration_limit + 1):
                probe_iterations = probe_iteration
                for spec in probe_specs:
                    change = 0.0
                    for cell in spec["pending"]:
                        before = self.values[cell]
                        after = self._eval_cell(cell)
                        self.values[cell] = after
                        change = max(change, self._value_change(before, after))
                    probe_changes[spec["members"]] = change
                self._evaluate_acyclic(groups, topo, cyclic, evaluable, seeded)
                if all(
                    not math.isinf(probe_changes[spec["members"]])
                    and probe_changes[spec["members"]]
                    <= strict_fixed_point_tolerance(spec, self.values)
                    for spec in probe_specs
                ):
                    break

            alternate_values = dict(self.values)
            self._resolved_targets = {}
            self._resolved_operation_targets = {}
            probe_adj, _ = self._active_graph(cells)
            probe_graph_signature = self._graph_signature(probe_adj)
            probe_target_signature = self._target_signature()
            probe_global_safe = (
                probe_graph_signature == final_graph_signature
                and probe_target_signature == final_target_signature
            )
            probe_residuals = {}
            for spec in probe_specs:
                probe_residuals[spec["members"]] = self._equation_residual(
                    spec["pending"]
                )
            probe_global_safe = probe_global_safe and all(
                residual is not None
                and residual <= strict_fixed_point_tolerance(spec, alternate_values)
                and not errors
                and not unresolved
                for spec in probe_specs
                for residual, errors, unresolved in [
                    probe_residuals[spec["members"]]
                ]
            )

            for spec in probe_specs:
                members = spec["members"]
                diagnostic = diagnostics_by_members[members]
                diagnostic["probe_iterations"] = probe_iterations
                diagnostic["probe_converged"] = probe_global_safe
                if not probe_global_safe:
                    diagnostic["uniqueness"] = "unknown"
                    continue
                distinct = any(
                    self._value_change(
                        canonical_values[cell], alternate_values[cell]
                    ) > iteration_delta
                    for cell in spec["pending"]
                )
                if not distinct:
                    continue
                diagnostic.update({
                    "unique": False,
                    "uniqueness": "demonstrated_non_unique",
                    "reason": "non-unique-fixed-point",
                    "canonical_fixed_point": {
                        cell: canonical_values[cell] for cell in spec["pending"][:20]
                    },
                    "alternate_fixed_point": {
                        cell: alternate_values[cell] for cell in spec["pending"][:20]
                    },
                    "alternate_fixed_point_member_count": len(spec["pending"]),
                })
            self.values = canonical_values
            self._resolved_targets = canonical_resolved_targets
            self._resolved_operation_targets = (
                canonical_resolved_operation_targets
            )
            self.runtime_address_radj = canonical_runtime_address_radj

        # Preserve the canonical proof endpoint before compatibility unresolved
        # markers are applied to disabled or demonstrated-non-unique cycles.
        self._proof_endpoint_values = dict(self.values)
        self._proof_active_radj = final_active_radj
        self._proof_resolved_targets = {
            target: set(sources)
            for target, sources in canonical_resolved_targets.items()
        }
        self._proof_resolved_operation_targets = {
            operation: set(targets)
            for operation, targets in canonical_resolved_operation_targets.items()
        }
        for spec in relevant_specs:
            diagnostic = diagnostics_by_members[spec["members"]]
            if not iterate:
                reason = "circular-reference"
            elif diagnostic["uniqueness"] == "demonstrated_non_unique":
                reason = "non-unique-circular-reference"
            else:
                continue
            for cell in spec["pending"]:
                self.values[cell] = Unresolved(reason)
                indeterminate.add(cell)

        cycle_diagnostics.sort(key=lambda item: self._cell_key(item["seed"]))
        cycle_groups = {
            index: tuple(spec["pending"])
            for index, spec in enumerate(cycle_specs, 1)
        }
        cycle_membership = {
            cell: index
            for index, members_in_cycle in cycle_groups.items()
            for cell in members_in_cycle
        }
        iterated = cycle_diagnostics

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
            "iteration_setting": iterate_setting,
            "iteration_limit": iteration_limit,
            "iteration_delta": iteration_delta,
            "workbook_iterations": workbook_iterations,
            "active_cycles": len(cycle_diagnostics),
            "active_topology_stable": topology_stable,
            "runtime_targets_stable": targets_stable,
            "active_topology_history": topology_history,
            "calculation_metadata": {
                "available": getattr(settings, "available", False),
                "iterate": iterate_setting,
                "iterate_origin": getattr(settings, "iterate_origin", "unknown"),
                "iterate_count": raw_limit,
                "iterate_count_origin": getattr(
                    settings, "iterate_count_origin", "unknown"
                ),
                "iterate_delta": raw_delta,
                "iterate_delta_origin": getattr(
                    settings, "iterate_delta_origin", "unknown"
                ),
                "raw_calc_pr": getattr(settings, "raw_calc_pr", {}),
                "reason": getattr(settings, "reason", ""),
            },
            "strict_proof": self.strict_proof,
            "expected_cache_reads": sum(
                len(v) for v in self.oracle_accesses.values()
            ),
            "runtime_read_cells": len(self.read_attempts),
            "missing_read_cells": len(self.missing_reads),
            "stable_self_cycles": sum(
                diagnostic["size"] == 1 and diagnostic["converged"]
                and diagnostic.get("uniqueness") != "demonstrated_non_unique"
                for diagnostic in cycle_diagnostics
            ),
        }
        # The final pass's active graph carries the dependencies actually read,
        # including runtime-resolved dynamic references (OFFSET targets) that
        # the static edge list cannot see. Verification walks the output cone
        # over these edges as well as the static ones.
        runtime_radj = final_active_radj
        runtime_address_radj = {
            target: set(sources)
            for target, sources in self.runtime_address_radj.items()
        }
        self._benchmark["run_seconds"] = time.perf_counter() - run_started
        benchmark = {
            name: dict(value) if isinstance(value, dict) else value
            for name, value in self._benchmark.items()
        }
        return EvalResult(
            self.values, self.unresolved, iterated, coverage,
            runtime_radj=runtime_radj,
            runtime_address_radj=runtime_address_radj,
            resolved_targets={
                target: tuple(sorted(sources))
                for target, sources in sorted(self._resolved_targets.items())
            },
            resolved_operation_targets={
                operation: tuple(sorted(targets))
                for operation, targets in sorted(
                    self._resolved_operation_targets.items()
                )
            },
            oracle_reads=self.oracle_reads.snapshot(),
            oracle_accesses=self.oracle_accesses.snapshot(),
            read_attempts=self.read_attempts.snapshot(),
            scalar_primitive_reads=native_scalar_primitive_reads,
            missing_reads=self.missing_reads.snapshot(),
            seed_provenance={
                "effective": {
                    "count": len(seeded),
                    "sample": sorted(seeded)[:20],
                },
                "categories": {
                    name: {
                        "count": len(ids),
                        "sample": sorted(ids)[:20],
                    }
                    for name, ids in sorted(seed_categories.items())
                },
            },
            seeded_cells=seeded,
            evaluated_cells=set(cells),
            cycle_membership=cycle_membership,
            cycle_groups=cycle_groups,
            endpoint_equation_checker=self._endpoint_equation_check,
            strict_proof=self.strict_proof,
            benchmark=benchmark,
        )

    @staticmethod
    def _value_change(before, after):
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            if math.isfinite(float(before)) and math.isfinite(float(after)):
                return abs(float(after) - float(before))
        return 0.0 if type(before) is type(after) and repr(before) == repr(after) else math.inf

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

    def _iterate_group(self, pending, iteration_limit, iteration_delta):
        max_change = math.inf
        refine_delta = min(iteration_delta, 1e-11)
        converged_step = None
        step = -1
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

    def _eval_cell(self, cid: str):
        self._current_cell = cid
        info = self.cg.info[cid]
        root = self.graph.root_of(cid)
        if root is not None:
            value = self._eval_node(root)
            if isinstance(value, RangeValues):
                node = info.node
                if getattr(node, "array_ref", ""):
                    member_row = getattr(node, "array_row", None)
                    member_col = getattr(node, "array_col", None)
                    member_row = 0 if member_row is None else member_row
                    member_col = 0 if member_col is None else member_col
                    if (
                        member_row < 0 or member_col < 0
                        or member_row >= value.rows or member_col >= value.cols
                    ):
                        return ExcelError("#N/A")
                    index = member_row * value.cols + member_col
                    return value[index] if index < len(value) else ExcelError("#N/A")
                # Legacy graphs did not persist array-member coordinates. Keep
                # their historical anchor behavior while new graphs project
                # every CSE/spill member through the metadata above.
                if getattr(node, "array_span", ""):
                    return self._array_element(node, value)
                return value[0] if value else None
            # A blank reference becomes numeric zero only when Excel stores
            # the scalar formula result. Keep primitive/range blanks as None
            # so INDEX reference contexts and aggregations retain their semantics.
            # Unresolved precedents, errors, and explicit empty strings survive.
            return 0.0 if value is None else value
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


def _xml_bool(value):
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return None


def workbook_calculation_metadata(path) -> CalculationMetadata:
    """Read only OOXML calculation settings, never worksheet cached values."""
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError:
        return CalculationMetadata(
            available=False, source=str(path), reason="file_not_found"
        )
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        return CalculationMetadata(
            available=False, source=str(path), reason="source_not_regular_file"
        )
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise OSError("source_not_regular_file")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                with zipfile.ZipFile(handle) as archive:
                    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        return CalculationMetadata(
            available=False,
            source=str(path),
            reason=f"{type(exc).__name__}: {exc}",
        )
    calc = root.find("{*}calcPr")
    if calc is None:
        return CalculationMetadata(
            available=True,
            iterate=False,
            source=str(path),
            reason="calcPr_absent",
            iterate_origin="default",
            iterate_count_origin="default",
            iterate_delta_origin="default",
            calc_mode_origin="default",
            full_calc_on_load_origin="default",
            force_full_calc_origin="default",
            raw_calc_pr={},
        )
    count_raw = calc.attrib.get("iterateCount")
    delta_raw = calc.attrib.get("iterateDelta")
    try:
        count = int(count_raw) if count_raw else None
        count_valid = (
            count_raw is None
            or 0 < count <= MAX_WORKBOOK_ITERATIONS
        )
        if not count_valid:
            count = None
    except (TypeError, ValueError):
        count = None
        count_valid = False
    try:
        delta = float(delta_raw) if delta_raw else None
        delta_valid = (
            delta_raw is None
            or math.isfinite(delta)
            and 0 <= delta <= MAX_ITERATION_DELTA
        )
        if not delta_valid:
            delta = None
    except (TypeError, ValueError):
        delta = None
        delta_valid = False
    raw = {str(key): str(value) for key, value in sorted(calc.attrib.items())}
    iterate_raw = calc.attrib.get("iterate")
    parsed_iterate = _xml_bool(iterate_raw)
    calc_mode_raw = calc.attrib.get("calcMode")
    parsed_calc_mode = (
        calc_mode_raw
        if calc_mode_raw in {"auto", "autoNoTable", "manual"}
        else None
    )
    full_calc_raw = calc.attrib.get("fullCalcOnLoad")
    parsed_full_calc = _xml_bool(full_calc_raw)
    force_full_raw = calc.attrib.get("forceFullCalc")
    parsed_force_full = _xml_bool(force_full_raw)
    return CalculationMetadata(
        available=True,
        iterate=parsed_iterate if iterate_raw is not None else False,
        iterate_count=count,
        iterate_delta=delta,
        calc_mode=parsed_calc_mode,
        full_calc_on_load=parsed_full_calc,
        force_full_calc=parsed_force_full,
        source=str(path),
        iterate_origin=(
            "unknown"
            if iterate_raw is not None and parsed_iterate is None
            else "explicit"
            if iterate_raw is not None
            else "default"
        ),
        iterate_count_origin=(
            "unknown"
            if not count_valid
            else "explicit"
            if count_raw is not None
            else "default"
        ),
        iterate_delta_origin=(
            "unknown"
            if not delta_valid
            else "explicit"
            if delta_raw is not None
            else "default"
        ),
        calc_mode_origin=(
            "unknown"
            if calc_mode_raw is not None and parsed_calc_mode is None
            else "explicit"
            if calc_mode_raw is not None
            else "default"
        ),
        full_calc_on_load_origin=(
            "unknown"
            if full_calc_raw is not None and parsed_full_calc is None
            else "explicit"
            if full_calc_raw is not None
            else "default"
        ),
        force_full_calc_origin=(
            "unknown"
            if force_full_raw is not None and parsed_force_full is None
            else "explicit"
            if force_full_raw is not None
            else "default"
        ),
        raw_calc_pr=raw,
    )


class CachedEmptyString(str):
    """Comparison-only evidence for an explicitly cached empty formula string."""

    def __new__(cls):
        return super().__new__(cls, "")


def _empty_string_formula_caches(path):
    # openpyxl maps both absent <v> and t="str"/<v/> to None. Preserve only
    # the latter, which source health already recognizes as a populated cache.
    # This reader is never installed in the reconstruction evaluator.
    import posixpath

    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    result = {}
    with zipfile.ZipFile(path) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {}
        for record in relationships.findall(f"{{{rel}}}Relationship"):
            identity = record.get("Id")
            if identity in targets:
                raise ValueError("duplicate worksheet relationship identity")
            targets[identity] = record
        parts = set()
        for sheet in workbook.findall(f"{{{main}}}sheets/{{{main}}}sheet"):
            name = sheet.get("name")
            record = targets.get(sheet.get(f"{{{office}}}id"))
            if record is None or record.get("TargetMode", "Internal") != "Internal":
                raise ValueError("missing or external worksheet relationship")
            if record.get("Type") != office + "/worksheet":
                continue  # Chartsheets have no cell caches.
            target = record.get("Target", "")
            part = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
            if not target or part.startswith("../") or part == ".." or name in result or part in parts:
                raise ValueError("ambiguous worksheet cache identity")
            parts.add(part)
            empty = set()
            seen = set()
            with archive.open(part) as stream:
                for _, cell in ElementTree.iterparse(stream, events=("end",)):
                    if cell.tag != f"{{{main}}}c":
                        continue
                    coordinate = cell.get("r", "")
                    if coordinate in seen:
                        raise ValueError("duplicate worksheet cache coordinate")
                    seen.add(coordinate)
                    values = cell.findall(f"{{{main}}}v")
                    formulas = cell.findall(f"{{{main}}}f")
                    match = A1_RE.fullmatch(coordinate)
                    if (match and cell.get("t") == "str" and len(formulas) == 1
                            and len(values) == 1 and values[0].text in (None, "")
                            and not len(values[0])):
                        empty.add((int(match.group(2)), col_number(match.group(1))))
                    cell.clear()
            result[name] = empty
    return result


class ExpectedValueCache:
    """Comparison-only workbook cache with explicit read provenance."""

    def __init__(self, path, book):
        self.path = str(path)
        self.book = book
        self.cache: dict = {}
        self.reads: dict = {}
        self.empty_string_caches = None

    def __call__(self, sheet, row, col):
        cid = a1(sheet, row, col)
        self.reads[cid] = self.reads.get(cid, 0) + 1
        if sheet not in self.cache:
            if sheet not in self.book.sheetnames:
                self.cache[sheet] = {}
            else:
                grid = {}
                for line in self.book[sheet].iter_rows():
                    for cell in line:
                        if cell.value is not None:
                            grid[(cell.row, cell.column)] = cell.value
                self.cache[sheet] = grid
        value = self.cache[sheet].get((row, col))
        if value is None:
            if self.empty_string_caches is None:
                self.empty_string_caches = _empty_string_formula_caches(self.path)
            if (row, col) in self.empty_string_caches.get(sheet, ()):
                return CachedEmptyString()
        if isinstance(value, datetime):
            return _datetime_to_serial(value)
        return value

    def close(self):
        self.book.close()


def workbook_expected_cache(path):
    """Open the saved-value cache for post-proof comparison only."""
    try:
        import openpyxl
    except ImportError:
        return None
    try:
        book = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception:
        return None
    return ExpectedValueCache(path, book)


def workbook_oracle(path):
    """Backward-compatible combined cache; strict proof never uses this API."""
    cache = workbook_expected_cache(path)
    if cache is None:
        return None
    calculation = workbook_calculation_metadata(path)
    cache.iterate = calculation.iterate
    cache.iterate_count = calculation.iterate_count
    cache.iterate_delta = calculation.iterate_delta
    return cache


def compare(expected: str, actual, tolerance=1e-6):
    """Relative comparison against the workbook's cached value."""
    if isinstance(actual, Unresolved):
        return "unresolved", None
    if isinstance(expected, CachedEmptyString):
        return ("match" if isinstance(actual, str) and actual == "" else "mismatch"), None
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
