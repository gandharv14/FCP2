"""Build an isolated 0029 pre-review successor, without claims or model calls."""
from __future__ import annotations
import argparse,ast,json,shutil
from pathlib import Path
from scripts.fleet import build_0190_successor as B,dialogue_0029_proof as P

ROOT=Path(__file__).resolve().parents[2]
Q=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z'
TASK=Q/'authoring/0029-current-03'
PREVIOUS={'path':str(TASK/'frozen-code.json'),'sha256':'3248a07a953dcc3e7fb4c8593ef28fc702e0ccf578b8db9a4a4518433032ce6e'}
REPAIR='scripts/batch/harbor_dialogue_pre_review_repair.py'
PROOF='scripts/fleet/dialogue_0029_proof.py'
VALIDATOR='.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py'

def function(raw,name,new):
    lines=raw.decode().splitlines(keepends=True);nodes=[n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name==name]
    P.require(len(nodes)==1,'0029 exact function patch required');n=nodes[0]
    return ''.join(lines[:n.lineno-1]+[new.rstrip()+'\n']+lines[n.end_lineno:]).encode()

def repair_code(raw):
    needle=b'M.validate_renewal('
    P.require(raw.count(needle)==2,'0029 known lease validator locations differ')
    raw=raw.replace(needle,b'__import__(\'scripts.fleet.dialogue_0029_proof\',fromlist=[\'validate_custody\']).validate_custody(')
    raw=B.apply(raw,[
        ("and all(expected.values())","and expected['lease_renewal'] is not None and expected['disclosure_revision'] is None"),
        ("and p['sentence_replacements']==1","and p['sentence_replacements']==36"),
        ("model_calls_added=0,sentence_replacements=1","model_calls_added=0,sentence_replacements=36")])
    needle=b'sentence_replacements=1,model_calls_added=0'
    P.require(raw.count(needle)==2,'0029 original reservation checks differ')
    raw=raw.replace(needle,b'sentence_replacements=36,model_calls_added=0')
    # Three exact validation locations; the predecessor has ordinary disclosure
    # and no manifest-revision continuation. Never synthesize one.
    old="L.validate_historical_integrity(old,before,p['old_snapshot']);M.validate_historical_integrity(old,before,p['old_snapshot'])"
    P.require(raw.decode().count(old)==2,'0029 known historical validators differ')
    raw=raw.replace(old.encode(),b"L.validate_historical_integrity(old,before,p['old_snapshot']);require(old.disclosure_revision is None,'0029 unexpected manifest revision')")
    raw=B.apply(raw,[("L.validate_historical_integrity(old,before,old_snapshot);M.validate_historical_integrity(old,before,old_snapshot)","L.validate_historical_integrity(old,before,old_snapshot);require(old.disclosure_revision is None,'0029 unexpected manifest revision')")])
    raw=B.apply(raw,[("    from scripts.batch import harbor_instruction_worker as W,harbor_instruction_lease_renewal as L,harbor_disclosure_manifest_revision as M\n", "    from scripts.batch import harbor_instruction_worker as W,harbor_instruction_lease_renewal as L,harbor_disclosure_manifest_revision as M\n    from scripts.fleet.dialogue_0029_proof import require_fleet_custody\n    require_fleet_custody(worker.root)\n")])
    raw=function(raw,'changes',"""def changes(snapshot,root):
    from scripts.fleet import dialogue_0029_proof as source_proof
    require(snapshot==source_proof.profile()['previous'],'0029 exact predecessor required')
    return source_proof.verified_code(root)
""")
    raw=function(raw,'repair_sentence',"""def repair_sentence(draft,claims,source,*,root):
    from scripts.fleet.dialogue_0029_proof import correction
    return correction(draft,claims,source,root=root)
""")
    raw=function(raw,'remaining_feedback',"""def remaining_feedback(binding,*,root):
    p=read(binding);owned(binding,p);claims=read(p['claims'])
    review=record(Path(p['dialogue_directory'])/'round-01/review.json');value=read(review)
    require(value.get('round')==1 and value.get('passed') is False,'0029 remaining feedback requires actual failed first review')
    proof=p['sentence_proof']
    return dict(schema_version='dialogue-0029-source-proved-remaining-feedback/v1',authority=binding,
        failed_review=review,source=p['source'],source_formulas=proof['source_formulas'],
        prior_correction_proof=proof,all_reviewer_findings=value,
        instruction='Use every finding from the actual first independent review. Preserve all 43 claim identities, complete source clause coverage, source periods, branch sections, arithmetic, conditions, signs and locked references. Correct only source-backed errors in the remaining ordinary author round. The original author Calls and deterministic pre-review correction remain retained provenance.')
""")
    tree=ast.parse(raw);node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='REVIEW_GUIDANCE' for t in n.targets))
    lines=raw.decode().splitlines(keepends=True)
    guidance='\nIndependently review all 43 claim identities and every final senior turn against the exact source. The source_proved_correction records deterministic postprocessing of 36 senior clauses, separately from both retained original author Calls. Verify prior/current historical periods, Assumption-Driven versus Hardcode branches, debt operands, and all unchanged claims. The source-proved two-row SUM range count is structural metadata, not a financial target value. Do not waive numerical correctness, arithmetic, sign, scope, required behavior, evidence or complete clause coverage. Return the ordinary first review; no prior dialogue review has been performed.'
    raw=''.join(lines[:node.lineno-1]+['REVIEW_GUIDANCE='+repr(guidance)+'\n']+lines[node.end_lineno:]).encode()
    return raw

