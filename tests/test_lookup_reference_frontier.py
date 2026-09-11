"""Frozen-v11 false lookup cycles: preserve values, narrow proven dependencies."""
import pytest
from openpyxl import Workbook
from xl_ast_graph import AstGraph,write_csv
from xl_seg import model,project
from xl_seg.evaluate import CalculationMetadata,Evaluator,Unresolved
from xl_segment import stabilize_runtime_proof


def run(tmp_path,operation='HLOOKUP',key='2',index='2',mode='TRUE',keys=(1,2,3),returns=(10,20,30),outside=False,extras=None,cache='999999'):
    tmp_path.mkdir(parents=True,exist_ok=True)
    h=operation=='HLOOKUP'; cells={}
    for i,(k,v) in enumerate(zip(keys,returns),1):
        cells[f'{chr(64+i)}1' if h else f'A{i}']=k
        cells[f'{chr(64+i)}2' if h else f'B{i}']=v
    cells.update(extras or {})
    target='E6' if outside else 'B3' if h else 'C2'
    formula=f'={operation}({key},A1:C3,{index},{mode})'
    w=Workbook();s=w.active;s.title='Sheet'
    for c,v in cells.items():s[c]=v
    s[target]=formula;file=tmp_path/'fixture.xlsx';w.save(file);w.close()
    ast=AstGraph(file,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    g=model.load(tmp_path,'fixture');g.nodes['Sheet!'+target].value=cache
    seed={'Sheet!'+c for c,v in cells.items() if not(isinstance(v,str) and v.startswith('='))}
    result,inputs,_,proof=stabilize_runtime_proof(g,project.build(g),seed,{'Sheet!'+target},
                         calculation=CalculationMetadata(available=True,iterate=False))
    return g,result,inputs,proof,'Sheet!'+target


@pytest.mark.parametrize('op',['HLOOKUP','VLOOKUP'])
@pytest.mark.parametrize('key,mode,expected',[('2','TRUE',20),('2','FALSE',20),('2.5','TRUE',20),('2.5','FALSE','#N/A'),('0','TRUE','#N/A')])
def test_unselected_self_does_not_create_cycle(tmp_path,op,key,mode,expected):
    g,result,inputs,proof,target=run(tmp_path,op,key=key,mode=mode)
    value=result.values[target]
    assert str(value)==expected if isinstance(expected,str) else value==expected
    assert target not in proof['runtime_radj'][target]
    search={'Sheet!A1','Sheet!B1','Sheet!C1'} if op=='HLOOKUP' else {'Sheet!A1','Sheet!A2','Sheet!A3'}
    assert search <= set(proof['runtime_radj'][target])
    assert not result.oracle_reads
    assert target not in inputs
    assert target not in result.cycle_membership


@pytest.mark.parametrize('op',['HLOOKUP','VLOOKUP'])
@pytest.mark.parametrize('mode',['TRUE','FALSE'])
def test_selected_self_remains_rejected(tmp_path,op,mode):
    _,result,_,proof,target=run(tmp_path,op,index='3',mode=mode)
    assert isinstance(result.values[target],Unresolved)
    assert target in proof['runtime_radj'][target]


@pytest.mark.parametrize('op',['HLOOKUP','VLOOKUP'])
@pytest.mark.parametrize('keys,key,mode,expected',[
    ((3,1,2),'2.5','TRUE',30),
    ((2,2,3),'2','FALSE',10),
    (('A','B','C'),'"B"','FALSE',20),
    ((None,2,3),'2','FALSE',20),
])
def test_key_semantics_unchanged(tmp_path,op,keys,key,mode,expected):
    _,result,_,_,target=run(tmp_path,op,keys=keys,key=key,mode=mode)
    assert result.values[target]==expected


@pytest.mark.parametrize('op',['HLOOKUP','VLOOKUP'])
@pytest.mark.parametrize('index,key,mode,keys,returns,expected',[
    ('0','2','TRUE',(1,2,3),(10,20,30),'#REF!'),
    ('4','2','TRUE',(1,2,3),(10,20,30),'#REF!'),
    ('2','1/0','TRUE',(1,2,3),(10,20,30),'#DIV/0!'),
    ('2','2','1/0',(1,2,3),(10,20,30),'#DIV/0!'),
    ('2','2','FALSE',(1,2,3),(10,'=1/0',30),'#DIV/0!'),
    ('2','2','FALSE',('=1/0',2,3),(10,20,30),20),
])
def test_existing_error_behavior_outside_table(tmp_path,op,index,key,mode,keys,returns,expected):
    _,result,_,_,target=run(tmp_path,op,index=index,key=key,mode=mode,keys=keys,returns=returns,outside=True)
    value=result.values[target]
    assert str(value)==expected if isinstance(expected,str) else value==expected


@pytest.mark.parametrize('op',['HLOOKUP','VLOOKUP'])
@pytest.mark.parametrize('cache',['-987654321','', '0'])
def test_formula_key_index_mode_have_complete_transitive_frontier(tmp_path,op,cache):
    extras={'I1':'=J1','I2':'=J2','I3':'=J3','J1':2,'J2':2,'J3':False}
    _,result,inputs,proof,target=run(tmp_path,op,key='I1',index='I2',mode='I3',extras=extras,cache=cache)
    assert result.values[target]==20
    assert {'Sheet!J1','Sheet!J2','Sheet!J3'} <= inputs
    assert {'Sheet!I1','Sheet!I2','Sheet!I3'} <= set(proof['runtime_radj'][target])
    assert not result.oracle_reads
    assert target not in inputs


def test_plan_never_reuses_old_selected_result_for_changed_key(tmp_path):
    g,_,_,_,target=run(tmp_path,extras={'J1':2},key='J1')
    e=Evaluator(g,project.build(g)); e.values.update({'Sheet!A1':1,'Sheet!B1':2,'Sheet!C1':3,'Sheet!J1':2})
    op=next(n for n in g.nodes.values() if n.op=='HLOOKUP')
    assert e._lookup_reference_plan(op)[2]=='Sheet!B2'
    e.values['Sheet!J1']=3
    assert e._lookup_reference_plan(op)[2]=='Sheet!C2'
    e.values['Sheet!J1']=0
    assert e._lookup_reference_plan(op)[2] is None
    e.values['Sheet!J1']=Unresolved('unproven')
    assert e._lookup_reference_plan(op) is None


@pytest.mark.parametrize('coordinate,truncated',[
    ('A1:C3',True),('A1:XFD1048576',False),('A1:A10001',False),
    ('A:A',False),('A0:C3',False),('A1:XFE3',False),('A1:C1048577',False),
    ('Other!A1:C3',False),
])
def test_range_rejected_before_any_reference_expansion(tmp_path,monkeypatch,coordinate,truncated):
    from dataclasses import replace
    from xl_seg import evaluate
    g,_,_,_,_=run(tmp_path)
    op=next(n for n in g.nodes.values() if n.op=='HLOOKUP')
    e=Evaluator(g,project.build(g))
    source=replace(g.nodes['Sheet!A1'],id='test-range',kind='range',coordinate=coordinate,range_truncated=truncated)
    g.nodes[source.id]=source
    specs=list(e._arg_specs(op));specs[1]=('scalar',source.id);e._arg_cache[op.id]=specs
    def forbidden(*args,**kwargs):raise AssertionError('unapproved reference expansion')
    monkeypatch.setattr(e,'_reference_arg',forbidden)
    monkeypatch.setattr(evaluate,'range_members',forbidden)
    assert e._lookup_reference_plan(op) is None


@pytest.mark.parametrize('kind,refs',[
    ('many',['Sheet!A1','Other!B1']), ('many',['Sheet!A1','Sheet!C1']),
    ('many',['Sheet!A1','malformed']), ('many',['Sheet!A1','Sheet!A1']),
    ('many',['Sheet!A1','Sheet!A2','Sheet!C1']),
    ('many',['Sheet!A1']*10001), ('span',['Sheet!A1']*10001),
])
def test_malformed_many_or_span_never_expands(tmp_path,monkeypatch,kind,refs):
    g,_,_,_,_=run(tmp_path)
    e=Evaluator(g,project.build(g));op=next(n for n in g.nodes.values() if n.op=='HLOOKUP')
    specs=list(e._arg_specs(op));specs[1]=(kind,refs);e._arg_cache[op.id]=specs
    def forbidden(*args,**kwargs):raise AssertionError('unapproved reference expansion')
    monkeypatch.setattr(e,'_reference_arg',forbidden)
    assert e._lookup_reference_plan(op) is None


def test_same_evaluator_replans_changed_index_and_mode(tmp_path):
    g,_,_,_,_=run(tmp_path,key='J1',index='J2',mode='J3',extras={'J1':2.5,'J2':2,'J3':True})
    e=Evaluator(g,project.build(g));e.values.update({'Sheet!A1':1,'Sheet!B1':2,'Sheet!C1':3,'Sheet!J1':2.5,'Sheet!J2':2,'Sheet!J3':True})
    op=next(n for n in g.nodes.values() if n.op=='HLOOKUP')
    assert e._lookup_reference_plan(op)[2]=='Sheet!B2'
    e.values['Sheet!J2']=3
    assert e._lookup_reference_plan(op)[2]=='Sheet!B3'
    e.values['Sheet!J3']=False
    assert e._lookup_reference_plan(op)[2] is None
    e.values['Sheet!J2']=2;e.values['Sheet!J1']=3
    assert e._lookup_reference_plan(op)[2]=='Sheet!C2'
    e.values['Sheet!J2']=Unresolved('unknown-index')
    assert e._lookup_reference_plan(op) is None


@pytest.mark.parametrize('op',['HLOOKUP','VLOOKUP'])
def test_search_key_cycle_is_retained(tmp_path,op):
    host='B3' if op=='HLOOKUP' else 'C2'
    _,result,_,proof,target=run(tmp_path,op,keys=(1,'='+host,3))
    assert isinstance(result.values[target],Unresolved)
    middle_key='Sheet!B1' if op=='HLOOKUP' else 'Sheet!A2'
    assert middle_key in proof['runtime_radj'][target]
    assert target in result.cycle_membership
