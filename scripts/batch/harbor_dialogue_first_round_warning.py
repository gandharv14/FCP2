"""Closed ordinary first-round warning history; historical code is data only.

The selected successor retains every original step and all three actual
Calls. It grants no semantic attempt and never modifies the rejected review.
"""
from dataclasses import replace
from pathlib import Path
from scripts.batch.harbor_admission import read,record,require

ALLOWED={'.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py',
 'scripts/batch/harbor_dialogue_first_round_warning.py',
 'scripts/batch/harbor_dialogue_warning_requalification.py',
 'scripts/batch/harbor_instruction_worker.py','scripts/batch/harbor_instruction_lease_renewal.py',
 'scripts/harbor_instruction_campaign.py','.pipeline-code.json'}
ALLOWED.add('scripts/batch/harbor_normalization_validator_bridge.py')
ALLOWED.add('scripts/batch/harbor_dialogue_source_warnings.py')
ALLOWED.update({'scripts/batch/harbor_instruction_repair.py','scripts/batch/harbor_completed_lanes.py'})
FINDING='The first VP response is conspicuously convoluted and self-correcting—"take ... Existing debt minus that result, reverse the subtraction"—rather than sounding like a natural Slack explanation.'
SENIOR='Set the row labelled "Equity purchase price" only for this cell. Multiply the named range "EBITDA" by the row labelled "Entry multiple", then take the row labelled "Existing debt" minus that result, reverse the subtraction so the product is less debt, and finally plus the row labelled "Cash".'

def style_only(finding,claims,draft):
    """Only this exact awkward, independently entailed response is classified.

    The caller still requires every claim entailed, complete coverage, no leaks,
    no extra content, all other findings recognized, and the owning mechanics.
    """
    rows=claims.get('claims') or []
    if template_style_only(finding,claims,draft):return True
    if finding!=FINDING or not rows:return False
    first=rows[0]
    if first.get('record_id')!='method_debt_movement::LBO::Equity purchase price::001':return False
    if first.get('junior_scope')!=['LBO!O17'] or first.get('reviewer_only',{}).get('evidence')!='=PRODUCT(EBITDA,D7)-O65+O55':return False
    block=draft.split('<!-- claim:',2)
    return len(block)==3 and block[1].endswith('**VP:** '+SENIOR+'\n\n')

def template_style_only(finding,claims,draft):
    """Bounded repetition findings; caller must independently pass accuracy."""
    import re
    npv=re.fullmatch(r'The (\d+) consecutive NPV exchanges repeat the same junior-question pattern with superficial rewording, making the file read like a checklist rather than Slack\.',finding)
    npv= npv or re.fullmatch(r'The (\d+) consecutive NPV exchanges repeat essentially the same junior question and senior answer with superficial wording changes, so the section reads like a checklist rather than Slack\.',finding)
    copied=re.fullmatch(r"Several projection and source-selection senior turns are near-copies of the cards' spoken lines, including the ([^\n]+) response for claim ::(\d{3})\.",finding)
    verbatim=re.fullmatch(r"The senior response for claim ::(\d{3}) reproduces the card's spoken line verbatim, and several projection and source-selection responses are near-copies of their spoken lines\.",finding)
    repeated=finding=='The repeated two-turn structure and rubric-like recitation of long row labels make the overall conversation read as generated documentation rather than a natural deal-team exchange.'
    repeated=repeated or finding=='Across the file, the repeated two-turn structure and constant recitation of long row labels make the conversation read like generated documentation rather than a natural deal-team exchange.'
    if not(npv or copied or verbatim or repeated):return False
    from scripts.batch.harbor_readiness import skill_module,ROOT
    V=skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue');rows=claims.get('claims')or[]
    mapped,faults=V.map_turns_to_claims(V.parse_turns(draft),rows)
    if faults or not rows or not all(len(mapped.get(c['record_id'],[]))==2 and sum(V.is_junior(t['speaker'])for t in mapped[c['record_id']])==1 and sum(V.is_senior(t['speaker'])for t in mapped[c['record_id']])==1 for c in rows):return False
    if npv:
        indices=[i for i,c in enumerate(rows)if c.get('reviewer_only',{}).get('entry')=='npv_timing']
        return len(indices)==int(npv[1]) and len(indices)>1 and indices==list(range(indices[0],indices[-1]+1))
    if verbatim:
        n=int(verbatim[1])-1
        if not 0<=n<len(rows):return False
        c=rows[n];seniors=[t['text']for t in mapped[c['record_id']]if V.is_senior(t['speaker'])]
        spoken=c.get('spoken')or''
        copied_exact=seniors==[spoken]or(spoken.count('its value')==1 and seniors==[spoken.replace('its value','its whole value',1)])
        return c.get('reviewer_only',{}).get('entry')=='source_selection'and copied_exact and any(x.get('reviewer_only',{}).get('entry')=='projection_rule'for x in rows)
    if copied:
        n=int(copied[2])-1
        if not 0<=n<len(rows):return False
        c=rows[n]
        return c.get('row_label')==copied[1] and c.get('reviewer_only',{}).get('entry')=='source_selection' and any(copied[1]in t['text']for t in mapped[c['record_id']]if V.is_senior(t['speaker'])) and any(x.get('reviewer_only',{}).get('entry')=='projection_rule'for x in rows)
    return all(isinstance(c.get('row_label'),str)and c['row_label']for c in rows)


