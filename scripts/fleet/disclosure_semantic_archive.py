"""Real archived-file evidence for an already adopted disclosure selection.

The original gate still checks live content. Only its historical disclosure
input and returned historical evidence are projected to executor snapshots.
"""
from contextlib import contextmanager
from dataclasses import fields,is_dataclass,replace
from pathlib import Path

SCHEMA='fleet-disclosure-semantic-archive/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def archives(A,receipt):
    value=A.read(receipt)
    require(set(value)-{'source_derivative'}==A.KEYS and value['schema_version']==A.SCHEMA
            and value['implementation']==A.record(A.__file__),'exact adopted disclosure receipt required')
    result=A.read(value['round_result']);run=A.read(value['round_plan']);submitted=[]
    require(result['aggregate']==value['aggregate']and result['review_plan']==value['review_plan']
            and type(result['actual_review_calls'])is int and result['actual_review_calls']>0,
            'adopted actual review identity differs')
    for index in range(result['actual_review_calls']):
        done=(A.history.batch_completion(run,value['review_plan'],index)if 'completed_prefix'in run
              else A.read(A.record(Path(run['output'])/'batches'/('%04d'%index)/'complete.json')))
        require(done['index']==index and done['plan']==value['review_plan'],'adopted shard order differs')
        submitted.extend(A.read(done['call']['submitted'])['budget_evidence'])
    pairs={}
    for pair in value['historical_records']:
        require(set(pair)=={'original','archive'},'closed historical file pair required')
        old,new=pair['original'],pair['archive']
        require(old['sha256']==new['sha256'],'historical file bytes differ')
        if old!=new:pairs.setdefault((old['path'],old['sha256']),[]).append(new)
    def resolve(old,seen):
        key=(old['path'],old['sha256'])
        require(key not in seen,'cyclic historical file linkage')
        if old in submitted:A.E.checked_bytes(old);return old
        found=[]
        for target in pairs.get(key,[]):
            resolved=resolve(target,seen|{key})
            if resolved is not None:found.append(resolved)
        require(not found or all(A.E.checked_bytes(x)==A.E.checked_bytes(found[0])for x in found),
                'conflicting submitted history')
        return found[0]if found else None
    mapped={}
    for key in pairs:
        target=resolve(dict(path=key[0],sha256=key[1]),set())
        if target is not None:mapped[key]=target
    old=value['historical_instruction'];key=(old['path'],old['sha256'])
    require(key in mapped,'adopted disclosure instruction has no real submitted archive')
    return value,mapped,mapped[key]


def historical_evidence(A,receipt,validated):
    """Return real archived evidence after the caller's full adoption check.

    The validated receipt must equal the authenticated immutable receipt. Every
    replacement follows its exact links to actual submitted budget snapshots.
    This helper grants no current custody or completed-review validity itself.
    """
    value,mapped,_=archives(A,receipt)
    require(value==validated,'historical evidence detached from validated adoption')
    original=[receipt,*[value[k]for k in('implementation','history_reader','round_plan','round_result','review_plan','aggregate','initial')],
              *[item['archive']for item in value['historical_records']]]
    result=[]
    for row in original:
        require(isinstance(row,dict)and set(row)=={'path','sha256'},'closed historical evidence binding required')
        selected=mapped.get((row['path'],row['sha256']),row)
        require(selected['sha256']==row['sha256'],'archive changed historical bytes')
        A.E.checked_bytes(selected);result.append(selected)
    return result