def build(output):
    output=Path(output).absolute();P.require(not output.exists(),'0029 build exists; choose a new version')
    old=P.read(PREVIOUS);base=Path(old['root']);inventory={r['relative']:r['sha256'] for r in old['files']}
    actual={str(p.relative_to(base)):P.sha(p.read_bytes()) for p in base.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    P.require(actual==inventory,'0029 entire predecessor changed')
    h=Q/'0029-dialogue-period-repair';v=P.read(P.record(h/'proposal-v2-proof.json'))
    inputs=dict(source=v['source'],claims=v['claims'],original_draft=v['original_draft'],proposal=P.record(h/'proposed-draft-v3.md'),v2_proof=P.record(h/'proposal-v2-proof.json'),v3_proof=P.record(h/'proposal-v3-proof.json'),diagnosis=v['original_diagnosis'],sections=v['section_proof'])
    proof=P.build_proof(inputs);output.mkdir(parents=True);candidate=output/'candidate';shutil.copytree(base,candidate,ignore=shutil.ignore_patterns('__pycache__'))
    source_proof=B.keep(output/'source-proof.json',proof);patches={}
    def changed(name,raw,edits=None):
        p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        patches[name]={'before':P.record(base/name) if (base/name).exists() else None,'after':B.keep(output/'witnesses'/name,raw)}
        if edits is not None:patches[name]['edits']=edits
    for name,edits in B.fleet_edits().items():
        edits=[(a,b.replace('0190 successor','0029 successor').replace('scripts.batch.harbor_normalization_existing_review','scripts.fleet.dialogue_0029_proof')) for a,b in edits]
        changed(name,B.apply((base/name).read_bytes(),edits),edits)
    for name in B.ADD:changed(name,(ROOT/name).read_bytes())
    changed(REPAIR,repair_code((base/REPAIR).read_bytes()))
    prefix_name='scripts/batch/harbor_instruction_repair.py'
    needle='else:result=check_completed_gate(view,CompletionEvidence(kind,selection),context)'
    replacement="else:result=check_completed_gate(view,CompletionEvidence(kind,selection),context,**({'runtime_producer':__import__('scripts.fleet.dialogue_0029_proof',fromlist=['runtime_producer']).runtime_producer(selection)} if kind=='verification' else {}))"
    changed(prefix_name,B.apply((base/prefix_name).read_bytes(),[(needle,replacement)]),[(needle,replacement)])
    needle="        allowed.update(licensed_row_label_literals(claim))\n"
    replacement=needle+"        from scripts.fleet.dialogue_0029_proof import cardinality_budget\n        allowed.update(cardinality_budget(text,claim,source_workbook))\n"
    changed(VALIDATOR,B.apply((base/VALIDATOR).read_bytes(),[(needle,replacement)]),[(needle,replacement)])
    legacy={}
    for p in sorted((TASK/'model-campaign').glob('*/attempt-*/plan.json')):
        out=P.read(P.record(p.with_name('outcome.json')));P.require(out['status']=='completed','0029 unresolved historical Call')
        for name,digest in out['evidence'].items():P.require(P.sha((p.parent/name).read_bytes())==digest,'0029 legacy evidence changed')
        names=dict(plan='plan.json',outcome='outcome.json',request='request.json',submitted='submitted.json',response='response.raw.json',completed='completed.json',parsed='parsed.json' if p.with_name('parsed.json').exists() else 'content.txt')
        legacy[str(p.parent)]={k:P.record(p.parent/n) for k,n in names.items()}
    P.require(len(legacy)==8 and not (TASK/'instruction/producer/requests/dialogue-review').exists(),'0029 exact eight Calls and unused review required')
    template=B.keep(output/'proof-template.py',(ROOT/PROOF).read_bytes())
    custody=[P.record(h/f'custody-{n:03d}/complete.json') for n in (1,2,3)]
    runtime=TASK/'instruction/steps/verification-runtime/runtime'
    producer_intent=P.record(TASK/'dispatcher-v2/first/022/intent.json')
    main_build=P.record(runtime/'main-build.command.json')
    historical_runtime=dict(execution=P.record(runtime/'execution.json'),main_build=main_build,producer_intent=producer_intent,
        python_executable=P.read(producer_intent)['executable']['path'],docker_executable=P.read(main_build)['argv'][0])
    profile=B.keep(output/'profile.json',dict(schema_version='harbor-0029-reviewed-fleet-successor/v1',previous=PREVIOUS,source_proof=source_proof,proof_template=template,patches=patches,legacy_calls=legacy,custody_chain=custody,historical_runtime=historical_runtime,semantic_budget_reset=False))
    proof_code=(ROOT/PROOF).read_text();needle='\nPROFILE=None\n';P.require(proof_code.count(needle)==1,'0029 profile insertion ambiguous')
    (candidate/PROOF).write_text(proof_code.replace(needle,'\nPROFILE='+repr(profile)+'\n',1))
    files={str(p.relative_to(candidate)):P.sha(p.read_bytes()) for p in sorted(candidate.rglob('*')) if p.is_file() and p.name!='.pipeline-code.json'}
    (candidate/'.pipeline-code.json').write_bytes(P.canonical(files));fingerprint=P.sha(P.canonical(files));code=output/'code'/fingerprint;code.parent.mkdir();candidate.rename(code)
    snapshot=B.keep(output/'snapshot.json',dict(root=str(code),files=[dict(relative=str(p.relative_to(code)),sha256=P.sha(p.read_bytes())) for p in sorted(code.rglob('*')) if p.is_file()],previous=PREVIOUS,profile=profile))
    return B.keep(output/'build.json',dict(schema_version='harbor-0029-pre-review-build/v1',snapshot=snapshot,profile=profile,source_proof=source_proof,builder=P.record(__file__),claims=43,changed_senior_clauses=36,source_formula_instances=153,legacy_completed_calls=8,model_calls=0,claims_changed=0,task_evidence_mutated=False,ready_for_execution=False,remaining='Owning lease takeover and full typed pre-review authorization probe, then actual Linux continuation validation.'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(a.output)))
