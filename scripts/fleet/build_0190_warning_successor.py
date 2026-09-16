"""Build one source-proved renewed first-round warning reader; no owning action."""
from pathlib import Path
import json,hashlib,shutil
from scripts.fleet.migration import binding,checked,retain

ROOT=Path(__file__).resolve().parents[2]
M='scripts/batch/harbor_normalization_existing_review.py'
FIRST='scripts/batch/harbor_dialogue_first_round_warning.py'
WARN='scripts/batch/harbor_dialogue_warning_requalification.py'
SW='scripts/batch/harbor_dialogue_source_warnings.py'
V='.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py'
W='scripts/batch/harbor_instruction_worker.py'
LINE='scripts/fleet/dialogue_warning_lineage_0190.py'
SOURCE='scripts/fleet/dialogue_source_warning_0190.py'
HISTORY='scripts/fleet/dialogue_warning_history_0190.py'

FIRST_SOURCE='''"""Only 0190's source-proved first review following its retained renewal."""
from dataclasses import replace
from scripts.batch.harbor_admission import require
from scripts.fleet import dialogue_warning_lineage_0190 as L
from scripts.fleet.dialogue_warning_history_0190 import RUN_SUFFIX

def ordinary(worker):
    from scripts.batch.harbor_instruction_lease_renewal import CONTINUATIONS
    return str(worker.run).endswith('/'+RUN_SUFFIX) and worker.lease_renewal is not None and all(
        getattr(worker,n,None) is None for n in CONTINUATIONS if n!='lease_renewal')

def history(worker,before,snapshot):
    require(ordinary(worker),'0190 ordinary renewed history required')
    return L.retained(worker,before,snapshot)

def validate_history(worker,before,permit):
    prior=permit.get('prior_continuations')
    require(prior=={'lease_renewal':worker.lease_renewal} and permit['round']==1
            and permit['semantic'] is None and permit.get('code_repair') is None
            and permit['model_calls']==0 and permit['semantic_budget_reset'] is False,
            '0190 warning changed renewed history or semantic cap')
    original=replace(worker,warning_requalification=None)
    proof=history(original,before,permit['old_snapshot'])
    require(proof==permit['ordinary_first_round'],'0190 historical warning proof differs')
    return proof
'''


