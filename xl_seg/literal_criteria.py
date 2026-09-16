"""Closed literal-criteria AVERAGEIF projection; no approval or artifact writes.

Only aligned, finite static references and genuine literal text selectors are
recognized. Numerical targets are evaluated natively after selection. Unsupported
criteria keep the unchanged conservative evaluator path. Source bytes, finite
iteration policy, endpoint checks, and qualification gates remain unchanged.
"""
import re
from xl_seg import model

LIMIT=512

def refs(e,spec):
    kind,value=spec
    if kind in {'span','many'}:
        if len(value)>LIMIT:return None
        found=tuple(value)
    elif kind=='scalar':
        node=e.graph.nodes.get(value)
        if node is None or node.range_truncated:return None
        if node.is_cell:found=(value,)
        elif node.kind=='range':
            start,sep,end=(node.coordinate or '').partition(':')
            if not sep:return None
            a=model.split_ref(node.sheet+'!'+start);b=model.split_ref(node.sheet+'!'+end)
            if a is None or b is None or a[1]>b[1] or a[2]>b[2]:return None
            if (b[1]-a[1]+1)*(b[2]-a[2]+1)>LIMIT:return None
            found=tuple(model.range_members(node.sheet,node.coordinate))
        else:return None
    else:return None
    coords=[model.split_ref(x) for x in found]
    if not coords or any(x is None for x in coords) or len(set(found))!=len(found):return None
    sheets={x[0] for x in coords};rows=sorted({x[1] for x in coords});cols=sorted({x[2] for x in coords})
    if len(sheets)!=1 or rows!=list(range(rows[0],rows[-1]+1)) or cols!=list(range(cols[0],cols[-1]+1)):return None
    if coords!=[(coords[0][0],r,c) for r in rows for c in cols]:return None
    return found,(len(rows),len(cols))

def projection(e,node):
    if node.op!='AVERAGEIF' or node.range_truncated:return None
    args=e._arg_specs(node)
    if len(args)!=3 or args[1][0]!='scalar':return None
    criterion=e.graph.nodes.get(args[1][1])
    if criterion is None or criterion.kind!='const' or criterion.op!='text':return None
    text=criterion.value
    if not isinstance(text,str) or text.strip()!=text or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 _-]{0,127}',text):return None
    left=refs(e,args[0]);right=refs(e,args[2])
    if left is None or right is None or left[1]!=right[1]:return None
    criteria,shape=left;targets,_=right;labels=[]
    for ref in criteria:
        cell=e.graph.nodes.get(ref)
        if cell is None or not cell.is_cell or cell.kind not in {'label','input'} or cell.formula or cell.range_truncated:return None
        if cell.value_type!='str' or not isinstance(cell.value,str) or not cell.value.isascii() or cell.value.strip()!=cell.value:return None
        current=e.values.get(ref,cell.value)
        if type(current) is not str or current!=cell.value:return None
        labels.append(cell.value)
    selected=tuple(ref for ref,label in zip(targets,labels) if label.lower()==text.lower())
    return dict(schema_version='literal-averageif-selector/v1',operation=node.id,
        criterion_node=criterion.id,criterion=text,criteria=list(criteria),labels=labels,
        all_targets=list(targets),selected_targets=list(selected),shape=list(shape),
        source_of_selectors='literal_ast_cells_only',oracle_reads=0,lane_approval=False)
