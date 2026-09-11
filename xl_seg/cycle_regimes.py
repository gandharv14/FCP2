"""Closed certificate for an IRR-selected finite fee schedule.

The original SCC and native endpoint remain intact. This recognizer proves all
source-defined fee alternatives produce an IRR below the lowest threshold, so
its selected equations are an acyclic computation, including the unique IRR.
"""
from __future__ import annotations
import math


class Unsupported(ValueError):
    pass


def point(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)):
        raise Unsupported('non-finite or non-numeric value')
    return (float(value),float(value))


def interval(values):
    lo,hi=min(values),max(values)
    if not math.isfinite(lo) or not math.isfinite(hi):raise Unsupported('unbounded arithmetic')
    bounded=(math.nextafter(lo,-math.inf),math.nextafter(hi,math.inf))
    if not all(math.isfinite(x) for x in bounded):raise Unsupported('outward rounding overflow')
    return bounded


def add(a,b):return interval((a[0]+b[0],a[1]+b[1]))
def neg(a):return (-a[1],-a[0])
def mul(a,b):return interval(tuple(x*y for x in a for y in b))
def div(a,b):
    if b[0]<=0<=b[1]:raise Unsupported('division interval crosses zero')
    return interval(tuple(x/y for x in a for y in b))


class Recognizer:
    def __init__(self,evaluator,members):
        self.e=evaluator;self.g=evaluator.graph;self.members=set(members)
        self.independent=set();self.visits=0

    def node(self,n):
        v=self.g.nodes.get(n)
        if v is None:raise Unsupported('missing expression node')
        return v

    def sources(self,spec):
        kind,payload=spec
        if kind=='scalar':
            n=self.g.nodes.get(payload)
            if n is not None and n.kind=='range':
                from xl_seg.model import range_members,split_ref
                span=n.coordinate or ''
                ends=span.split(':')
                if len(ends)!=2:raise Unsupported('opaque range')
                a,b=(split_ref(n.sheet+'!'+x.replace('$','')) for x in ends)
                if a is None or b is None or (abs(a[1]-b[1])+1)*(abs(a[2]-b[2])+1)>256:raise Unsupported('range bound')
                return list(range_members(n.sheet,span))
            return [payload]
        if kind in {'span','many'}:
            if len(payload)>256:raise Unsupported('range bound')
            return list(payload)
        raise Unsupported('missing argument')

    def args(self,n):return [self.sources(s) for s in self.e._arg_specs(n)]
    def scalar_args(self,n,count):
        groups=self.args(n)
        if len(groups)!=count or any(len(g)!=1 for g in groups):raise Unsupported('non-scalar signature')
        return [g[0] for g in groups]

    def refs(self,root):
        refs=set();seen=set();stack=[root]
        while stack:
            key=stack.pop()
            if key in seen:continue
            seen.add(key);self.visits+=1
            if self.visits>50000:raise Unsupported('proof traversal bound')
            n=self.g.nodes.get(key)
            if n is None:continue # An absent range cell is an actual blank.
            if n.is_cell:refs.add(key);continue
            if n.kind=='const':continue
            if n.kind!='op' or n.op not in {'+','-','*','/','u-','u+','%','SUM','PRODUCT','MIN','MAX','AVERAGE','IF','IFERROR','AND','OR','NOT','=','<>','<','<=','>','>='}:
                raise Unsupported('dynamic or opaque external dependency syntax')
            for group in self.args(n):stack.extend(group)
        return refs

    def outside(self,key):
        if key in self.members:raise Unsupported('cyclic fee or external parameter')
        if key not in self.independent:
            seen=set();stack=[key]
            while stack:
                cell=stack.pop()
                if cell in seen:continue
                seen.add(cell)
                if cell in self.members:raise Unsupported('external parameter crosses cycle')
                n=self.g.nodes.get(cell)
                if n is None:raise Unsupported('missing external parameter')
                if n.kind=='formula':
                    root=self.g.root_of(cell)
                    if root is None:raise Unsupported('missing external formula')
                    stack.extend(self.refs(root))
            self.independent.update(seen)
        return point(self.e.values.get(key))

    def value(self,key,known):
        n=self.node(key)
        if n.is_cell:
            if key in known:return known[key]
            return self.outside(key)
        if n.kind=='const':return point(self.e._eval_node(key))
        if n.kind!='op':raise Unsupported('unsupported expression')
        if n.op not in {'+','-','*','/','u-','u+','%','SUM','PRODUCT','MIN','MAX'}:raise Unsupported('non-arithmetic selected equation')
        groups=self.args(n);args=[]
        for group in groups:
            for source in group:
                if source not in self.g.nodes and n.op=='SUM':args.append(point(0));continue
                args.append(self.value(source,known))
        if n.op in {'+','-','*','/'}:
            if len(args)!=2:raise Unsupported('binary signature')
            a,b=args
            return {'+':add,'-':lambda x,y:add(x,neg(y)),'*':mul,'/':div}[n.op](a,b)
        if n.op in {'u-','u+','%'}:
            if len(args)!=1:raise Unsupported('unary signature')
            return neg(args[0]) if n.op=='u-' else div(args[0],point(100)) if n.op=='%' else args[0]
        if not args:raise Unsupported('empty function')
        if n.op=='MIN':return (min(a[0] for a in args),min(a[1] for a in args))
        if n.op=='MAX':return (max(a[0] for a in args),max(a[1] for a in args))
        result=point(0 if n.op=='SUM' else 1)
        for arg in args:result=(add if n.op=='SUM' else mul)(result,arg)
        return result

    def threshold(self,key,irr,op):
        node=self.node(key)
        if node.op!=op:raise Unsupported('fee comparison signature')
        left,right=self.scalar_args(node,2)
        if left!=irr:raise Unsupported('fee selector uses other variable')
        self.value(right,{}) # Closed arithmetic over independent source values.
        value=point(self.e._eval_node(right))[0]
        if value<=-1:raise Unsupported('nonconstant IRR threshold')
        return value

    def fee(self,key,irr):
        root=self.node(self.g.root_of(key));sign=1
        if root.op=='u-':sign=-1;root=self.node(self.scalar_args(root,1)[0])
        if root.op!='IF':raise Unsupported('fee schedule signature')
        condition,high,other=self.scalar_args(root,3)
        upper=self.threshold(condition,irr,'>')
        inner=self.node(other)
        if inner.op!='IF':raise Unsupported('finite fee schedule signature')
        condition,middle,low=self.scalar_args(inner,3);both=self.node(condition)
        if both.op!='AND':raise Unsupported('fee band signature')
        a,b=self.scalar_args(both,2)
        lower=self.threshold(a,irr,'>=')
        if self.threshold(b,irr,'<')!=upper or not lower<upper:raise Unsupported('inconsistent fee thresholds')
        candidates=[self.value(n,{}) for n in [low,middle,high]]
        if sign<0:candidates=[neg(v) for v in candidates]
        return lower,upper,[low,middle,high],candidates

    def cashflows(self,irr):
        root=self.node(self.g.root_of(irr))
        if root.op=='IFERROR':
            inner,fallback=self.scalar_args(root,2);fall=self.node(fallback)
            if fall.kind!='const' or fall.op!='text':raise Unsupported('IRR fallback is not literal text')
            root=self.node(inner)
        if root.op!='IRR':raise Unsupported('not IRR')
        groups=self.args(root)
        if len(groups)!=1:raise Unsupported('custom IRR guess unsupported')
        numeric=[];blanks=[]
        for cell in groups[0]:
            n=self.g.nodes.get(cell)
            if n is None:blanks.append(cell);continue
            value=self.e.values.get(cell)
            if value in (None,'') and n.kind in {'input','label'}:blanks.append(cell);continue
            point(value);numeric.append(cell)
        if not 2<=len(numeric)<=128:raise Unsupported('cashflow length')
        first,last=numeric[0],numeric[-1]
        if first in self.members or last not in self.members:raise Unsupported('cashflow endpoint identity')
        principal=neg(self.outside(first))
        if principal[0]<=0:raise Unsupported('initial flow must be negative')
        for cell in numeric[1:-1]:
            if cell in self.members or self.outside(cell)!=(0.,0.):raise Unsupported('intermediate flows must be independent zero')
        return principal,last,len(numeric)-1,numeric,blanks

    def prove(self):
        if not self.e.strict_proof or self.e.oracle is not None or self.e.oracle_reads:
            raise Unsupported('strict unseeded proof required')
        candidates=[]
        for m in sorted(self.members):
            try:
                shape=self.cashflows(m)
            except Unsupported:continue
            candidates.append((m,shape))
        if len(candidates)!=1:raise Unsupported('exactly one supported IRR member required')
        irr,(principal,terminal,period,flows,blanks)=candidates[0]
        fees=[]
        for m in sorted(self.members-{irr}):
            try:fees.append((m,self.fee(m,irr)))
            except Unsupported:continue
        if len(fees)!=1:raise Unsupported('exactly one exhaustive finite fee schedule required')
        fee,(lower,upper,nodes,amounts)=fees[0]
        pending=self.members-{irr,fee};order=[];dependencies={}
        while pending:
            eligible=[]
            for m in sorted(pending):
                root=self.g.root_of(m)
                if root is None:raise Unsupported('missing selected equation')
                dependencies[m]=self.refs(root)&self.members
                if not (dependencies[m]&pending) and irr not in dependencies[m]:eligible.append(m)
            if not eligible:raise Unsupported('selected equations still cyclic')
            order.extend(eligible);pending.difference_update(eligible)
        ceiling=principal
        factor=add(point(1),point(lower))
        for _ in range(period):ceiling=mul(ceiling,factor)
        regimes=[]
        for index,amount in enumerate(amounts):
            known={fee:amount}
            for m in order:known[m]=self.value(self.g.root_of(m),known)
            payout=known[terminal]
            if payout[0]<=0:raise Unsupported('terminal flow not strictly positive')
            if payout[1]>=ceiling[0]:raise Unsupported('regime reaches or approaches IRR band')
            regimes.append({'index':index,'fee_expression':nodes[index],'fee_interval':amount,'member_intervals':known,'terminal_interval':payout,'threshold_terminal_interval':ceiling})
        # Also enclose asynchronous mixtures of fee alternatives, not only
        # the three coherent equilibria. Without this stronger envelope the
        # proof could establish a unique fixed point but miss an oscillation.
        envelope={fee:(min(x[0] for x in amounts),max(x[1] for x in amounts))}
        for m in order:envelope[m]=self.value(self.g.root_of(m),envelope)
        if envelope[terminal][0]<=0 or envelope[terminal][1]>=ceiling[0]:
            raise Unsupported('mixed-regime transient envelope reaches IRR band')
        native_rate=point(self.e.values.get(irr))[0]
        if not -1<native_rate<lower:raise Unsupported('native IRR branch differs from proved regime')
        from xl_seg.evaluate import EXCEL_DEFAULT_CHANGE
        raw_delta=getattr(self.e.calculation,'iterate_delta',None)
        delta=float(raw_delta) if isinstance(raw_delta,(int,float)) and raw_delta>=0 else EXCEL_DEFAULT_CHANGE
        residual,errors,unresolved=self.e._equation_residual(tuple(sorted(self.members)))
        if residual is None or not math.isfinite(residual) or residual>delta or errors or unresolved:
            raise Unsupported('native endpoint equation residual is not within its finite-stop tolerance')
        native_fee=point(self.e.values.get(fee))[0]
        fee_equation=point(self.e._eval_cell(fee))[0]
        if not amounts[0][0]<=fee_equation<=amounts[0][1]:raise Unsupported('native fee equation differs from proved regime')
        return {'certified':True,'contractive':True,'kind':'finite_irr_regime_acyclic',
                'members':sorted(self.members),'irr_member':irr,'fee_member':fee,
                'selected_regime':0,'thresholds':[lower,upper],'regimes':regimes,
                'native_fee':native_fee,'fee_equation':fee_equation,
                'native_fee_residual':abs(native_fee-fee_equation),'native_equation_residual':residual,
                'native_iteration_delta':delta,
                'cashflow_members':flows,'ignored_blank_cells':blanks,'periods':period,
                'principal_interval':principal,'mixed_regime_envelope':envelope,'acyclic_order':[fee,*order,irr],
                'contraction_bound':0.0,'contraction_power':2*len(self.members)+2,
                'proof':'Every fee alternative and its mixed transient envelope yield a unique one-positive-flow IRR below the lowest band; after propagation through the acyclic retained equations, the fee is fixed and a further acyclic propagation is constant.',
                'native_endpoint_replaced':False,'independent_endpoint_equivalence_required':True}


def certify(evaluator,members):
    try:return Recognizer(evaluator,members).prove()
    except (Unsupported,OverflowError,ZeroDivisionError,TypeError,ValueError) as exc:
        return {'certified':False,'reason':'finite_irr_regime_unsupported','detail':str(exc)}
