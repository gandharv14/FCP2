"""Reproducible publication preparation from a stopped fresh author.

This command records custody and prepares the original publication proof. It
never claims, authors, renews, publishes, or infers readiness from a process exit.
"""
from pathlib import Path
import argparse,hashlib,inspect,json,shlex,subprocess,sys
if __package__ in(None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,retain
from scripts.fleet import build_fresh_publication as build,build_publication_wait as wait


def native_custody(payload):
    from pathlib import Path
    import base64,hashlib,json,os
    p=payload['launch'];process=payload['process'];fresh=payload['fresh'];files={}
    def read(row):
        path=Path(row['path']);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('author artifact changed')
        files[str(path)]=raw;return json.loads(raw)
    def record(path):
        path=Path(path)
        if not path.is_absolute()or any(x.is_symlink()for x in(path,*path.parents)):raise ValueError('unsafe author artifact')
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if read(p['job'])!=payload['job'] or read(payload['fresh_row'])!=fresh:raise ValueError('prepared author differs')
    if read(dict(path=p['remote_receipt'],sha256=payload['process_sha256']))!=process:raise ValueError('author process receipt changed')
    job=payload['job'];needles=[job['run'],job['result'],p['job']['path']]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit()or int(entry.name)==os.getpid():continue
        try:
            fields=(entry/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0]=='Z':continue
            argv=[a.decode(errors='replace')for a in(entry/'cmdline').read_bytes().split(b'\0')if a]
        except(FileNotFoundError,PermissionError):continue
        if int(entry.name)==process['pid']and fields[19]==str(process['start_ticks']):raise ValueError('author still active')
        if any(n in a for n in needles for a in argv):raise ValueError('author-related process still active')
    outrow=record(Path(job['run'])/'outcome.json');out=read(outrow);intent=read(out['intent'])
    if out['returncode']!=0 or out['failure_type']is not None or intent['job']!=p['job']or intent['owner']!=p['new_owner']:raise ValueError('author did not stop successfully')
    ready=read(out['result'])
    if out['result']['path']!=job['result']or ready['status']!='PUBLICATION_READY'or ready['publication_performed']is not False:raise ValueError('original author not READY')
    author=read(ready['job']);config=read(author['config'])
    read(ready['checkpoint']);read(ready['outcome'])
    trackers=list(Path(fresh['home']).glob('**/trackers/workbooks/'+fresh['workbook']+'.json'))
    if len(trackers)!=1:raise ValueError('unique current tracker required')
    tr=record(trackers[0]);tracker=read(tr)
    if tracker['workbook_id']!=fresh['workbook']or tracker['execution_state']!='publication_queued'or tracker['terminal_verdict']!='PUBLICATION_READY':raise ValueError('tracker not queued')
    return dict(status='STOPPED_FRESH_AUTHOR_CUSTODY',outcome=outrow,ready=out['result'],tracker=tr,
                files=[dict(original=dict(path=p,sha256=hashlib.sha256(raw).hexdigest()),data=base64.b64encode(raw).decode())for p,raw in sorted(files.items())],processes=[])


def selection(launch_row,fresh_row,*,completed_pre_dispatch=None):
    launched=checked(launch_row);intent=checked(launched['intent']);p=checked(intent['plan']);job=checked(p['job']);fresh=checked(fresh_row)
    if fresh['schema_version']!='harbor-fleet-fresh-plan/v1'or job['workbook']!=fresh['workbook']or job['cwd']!=fresh['code_root']:raise ValueError('fresh author selection differs')
    if completed_pre_dispatch is not None:
        continued=checked(completed_pre_dispatch);original=checked(continued['original_worker_job'])
        if (continued['schema_version']!='fleet-pre-dispatch-continuation/v1' or 'renewal_setup'not in continued
            or job['driver']!=continued['helper'] or job['inputs'][0]!=completed_pre_dispatch
            or original['inputs'][0]!=fresh_row or original['source']!=job['source']
            or original['version']!=job['version'] or continued['workbook']!=fresh['workbook']):
            raise ValueError('completed pre-dispatch source and launch differ')
    process=launched['remote']
    if process['root_intent']!=launched['intent']or process['job']!=p['job']:raise ValueError('launch process provenance differs')
    raw=(json.dumps(process,sort_keys=True,indent=2)+'\n').encode()
    return dict(launch=p,process=process,process_sha256=hashlib.sha256(raw).hexdigest(),job=job,fresh=fresh,fresh_row=fresh_row)


def source_history_probe(text):
    start=text.index('from scripts.fleet import history as H\n');end=text.index('cp,v=D.successful',start)
    opening="""if 'legacy_research_history'in plan:
 from scripts.batch import harbor_legacy_research_history as L
 def read_only_survey(roots,ids):
  selected=L.validate(history,workbook=plan['workbook'],history_roots=roots,known_ids=ids)
  return L.author_survey(selected)
 L.projected_survey=read_only_survey
else:
 from scripts.fleet import history as H
 def read_only_survey(roots,ids):return H.inspect_projection(roots,ids,plan['workbook'],history)
 H.projected_survey=read_only_survey
prefix=pub.fresh_publication.history_prefix(plan)
os.environ.update({prefix+'PATH':history['path'],prefix+'SHA256':history['sha256'],'HARBOR_FLEET_WORKBOOK':plan['workbook'],'HARBOR_FLEET_URL':'http://127.0.0.1:8765'})
pub.fresh_publication.history_survey(pub,plan,history,pub.fresh_publication.selected_history_survey(plan))
ready=pub.fresh_publication.ready_value(pub,plan);job=pub.checked(ready['job']);D=pub.fresh_publication.dispatcher(pub,pub.pinned_dispatcher,plan,ready,job);pub.publication_only();env=D.setup(job)
"""
    return text[:start]+opening+text[end:]


def source_history(job,fresh):
    if 'legacy_research_history'in fresh:
        if 'history'in job:raise ValueError('mixed fresh source history')
        return {'legacy_research_history':fresh['legacy_research_history']}
    return {'history':job['history']}


def prepare(repository,launch_row,fresh_row,output,*,completed_pre_dispatch=None):
    import base64
    root=Path(repository).absolute();out=Path(output).absolute();payload=selection(launch_row,fresh_row,completed_pre_dispatch=completed_pre_dispatch)
    if out.exists():raise ValueError('preparation already exists; inspect retained result')
    out.mkdir(parents=True,mode=0o700)
    retain(out/'selection.json',dict(launch=launch_row,fresh=fresh_row,helper=binding(__file__),**({'completed_pre_dispatch':completed_pre_dispatch}if completed_pre_dispatch is not None else {})))
    command=['gcloud','compute','ssh',payload['launch']['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([payload['job']['command'][0],'-I','-B','-c',inspect.getsource(native_custody)+'\nimport json,sys\nprint(json.dumps(native_custody(json.load(sys.stdin))))'])]
    r=subprocess.run(command,input=json.dumps(payload).encode(),capture_output=True,timeout=120)
    (out/'custody.stderr').write_bytes(r.stderr)
    if r.returncode:raise RuntimeError('native stopped-author custody failed; inspect retained stderr')
    custody=json.loads(r.stdout);mapping=[];directory=out/'custody';directory.mkdir(mode=0o700)
    for i,row in enumerate(custody.pop('files')):
        raw=base64.b64decode(row['data'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=row['original']['sha256']:raise ValueError('custody transfer changed')
        path=directory/(f'{i:03d}-'+Path(row['original']['path']).name);path.write_bytes(raw);path.chmod(0o600)
        mapping.append(dict(original=row['original'],local=binding(path)))
    retain(out/'custody.json',dict(custody,mapping=mapping))
    fresh=payload['fresh'];job=payload['job'];control=root/'diagnostics/vm-fleet-20260912/control'
    manifest=build.build(out/'implementation-001',control/'0029-publication-003/collector.py')
    manifest=wait.build(out/'implementation-002',manifest,control/'0029-publication-003/collector.py')
    m=checked(manifest);base=checked(binding(control/'source-first/0010/publication-001/plan-base.json'))
    for key in ('history','legacy_research_history'):base.pop(key,None)
    policy=retain(out/'wait-policy.json',dict(schema_version='harbor-fleet-publication-wait/v1',workbook=job['workbook'],source_sha256=job['source']['sha256'],lease_tracker=custody['tracker'],maximum_wait_seconds=120,authorization=binding(root/'diagnostics/bulk-production/authorization-20260913.md')))
    plan_value=dict(base,workbook=job['workbook'],code_root=fresh['code_root'],fresh_authoring=fresh_row,**source_history(job,fresh),source_sha256=job['source']['sha256'],ready=custody['ready'],journal='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+job['workbook']+'-publication-fresh-001',publication_wait=policy)
    if completed_pre_dispatch is not None:
        reader=out/'completed_pre_dispatch_publication.py';reader.write_bytes((root/'scripts/fleet/completed_pre_dispatch_publication.py').read_bytes())
        plan_value['completed_pre_dispatch']=dict(reader=binding(reader),plan=completed_pre_dispatch)
    plan=retain(out/'plan.json',plan_value)
    native=out/'native-ready-001';native.mkdir();text=(control/'source-first/0010/publication-001/native-ready-base-001/probe.py').read_text()
    from scripts.fleet.publication_staging import child_selection_probe
    text=child_selection_probe(text)
    if 'legacy_research_history'in fresh or completed_pre_dispatch is not None:text=source_history_probe(text)
    text='\n'.join('PUBLISHER='+repr(m['publisher'])if l.startswith('PUBLISHER=')else 'PLAN='+repr(plan)if l.startswith('PLAN=')else l for l in text.splitlines())+'\n';(native/'probe.py').write_text(text)
    runner=(root/'diagnostics/bulk-production/0303-continuation/publication-001/native-ready-004/run.py').read_text();runner='\n'.join('PROBE='+repr(binding(native/'probe.py'))if l.startswith('PROBE=')else l for l in runner.splitlines())+'\n';(native/'run.py').write_text(runner)
    files=[str(p)for directory in(out/'implementation-001',out/'implementation-002')for p in directory.iterdir()if p.is_file()]+[policy['path'],plan['path'],next(iter(source_history(job,fresh).values()))['path'],str(native/'probe.py'),str(native/'run.py')]
    if completed_pre_dispatch is not None:files[-1:-1]=[plan_value['completed_pre_dispatch']['reader']['path'],completed_pre_dispatch['path']]
    retain(out/'native-files.json',files)
    return retain(out/'prepared.json',dict(plan=plan,implementation=manifest,custody=binding(out/'custody.json'),models_dispatched=0,publication_mutations=0))


def main():
    a=argparse.ArgumentParser(description=__doc__)
    for key in('repository','launch','launch-sha256','fresh','fresh-sha256','output'):a.add_argument('--'+key,required=True)
    p=a.parse_args();print(json.dumps(prepare(p.repository,dict(path=p.launch,sha256=p.launch_sha256),dict(path=p.fresh,sha256=p.fresh_sha256),p.output)))


if __name__=='__main__':main()


def prepare_queued(repository,prepared_row,allowance,output,*,completed_proof=None):
    """Prepare the existing same-owner queued lease primitive; do not renew."""
    root=Path(repository).absolute();prepared=checked(prepared_row);home=Path(prepared_row['path']).parent
    selected=checked(binding(home/'selection.json'))
    if completed_proof is None:payload=selection(selected['launch'],selected['fresh'],completed_pre_dispatch=selected.get('completed_pre_dispatch'))
    else:
        launched=checked(selected['launch']);launch=checked(checked(launched['intent'])['plan']);job=checked(launch['job']);task=checked(selected['completed'])
        if launch['root_plan']!=selected['completed']or task['schema_version']!='fleet-instruction-unacquired-model-continuation/v1'or 'completed_tail'not in task:raise ValueError('exact completed model selection required')
        payload=dict(launch=launch,job=job)
    custody=checked(prepared['custody']);rows={x['original']['path']:x for x in custody['mapping']}
    pubplan=checked(prepared['plan']);proof_native=checked(completed_proof)if completed_proof is not None else json.loads((home/'native-ready-001/native.json').read_bytes());guard=json.loads(proof_native['stdout'])
    if guard['returncode']!=0 or guard['failure']is not None:raise ValueError('full original publication proof required')
    proof=json.loads(guard['log'])
    if proof['plan']!=prepared['plan']or proof['tracker']!=custody['tracker']:raise ValueError('queued proof detached')
    out=Path(output).absolute();out.mkdir();impl=out/'implementation';impl.mkdir()
    for name,source in [('runner.py','scripts/fleet/queued_publication_renewal.py'),('queued.py','scripts/batch/harbor_queued_publication_lease.py'),('root.py','scripts/fleet/queued_renewal_launch.py')]:
        (impl/name).write_bytes((root/source).read_bytes())
    pr=retain(out/'ready-proof.json',proof);job=payload['job'];launch=payload['launch'];wb=job['workbook'];control=root/'diagnostics/vm-fleet-20260912/control'
    lp=retain(out/'lease-profile.json',dict(schema_version='harbor-queued-publication-lease/v1',workbook=wb,source_sha256=job['source']['sha256'],tracker=proof['tracker'],ready_proof=pr,allowance=allowance,home='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+wb+'-fresh-queued-lease-001'))
    process=rows[launch['remote_receipt']]['original']
    plan=retain(out/'plan.json',dict(schema_version='fleet-queued-publication-renewal/v1',implementation=binding(impl/'runner.py'),publisher=proof['publisher'],publication_plan=prepared['plan'],queued_reader=binding(impl/'queued.py'),lease_profile=lp,process=process,job=launch['job'],outcome=custody['outcome']))
    base=checked(binding(control/'source-pool-next-001/publication-001/0282/queued-lease-001/root-profile.json'))
    rp=retain(out/'root-profile.json',dict(base,helper=binding(impl/'root.py'),runner=binding(impl/'runner.py'),plan=plan,host=launch['host'],python=job['command'][0],expected_fence=launch['expected_fence']+1,expected_owner=launch['new_owner'],source_sha256=job['source']['sha256'],version=job['version'],outcome=custody['outcome'],coordinator=launch['coordinator_receipt']))
    probe=out/'probe.py';probe.write_text('import sys,importlib.util,json\np='+repr(plan)+'\nr='+repr(binding(impl/'runner.py'))+'\ns=importlib.util.spec_from_file_location("queued_publication_selected",r["path"]);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\nassert m.binding(r["path"])==r\nprint(json.dumps(m.run(p,renew=False)))\n')
    files=[*[str(p)for p in impl.iterdir()],pr['path'],lp['path'],plan['path'],rp['path'],allowance['path'],str(probe)]
    retain(out/'stage-files.json',files)
    cmd=[str(root/'.venv/bin/python'),'-I','-B',str(impl/'root.py'),'--profile',rp['path'],'--sha256',rp['sha256']]
    retain(out/'commands.json',dict(preflight=[*cmd,'--journal',str(out/'root-preflight-001')],execute=[*cmd,'--execute','--journal',str(out/'root-execute-001')]))
    return rp


def fetch_queued(root_profile,output):
    """Collect the one selected lease completion without repeating its CAS."""
    import base64
    p=checked(root_profile);plan=checked(p['plan']);lease=checked(plan['lease_profile']);out=Path(output).absolute();out.mkdir(mode=0o700)
    code='from pathlib import Path;import json,hashlib,base64;h=Path('+repr(lease['home'])+');print(json.dumps([dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),data=base64.b64encode(p.read_bytes()).decode())for p in sorted(h.iterdir())if p.is_file()]))'
    c=['gcloud','compute','ssh',p['host'],'--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet','--command',shlex.join([p['python'],'-I','-B','-c',code])]
    r=subprocess.run(c,capture_output=True,timeout=120);(out/'stderr').write_bytes(r.stderr)
    if r.returncode:raise RuntimeError('queued completion custody unavailable; never repeat CAS')
    rows=[]
    for row in json.loads(r.stdout):
        raw=base64.b64decode(row['data'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('queued custody transfer differs')
        path=out/Path(row['path']).name;path.write_bytes(raw);path.chmod(0o600);rows.append(dict(native=dict(path=row['path'],sha256=row['sha256']),copy=binding(path)))
    return retain(out/'mapping.json',dict(schema_version='fleet-offline-bindings/v1',copies=rows))


def project_queued(prepared_row,root_profile,actual):
    prepared=checked(prepared_row);home=Path(prepared_row['path']).parent;p=checked(prepared['plan']);rp=checked(root_profile);q=checked(rp['plan']);rows=checked(actual)['copies']
    completion=next(r for r in rows if r['native']['path']==str(Path(checked(q['lease_profile'])['home'])/'complete.json'))
    value=checked(completion['copy'])
    if value['status']!='QUEUED_LEASE_RENEWED'or value['automatic_retry']is not False:raise ValueError('queued extension not completed')
    policy=checked(p['publication_wait']);policy['lease_tracker']=dict(policy['lease_tracker'],sha256=value['after']['sha256']);p['publication_wait']=retain(home/'wait-policy-002.json',policy);p['queued_lease']=dict(reader=q['queued_reader'],completion=completion['native']);plan=retain(home/'plan-002.json',p)
    out=home/'native-ready-002';out.mkdir();text=(home/'native-ready-001/probe.py').read_text();text='\n'.join('PLAN='+repr(plan)if l.startswith('PLAN=')else l for l in text.splitlines())+'\n';text=text.replace('request=D.publication_request(env,v)','request=pub.fresh_publication.projected_request(pub,env[2],D.publication_request(env,v),plan)');(out/'probe.py').write_text(text)
    text=(home/'native-ready-001/run.py').read_text();text='\n'.join('PROBE='+repr(binding(out/'probe.py'))if l.startswith('PROBE=')else l for l in text.splitlines())+'\n';(out/'run.py').write_text(text)
    retain(out/'stage-files.json',[p['publication_wait']['path'],plan['path'],str(out/'probe.py'),str(out/'run.py')]);return plan


def prepare_root(repository,prepared_row,publication_plan,guard_result,output,controller):
    """Build the ordinary original publication launcher after the native proof."""
    from scripts.fleet import publication_launch as L
    root=Path(repository).absolute();prepared=checked(prepared_row);home=Path(prepared_row['path']).parent
    selected=checked(binding(home/'selection.json'));payload=selection(selected['launch'],selected['fresh'],completed_pre_dispatch=selected.get('completed_pre_dispatch'));launch=payload['launch'];job=payload['job']
    observed=json.loads(checked(guard_result)['stdout']);proof=json.loads(observed['log'])
    if observed['returncode']!=0 or observed['failure']is not None or proof['plan']!=publication_plan:raise ValueError('current full native proof required')
    out=Path(output).absolute();out.mkdir();pr=retain(out/'proof.json',proof);gr=retain(out/'guard.json',observed);custody=checked(prepared['custody']);mapping={x['original']['path']:x for x in custody['mapping']};process=mapping[launch['remote_receipt']]
    # Select the existing coordinator-compatible capacity reader; no policy change.
    capacity=root/'diagnostics/vm-fleet-20260912/control/publication-control-017/implementation/scripts/fleet/capacity_policy.py'
    p=dict(schema_version=L.SCHEMA,host=launch['host'],plan=publication_plan,publisher=proof['publisher'],implementation=prepared['implementation'],proof=pr,guard=gr,worker_outcome=custody['outcome'],process=process['original'],process_copy=process['local'],expected_fence=launch['expected_fence']+1,expected_owner=launch['new_owner'],coordinator_receipt=launch['coordinator_receipt'],owner=launch['host']+':publication:'+job['workbook']+':fresh001',python=job['command'][0],capacity_policy=binding(capacity))
    profile=retain(out/'profile.json',p);L.load(profile)
    cmd=[str(root/'.venv/bin/python'),'-I','-B',str(controller),'--profile',profile['path'],'--sha256',profile['sha256']]
    retain(out/'commands.json',dict(preflight=cmd,execute=[*cmd,'--execute','--journal',str(out/'launch-001')]))
    from scripts.fleet.publication_staging import prepare as prepare_staging
    prepare_staging(profile,out);return profile