def build(home, old_snapshot, source_proof):
    home=Path(home).resolve();home.mkdir(exist_ok=False)
    old=checked(old_snapshot);root=Path(old['root'])
    expected={r['relative']:r['sha256']for r in old['files']}
    assert root.name=='c7ad14b03f7c0ec3c6364efbb5e388affd9036ac15d745131ea306602700808f'
    assert old_snapshot['sha256']=='005d315edca5fc52fc6639d2bd14d5830ceaf3b88d991b106ce59d75a3408924'
    actual={str(p.relative_to(root)):binding(p)['sha256']for p in root.rglob('*')if p.is_file()and '__pycache__'not in p.parts}
    assert actual==expected
    proof=checked(source_proof);assert proof['status']=='SOURCE_PROVED_PRESENTATION_WARNINGS'and proof['models']==proof['owning_changes']==0
    assert source_proof['sha256']=='aad9463417265e877003ec4c56d71db4d85f1c1e1311e8b077853cfe574bab21'
    edits={}
    def change(name,a,b):
        current=(root/name).read_text()
        for x,y in edits.get(name,[]):current=current.replace(x,y,1)
        assert current.count(a)==1,(name,a)
        edits.setdefault(name,[]).append((a,b))
    # The prior independently tested first-round extension adds no model path.
    shared=(ROOT/WARN).read_bytes();assert hashlib.sha256(shared).hexdigest()=='5e4ff4d7d7ff6cce045e2850bf2868fc17c0879aee9444d05ccdc3cacf779917'
    change(WARN,(root/WARN).read_text(),shared.decode())
    needle="ALLOWED.add('scripts/batch/harbor_dialogue_first_round_warning.py')"
    roles=[SOURCE,HISTORY,LINE,M,'.pipeline-code.json']
    change(WARN,needle,needle+'\nALLOWED.update('+repr(sorted(roles))+')')
    change(W,"and renewal is None and worker.disclosure_revision is None:\n", "and renewal is None and worker.disclosure_revision is None and worker.warning_requalification is None:\n")
    change(M,'def _fleet_predecessor(now):\n', 'def _fleet_predecessor(now):\n from scripts.fleet.dialogue_warning_lineage_0190 import predecessor\n now=predecessor(now)\n')
    change(SW,'def validate(review,claims,draft,template,writer_filled,paraphraser_filled,source,*,task_dir,root):\n',
        'def validate(review,claims,draft,template,writer_filled,paraphraser_filled,source,*,task_dir,root):\n    if review.get("sha256")=="702eb92d6397c0293088ad246af7592f5ac1da872107a4a39305a2624245d527":\n        from scripts.fleet.dialogue_source_warning_0190 import validate as exact\n        return exact(review,claims,draft,template,writer_filled,paraphraser_filled,source,task_dir=task_dir,root=root)\n')
    change(SW,'    require(proof[\'root\']==str(Path(root).resolve()),\'warning proof belongs to different current code\')',
        '    if proof.get("schema_version")=="fleet-0190-dialogue-source-warning/v1":\n        from scripts.fleet.dialogue_source_warning_0190 import revalidate as exact\n        return exact(proof,review_payload,claims_payload,draft_text,root=root)\n    require(proof[\'root\']==str(Path(root).resolve()),\'warning proof belongs to different current code\')')
    change(V,'    if source_warning_proof is not None:\n        from scripts.batch import harbor_dialogue_source_warnings as source_warnings\n',
        '    if source_warning_proof is not None and source_warning_proof.get("schema_version")=="fleet-0190-dialogue-source-warning/v1":\n        from scripts.fleet.dialogue_source_warning_0190 import revalidate as exact\n        return exact(source_warning_proof, review, claims_payload, draft_text, root=Path(__file__).resolve().parents[4])["warnings"]\n    if source_warning_proof is not None:\n        from scripts.batch import harbor_dialogue_source_warnings as source_warnings\n')
    stage=home/'staging';shutil.copytree(root,stage,ignore=shutil.ignore_patterns('__pycache__'))
    changes={};(home/'delta').mkdir()
    for i,(name,steps) in enumerate(edits.items()):
        text=(root/name).read_text()
        for a,b in steps:text=text.replace(a,b,1)
        raw=text.encode();(stage/name).write_bytes(raw);p=home/'delta'/('%02d.py'%i);p.write_bytes(raw)
        changes[name]=dict(before=binding(root/name),after=binding(p),edits=steps)
    additions={}
    for i,(name,raw) in enumerate([(FIRST,FIRST_SOURCE.encode()),(SOURCE,(ROOT/SOURCE).read_bytes()),(HISTORY,(ROOT/HISTORY).read_bytes())]):
        p=home/'delta'/('added-%02d.py'%i);p.write_bytes(raw);target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);additions[name]=binding(p)
    profile=retain(home/'profile.json',dict(schema_version='fleet-0190-warning-code-lineage/v1',old_snapshot=old_snapshot,changes=changes,additions=additions,source_proof=source_proof,semantic_budget_reset=False,models_dispatched=0))
    text=(ROOT/LINE).read_text();assert text.count('PROFILE = None')==1;text=text.replace('PROFILE = None','PROFILE = '+repr(profile));(stage/LINE).write_text(text)
    files={str(p.relative_to(stage)):binding(p)['sha256']for p in stage.rglob('*')if p.is_file()and p.name!='.pipeline-code.json'}
    fingerprint=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    (stage/'.pipeline-code.json').write_text(json.dumps(files,sort_keys=True,indent=2)+'\n')
    final=home/'code'/fingerprint;final.parent.mkdir();stage.rename(final)
    snapshot=retain(home/'snapshot.json',dict(root=str(final),files=[dict(relative=str(p.relative_to(final)),sha256=binding(p)['sha256'])for p in sorted(final.rglob('*'))if p.is_file()]))
    return retain(home/'build.json',dict(snapshot=snapshot,profile=profile,source_proof=source_proof,original=old_snapshot,models=0,owning_operations=0,task_artifacts_modified=False,semantic_budget_reset=False))

if __name__=='__main__':
    import sys
    h=Path(sys.argv[1]).resolve();parent=h.parent
    print(build(h,binding(parent/'old-code.json'),binding(parent/'native-source-proof-001.json')))
