"""A native iteration's scalar primitive reads are execution dependencies."""
from openpyxl import Workbook
from xl_ast_graph import AstGraph,write_csv
from xl_seg import model,project,evaluate
from xl_segment import stabilize_runtime_proof


def run(tmp_path,formulas,output='A1'):
    book=Workbook();sheet=book.active;sheet.title='S'
    for ref,value in formulas.items():sheet[ref]=value
    p=tmp_path/'book.xlsx';book.save(p);book.close()
    ast=AstGraph(p,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    graph=model.load(tmp_path,'book');cg=project.build(graph)
    calc=evaluate.CalculationMetadata(available=True,iterate=True,iterate_count=100,iterate_delta=.001)
    return stabilize_runtime_proof(graph,cg,set(),{'S!'+output},calculation=calc)


def test_transient_branch_input_remains_proven_but_unrelated_input_is_removed(tmp_path):
    result,seeds,radj,proof=run(tmp_path,{'A1':'=IF(A2<1,B1,2)','A2':'=0.5*A1+0.5','B1':1,'Z1':999})
    assert proof['closure']['stabilized'] and proof['closure']['passes']<12
    assert result.values['S!A1']==2 and result.values['S!A2']==1.5
    assert seeds=={'S!B1'} and 'S!B1' in radj['S!A1']
    assert result.scalar_primitive_reads['S!B1']=={'S!A1'}
    assert not result.oracle_reads and not result.oracle_accesses


def test_unselected_scalar_branch_is_not_retained(tmp_path):
    result,seeds,radj,proof=run(tmp_path,{'A1':'=IF(B1>0,C1,D1)','B1':1,'C1':3,'D1':99,'Z1':8})
    assert proof['closure']['stabilized'] and result.values['S!A1']==3
    assert seeds=={'S!B1','S!C1'}
    assert 'S!D1' not in radj['S!A1'] and 'S!Z1' not in seeds


def test_guarded_boolean_selector_composes_all_comparisons(tmp_path):
    result,_,_,proof=run(tmp_path,{'A1':'=0.5*A2+1','A2':'=IF(AND(A1>0,A1<5),A1,0)'})
    assert proof['closure']['stabilized']
    assert all(c['certified'] for c in result.iterated)
    assert result.values['S!A1']<2 # Native finite stop is never tightened.


def test_or_cannot_hide_unknown_operand_in_certificate(tmp_path):
    result,*_=run(tmp_path,{'A1':'=IF(OR(A1>0,UNSUPPORTED(A1)),0.5*A1+1,0)'})
    assert not all(c['certified'] for c in result.iterated)


def test_boolean_boundary_without_equation_proof_stays_uncertified(tmp_path):
    result,*_=run(tmp_path,{'A1':'=IF(AND(A1>=0,A1<1),0,-1)'})
    assert not all(c['certified'] for c in result.iterated)


def test_added_primitive_read_does_not_enumerate_static_adjacency():
    from types import SimpleNamespace
    from xl_segment import _runtime_proof_radj
    class NeverEnumerate:
        def __iter__(self):raise AssertionError('expanded lazy static adjacency')
        def __contains__(self,value):return value=='S!OLD'
    lazy=NeverEnumerate()
    cg=SimpleNamespace(info={c:SimpleNamespace(node=SimpleNamespace(kind=k)) for c,k in [('S!A1','formula'),('S!B1','input')]},radj={'S!A1':lazy})
    result=SimpleNamespace(runtime_radj={},scalar_primitive_reads={'S!B1':{'S!A1',None,'S!UNKNOWN'}})
    radj=_runtime_proof_radj(cg,result)
    assert 'S!B1' in radj['S!A1'] and 'S!OLD' in radj['S!A1']
    assert radj['S!A1'].groups==(lazy,)
    result.runtime_radj={'S!A1':{'S!A1'}}
    assert 'S!A1' in _runtime_proof_radj(cg,result)['S!A1']
