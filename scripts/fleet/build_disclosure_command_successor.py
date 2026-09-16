"""Freeze a minimal retained disclosure-command successor, without task writes."""
from pathlib import Path
import hashlib,json,shutil,sys,subprocess
from scripts.fleet.migration import binding,checked,retain
ROOT=Path(__file__).resolve().parents[2]
CORE='scripts/batch/harbor_disclosure_command_repair.py'
W='scripts/batch/harbor_instruction_worker.py'
R='scripts/batch/harbor_instruction_repair.py'
I='scripts/harbor_instruction_campaign.py'
D='.cursor/skills/task-disclosure/scripts/disclose.py'
REGISTRY='.cursor/skills/task-disclosure/REGISTRY.md'

def copy_snapshot_file(source,destination):
 # Large retained verification manifests dominate snapshot storage. APFS
 # clones have private inodes and preserve original bytes on later edits.
 # Other platforms/files retain the ordinary portable copy behavior.
 if sys.platform=='darwin' and Path(source).stat().st_size>=1024*1024:
  result=subprocess.run(['/bin/cp','-cp',str(source),str(destination)],capture_output=True)
  if result.returncode==0:return str(destination)
 return shutil.copy2(source,destination)

def patches(root):
 values={name:(root/name).read_text()for name in (W,R,I)}
 def change(name,old,new):
  assert values[name].count(old)==1,(name,old)
  values[name]=values[name].replace(old,new,1)
 change(W,'def task(worker, selection, tracker):\n    return producer.Task','def task(worker, selection, tracker):\n    from scripts.batch.harbor_disclosure_command_repair import producer_run\n    return producer.Task')
 change(W,"worker.run/'producer', selection.content.model_policy, worker.root,worker.code_repair,","producer_run(worker), selection.content.model_policy, worker.root,worker.code_repair,")
 old='def _operation(worker, selection, lease, phase, directory, *, models, containers, commands, credential, lock_fd):\n'
 change(W,old,old+"    from scripts.batch import harbor_disclosure_command_repair as command_repair\n    if phase=='disclosure-build' and command_repair.selected(worker):\n        return command_repair.remaining_operation(worker,selection,lease,directory,commands=commands,lock_fd=lock_fd)\n")
 old='    required=record(pending_now[-1]) if pending_now else preceding\n'
 change(W,old,old+"    if worker.code_repair is not None and not pending_now and next_phase=='disclosure-build':\n        from scripts.batch.harbor_disclosure_command_repair import selected as command_repair_selected\n        if command_repair_selected(worker):required=worker.code_repair\n")
 change(R,'def validate(worker,before):\n    receipt=read(worker.code_repair)',"def validate(worker,before):\n    from scripts.batch import harbor_disclosure_command_repair as command_repair\n    if command_repair.selected(worker):return command_repair.validate(worker,before)\n    receipt=read(worker.code_repair)")
 old="        if completion.get('schema_version')=='harbor-dialogue-pre-review-repair/v1':"
 change(I,old,"        if completion.get('schema_version')=='harbor-disclosure-command-repair/v1':\n            configured=worker_decode(read(descriptor)['worker'])\n            require(configured.code_repair==bound,'disclosure command checkpoint detached from worker')\n            permit=completion\n        elif completion.get('schema_version')=='harbor-dialogue-pre-review-repair/v1':")
 return {name:value.encode()for name,value in values.items()}

def build(home,old_snapshot,renderer,*,registry=None,core=None):
 home=Path(home).absolute();assert not home.exists();old=checked(old_snapshot);root=Path(old['root'])
 expected={x['relative']:x['sha256']for x in old['files']};assert len(expected)==len(old['files'])
 actual={str(x.relative_to(root)):binding(x)['sha256']for x in root.rglob('*')if x.is_file()and '__pycache__'not in x.parts};assert actual==expected
 core=core or binding(ROOT/CORE)
 for row in (renderer,core,*([registry]if registry else [])):assert binding(row['path'])==row
 edits=patches(root);edits[D]=Path(renderer['path']).read_bytes();edits[CORE]=Path(core['path']).read_bytes()
 if registry:edits[REGISTRY]=Path(registry['path']).read_bytes()
 home.mkdir(parents=True);stage=home/'stage';shutil.copytree(root,stage,ignore=shutil.ignore_patterns('__pycache__'),copy_function=copy_snapshot_file)
 for relative,raw in edits.items():target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
 manifests=set(expected)&{'.pipeline-code.json','.pipeline-code-manifest.json'};assert len(manifests)<=1;manifest=next(iter(manifests),'.pipeline-code.json')
 files={str(x.relative_to(stage)):binding(x)['sha256']for x in stage.rglob('*')if x.is_file()and x.name!=manifest}
 fingerprint=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 (stage/manifest).write_text(json.dumps(files,sort_keys=True,indent=2)+'\n');final=home/'code'/fingerprint;final.parent.mkdir();stage.rename(final)
 inventory=[dict(relative=str(x.relative_to(final)),sha256=binding(x)['sha256'])for x in sorted(final.rglob('*'),key=lambda p:str(p.relative_to(final)))if x.is_file()]
 snapshot=retain(home/'snapshot.json',dict(root=str(final),files=inventory))
 return retain(home/'build.json',dict(schema_version='fleet-disclosure-command-successor/v1',old_snapshot=old_snapshot,snapshot=snapshot,renderer=renderer,registry=registry,core=core,changes=sorted(k for k,v in {x['relative']:x['sha256']for x in inventory}.items()if expected.get(k)!=v),models=0,task_mutations=0))