@contextmanager
def installed(A,profile,receipt):
    """Scope original gate aliases by exact module/function identity; restore all."""
    from scripts.batch import harbor_complete_readiness as C,harbor_completed_lanes as L
    require(set(profile)=={'schema_version','complete','lanes','functions'}and profile['schema_version']==SCHEMA
            and profile['complete']==A.record(C.__file__)and profile['lanes']==A.record(L.__file__)
            and profile['functions']==dict(evidence=A.function_sha(C.evidence_records),disclosure=A.function_sha(C.check_disclosure)),
            'exact original semantic gate witness required')
    value,mapped,instruction=archives(A,receipt)
    review_plan=A.read(value['review_plan']);reviewed=review_plan['instruction']
    reviewed_archive=instruction if reviewed==instruction else mapped.get((reviewed['path'],reviewed['sha256']))
    if 'source_derivative'in value:
        authority=A.read(review_plan['review_round'])
        require(authority['source_derivative']==value['source_derivative'],
                'semantic archive detached from reviewed source derivative')
        derivative=A.read(value['source_derivative']);proof=A.read(derivative['proof'])
        require(reviewed_archive is not None
                and reviewed_archive['sha256']==reviewed['sha256']==proof['candidate_instruction']['sha256']
                and A.E.checked_bytes(reviewed_archive)==A.E.checked_bytes(reviewed),
                'corrected instruction has no exact submitted archive linkage')
    else:
        require(reviewed_archive is not None and reviewed_archive['sha256']==instruction['sha256']
                and A.E.checked_bytes(reviewed_archive)==A.E.checked_bytes(instruction),
                'actual review instruction has no exact submitted archive linkage')
    instruction=reviewed_archive
    old_evidence=C.evidence_records;old_disclosure=C.check_disclosure;old_aggregate=A.review.validate_aggregate
    def selected(selection):
        return isinstance(selection,C.ReadinessSelection)and selection.semantic is not None and selection.semantic.disclosure_partition_aggregate==value['aggregate']
    def validate_selection(selection):
        require(selection.content.workbook==review_plan['workbook'],
                'foreign disclosure workbook')
        require(selection.content.bindings['original_raw_source']==value['source']
                and selection.semantic.disclosure_instruction in (value['historical_instruction'],reviewed,instruction),
                'foreign historical disclosure selection')
    def project(v):
        if isinstance(v,dict):
            if set(v)=={'path','sha256'}:
                found=mapped.get((v['path'],v['sha256']))
                if found is not None:A.E.checked_bytes(found);return found
                return v
            return {k:project(x)for k,x in v.items()}
        if is_dataclass(v):return replace(v,**{f.name:project(getattr(v,f.name))for f in fields(v)})
        if isinstance(v,tuple):return tuple(project(x)for x in v)
        if isinstance(v,list):return [project(x)for x in v]
        return v
    # Only the original disclosure function's final evidence expression uses
    # this archive projector. Nested round-prefix readers must retain their
    # own original binding inventory, even while the disclosure gate runs.
    from types import FunctionType
    scope=dict(old_disclosure.__globals__)
    scope['evidence_records']=lambda v:old_evidence(project(v))
    original_disclosure=FunctionType(old_disclosure.__code__,scope,old_disclosure.__name__,old_disclosure.__defaults__,old_disclosure.__closure__)
    original_disclosure.__kwdefaults__=old_disclosure.__kwdefaults__
    aggregate_depth=0
    def evidence(v):
        if selected(v):validate_selection(v);return old_evidence(project(v))
        # The selected original aggregate calls this only for its final returned
        # evidence. Its nested typed prefix inventory starts with other bindings.
        if aggregate_depth and isinstance(v,list)and v and v[0]==value['aggregate']:
            return old_evidence(project(v))
        return old_evidence(v)
    def disclosure(selection,*,root=C.ROOT):
        if not selected(selection):return old_disclosure(selection,root=root)
        validate_selection(selection);A.E.checked_bytes(instruction)
        projected=replace(selection,semantic=replace(selection.semantic,disclosure_instruction=instruction))
        return original_disclosure(projected,root=root)
    def aggregate(binding,*args,**kwargs):
        nonlocal aggregate_depth
        if binding!=value['aggregate']:return old_aggregate(binding,*args,**kwargs)
        if kwargs.get('instruction')==instruction:kwargs={**kwargs,'instruction':reviewed}
        aggregate_depth+=1
        try:return old_aggregate(binding,*args,**kwargs)
        finally:aggregate_depth-=1
    aliases=[]
    for scope in (vars(C),vars(L)):
        for name,obj in list(scope.items()):
            if obj is old_evidence:aliases.append((scope,name,obj));scope[name]=evidence
            elif obj is old_disclosure:aliases.append((scope,name,obj));scope[name]=disclosure
    A.review.validate_aggregate=aggregate
    try:yield
    finally:
        A.review.validate_aggregate=old_aggregate
        for scope,name,obj in reversed(aliases):scope[name]=obj
