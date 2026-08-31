#!/usr/bin/env python3
"""Turn unstripped custom-method AST English into spoken, A1-free clauses."""

from __future__ import annotations

import re


CELL_PHRASE_RE = re.compile(
    r"(?:fixed\s+)?(?P<kind>cell|range)\s+"
    r"(?P<sheet>'[^']+'|[A-Za-z0-9_][A-Za-z0-9_ .&-]*)!"
    r"(?P<a>\$?[A-Z]{1,3}\$?\d{1,7})"
    r"(?::(?P<b>\$?[A-Z]{1,3}\$?\d{1,7}))?"
    r"(?:\s+on the rows? labelled\s+(?P<label>(?:\"[^\"]+\")(?:(?:,\s*\"[^\"]+\")*(?:\s+and\s+\"[^\"]+\"))?))?",
    re.I,
)
ABS_REF_RE = re.compile(r"\$([A-Z]{1,3})\$?(\d{1,7})", re.I)
A1_RE = re.compile(r"\$?([A-Z]{1,3})\$?(\d{1,7})", re.I)
LABEL_RE = re.compile(r'"([^"]+)"')
NEEDS_SPOKEN_RE = re.compile(r"\b(?:copied-column|cell |range )", re.I)
SHOWN_FOR_RE = re.compile(r"\s*,?\s*shown for\b.*$", re.I)
UNQUOTED_STRING_LITERALS = frozenset({"", "N/A", "#N/A", "NA"})


def col_to_num(letters: str) -> int:
    n = 0
    for ch in (letters or "").upper():
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - 64)
    return n


def parse_a1(coord: str) -> tuple[str, int] | None:
    match = A1_RE.fullmatch((coord or "").strip())
    if not match:
        return None
    return match.group(1).upper(), int(match.group(2))


def parse_ref_key(text: str) -> tuple[str, str, int] | None:
    if "!" not in (text or ""):
        return None
    sheet, coord = text.rsplit("!", 1)
    parsed = parse_a1(coord.split(":")[0])
    if not parsed:
        return None
    return sheet.strip().strip("'"), parsed[0], parsed[1]


def clean_sheet(sheet: str) -> str:
    return (sheet or "").strip().strip("'")


def all_labels(raw: str) -> list[str]:
    return [item.strip() for item in LABEL_RE.findall(raw or "") if item.strip()]


def named_rows(labels: list[str]) -> str:
    if not labels:
        return "the figure"
    if len(labels) == 1:
        return f'the row labelled "{labels[0]}"'
    if len(labels) == 2:
        return f'the rows labelled "{labels[0]}" and "{labels[1]}"'
    return (
        "the rows labelled "
        + ", ".join(f'"{item}"' for item in labels[:-1])
        + f', and "{labels[-1]}"'
    )


def record_evidence(record: dict) -> str:
    fields = record.get("fields") or {}
    profile = record.get("method_profile") or {}
    parts = [
        record.get("evidence") or "",
        profile.get("formula") or "",
        " ".join(str(item) for item in (profile.get("raw_references") or [])),
        fields.get("formula") or "",
    ]
    return " ".join(str(part) for part in parts if part)


def absolute_keys(evidence: str) -> set[tuple[str, int]]:
    return {(m.group(1).upper(), int(m.group(2))) for m in ABS_REF_RE.finditer(evidence or "")}


def needs_spoken(record: dict) -> bool:
    fields = record.get("fields") or {}
    blob = " ".join(
        str(part or "")
        for part in (
            fields.get("steps"),
            record.get("rendered"),
            record.get("sentence"),
        )
    )
    return bool(NEEDS_SPOKEN_RE.search(blob))


def collect_phrases(steps: str) -> list[dict]:
    found = []
    for match in CELL_PHRASE_RE.finditer(steps or ""):
        start = parse_a1(match.group("a"))
        end = parse_a1(match.group("b") or "") if match.group("b") else start
        if not start:
            continue
        found.append({
            "text": match.group(0),
            "kind": match.group("kind").lower(),
            "sheet": clean_sheet(match.group("sheet")),
            "col": start[0],
            "row": start[1],
            "end_col": end[0] if end else start[0],
            "end_row": end[1] if end else start[1],
            "labels": all_labels(match.group("label") or ""),
        })
        found[-1]["label"] = found[-1]["labels"][0] if found[-1]["labels"] else ""
    return found


def period_word(col: str, rep_col: str) -> str:
    left = col_to_num(col)
    right = col_to_num(rep_col)
    if left < right:
        return "last period's"
    if left > right:
        return "next period's"
    return "this period's"


def is_locked(phrase: dict, evidence: str) -> bool:
    keys = absolute_keys(evidence)
    return (phrase["col"], phrase["row"]) in keys or (
        phrase["kind"] == "range" and (phrase["end_col"], phrase["end_row"]) in keys
    )


