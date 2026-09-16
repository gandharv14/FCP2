"""Prepare immutable source-revision data; never claim or execute a task."""
from pathlib import Path
import ast,hashlib,json,subprocess
from scripts.fleet.migration import binding,retain


def checked(row):
    p=Path(row['path'])
    if binding(p)!=row:raise ValueError('selected source revision bytes changed')
    return p


def inventory(snapshot):
    value=json.loads(checked(snapshot).read_bytes());root=Path(value['root'])
    expected=({r['relative']:r['sha256']for r in value['files']}if 'files'in value
              else{k:r['sha256']for k,r in value['bindings'].items()})
    actual={str(p.relative_to(root)):binding(p)['sha256']for p in root.rglob('*')if p.is_file()}
    if actual!=expected:raise ValueError('complete original snapshot differs')
    return root,expected


def source_dispatch(text):
    """Add the already supported source revision consumer to original readers."""
    for signature,call in [('def validate(worker,before):','validate(worker,before)'),
                           ('def validate_task(task):','validate_task(task)'),
                           ('def validate_review_history(binding,call,manifest,instruction,disclosure):',
                            'validate_review_history(binding,call,manifest,instruction,disclosure)')]:
        node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)and n.name==call.split('(')[0])
        segment=ast.get_source_segment(text,node)
        if "if p.get('source_revision') is not None:"in segment:continue
        anchor="    if p.get('schema_version')=='harbor-disclosure-manifest-revision/v1':"
        if segment.count(anchor)!=1:raise ValueError('original revision reader boundary differs')
        addition="    if p.get('source_revision') is not None:\n        from scripts.batch.harbor_disclosure_source_revision import "+node.name+"\n        return "+call+"\n"
        text=text.replace(segment,segment.replace(anchor,addition+anchor),1)
    return text


def producer_dispatch(text):
    node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)and n.name=='task')
    segment=ast.get_source_segment(text,node)
    if 'from scripts.batch.harbor_disclosure_source_revision import producer_run'in segment:return text
    if segment.count("worker.run/'producer'")!=1:raise ValueError('original task producer boundary differs')
    patched=segment.replace('    return producer.Task(',
        '    from scripts.batch.harbor_disclosure_source_revision import producer_run\n    return producer.Task(',1)
    patched=patched.replace("worker.run/'producer'",'producer_run(worker)')
    return text.replace(segment,patched,1)


def lease_dispatch(text):
    node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)and n.name=='_retained_lease_adapter')
    segment=ast.get_source_segment(text,node)
    if 'from scripts.batch.harbor_disclosure_source_revision import historical_lease'in segment:return text
    anchor="        revision=read(worker.disclosure_revision)\n"
    if segment.count(anchor)!=1:raise ValueError('original renewal adapter boundary differs')
    added="        if revision.get('source_revision') is not None and revision.get('prior_continuations',{}).get('lease_renewal')==worker.lease_renewal:\n            from scripts.batch.harbor_disclosure_source_revision import historical_lease\n            return historical_lease(worker,before)\n"
    return text.replace(segment,segment.replace(anchor,anchor+added),1)


def original_inventory(snapshot,destination):
    root,files=inventory(snapshot)
    return retain(Path(destination),dict(root=str(root),files=[dict(relative=n,sha256=files[n])for n in sorted(files)]))


