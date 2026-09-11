import dataclasses,json,time,collections
import pytest
from openpyxl import Workbook
from xl_ast_graph import AstGraph,write_csv
from xl_seg import model,project
from xl_seg.evaluate import CalculationMetadata, Evaluator


class WithoutCycleIndependentReuse(Evaluator):
    """Reference execution disables only this optimization, not proof checks."""

    def _cycle_independent_values(self, cells, seeded):
        return frozenset()


B = WithoutCycleIndependentReuse
P = Evaluator

def graph(tmp_path,formulas):
 wb=Workbook();ws=wb.active;ws.title='S'
 for ref,value in formulas.items():ws[ref]=value
 path=tmp_path/'fixture.xlsx';wb.save(path);wb.close();ast=AstGraph(path,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges);g=model.load(tmp_path,'fixture');return g,project.build(g)

def normalize(v):
 if isinstance(v,dict):return {str(k):normalize(x) for k,x in v.items() if k not in ('seconds','run_seconds')}
 if isinstance(v,(tuple,list)):return [normalize(x) for x in v]
 if isinstance(v,set):return sorted(normalize(x) for x in v)
 if callable(v):return '<checker>'
 if type(v).__name__ in ('Unresolved','ExcelError'):return repr(v)
 return v

def run(module,g,cg,*,delta=.001,count=100,probes=False,scope=None,inputs=None):
 evaluator=module(g,cg,strict_proof=True,calculation=CalculationMetadata(available=True,iterate=True,iterate_count=count,iterate_delta=delta,iterate_origin='explicit',iterate_count_origin='explicit',iterate_delta_origin='explicit'),run_probes=probes,proof_scope=scope)
 calls=collections.Counter();original=evaluator._eval_cell
 def record(c):calls[c]+=1;return original(c)
 evaluator._eval_cell=record;start=time.perf_counter();result=evaluator.run(inputs if inputs is not None else {c for c in cg.info if g.nodes[c].kind in ('input','label')});elapsed=time.perf_counter()-start
 return evaluator,result,calls,elapsed

def compare(tmp_path,formulas,**kwargs):
 g,cg=graph(tmp_path,formulas);b,br,bc,bt=run(B,g,cg,**kwargs);p,pr,pc,pt=run(P,g,cg,**kwargs)
 bd={f.name:getattr(br,f.name) for f in dataclasses.fields(br)};pd={f.name:getattr(pr,f.name) for f in dataclasses.fields(pr)}
 assert normalize(bd)==normalize(pd)
 return g,cg,p,pr,bc,pc,bt,pt

@pytest.mark.parametrize('formula',['=B1+2','=SUM(B1:B3)','=IFERROR(B1/0,7)','=EOMONTH(B1,1)','=SUMIFS(B1:B3,C1:C3,">1")'])
def test_reuses_only_closed_independent_reconstructed_scalar(tmp_path,formula):
 g,cg,p,res,old,new,*_=compare(tmp_path,{'A1':'=.5*A1+1','A2':'=ROUND(A1,3)','B1':10,'B2':20,'B3':30,'C1':1,'C2':2,'C3':3,'D1':formula})
 assert new['S!D1']<old['S!D1'];assert old['S!A1']==new['S!A1'];assert old['S!A2']==new['S!A2'];assert res.values['S!A1']==1.9990234375

@pytest.mark.parametrize('formula',['=A1+2','=IF(A1>1,B1,B2)','=INDIRECT("B1")','=OFFSET(B1,0,0)','=INDEX(B1:B2,1)','=VLOOKUP(B1,B1:C2,2,FALSE)','=CHOOSE(1,B1,B2)','=SUMIF(B1:B2,">0",C1)','=TODAY()','=NO_SUCH_FUNCTION(B1)','=SUMIFS(B1:B2,C1:C2,A1)'])
def test_uncertain_or_cycle_dependent_expressions_are_not_reused(tmp_path,formula):
 g,cg,p,res,old,new,*_=compare(tmp_path,{'A1':'=.5*A1+1','B1':10,'B2':20,'C1':1,'C2':2,'D1':formula})
 assert old['S!D1']==new['S!D1']

@pytest.mark.parametrize('cycle',['=A1','=A1+1','=1-A1','=.5*A1+1'])
def test_cycle_contract_stopping_and_rejection_unchanged(tmp_path,cycle):
 compare(tmp_path,{'A1':cycle,'A2':'=ROUND(A1,3)','B1':5,'C1':'=B1+3'},count=15,probes=True)

def test_changed_supplied_criterion_is_recomputed_on_fresh_run(tmp_path):
 g,cg=graph(tmp_path,{'A1':'=.5*A1+1','B1':10,'B2':20,'C1':1,'C2':2,'D1':'=SUMIFS(B1:B2,C1:C2,E1)','E1':'>1'})
 _,r1,_,_=run(P,g,cg);g.nodes['S!E1'].value='<2';_,r2,_,_=run(P,g,cg);_,baseline,_,_=run(B,g,cg)
 assert r1.values['S!D1']==20 and r2.values['S!D1']==10 and r2.values==baseline.values