def spoken_locator(phrase: dict, phrases: list[dict], representative: str, evidence: str) -> str:
    label = phrase["label"]
    label_cells = {
        (item["sheet"], item["col"], item["row"])
        for item in phrases
        if item.get("label") == label and label
    }
    unique = bool(label) and len(label_cells) == 1
    parsed_rep = parse_ref_key(representative)
    rep_sheet = parsed_rep[0] if parsed_rep else ""
    rep_col = parsed_rep[1] if parsed_rep else ""
    rep_row = parsed_rep[2] if parsed_rep else 0
    locked = is_locked(phrase, evidence)
    off_sheet = bool(rep_sheet and phrase["sheet"] and phrase["sheet"] != rep_sheet)
    tab = f" on the {phrase['sheet']} tab" if off_sheet else ""

    if phrase["kind"] == "range" and phrase["col"] != phrase["end_col"] and label and rep_col:
        left = period_word(phrase["col"], rep_col)
        right = period_word(phrase["end_col"], rep_col)
        if left != right:
            return f"{left} and {right} row labelled \"{label}\"{tab}"

    named = named_rows(phrase.get("labels") or ([label] if label else []))
    if unique and not locked:
        return named + tab
    if locked:
        if label:
            return f"the locked input on {named}{tab}"
        return f"the locked input{tab}"
    if not rep_col:
        return named + tab
    period = period_word(phrase["col"], rep_col)
    if not label:
        return f"{period} figure{tab}"
    same_period = {
        (item["sheet"], item["col"], item["row"])
        for item in phrases
        if item.get("label") == label
        and not is_locked(item, evidence)
        and period_word(item["col"], rep_col) == period
    }
    row_name = named[4:] if named.startswith("the ") else named
    if len(same_period) > 1 and phrase["row"] != rep_row:
        side = "a few lines above" if phrase["row"] < rep_row else "a few lines below"
        return f"{period} {row_name} {side}{tab}"
    return f"{period} {row_name}{tab}"


def replace_phrases(steps: str, representative: str, evidence: str) -> str:
    phrases = collect_phrases(steps)
    out = steps or ""
    for phrase in sorted(phrases, key=lambda item: len(item["text"]), reverse=True):
        out = out.replace(phrase["text"], spoken_locator(phrase, phrases, representative, evidence))
    return out


class _Parser:
    def __init__(self, text: str):
        self.s = text or ""
        self.i = 0

    def skip(self) -> None:
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def peek(self, lit: str) -> bool:
        self.skip()
        return self.s.startswith(lit, self.i)

    def eat(self, lit: str) -> bool:
        self.skip()
        if not self.s.startswith(lit, self.i):
            return False
        self.i += len(lit)
        return True

    def remaining(self) -> str:
        return self.s[self.i:]

    def parse_atom(self) -> dict:
        self.skip()
        start = self.i
        depth = 0
        in_quote = False
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == '"':
                in_quote = not in_quote
            elif not in_quote:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        break
                    depth -= 1
                elif depth == 0 and ch == ";":
                    break
            self.i += 1
        text = self.s[start:self.i].strip()
        return {"op": "atom", "text": text}

    def parse_operand(self) -> dict:
        self.skip()
        if self.eat("("):
            node = self.parse_expr()
            conjunctions = [node]
            while self.eat("and "):
                conjunctions.append(self.parse_expr())
            if len(conjunctions) > 1:
                node = {"op": "and", "args": conjunctions}
            self.skip()
            if not self.eat(")"):
                rest = self.parse_atom()
                joined = (node.get("text") or "") + " " + rest.get("text", "")
                return {"op": "atom", "text": joined.strip()}
            return node
        return self.parse_atom()

    def try_operands(self, count: int) -> list[dict] | None:
        mark = self.i
        args = []
        for _ in range(count):
            if not self.peek("("):
                self.i = mark
                return None
            args.append(self.parse_operand())
        return args

    def parse_and_list_after(self, first: dict) -> list[dict]:
        args = [first]
        while self.eat("and "):
            if self.peek("("):
                args.append(self.parse_operand())
            else:
                args.append(self.parse_atom())
                break
        return args

    def parse_expr(self) -> dict:
        self.skip()
        if self.eat("when "):
            cond = self.parse_operand()
            self.eat("is true, use ")
            yes = self.parse_operand()
            self.eat(";")
            self.eat("otherwise use ")
            no = self.parse_operand() if self.peek("(") else self.parse_atom()
            return {"op": "if", "args": [cond, yes, no]}
        if self.eat("use ") and self.peek("("):
            mark = self.i - 4
            first = self.parse_operand()
            if self.eat(",") and self.eat("or use "):
                second = self.parse_operand()
                self.eat("if that calculation errors")
                return {"op": "iferror", "args": [first, second]}
            if self.eat("as the option number, then choose "):
                return {"op": "atom", "text": "use " + _plain(first) + " as the option number, then choose " + self.remaining().strip()}
            self.i = mark
        if self.eat("take the greater of "):
            first = self.parse_operand()
            return {"op": "max", "args": self.parse_and_list_after(first)}
        if self.eat("take the lesser of "):
            first = self.parse_operand()
            return {"op": "min", "args": self.parse_and_list_after(first)}
        if self.eat("take the mean of "):
            first = self.parse_operand()
            return {"op": "avg", "args": self.parse_and_list_after(first)}
        if self.eat("take the negative of "):
            return {"op": "neg", "args": [self.parse_operand()]}
        if self.eat("multiply "):
            a = self.parse_operand()
            self.eat("by ")
            b = self.parse_operand()
            return {"op": "mul", "args": [a, b]}
        if self.eat("subtract "):
            b = self.parse_operand()
            self.eat("from ")
            a = self.parse_operand()
            return {"op": "sub", "args": [a, b]}
        if self.eat("divide "):
            a = self.parse_operand()
            self.eat("by ")
            b = self.parse_operand()
            return {"op": "div", "args": [a, b]}
        if self.eat("raise "):
            a = self.parse_operand()
            self.eat("to the power ")
            b = self.parse_operand()
            return {"op": "pow", "args": [a, b]}
        if self.eat("add "):
            first = self.parse_operand()
            return {"op": "add", "args": self.parse_and_list_after(first)}
        if self.eat("join "):
            a = self.parse_operand()
            self.eat("with ")
            b = self.parse_operand()
            return {"op": "join", "args": [a, b]}
        if self.eat("round "):
            a = self.parse_operand()
            self.eat("to the nearest multiple of ")
            b = self.parse_operand()
            return {"op": "round", "args": [a, b]}
        if self.peek("("):
            mark = self.i
            left = self.parse_operand()
            for op, word in (
                ("eq", "equals "),
                ("ne", "does not equal "),
                ("gt", "is greater than "),
                ("lt", "is less than "),
                ("ge", "is at least "),
                ("le", "is at most "),
            ):
                if self.eat(word):
                    right = self.parse_operand() if self.peek("(") else self.parse_atom()
                    return {"op": op, "args": [left, right]}
            self.i = mark
            return self.parse_operand()
        return self.parse_atom()


