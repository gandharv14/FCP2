from types import SimpleNamespace
from xl_seg.evaluate import Evaluator,ExcelError,Unresolved
from xl_seg.model import range_members

def pair():
 return [Evaluator(SimpleNamespace(nodes={}),None,strict_proof=True,optimize=x) for x in [False,True]]
def comparable(value):
 return (list(value),value.rows,value.cols,value.refs)
def test_changed_values_and_read_accounting_are_not_cached():
 refs=range_members("2030 O'Brien",'A1:C2');a,b=pair()
 for e in [a,b]:
  e.values={refs[0]:False,refs[2]:'label',refs[3]:2};e._current_cell='Out!A1'
 first=[e._arg_value(('span',refs)) for e in [a,b]]
 assert comparable(first[0])==comparable(first[1])
 for e in [a,b]:
  e.values[refs[3]]=99;e._current_cell='Out!A2'
 again=[e._arg_value(('span',refs)) for e in [a,b]]
 assert comparable(again[0])==comparable(again[1]);assert again[1][3]==99
 assert a.read_attempts==b.read_attempts and a.missing_reads==b.missing_reads
 assert b.read_attempts[refs[0]]=={'Out!A1','Out!A2'}
 assert b.missing_reads[refs[1]]=={'Out!A1','Out!A2'}
def test_changed_reference_sequence_and_sheet_identity_have_distinct_keys():
 a,b=pair();refs=['1 Space!A1','1 Space!B1']
 for current in [refs,list(reversed(refs)),refs+['1 Space!A2'],["O'Brien!A1"]]:
  assert comparable(a._arg_value(('span',current)))==comparable(b._arg_value(('span',current)))
 assert len(b._span_shape_cache)==4 and not a._span_shape_cache
 refs.append('1 Space!A3')
 assert comparable(a._arg_value(('span',refs)))==comparable(b._arg_value(('span',refs)))
def test_error_unresolved_and_blank_positions_preserved():
 refs=range_members('S','A1:C1');a,b=pair();err=ExcelError('#N/A');unknown=Unresolved('proof')
 for e in [a,b]:e.values={refs[0]:err,refs[2]:unknown}
 for repeat in range(3):
  result=[e._arg_value(('span',refs)) for e in [a,b]]
  assert comparable(result[0])==comparable(result[1]);assert result[1][0] is err and result[1][1] is None and result[1][2] is unknown

def test_bounded_cache_saturation_falls_back_to_exact_shape_reads():
 a,b=pair();b._span_shape_cache_members=1000000
 refs=range_members('S','A1:C3')
 assert comparable(a._arg_value(('span',refs)))==comparable(b._arg_value(('span',refs)))
 assert not b._span_shape_cache and b._span_shape_cache_members==1000000

def test_absolute_reversed_span_normalization_and_multisheet_isolation():
 from types import SimpleNamespace
 a,b=pair()
 for e in [a,b]:
  e.graph.nodes={'S!A1':SimpleNamespace(sheet='S'),"1 O'Brien!A1":SimpleNamespace(sheet="1 O'Brien")}
  e.values={key:0 for key in e.graph.nodes}
 for source,span in [('S!A1','S!$C$2:$A$1'),("1 O'Brien!A1","'1 O''Brien'!$A$1:$C$2")]:
  group=[SimpleNamespace(via_range=span,source=source)]
  specs=[e._spec(group) for e in [a,b]]
  assert specs[0]==specs[1]
  assert comparable(a._arg_value(specs[0]))==comparable(b._arg_value(specs[1]))
 assert len(b._span_shape_cache)==2

def test_unknown_raw_reference_preserves_existing_fallback_semantics():
 a,b=pair();refs=['invalid reference']
 assert comparable(a._arg_value(('span',refs)))==comparable(b._arg_value(('span',refs)))
 assert a.missing_reads==b.missing_reads

def test_entry_limit_saturation_does_not_change_results():
 a,b=pair();b._span_shape_cache={(str(i),):(1,1) for i in range(32768)}
 refs=range_members('S','A1:B2')
 assert comparable(a._arg_value(('span',refs)))==comparable(b._arg_value(('span',refs)))
 assert len(b._span_shape_cache)==32768 and tuple(refs) not in b._span_shape_cache
