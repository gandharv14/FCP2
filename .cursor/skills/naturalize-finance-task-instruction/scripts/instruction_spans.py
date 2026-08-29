#!/usr/bin/env python3
"""Byte-accurate editable spans for finance-task instructions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


UTF8_BOM = b"\xef\xbb\xbf"
SPAN_VERSION = "instruction-spans-v2"
_OPEN_FENCE_RE = re.compile(rb"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING_RE = re.compile(rb"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
_INPUT_RE = re.compile(
    rb"^ {0,3}##[ \t]+Input(?:[ \t]+#+)?[ \t]*$", re.IGNORECASE
)


class InstructionSpanError(ValueError):
    """A structured, fail-closed span discovery error."""

    def __init__(self, reason_code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "reason_code": self.reason_code,
            "error": str(self),
        }
        if self.details:
            result["details"] = self.details
        return result


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ByteSpan:
    start: int
    end: int
    sha256: str

    @classmethod
    def from_source(cls, source: bytes, start: int, end: int) -> "ByteSpan":
        return cls(start=start, end=end, sha256=sha256_bytes(source[start:end]))

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "length": self.end - self.start,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    start: int
    body_start: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "title": self.title,
            "start": self.start,
            "body_start": self.body_start,
        }


@dataclass(frozen=True)
class InstructionSpans:
    source_sha256: str
    source_size: int
    bom: bool
    newline: str
    final_newline: bool
    preamble_body: ByteSpan
    input_body: ByteSpan
    fenced_blocks: tuple[ByteSpan, ...]
    headings: tuple[Heading, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": SPAN_VERSION,
            "source_sha256": self.source_sha256,
            "source_size": self.source_size,
            "bom": self.bom,
            "newline": self.newline,
            "final_newline": self.final_newline,
            "spans": {
                "preamble_body": self.preamble_body.as_dict(),
                "input_body": self.input_body.as_dict(),
            },
            "fenced_blocks": [span.as_dict() for span in self.fenced_blocks],
            "headings": [heading.as_dict() for heading in self.headings],
        }


def _lines(data: bytes, start: int = 0) -> Iterator[tuple[int, int, bytes]]:
    position = start
    while position < len(data):
        match = re.search(rb"\r\n|\n|\r", data[position:])
        if match is None:
            yield position, len(data), data[position:]
            return
        newline_start = position + match.start()
        end = position + match.end()
        yield position, end, data[position:newline_start]
        position = end


def _newline_name(source: bytes) -> str:
    match = re.search(rb"\r\n|\n|\r", source)
    if not match:
        return "none"
    return {"\r\n": "crlf", "\n": "lf", "\r": "cr"}[
        match.group(0).decode("ascii")
    ]


def _newline_bytes(name: str) -> bytes:
    return {"crlf": b"\r\n", "lf": b"\n", "cr": b"\r", "none": b"\n"}[name]


def _has_final_newline(source: bytes) -> bool:
    return source.endswith((b"\n", b"\r"))


def scan_instruction(source: bytes) -> InstructionSpans:
    """Find the only two editable bodies without decoding or rewriting source."""
    if not isinstance(source, bytes):
        raise TypeError("source must be raw bytes")
    try:
        source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InstructionSpanError(
            "invalid_utf8",
            "instruction is not valid UTF-8",
            offset=exc.start,
        ) from exc

    bom_size = len(UTF8_BOM) if source.startswith(UTF8_BOM) else 0
    headings: list[Heading] = []
    input_indexes: list[int] = []
    fence: tuple[int, int, int] | None = None
    fenced_blocks: list[ByteSpan] = []

    for line_start, line_end, content in _lines(source, bom_size):
        if fence is not None:
            marker, minimum, fence_start = fence
            close_re = rb"^ {0,3}" + bytes([marker]) + rb"{" + str(minimum).encode() + rb",}[ \t]*$"
            if re.match(close_re, content):
                fenced_blocks.append(
                    ByteSpan.from_source(source, fence_start, line_end)
                )
                fence = None
            continue

        opening = _OPEN_FENCE_RE.match(content)
        if opening:
            run = opening.group(1)
            # CommonMark does not allow a backtick in backtick-fence info strings.
            if run[:1] != b"`" or b"`" not in opening.group(2):
                fence = (run[0], len(run), line_start)
                continue

        heading_match = _HEADING_RE.match(content)
        if not heading_match:
            continue
        title_bytes = heading_match.group(2)
        try:
            title = title_bytes.decode("utf-8").strip()
        except UnicodeDecodeError as exc:  # guarded by the whole-document decode
            raise InstructionSpanError("invalid_utf8", "heading is not valid UTF-8") from exc
        title = re.sub(r"[ \t]+#+[ \t]*$", "", title)
        heading = Heading(
            level=len(heading_match.group(1)),
            title=title,
            start=line_start,
            body_start=line_end,
        )
        headings.append(heading)
        if _INPUT_RE.match(content):
            input_indexes.append(len(headings) - 1)

    if fence is not None:
        raise InstructionSpanError(
            "unclosed_fence",
            "instruction contains an unclosed fenced code block",
        )
    if not input_indexes:
        raise InstructionSpanError(
            "missing_input_heading",
            "instruction has no eligible level-two Input heading outside fences",
        )
    if len(input_indexes) != 1:
        raise InstructionSpanError(
            "duplicate_input_heading",
            "instruction must contain exactly one eligible level-two Input heading outside fences",
            count=len(input_indexes),
        )

    input_index = input_indexes[0]
    first_heading_start = headings[0].start
    input_heading = headings[input_index]
    input_end = (
        headings[input_index + 1].start
        if input_index + 1 < len(headings)
        else len(source)
    )
    preamble_boundary = re.search(
        rb"(?:[ \t]*(?:\r\n|\n|\r))+\Z",
        source[bom_size:first_heading_start],
    )
    preamble_end = (
        bom_size + preamble_boundary.start()
        if preamble_boundary is not None
        else first_heading_start
    )
    input_prefix = re.match(
        rb"(?:[ \t]*(?:\r\n|\n|\r))+",
        source[input_heading.body_start:input_end],
    )
    input_start = input_heading.body_start + (
        input_prefix.end() if input_prefix is not None else 0
    )
    input_boundary = re.search(
        rb"(?:[ \t]*(?:\r\n|\n|\r))+\Z",
        source[input_start:input_end],
    )
    trimmed_input_end = (
        input_start + input_boundary.start()
        if input_boundary is not None
        else input_end
    )
    preamble_span = ByteSpan.from_source(source, bom_size, preamble_end)
    input_span = ByteSpan.from_source(source, input_start, trimmed_input_end)
    if preamble_span.end > input_span.start:
        raise InstructionSpanError(
            "overlapping_spans",
            "editable instruction spans overlap",
        )

    return InstructionSpans(
        source_sha256=sha256_bytes(source),
        source_size=len(source),
        bom=bool(bom_size),
        newline=_newline_name(source),
        final_newline=_has_final_newline(source),
        preamble_body=preamble_span,
        input_body=input_span,
        fenced_blocks=tuple(fenced_blocks),
        headings=tuple(headings),
    )


def _normalize_replacement(value: bytes | str, newline: str, label: str) -> bytes:
    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, bytes):
        raw = value
    else:
        raise TypeError("%s replacement must be bytes or str" % label)
    if raw.startswith(UTF8_BOM) or UTF8_BOM in raw:
        raise InstructionSpanError(
            "replacement_contains_bom",
            "%s replacement must not contain a UTF-8 BOM" % label,
            span=label,
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InstructionSpanError(
            "invalid_replacement_utf8",
            "%s replacement is not valid UTF-8" % label,
            span=label,
            offset=exc.start,
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", _newline_bytes(newline).decode("ascii")).encode("utf-8")


def _verify_snapshot(source: bytes, spans: InstructionSpans) -> None:
    if len(source) != spans.source_size or sha256_bytes(source) != spans.source_sha256:
        raise InstructionSpanError(
            "source_hash_mismatch",
            "source bytes do not match the immutable span snapshot",
            expected_sha256=spans.source_sha256,
            actual_sha256=sha256_bytes(source),
        )
    for label, span in (
        ("preamble_body", spans.preamble_body),
        ("input_body", spans.input_body),
    ):
        actual = sha256_bytes(source[span.start : span.end])
        if actual != span.sha256:
            raise InstructionSpanError(
                "span_hash_mismatch",
                "%s bytes do not match the span snapshot" % label,
                span=label,
                expected_sha256=span.sha256,
                actual_sha256=actual,
            )


def assemble_instruction(
    source: bytes,
    spans: InstructionSpans,
    preamble_body: bytes | str,
    input_body: bytes | str,
) -> bytes:
    """Replace only the two editable bodies and retain all other source bytes."""
    _verify_snapshot(source, spans)
    preamble = _normalize_replacement(
        preamble_body, spans.newline, "preamble_body"
    )
    input_replacement = _normalize_replacement(input_body, spans.newline, "input_body")
    result = b"".join(
        (
            source[: spans.preamble_body.start],
            preamble,
            source[spans.preamble_body.end : spans.input_body.start],
            input_replacement,
            source[spans.input_body.end :],
        )
    )
    if result.startswith(UTF8_BOM) != spans.bom:
        raise InstructionSpanError("bom_changed", "UTF-8 BOM preservation failed")
    if _has_final_newline(result) != spans.final_newline:
        raise InstructionSpanError(
            "final_newline_changed",
            "final-newline presence must match the source",
        )
    return result


def extract_editable_bodies(
    document: bytes, spans: InstructionSpans | None = None
) -> tuple[bytes, bytes]:
    current = spans or scan_instruction(document)
    _verify_snapshot(document, current)
    return (
        document[current.preamble_body.start : current.preamble_body.end],
        document[current.input_body.start : current.input_body.end],
    )


def write_spans_json(path: Path, spans: InstructionSpans) -> None:
    rendered = json.dumps(spans.as_dict(), indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8")