def _clause_text(node: dict) -> str:
    if not node:
        return ""
    if node.get("op") == "atom":
        text = (node.get("text") or "").strip()
        literal = re.fullmatch(r'"([^"]*)"', text)
        if literal and literal.group(1) in UNQUOTED_STRING_LITERALS:
            return literal.group(1)
        return text
    core, suffix = _linearize(node)
    return "; ".join(part for part in [core, *suffix] if part)


def _plain(node: dict) -> str:
    return _clause_text(node)


def _wrap(node: dict) -> str:
    text = _clause_text(node)
    if not text:
        return ""
    if _simple(node) or node.get("op") == "atom":
        return text
    if text.startswith("(") and text.endswith(")"):
        return text
    return f"({text})"


def _is_zero(node: dict) -> bool:
    return node.get("op") == "atom" and (node.get("text") or "").strip() == "0"


def _is_neg_one(node: dict) -> bool:
    if node.get("op") == "neg":
        inner = (node.get("args") or [None])[0] or {}
        return inner.get("op") == "atom" and (inner.get("text") or "").strip() == "1"
    return node.get("op") == "atom" and (node.get("text") or "").strip() in {"-1", "negative 1"}


def _simple(node: dict) -> bool:
    if node.get("op") == "atom":
        return True
    if node.get("op") in {"neg"} and _is_neg_one(node):
        return True
    return False


def _join_and(parts: list[str]) -> str:
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _locked_role(node: dict, role: str) -> str:
    text = _plain(node)
    if "locked input" not in text.lower():
        return text
    return re.sub(
        r"\bthe locked input\b",
        f"the {role} locked input",
        text,
        count=1,
        flags=re.I,
    )


CMP = {
    "eq": "equals",
    "ne": "does not equal",
    "gt": "is greater than",
    "lt": "is less than",
    "ge": "is at least",
    "le": "is at most",
}