def test_array_truncated_missing_input_and_hidden_dependency_rejected(tmp_path):
 g,cg=graph(tmp_path,{'A1':'=.5*A1+1','B1':4,'C1':'=B1+1','D1':'=C1+1'})
 p,res,_,_=run(P,g,cg);assert {'S!C1','S!D1'}<=p._cycle_independent_values(list(cg.info),{'S!B1'})
 g.nodes['S!C1'].array_ref='C1:C1';assert 'S!D1' not in p._cycle_independent_values(list(cg.info),{'S!B1'});g.nodes['S!C1'].array_ref=''
 g.nodes['S!C1'].range_truncated=True;assert 'S!D1' not in p._cycle_independent_values(list(cg.info),{'S!B1'});g.nodes['S!C1'].range_truncated=False
 assert 'S!D1' not in p._cycle_independent_values(list(cg.info),set())


def test_synthetic_profile(tmp_path):
 data={'A1':'=.98*A1+1','A2':'=ROUND(A1,3)'}
 for row in range(1,40):data[f'B{row}']=row;data[f'C{row}']=row
 for row in range(1,25):data[f'D{row}']='=SUMIFS($B$1:$B$39,$C$1:$C$39,">10",$C$1:$C$39,"<30")'
 g,cg,p,res,old,new,bt,pt=compare(tmp_path,data,count=1000,delta=.001)
 report={'baseline_seconds':bt,'proposal_seconds':pt,'baseline_independent_calls':old['S!D1'],'proposal_independent_calls':new['S!D1'],'cycle_calls_equal':old['S!A1']==new['S!A1'],'cycle_iterations':res.iterated[0]['iterations'],'semantic_and_proof_equal':True,'actual_corpus_replay':False}
 (tmp_path/'synthetic-profile.json').write_text(json.dumps(report,indent=2)+'\n');assert new['S!D1']<old['S!D1']/10

@pytest.mark.parametrize('hidden',['=INDIRECT("B1")','=OFFSET(B1,0,0)','=INDEX(B1:B2,1)','=VLOOKUP(B1,B1:C2,2,FALSE)','=SUMIF(B1:B2,">0",C1)','=A1'])
def test_hidden_frontiers_decline_through_ordinary_dependant(tmp_path,hidden):
 _,_,_,_,old,new,*_=compare(tmp_path,{'A1':'=.5*A1+1','B1':10,'B2':20,'C1':1,'C2':2,'D1':hidden,'E1':'=D1+1'})
 assert old['S!E1']==new['S!E1']

def test_unselected_summand_still_in_static_closure(tmp_path):
 _,_,_,_,old,new,*_=compare(tmp_path,{'A1':'=.5*A1+1','B1':10,'B2':'=A1','C1':1,'C2':2,'D1':'=SUMIFS(B1:B2,C1:C2,1)'})
 assert old['S!D1']==new['S!D1']

@pytest.mark.parametrize('formula',['=SUM(B1:B3)','=B1&" units"','=IFERROR(B1/0,"none")'])
def test_blank_text_boolean_values_and_reads_preserved(tmp_path,formula):
 compare(tmp_path,{'A1':'=.5*A1+1','B1':True,'B3':2,'D1':formula})

def test_rejected_formula_cache_values_never_seed_memo(tmp_path):
 g,cg=graph(tmp_path,{'A1':'=.5*A1+1','B1':3,'D1':'=B1+2'})
 for n in g.nodes.values():
  if n.kind=='formula':n.value='987654321'
 _,br,_,_=run(B,g,cg);_,pr,_,_=run(P,g,cg)
 assert pr.values['S!D1']==5 and pr.values==br.values
 assert not pr.oracle_reads and not pr.oracle_accesses

@pytest.mark.parametrize('mutation',['truncated','mixed_sheet','duplicate','oversized','empty'])
def test_uncertain_sumifs_geometry_declines_before_reuse(tmp_path,mutation):
 g,cg=graph(tmp_path,{'A1':'=.5*A1+1','B1':10,'B2':20,'C1':1,'C2':2,'D1':'=SUMIFS(B1:B2,C1:C2,">1")'})
 p,_,_,_=run(P,g,cg);node=g.nodes[g.root_of('S!D1')];specs=list(p._arg_specs(node));assert 'S!D1' in p._cycle_independent_values(list(cg.info),{'S!B1','S!B2','S!C1','S!C2'})
 if mutation=='truncated':node.range_truncated=True
 elif mutation=='empty':p._arg_cache[node.id]=[]
 else:
  payload={'mixed_sheet':['S!B1','Other!B2'],'duplicate':['S!B1','S!B1'],'oversized':['S!B1']*10001}[mutation];specs[0]=('span',payload);p._arg_cache[node.id]=specs
 assert 'S!D1' not in p._cycle_independent_values(list(cg.info),{'S!B1','S!B2','S!C1','S!C2'})
