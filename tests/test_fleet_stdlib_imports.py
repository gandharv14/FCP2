from pathlib import Path
import subprocess
import sys

HELPER = Path(__file__).resolve().parents[1] / "scripts/fleet/stdlib_imports.py"


def cold(tmp_path, body):
    shadow = tmp_path / "inspect.py"
    shadow.write_text("raise RuntimeError('task inspect must not execute')\n")
    bootstrap = (
        "import importlib.util,sys,json,types\n"
        f"s=importlib.util.spec_from_file_location('selected_stdlib', {str(HELPER)!r})\n"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
        f"sys.path.insert(0,{str(tmp_path)!r})\n"
        + body
    )
    return subprocess.run([sys.executable, "-I", "-B", "-c", bootstrap],
                          capture_output=True, text=True, timeout=30)


def test_cold_shadowed_path_still_loads_real_asyncio_dependencies(tmp_path):
    result = cold(tmp_path, "assert 'inspect' not in sys.modules\n"
                  "a=m.preload_inspect();import asyncio,dataclasses\n"
                  "from inspect import isgenerator\n"
                  "assert isgenerator((x for x in ()))\n"
                  "assert m.preload_inspect() is a\nprint('PASS')\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS"


def test_unprepared_cold_import_reproduces_shadow_failure(tmp_path):
    result = cold(tmp_path, "assert 'inspect' not in sys.modules\nimport asyncio\n")
    assert result.returncode != 0
    assert "task inspect must not execute" in result.stderr


def test_foreign_cached_module_is_rejected_without_replacement(tmp_path):
    result = cold(tmp_path, "bad=types.ModuleType('inspect');bad.__file__='/tmp/foreign.py'\n"
                  "sys.modules['inspect']=bad\n"
                  "try:m.preload_inspect()\n"
                  "except ValueError:assert sys.modules['inspect'] is bad;print('PASS')\n"
                  "else:raise AssertionError('foreign module accepted')\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS"


def test_real_cached_module_is_preserved(tmp_path):
    result = cold(tmp_path, "sys.path.pop(0);import inspect\n"
                  "assert m.preload_inspect() is inspect\nprint('PASS')\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS"


def test_wrong_cached_spec_cannot_borrow_real_filename(tmp_path):
    result = cold(tmp_path, "sys.path.pop(0);import inspect\n"
                  "inspect.__spec__.origin='/tmp/foreign.py'\n"
                  "try:m.preload_inspect()\n"
                  "except ValueError:print('PASS')\n"
                  "else:raise AssertionError('foreign origin accepted')\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS"
