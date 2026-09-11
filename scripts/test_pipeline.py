"""Run unit, hidden skill, and optionally private-corpus integration checks."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-roots", nargs="+")
    parser.add_argument("--host-controls", action="store_true",
                        help="also probe real macOS sandbox controls; requires host permission")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error("the pipeline test runtime requires Python 3.12")
    env = dict(os.environ)
    if args.corpus_roots:
        env["FCP_CORPUS_ROOTS"] = os.pathsep.join(str(Path(p).resolve(strict=True))
                                               for p in args.corpus_roots)
    marker = "not real_excel" + ("" if args.corpus_roots else " and not private_corpus")
    if not args.host_controls:
        marker += " and not host_controls"
    commands = [
        [sys.executable, "-m", "pytest", "-q", "-m", marker,
         "tests", "grader/tests", "test_evaluate.py", "test_input_mask.py",
         ".cursor/skills/task-disclosure/scripts",
         ".cursor/skills/profile-mcp-sources/scripts/test_assemble_profiles.py"],
        [sys.executable, ".cursor/skills/additional-assumptions-dialogue/scripts/test_dialogue_regressions.py"],
    ]
    outcomes = [subprocess.run(command, cwd=ROOT, env=env).returncode for command in commands]
    return 1 if any(outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
