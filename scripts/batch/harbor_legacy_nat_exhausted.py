"""Retain terminal rejected legacy NAT attempts before fresh bounded work.

This is custody evidence only. It adopts no historical quality verdict and
never prepares a model call, changes a counter, or qualifies a publication.
"""
from pathlib import Path
from . import harbor_legacy_research_history as L

KIND='terminal_nat_exhausted'
REASONS={'protected_section_changed','heading_structure_changed','token_mismatch'}


def selected(p):
    return p.get('staged_history',{}).get('dialogue_custody_kind')==KIND


def status(p,old):
    L.require(selected(p) and p['schema_version']==L.MCP_STAGED_SOURCE_SCHEMA
        and old['status']=='blocked_instruction_naturalization' and old['mode']=='mcp'
        and old['promotion_attempted']is False and old.get('additional_assumptions')is None,
        'only unpromoted pre-review exhausted NAT custody supported')
    blocker=old['first_blocker']
    L.require(isinstance(blocker,dict)and blocker.get('gate')in
        {'instruction_naturalization','instruction-naturalization'},'another failed stage requires diagnosis')
    L.require(old['gates'].get('instruction_naturalization')in {'blocker','fail'},
        'explicit original exhausted NAT gate required')


def extra_evidence(p,actual):
    """Optional empty repair proposal, retained without accepting a new repair."""
    if 'leak_patch.json'not in actual:return set()
    v=L.read(actual['leak_patch.json'])
    L.require(set(v)=={'generated_at','proposals','source_report_sha256','workbook'}
        and v['proposals']==[] and v['source_report_sha256']==actual['leakscan_report.json']['sha256']
        and v['workbook']in {'inputs_out/'+p['workbook']+'-inputs.xlsx','../inputs_out/'+p['workbook']+'-inputs.xlsx'}
        and isinstance(v['generated_at'],str),'nonempty or detached prior leak repair')
    return {'leak_patch.json'}


def history(p,old,package_files):
    """Verify rejected candidates, original source and complete closed namespaces."""
    status(p,old);h=p['staged_history'];wb=p['workbook'];package=Path(h['package'])
    roots=h['namespaces'];natural=[r for r in roots if Path(r).name==wb+'-instruction-naturalization']
    L.require(len(natural)==1,'one exact original NAT namespace required')
    nat=Path(natural[0]);files=roots[str(nat)]
    L.require(set(roots)=={str(package),p['tracker']['path'],str(nat),str(nat.parent/'disclosure'/(wb+'-outputs'))},
        'additional archived, author or unknown history requires reconciliation')
    state=L.read(files['state.json'])
    L.require(state['version']=='naturalize-recovery-v1' and state['status']=='exhausted'
        and type(state['attempt_limit'])is int and state['attempt_limit']==2
        and state['next_attempt']is None and state['final_instruction_sha256']is None
        and state['instruction_path']==state['source_path']==str(package/'instruction.md'),
        'legacy NAT is not closed exhausted at its original allowance')
    L.require(state['source_sha256']==files['source.snapshot.md']['sha256']==package_files['instruction.md']['sha256']
        and state['answer_key_sha256']==files['answer_key.snapshot.json']['sha256']==package_files['tests/answer_key.json']['sha256'],
        'rejected NAT changed original instruction or answer key')
    required={'answer_key.snapshot.json','frozen_spans.json','state.json','recovery.lock','spans.json','writer_source.md','validation.json','source.snapshot.md'}
    optional={'restore.json','candidate.md','candidate.marked.md'}
    per_attempt={'candidate.md','preamble_body.md','input_body.md','validation.json'}
    per_optional={'source.snapshot.md','input_body.marked.md','preamble_body.marked.md'}
    required|={f'attempt-{n:02d}/'+x for n in (1,2)for x in per_attempt}
    optional|={f'attempt-{n:02d}/'+x for n in (1,2)for x in per_optional}
    L.require(required<=set(files)<=required|optional,'partial or unknown original NAT attempt namespace')
    attempts=state['attempts'];L.require(len(attempts)==2,'both consumed NAT attempts required')
    for n,a in enumerate(attempts,1):
        prefix=f'attempt-{n:02d}/';report=L.read(files[prefix+'validation.json'])
        L.require(set(a)=={'attempt','candidate_sha256','input_submission_sha256','preamble_submission_sha256','reason_codes','valid'}
            and type(a['attempt'])is int and a['attempt']==n and a['valid']is False
            and isinstance(a['reason_codes'],list)and bool(a['reason_codes'])
            and set(a['reason_codes'])<=REASONS,'original rejection or attempt consumption differs')
        for key in ('attempt','candidate_sha256','input_submission_sha256','preamble_submission_sha256','reason_codes','valid'):
            L.require(report[key]==a[key],'attempt report detached from terminal state')
        for key,name in [('candidate_sha256','candidate.md'),('input_submission_sha256','input_body.md'),('preamble_submission_sha256','preamble_body.md')]:
            L.require(a[key]==files[prefix+name]['sha256'],'original rejected candidate or submitted bytes changed')
        L.require(report['source_sha256']==state['source_sha256'],'NAT attempt uses another source')
    L.require(L.read(files['validation.json'])==L.read(files['attempt-02/validation.json']),
        'latest original rejection differs')
    L.require(type(h['naturalization_reservations_upper_bound'])is int
        and h['naturalization_reservations_upper_bound']==2
        and type(h['dialogue_rounds_upper_bound'])is int and h['dialogue_rounds_upper_bound']==0
        and h['new_authoring_authorized']is False,'consumed NAT allowance cannot reset')
    return {'old_nat_attempts_consumed':2,'old_dialogue_rounds':0,'historical_quality_adopted':False}