def freeze(original_snapshot,renderer_selection,core,destination):
    """Derive only renderer/source-reader changes over the exact original code."""
    root,before=inventory(original_snapshot);selection=json.loads(checked(renderer_selection).read_bytes())
    for k in ('original_disclose','original_registry','selected_disclose','selected_registry'):checked(selection[k])
    rel='.cursor/skills/task-disclosure/scripts/disclose.py';registry='.cursor/skills/task-disclosure/REGISTRY.md'
    if selection['original_disclose']!=binding(root/rel)or selection['original_registry']!=binding(root/registry):
        raise ValueError('renderer proof belongs to another original source runtime')
    checked(core);destination=Path(destination);destination.mkdir();stage=destination/'stage'
    subprocess.run(['/bin/cp','-cR',str(root),str(stage)],check=True)
    changes={rel:checked(selection['selected_disclose']).read_bytes(),registry:checked(selection['selected_registry']).read_bytes(),
       'scripts/batch/harbor_disclosure_source_revision.py':checked(core).read_bytes(),
       'scripts/batch/harbor_disclosure_revision.py':source_dispatch((root/'scripts/batch/harbor_disclosure_revision.py').read_text()).encode(),
       'scripts/batch/harbor_instruction_lease_renewal.py':lease_dispatch((root/'scripts/batch/harbor_instruction_lease_renewal.py').read_text()).encode(),
       'scripts/batch/harbor_instruction_worker.py':producer_dispatch((root/'scripts/batch/harbor_instruction_worker.py').read_text()).encode()}
    for name,raw in changes.items():
        p=stage/name
        if p.exists():p.unlink()
        p.write_bytes(raw)
    manifests={n for n in ('.pipeline-code.json','.pipeline-code-manifest.json')if(stage/n).exists()}
    if len(manifests)!=1:raise ValueError('one original pipeline manifest required')
    manifest=next(iter(manifests));files={str(p.relative_to(stage)):binding(p)['sha256']for p in stage.rglob('*')if p.is_file()and str(p.relative_to(stage))!=manifest}
    raw=(json.dumps(files,sort_keys=True,separators=(',',':'))+'\n').encode();(stage/manifest).unlink();(stage/manifest).write_bytes(raw)
    version=hashlib.sha256(raw).hexdigest();selected=destination/'code'/version;selected.parent.mkdir();stage.rename(selected)
    after={str(p.relative_to(selected)):binding(p)['sha256']for p in selected.rglob('*')if p.is_file()}
    snapshot=retain(destination/'snapshot.json',dict(root=str(selected),files=[dict(relative=n,sha256=after[n])for n in sorted(after)]))
    delta=sorted(n for n in after if before.get(n)!=after[n])
    return retain(destination/'build.json',dict(original_snapshot=original_snapshot,selected_snapshot=snapshot,
        renderer_selection=renderer_selection,source_revision_core=core,changes=delta,models=0,claims=0))


def validation(profile,destination):
    """Generate the original rejected-Call and candidate proof, without authority."""
    destination=Path(destination);destination.mkdir();row=retain(destination/'profile.json',profile)
    snapshot=json.loads(checked(profile['snapshot']).read_bytes());root=Path(snapshot['root'])
    code='''from pathlib import Path
from dataclasses import replace
import json,sys,time,os
os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
os.environ['HARBOR_FLEET_CONTINUATION_READ_ONLY']='1'
sys.path.insert(0,ROOT)
from scripts import harbor_instruction_campaign as I
from scripts.batch import harbor_instruction_worker as W,harbor_disclosure_source_revision as M
p=M.read(PROFILE);descriptor=M.read(p['descriptor']);worker=replace(I.worker_decode(descriptor['worker']),root=Path(ROOT))
selection=W.codec.decode(M.read(M.record(worker.run/'steps/disclosure-review/intent.json'))['selection'])
tracker=worker.context.tracker_root/(p['workbook']+'.json');before=M.record(tracker);oldtree=W.static.tree(selection.content.stage);start=time.monotonic()
M.profile(PROFILE,worker)
request,result,body,reviewed,payload=M.original_review(p,worker,selection)
manifest,delta,faith,verified=M.candidate(p,worker,selection,body,reviewed,payload)
assert M.record(tracker)==before and W.static.tree(selection.content.stage)==oldtree
out=dict(status='ORIGINAL_REJECTED_CALL_AND_SOURCE_CANDIDATE_VALIDATED',profile=PROFILE,tracker=before,
 original_attempt_id=request.prepared.attempt_id,delta=delta,elapsed_seconds=time.monotonic()-start,
 models_dispatched=0,task_mutations=0,qualified=False)
f=Path(__file__).parent/'proof.json'
with f.open('x')as stream:json.dump(out,stream,sort_keys=True,indent=2);stream.write('\\n')
print(json.dumps(dict(status=out['status'],proof=M.record(f),delta=delta,models_dispatched=0,task_mutations=0)))
'''
    probe=destination/'probe.py';probe.write_text('ROOT='+repr(str(root))+'\nPROFILE='+repr(row)+'\n'+code)
    return dict(profile=row,probe=binding(probe))


