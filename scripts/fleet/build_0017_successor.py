"""Immutable 0017 source-defined cumulative-ratio continuation, no execution."""
import argparse,ast,json,shutil
from pathlib import Path
from scripts.fleet import build_0190_successor as B,disclosure_0017_proof as P
ROOT=B.ROOT
HOME=B.Q/'authoring/0017-generated-05'
PROOF='scripts/fleet/disclosure_0017_proof.py'
REPAIR='scripts/fleet/disclosure_0017_repair.py'
REGISTRY='.cursor/skills/task-disclosure/REGISTRY.md'
RENDERER='.cursor/skills/task-disclosure/scripts/disclose.py'
ENTRY='''
## `method_cumulative_ratio`

- **Id.** `method_cumulative_ratio`
- **Question.** How does the disclosed cumulative amount relate to its fixed denominator?
- **Alternatives.** `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
- **Detection.** Only the separately hash-bound 0017 source proof selects the exact
  cumulative Uses of Capital to Management Adjusted EBITDA family. The numerator's
  first row remains fixed and its final row advances. This is a ratio, not a claim
  about debt draws, repayments, discounting or a financial modeling assumption.
- **Out of catalog.** Render every source operand and reference lock independently.
  The ordinary method predicate suppresses graded cells and their equality aliases;
  the ordinary scope gate requires the complete non-graded copied family.
'''

def edits():
    w='scripts/batch/harbor_instruction_worker.py';r='scripts/batch/harbor_instruction_repair.py'
    return {
      RENDERER:[('METHOD_ENTRY_IDS = set(METHOD_ROLE_PATTERNS)',"METHOD_ENTRY_IDS = set(METHOD_ROLE_PATTERNS) | {'method_cumulative_ratio'}")],
      w:[("                         worker.run/'producer', selection.content.model_policy", "                         __import__('scripts.fleet.disclosure_0017_repair',fromlist=['producer_run']).producer_run(worker), selection.content.model_policy"),
         ("    if phase == 'disclosure-build':\n", "    if phase == 'disclosure-build':\n        from scripts.fleet import disclosure_0017_repair as scope_repair\n        if scope_repair.selected(worker):\n            return scope_repair.remaining_operation(worker,t,selection,lease,directory,commands=commands,lock_fd=lock_fd)\n"),
         ("    required=record(pending_now[-1]) if pending_now else preceding\n", "    required=record(pending_now[-1]) if pending_now else preceding\n    if worker.code_repair is not None and next_phase=='disclosure-build' and not pending_now:\n        from scripts.fleet import disclosure_0017_repair as scope_repair\n        if scope_repair.selected(worker):required=worker.code_repair\n")],
      r:[("def validate(worker,before):\n", "def validate(worker,before):\n    from scripts.fleet import disclosure_0017_repair as scope_repair\n    if scope_repair.selected(worker):return scope_repair.validate(worker,before)\n"),
         ("    receipt = read(worker.code_repair)\n    require(receipt['schema_version'] == SCHEMA", "    from scripts.fleet import disclosure_0017_repair as scope_repair\n    if scope_repair.selected(worker):\n        receipt=scope_repair.validate(worker,before)\n        return receipt,[worker.code_repair,receipt['initial'],receipt['old_snapshot'],receipt['source_proof'],receipt['takeover']]\n    receipt = read(worker.code_repair)\n    require(receipt['schema_version'] == SCHEMA")],
    }

