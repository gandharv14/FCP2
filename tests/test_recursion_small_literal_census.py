import collections
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from recursion_small_literal_census import Book,constant_arithmetic,literals


def test_e_column_references_are_not_scientific_notation_literals():
    assert not constant_arithmetic('=E43-E55')
    assert not constant_arithmetic('=1-E249')
    assert constant_arithmetic('=-843-19')
    assert constant_arithmetic('=1.5E3+19')
    assert constant_arithmetic('=44727*49%')
    assert [x['value'] for x in literals('=44727*49%')]==[44727,0.49]


def test_only_reachable_formula_hosts_and_counterfactual_impact():
    b=Book.__new__(Book)
    b.cells={'Model!A1':945.,'Model!A2':100.,'Model!A3':9.45,'Model!A4':19.,'Model!A5':38.}
    b.formulas={'Model!A1':'=926+19','Model!A3':'=A1/A2','Model!A5':'=A4*2'}
    b.names={};b.ast={};b.sheetcells={'Model':[(1,i,'Model!A'+str(i)) for i in range(1,6)]}
    cone,reverse,unresolved=b.cone(['Model!A3'])
    assert cone=={'Model!A1','Model!A2','Model!A3'} and not unresolved
    assert b.calculate(['Model!A3'],'Model!A1',926.,reverse)=={'Model!A3':9.26}
    assert b.cells['Model!A1']==945. and b.formulas['Model!A1']=='=926+19'


def test_unresolved_dynamic_reference_is_explicit():
    b=Book.__new__(Book);b.cells={'Model!A1':1};b.formulas={'Model!A1':'=INDIRECT("B1")'}
    b.names={};b.ast={};b.sheetcells={'Model':[(1,1,'Model!A1')]}
    _,_,errors=b.cone(['Model!A1'])
    assert {'cell':'Model!A1','reason':'dynamic_reference'} in errors


def test_embedded_scope_includes_stripped_blank_host_cells():
    from recursion_small_literal_census import embedded
    b=Book.__new__(Book)
    b.cells={'Embedded Assumptions!A2':'Blank Sheet','Embedded Assumptions!B2':'I7:O7','Embedded Assumptions!C2':'Count','Embedded Assumptions!D2':8,'Embedded Assumptions!E2':'=4+4'}
    b.sheetcells={}
    result=embedded(b)
    assert len(result)==7
    assert result['Blank Sheet!L7'][0]['formula']=='=4+4'
    assert 'Blank Sheet!H7' not in result


def test_approved_record_status_case_is_normalized(tmp_path):
    import gzip,json
    from recursion_small_literal_census import records_for
    runtime=tmp_path/'runtime';runtime.mkdir()
    row={'id':'source1','status':'approved','scenario':'base case','basis':'Externally sourced assumption','metric':'Model!A1 input','value':19}
    with gzip.open(runtime/'records.jsonl.gz','wt') as f:f.write(json.dumps(row)+'\n')
    matching,_=records_for(tmp_path,['Model!A1'],{19})
    assert matching['Model!A1'][0]['id']=='source1'
