"""Load an explicitly bound original author helper before its pinned dispatcher.

Prospective utility only: callers must supply both bindings from their closed,
root-selected authority. This does not infer an original helper from a path or
supply authority to a legacy plan. Existing frozen callers remain unchanged.
"""
from pathlib import Path
import sys


def load(P, driver, author_helper, *, namespace):
    P.require(type(namespace) is str and bool(namespace) and namespace != 'author_workbook',
              'dedicated dispatcher module namespace required')
    P.require(set(driver) == set(author_helper) == {'path', 'sha256'},
              'explicit closed driver and original helper bindings required')
    P.require(Path(author_helper['path']).name == 'author_workbook.py',
              'selected original author helper required')
    # Verify BOTH before importing either, so a bad driver cannot execute even
    # the helper as a side effect of an ultimately rejected selection.
    P.require(P.binding(driver['path']) == driver and P.binding(author_helper['path']) == author_helper,
              'selected dispatcher/helper changed before import')
    cached = sys.modules.get('author_workbook')
    P.require(cached is None or Path(getattr(cached, '__file__', '')).absolute() == Path(author_helper['path']),
              'foreign cached original author helper')
    P.load('author_workbook', author_helper)
    return P.load(namespace, driver)