def _linearize(node: dict) -> tuple[str, list[str]]:
    op = node.get("op")
    args = node.get("args") or []
    if op == "atom":
        text = (node.get("text") or "").strip()
        literal = re.fullmatch(r'"([^"]*)"', text)
        if literal and literal.group(1) in UNQUOTED_STRING_LITERALS:
            return literal.group(1), []
        return text, []
    if op == "neg":
        inner = args[0] if args else {"op": "atom", "text": ""}
        if inner.get("op") == "atom" and re.fullmatch(r"-?\d+(?:\.\d+)?", (inner.get("text") or "").strip()):
            num = (inner.get("text") or "").strip()
            return (num if num.startswith("-") else f"-{num}"), []
        core, suffix = _linearize(inner)
        if suffix or not _simple(inner):
            return core, suffix + ["flip the sign"]
        return f"flip the sign of {core}", []
    if op == "max":
        if len(args) == 2 and _is_zero(args[0]):
            core, suffix = _linearize(args[1])
            return core, suffix + ["floor that at zero"]
        if len(args) == 2 and _is_zero(args[1]):
            core, suffix = _linearize(args[0])
            return core, suffix + ["floor that at zero"]
        parts = [_wrap(arg) for arg in args]
        return f"take the greater of {_join_and(parts)}", []
    if op == "min":
        parts = [_wrap(arg) for arg in args]
        return f"take the smaller of {_join_and(parts)}", []
    if op == "avg":
        parts = [_plain(arg) for arg in args]
        return f"the average of {_join_and(parts)}", []
    if op == "mul":
        a, b = args[0], args[1]
        if _is_neg_one(b):
            core, suffix = _linearize(a)
            return core, suffix + ["flip the sign"]
        if _is_neg_one(a):
            core, suffix = _linearize(b)
            return core, suffix + ["flip the sign"]
        if not _simple(a):
            core, suffix = _linearize(a)
            return core, suffix + [f"multiply by {_wrap(b)}"]
        if not _simple(b):
            core, suffix = _linearize(b)
            return core, suffix + [f"multiply by {_wrap(a)}"]
        return f"multiply {_plain(a)} by {_plain(b)}", []
    if op == "sub":
        return f"{_wrap(args[0])} minus {_wrap(args[1])}", []
    if op == "add":
        return " plus ".join(_wrap(arg) for arg in args), []
    if op == "and":
        return _join_and([_wrap(arg) for arg in args]), []
    if op == "div":
        return f"{_wrap(args[0])} divided by {_wrap(args[1])}", []
    if op == "pow":
        return f"{_wrap(args[0])} raised to {_wrap(args[1])}", []
    if op == "if":
        cond = _plain(args[0])
        yes = _locked_role(args[1], "result")
        if not _simple(args[1]):
            yes = f"({yes})"
        no = _wrap(args[2]) if len(args) > 2 else "false"
        return f"if {cond}, use {yes}; otherwise {no}", []
    if op == "iferror":
        return f"use {_plain(args[0])}, or use {_plain(args[1])} if that errors", []
    if op == "join":
        return f"join {_plain(args[0])} with {_plain(args[1])}", []
    if op == "round":
        return f"round {_plain(args[0])} to the nearest multiple of {_plain(args[1])}", []
    if op in CMP:
        role = (
            "lower bound"
            if op in {"gt", "ge"}
            else "upper bound"
            if op in {"lt", "le"}
            else ""
        )
        right = _locked_role(args[1], role) if role else _plain(args[1])
        return f"{_plain(args[0])} {CMP[op]} {right}", []
    return _plain(args[0]) if args else (node.get("text") or ""), []


def flatten_spoken(text: str) -> str:
    parser = _Parser(text)
    try:
        tree = parser.parse_expr()
        parser.skip()
        leftover = parser.remaining().strip()
        core, suffix = _linearize(tree)
        spoken = "; ".join([part for part in [core, *suffix] if part])
        if leftover and leftover not in spoken:
            spoken = f"{spoken} {leftover}".strip()
        return re.sub(r"\s+", " ", spoken).strip(" :;")
    except Exception:
        return re.sub(r"\s+", " ", text or "").strip()


def speak_steps(
    steps: str,
    representative: str = "",
    evidence: str = "",
    sheet: str = "",
    row_label: str = "",
) -> str:
    if not (steps or "").strip():
        return ""
    replaced = replace_phrases(steps, representative, evidence)
    spoken = flatten_spoken(replaced)
    spoken = SHOWN_FOR_RE.sub("", spoken).strip(" :,")
    if row_label and sheet and not re.match(r"^on\s+", spoken, re.I):
        spoken = (
            f'On {sheet}, the row labelled "{row_label}" is copied across the forecast: {spoken}'
        )
    if spoken and not spoken.endswith("."):
        spoken += "."
    return spoken


def speak_record(record: dict) -> str:
    from aa_lib import clean_label, sheet_from_cells

    fields = record.get("fields") or {}
    cells = list(record.get("cells") or [])
    if not cells and fields.get("representative"):
        cells = [fields["representative"]]
    return speak_steps(
        str(fields.get("steps") or ""),
        representative=str(fields.get("representative") or ""),
        evidence=record_evidence(record),
        sheet=sheet_from_cells(cells),
        row_label=clean_label(fields.get("label") or record.get("label") or ""),
    )
