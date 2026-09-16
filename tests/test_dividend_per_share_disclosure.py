"""A per-share dividend amount must never be described as an earnings ratio."""
import importlib.util
from pathlib import Path
import openpyxl
import pytest


@pytest.fixture
def D(monkeypatch):
    file=Path(__file__).resolve().parents[1]/'.cursor/skills/task-disclosure/scripts/disclose.py'
    monkeypatch.syspath_prepend(str(file.parent));s=importlib.util.spec_from_file_location('dividend_disclosure',file)
    d=importlib.util.module_from_spec(s);s.loader.exec_module(d);return d


def record(D,tmp_path,first,second,formula='=L40*L39'):
    book=openpyxl.Workbook();sheet=book.active;sheet.title='Acc-Dil'
    for cell,value in {'B39':first,'L39':1.2,'B40':second,'L40':100,
                       'B41':'Target dividends','L41':formula}.items():sheet[cell]=value
    path=tmp_path/'source.xlsx';book.save(path);gold=D.Book(path)
    rows=D.detect_distribution_policy(gold,{'Acc-Dil!L41'},set())
    assert len(rows)==1
    return rows[0],D.render_sentence(rows[0])


@pytest.mark.parametrize('dividend,shares',[('Target 2011E dividend per share','Target basic shares'),
    ('Acquiror 2011E dividend per share','New shares issued')])
def test_source_dividend_product_keeps_both_operands(D,tmp_path,dividend,shares):
    row,sentence=record(D,tmp_path,dividend,shares)
    assert row['value']=='formula_mechanics'
    assert dividend in sentence and shares in sentence
    assert 'earnings'not in sentence.lower() and 'multipl' in sentence.lower()


@pytest.mark.parametrize('formula',['=L40*L39','=L39*L40'])
def test_true_earnings_payout_retains_named_rule(D,tmp_path,formula):
    row,sentence=record(D,tmp_path,'Dividend payout ratio','Net income',formula)
    assert row['value']=='payout_ratio'and 'earnings'in sentence.lower()


@pytest.mark.parametrize('other',['Available cash','Net income per share','EPS'])
def test_payout_label_alone_cannot_invent_earnings_basis(D,tmp_path,other):
    row,sentence=record(D,tmp_path,'Dividend payout ratio',other)
    assert row['value']=='formula_mechanics'and other in sentence
