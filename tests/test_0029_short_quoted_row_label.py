import ast
import json
import re
from pathlib import Path
import pytest

FILE=Path(__file__).parents[1]/'diagnostics/authoring-queue-extension-20260911T185027Z/0029-dialogue-period-repair/aa_lib.py'

def covered():
    nodes=[]
    for node in ast.parse(FILE.read_text()).body:
        if isinstance(node,ast.FunctionDef) and node.name in {'normalize','_stem','clause_covered'}:nodes.append(node)
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='STOPWORDS' for t in node.targets):nodes.append(node)
    ns={'json':json,'re':re};exec(compile(ast.Module(body=nodes,type_ignores=[]),str(FILE),'exec'),ns);return ns['clause_covered']

def test_actual_short_source_label_possessive_is_covered():
    f=covered();assert f('the row labelled "(–) D&A"','Only for this cell, test whether last period’s row labelled "(–) D&A" is nonzero.')

@pytest.mark.parametrize('text',['the text literal "(–) D&A"','the row labelled "(–) D&B"','D&A is our topic','the row labelled "(–) D&A extra"'])
def test_short_label_requires_exact_typed_identity(text):
    assert not covered()('the row labelled "(–) D&A"',text)

def test_formula_literal_still_requires_explicit_type():
    f=covered();assert not f('the text literal "X"','the row labelled "X"');assert f('the text literal "X"','the text literal "X"')

def test_existing_long_clause_and_missing_clause_unchanged():
    f=covered();assert f('the row labelled "Revenue"','Revenue is the row to use');assert not f('multiply by the growth factor','Revenue is the row to use')
