"""Actual retained Calls against the combined reusable current consumers."""
from pathlib import Path
import subprocess,sys
from test_0274_warning_proof_propagation import PROGRAM,ROOT

def test_combined_current_consumers_preserve_actual_authority(tmp_path):
    program=PROGRAM.replace("shutil.copytree(F/'0274-warning-development',root,dirs_exist_ok=True)","shutil.copytree(Path.cwd()/'scripts',root/'scripts',dirs_exist_ok=True)\nshutil.copytree(Path.cwd()/'.cursor/skills/additional-assumptions-dialogue',root/'.cursor/skills/additional-assumptions-dialogue',dirs_exist_ok=True)")
    program=program.replace("# Publication copy gets its own task-directory proof from the same real Calls.","""# Whole readiness must not forward any static privilege after actual Call failure.
class UnprivilegedStatic(Exception):pass
old_inspect=G.static.inspect_readiness
def inspect_without_privilege(*args,**kwargs):
 assert kwargs.get('source_warning_proof') is None
 raise UnprivilegedStatic()
G.static.inspect_readiness=inspect_without_privilege
try:
 try:G.complete_readiness(bad,root=root)
 except UnprivilegedStatic:pass
 else:raise AssertionError('unprivileged static boundary not reached')
finally:G.static.inspect_readiness=old_inspect
# The reusable warning adapter requires exactly the authenticated parsed copy.
from scripts.batch import harbor_dialogue_warning_requalification as Q
raw=copy.deepcopy(result);raw['source_warning_proof']['evidence'][0]=s.semantic.dialogue_call.parsed
assert Q._checked_source_proof(raw,s.semantic.dialogue_call,s.content.dialogue_review,s.content.dialogue_claims,s.content.dialogue_reviewed_draft,root=root)==proof
changed=copy.deepcopy(raw);changed['source_warning_proof']['claim_count']-=1
rejects(lambda:Q._checked_source_proof(changed,s.semantic.dialogue_call,s.content.dialogue_review,s.content.dialogue_claims,s.content.dialogue_reviewed_draft,root=root))
# Publication copy gets its own task-directory proof from the same real Calls.""")
    result=subprocess.run([sys.executable,'-B','-c',program,str(tmp_path)],cwd=ROOT,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip()=='PASS'
