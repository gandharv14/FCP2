"""Canonical isolated Python entry for prospective direct fleet scripts."""
from pathlib import Path
import re
import os


def command(python, driver, *arguments):
    return [str(python), '-I', '-B', str(driver), *map(str, arguments)]


def environment(python, inherited):
    """Keep the selected interpreter's lexical tool wrappers ahead of the OS."""
    return {**inherited, 'PATH':str(Path(python).absolute().parent)+os.pathsep+inherited.get('PATH',os.defpath)}


def validate(argv, driver):
    """Reject the proven direct-script shape that exposes fleet/inspect.py.

    Unrelated legacy wrappers and non-fleet scripts keep their own contracts.
    The flag must precede the script; an application argument cannot enable it.
    """
    path = Path(driver)
    if path.suffix != '.py' or path.parent.name != 'fleet' or path.parent.parent.name != 'scripts':
        return
    if not argv or not re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', Path(argv[0]).name):
        return
    if '-c' in argv[1:] or '-m' in argv[1:]:
        return
    position = next((i for i, value in enumerate(argv[1:], 1) if not value.startswith('-')), None)
    if position is not None and argv[position] == str(driver) and '-I' not in argv[1:position]:
        raise ValueError('direct scripts/fleet Python entry requires isolated -I before driver')