def continuation(*,workbook,source_profile,snapshot,fresh_plan,original_job,initial_result,
                 failure_outcome,takeover_profile,takeover_completion,takeover_runtime,
                 tracker,owner,fence,output,destination,repository,original_snapshot_copy=None,
                 capacity_policy=None):
    """Select the existing DCC and retained-runtime workflow from exact bindings."""
    destination=Path(destination);destination.mkdir();helpers=destination/'implementation';helpers.mkdir()
    bound={}
    for name in ['disclosure_command_continue','instruction_semantic_continue','retained_runtime','stdlib_imports','capacity_policy']:
        p=helpers/(name+'.py')
        source=checked(capacity_policy)if name=='capacity_policy'and capacity_policy is not None else Path(repository)/'scripts/fleet'/p.name
        p.write_bytes(source.read_bytes());bound[name]=binding(p)
    profile=json.loads(checked(source_profile).read_bytes());selected=json.loads(checked(snapshot).read_bytes())
    takeover=json.loads(checked(takeover_profile).read_bytes())
    profile.update(snapshot=snapshot,candidate_runs=str(destination/'candidate-runs'),lease_history=takeover_completion)
    if profile['old_snapshot']!=takeover['original_snapshot']:raise ValueError('source/takeover original snapshots differ')
    source=retain(destination/'source-profile.json',profile)
    # This root is authenticated by the complete native source profile, without
    # requiring a local duplicate of every retained historical artifact.
    old_binding=original_snapshot_copy or takeover['original_snapshot']
    if old_binding['sha256']!=takeover['original_snapshot']['sha256']:raise ValueError('original snapshot copy differs')
    old=Path(json.loads(checked(old_binding).read_bytes())['root'])
    new=Path(selected['root'])
    context=retain(destination/'context.json',dict(workbook=workbook,fresh_plan=fresh_plan,original_job=original_job))
    base=dict(schema_version='fleet-retained-runtime/v1',runtime=bound['retained_runtime'],entrypoint=bound['instruction_semantic_continue'],
        read_call=dict(function='load',args=[context],kwargs={}),patches=[],guard_modules=[],child_plans=[],journal_roots=[],dependencies=[],symbols=[])
    lease=dict(base,dependencies=[dict(module_name='scripts.batch.'+n,original=binding(old/'scripts/batch'/(n+'.py')),selected=takeover['shared'][k])
        for n,k in [('harbor_tracker_lifecycle','lifecycle'),('harbor_lease_takeover','takeover')]])
    lease_profile=retain(destination/'lease-runtime.json',lease)
    names=['harbor_readiness','harbor_ready_transitions','harbor_instruction_worker','harbor_instruction_repair','harbor_disclosure_revision']
    if binding(old/'scripts/batch/harbor_instruction_lease_renewal.py')['sha256']!=binding(new/'scripts/batch/harbor_instruction_lease_renewal.py')['sha256']:
        names.append('harbor_instruction_lease_renewal')
    runtime=dict(base,dependencies=[dict(module_name='scripts.batch.'+n,original=binding(old/'scripts/batch'/(n+'.py')),selected=binding(new/'scripts/batch'/(n+'.py')))for n in names],
        symbols=[dict(module_name='scripts.harbor_instruction_campaign',original=binding(old/'scripts/harbor_instruction_campaign.py'),selected=binding(new/'scripts/harbor_instruction_campaign.py'),names=['_prior'])])
    runtime_profile=retain(destination/'runtime.json',runtime)
    p=dict(schema_version='fleet-disclosure-command-continuation/v1',helper=bound['disclosure_command_continue'],utilities=bound['instruction_semantic_continue'],stdlib=bound['stdlib_imports'],
        workbook=workbook,source=profile['original'],owner=owner,fence=fence,capacity_policy=bound['capacity_policy'],authorization=profile['authorization'],
        context=dict(helper=bound['instruction_semantic_continue'],plan=context,function='load',kwargs={},projection=dict(dispatcher=2,environment=3),manager=None),
        original_job=original_job,descriptor=profile['descriptor'],initial_result=initial_result,failure_outcome=failure_outcome,tracker=tracker,lease=None,profile=source,
        core=binding(new/'scripts/batch/harbor_disclosure_source_revision.py'),runtime=bound['retained_runtime'],runtime_profile=runtime_profile,output=output,
        renewal_module=None,source_revision=True,completed_takeover=dict(runtime=takeover_runtime,plan=takeover_profile,completion=takeover_completion,
            dependency_runtime=bound['retained_runtime'],dependency_profile=lease_profile))
    return retain(destination/'plan.json',p)
