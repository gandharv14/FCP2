"""extract_tool_commands: the controller's command log from a stream-json transcript."""

from __future__ import annotations

from pathlib import Path

from scripts.batch.hardened_runner import extract_tool_commands

FIXTURE = Path(__file__).parent / "fixtures" / "stream_toolcall_v1.jsonl"


def test_extract_tool_commands_from_fixture() -> None:
    records = extract_tool_commands(FIXTURE.read_text(encoding="utf-8"))
    by_id = {record["call_id"]: record for record in records}

    # Order of first appearance is kept; started and completed are merged.
    assert [record["call_id"] for record in records] == ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    shell = by_id["c1"]
    assert shell["kind"] == "shellToolCall"
    assert shell["command"] == "python xl_source_health.py --workbook 0001"
    assert shell["cwd"] == "/w/task-0001"
    assert shell["exit_code"] == 0 and shell["ok"] is True and shell["subtype"] == "completed"
    # A failed command keeps its exit code; that is what a.md must not hide.
    assert by_id["c4"]["exit_code"] == 2
    # File tools record the path only.
    assert by_id["c3"] == {"call_id": "c3", "kind": "editToolCall", "subtype": "completed", "path": "xl_seg/emit.py", "ok": True}
    assert by_id["c5"]["path"] == "a.md"
    # A call that never completed stays at started with no exit code.
    assert by_id["c6"]["subtype"] == "started" and "exit_code" not in by_id["c6"]
    # Unknown tool kinds and error results do not raise.
    assert by_id["c7"]["kind"] == "newFancyToolCall" and by_id["c7"]["ok"] is False


def test_extract_tool_commands_tolerates_garbage() -> None:
    assert extract_tool_commands("") == []
    assert extract_tool_commands("not json\n{\"type\": \"tool_call\"}\n{\"type\": \"tool_call\", \"tool_call\": {}}\n") == []
    anonymous = extract_tool_commands(
        '{"type": "tool_call", "subtype": "started", "tool_call": {"shellToolCall": {"args": {"command": "ls"}}}}\n'
        '{"type": "tool_call", "subtype": "started", "tool_call": {"shellToolCall": {"args": {"command": "pwd"}}}}\n'
    )
    assert [item["command"] for item in anonymous] == ["ls", "pwd"]
    assert [item["call_id"] for item in anonymous] == ["anonymous-1", "anonymous-2"]