def build(output):
    import importlib.util
    output=Path(output).absolute();P.require(not output.exists(),'0017 immutable output already exists')
    previous=P.record(HOME/'frozen-code.json');prior=P.read(previous);old=Path(prior['root']);inventory={r['relative']:r['sha256'] for r in prior['files']}
    actual={str(p.relative_to(old)):P.sha(p.read_bytes()) for p in old.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    P.require(actual==inventory,'0017 complete original snapshot changed')
    output.mkdir(parents=True);candidate=output/'candidate';shutil.copytree(old,candidate,ignore=shutil.ignore_patterns('__pycache__'))
    spec=importlib.util.spec_from_file_location('original_0017_renderer',old/RENDERER);D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)
    stage=HOME/'environment/stages/0017-outputs';old_disclosure=P.read(P.record(stage/'tests/disclosure.json'))
    retained_disclosure=B.keep(output/'source-evidence/original-disclosure.json',(stage/'tests/disclosure.json').read_bytes())
    inputs=dict(source=P.record(old_disclosure['golden']),disclosure=retained_disclosure,records=P.record(HOME/'instruction/producer/disclosure-build/0017-outputs/records.json'),faithcheck=P.record(HOME/'instruction/producer/disclosure-build/0017-outputs/faithcheck.json'),delivered=P.record(D.find_environment(stage)),answer_key=P.record(stage/'tests/answer_key.json'))
    proof=B.keep(output/'source-proof.json',P.source_proof(D,inputs));patches={}
    def changed(name,raw,changes=None):
        p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        patches[name]=dict(before=P.record(old/name) if (old/name).exists() else None,after=B.keep(output/'witnesses'/name,raw))
        if changes is not None:patches[name]['edits']=changes
    for name,changes in B.fleet_edits().items():
        changes=[(a,b.replace('0190 successor','0017 successor').replace('scripts.batch.harbor_normalization_existing_review','scripts.fleet.disclosure_0017_proof')) for a,b in changes]
        changed(name,B.apply((old/name).read_bytes(),changes),changes)
    for name in (*B.ADD,REPAIR):changed(name,(ROOT/name).read_bytes())
    for name,changes in edits().items():changed(name,B.apply((old/name).read_bytes(),changes),changes)
    changed(REGISTRY,(old/REGISTRY).read_bytes()+ENTRY.encode())
    legacy={}
    for p in sorted((HOME/'model-campaign').glob('*/attempt-*/plan.json')):
        outcome=P.read(P.record(p.with_name('outcome.json')));P.require(outcome['status']=='completed','0017 unresolved original Call')
        for name,digest in outcome['evidence'].items():P.require(P.record(p.parent/name)['sha256']==digest,'0017 original Call evidence changed')
        names=dict(plan='plan.json',outcome='outcome.json',request='request.json',submitted='submitted.json',response='response.raw.json',completed='completed.json',parsed='parsed.json' if p.with_name('parsed.json').exists() else 'content.txt')
        legacy[str(p.parent)]={k:P.record(p.parent/v) for k,v in names.items()}
    P.require(len(legacy)==3,'0017 exact three completed Calls required')
    step=HOME/'instruction/steps/disclosure-build';descriptor=P.read(P.record(HOME/'dispatcher-v2/first/008/descriptor.json'));campaign={k:v['value'] for k,v in descriptor['worker']['campaign']['value'].items()}
    history={k:P.record(p) for k,p in dict(initial=HOME/'instruction/initial.json',initial_tracker=HOME/'instruction/initial-tracker.json',exception=step/'exception.json',failed_command=step/'after-03/complete.json',role_request=step/'role-request-01.json',role_result=HOME/'instruction/producer/role-arbitration/attempt-01/result.json',role_resolutions=HOME/'instruction/producer/disclosure-build/0017-outputs/role_resolutions.json',command_plan=step/'command-plan.json').items()}
    history['commands']=[P.record(step/name/'complete.json') for name in ['before-00','before-01','before-02','roles-validate','after-00','after-01','after-02']]
    template=B.keep(output/'proof-template.py',(ROOT/PROOF).read_bytes())
    profile=B.keep(output/'profile.json',dict(schema_version='harbor-0017-reviewed-cumulative-ratio-successor/v1',previous=previous,source_proof=proof,original_disclosure=P.record(stage/'tests/disclosure.json'),proof_template=template,patches=patches,legacy_calls=legacy,history=history,campaign=campaign,stage_tree={str(p.relative_to(stage)):P.sha(p.read_bytes()) for p in stage.rglob('*') if p.is_file()},semantic_budget_reset=False))
    text=(ROOT/PROOF).read_text();P.require(text.count('\nPROFILE=None\n')==1,'0017 exact profile template required');(candidate/PROOF).write_text(text.replace('\nPROFILE=None\n','\nPROFILE='+repr(profile)+'\n'))
    files={str(p.relative_to(candidate)):P.sha(p.read_bytes()) for p in sorted(candidate.rglob('*')) if p.is_file() and p.name!='.pipeline-code.json'}
    (candidate/'.pipeline-code.json').write_bytes(P.canonical(files));fingerprint=P.sha(P.canonical(files));code=output/'code'/fingerprint;code.parent.mkdir();candidate.rename(code)
    snapshot=B.keep(output/'snapshot.json',dict(root=str(code),files=[dict(relative=str(p.relative_to(code)),sha256=P.sha(p.read_bytes())) for p in sorted(code.rglob('*')) if p.is_file()]))
    return B.keep(output/'build.json',dict(schema_version='harbor-0017-cumulative-ratio-build/v1',snapshot=snapshot,profile=profile,source_proof=proof,builder=P.record(__file__),legacy_completed_calls=3,disclosure_review_ordinal=1,disclosure_review_limit=1,model_calls=0,claims_changed=0,task_evidence_mutated=False,execution_ready=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(a.output)))