def ordinary(worker):
    return all(getattr(worker,name,None) is None for name in
        ('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery',
         'code_repair','warning_requalification','disclosure_repair','disclosure_revision','dialogue_pre_review_repair'))

def inventory(root):
    result={}
    for p in root.rglob('*'):
        require(not p.is_symlink(),'warning snapshot contains symlink')
        if '__pycache__' in p.relative_to(root).parts:continue
        if p.is_file():result[str(p.relative_to(root))]=record(p)['sha256']
    return result


def validate_code_manifest(root):
    """A successor may update its own manifest only to describe its exact bytes."""
    import json
    actual=inventory(root)
    require('.pipeline-code.json' in actual,'successor code manifest missing')
    expected={name:digest for name,digest in actual.items() if name!='.pipeline-code.json'}
    require(json.loads((root/'.pipeline-code.json').read_bytes())==expected,
            'successor code manifest does not describe exact code inventory')

def history(worker,before,snapshot):
    from scripts.batch import harbor_instruction_worker as w
    from scripts.batch import harbor_instruction_lease_renewal as renewal
    require(ordinary(replace(worker,warning_requalification=None)),'ordinary warning cannot borrow specialized history')
    old=read(snapshot);oldroot=Path(old['root'])
    expected={r['relative']:r['sha256'] for r in old['files']}
    require(len(expected)==len(old['files']) and inventory(oldroot)==expected,'original complete code inventory changed')
    actual=inventory(worker.root)
    require(set(expected)<=set(actual),'warning successor removed code')
    delta=sorted(k for k in actual if actual[k]!=expected.get(k))
    require(set(delta)<=ALLOWED and bool(set(delta)&{
        '.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py',
        'scripts/batch/harbor_dialogue_source_warnings.py'}),'warning successor has unrelated code delta')
    if '.pipeline-code.json' in delta:validate_code_manifest(worker.root)
    code=renewal._historical_inventory(replace(worker,root=oldroot),snapshot)
    renewed=None
    if worker.lease_renewal is not None:
        # A renewal changes custody, not the author/review generation. Validate
        # every original renewal CAS and retained predecessor before accepting it.
        renewed=renewal.validate_historical_integrity(replace(worker,root=oldroot,warning_requalification=None),before,snapshot)
    initial=record(worker.run/'initial.json');saved=read(initial)
    require(saved['code']==code,'ordinary initial code differs from original snapshot')
    from scripts.batch import harbor_transition_codec as codec
    original=codec.decode(saved['selection']).content.bindings['original_raw_source']
    require(before['workbook_id']==codec.decode(saved['selection']).content.workbook and before['bindings']['original_raw_source']==original,'ordinary warning original source changed')
    steps=worker.run/'steps';records=[]
    for p in sorted(steps.rglob('*')):
        require(not p.is_symlink(),'ordinary history contains symlink')
        if p.is_file():records.append(record(p))
    for phase in w.STEPS[:12]:
        completed=record(steps/phase/'complete.json');value=read(completed)
        require(value['status']=='completed' and value['step']==phase,'ordinary first-round prefix incomplete')
        intent=read(value['intent'])
        require(intent['initial']==initial and intent['step']==phase and intent['code']==code,'ordinary completed intent detached')
        request=steps/phase/'request.json'
        if request.exists():
            req=w.request_saved(request);outcome=w.executor.inspect(req.prepared,worker.campaign)
            require(outcome.status=='completed' and outcome.call is not None,'ordinary completed request lacks real Call')
    blocked=record(steps/'dialogue-review/blocked.json');value=read(blocked)
    require(value['status']=='DIALOGUE_REPAIR_REQUIRED','different original rejected review')
    require(not (steps/'dialogue-review/complete.json').exists() and not (steps/'dialogue-review/exception.json').exists(),'ordinary review no longer rejected')
    intent=read(value['intent'])
    require(intent['initial']==initial and intent['code']==code,'original review code/initial changed')
    for phase in w.STEPS[13:]:require(not (steps/phase).exists(),'ordinary rejected history has a later step')
    return dict(schema_version='harbor-ordinary-first-round-warning-history/v1',code=code,initial=initial,
                blocked=blocked,records=records,original=original,snapshot=snapshot,code_changes=delta,
                **({'lease_renewal':worker.lease_renewal,'renewal_history':renewed} if renewed is not None else {}))

def validate_history(worker,before,permit):
    expected={'lease_renewal':worker.lease_renewal} if worker.lease_renewal is not None else {}
    require(permit.get('prior_continuations')==expected and permit['round']==1
            and permit['semantic'] is None and permit.get('code_repair') is None
            and permit['model_calls']==0 and permit['semantic_budget_reset'] is False,
            'ordinary warning changed history or semantic budget')
    original=replace(worker,warning_requalification=None)
    proof=history(original,before,permit['old_snapshot'])
    require(proof==permit['ordinary_first_round'],'ordinary first-round evidence changed')
    return proof
