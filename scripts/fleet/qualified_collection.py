"""Verify and stage an independently published task without claiming or collecting."""
from pathlib import Path,PurePosixPath
import hashlib,importlib.util,json,shlex,subprocess,sys,tarfile
if __package__ in(None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,safe
from scripts.fleet.pool import persist
from scripts.fleet.prepare_queue import HOSTS
COLLECTOR_SHA='d8b9cc78312918e38040442f781dad755db7605292c6d04f0529f580e15c2149'
def require(ok,message):
 if not ok:raise ValueError(message)

def verify_archive(archive,manifest,workbook,collector):
    require(collector['sha256']==COLLECTOR_SHA,'original portable collector required')
    require(manifest['schema_version']=='harbor-fleet-qualified-return/v1' and manifest['workbook']==workbook and manifest['model_calls']==0,'qualified manifest identity differs')
    require(binding(archive)['sha256']==manifest['archive_sha256'],'publication archive hash differs')
    with tarfile.open(archive,'r:gz') as t:
        members=t.getmembers();require(len(members)==len(manifest['files']) and {m.name for m in members}==set(manifest['files']),'publication membership differs')
        for m in members:
            name=PurePosixPath(m.name);r=manifest['files'][m.name];require(not name.is_absolute() and '..' not in name.parts and m.isfile() and m.size==r['size_bytes'],'unsafe publication member')
            h=hashlib.sha256()
            with t.extractfile(m) as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
            require(h.hexdigest()==r['sha256'],'publication member hash differs')
        report=json.loads(t.extractfile('evidence/independent-verification.json').read());generation=json.loads(t.extractfile('task/generation-manifest.json').read());tracker=json.loads(t.extractfile('evidence/tracker.json').read())
    require(report['schema_version']=='independent-published-task-copy-check/v1' and report['workbook']==workbook and all(report[k] is True for k in ('passed','byte_identical_to_immutable_generation','current_qualification_verified')) and report['model_calls']==report['runtime_operations']==0,'independent publication verification required')
    require(generation['identity']['workbook_id']==workbook and manifest['files']['task/generation-manifest.json']['sha256']==report['task_manifest']['sha256'],'publication generation differs')
    rows=manifest['files'];task_files={k[5:]:v for k,v in rows.items() if k.startswith('task/')}
    require(report['output']==manifest['remote_output'] and manifest['verification']['sha256']==rows['evidence/independent-verification.json']['sha256'] and len(task_files)==report['delivered_files'] and sum(r['size_bytes'] for r in task_files.values())==report['delivered_bytes'],'independent report/package binding differs')
    for name,bound in manifest['evidence'].items():require(rows['evidence/'+name]['sha256']==bound['sha256'],'remote evidence hash differs')
    require(tracker['workbook_id']==workbook and tracker['execution_state']=='PROMOTED' and tracker['promotion_failure'] is None and tracker['promotion_ids']['task_generation_id']==generation['generation_id'] and rows['evidence/tracker.json']['sha256']==report['tracker']['sha256'],'promoted tracker identity differs')
    require(binding(collector['path'])==collector,'portable collector changed')
    s=importlib.util.spec_from_file_location('qualified_portable_collector',collector['path']);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
    tree={k[5:]:v for k,v in manifest['files'].items() if k.startswith('task/')};m.manifest_tree(generation,tree,workbook)
    return report


def verification_continuation(plan_binding,result_binding,certificate):
    """Authenticate a separate verification completion without rewriting P."""
    c=checked(certificate)
    require(set(c)=={'schema_version','profile','guard','reconciliation'} and c['schema_version']=='fleet-publication-independent-completion/v1','closed independent completion required')
    p=checked(c['profile']);plan=checked(plan_binding)
    require(set(p)=={'schema_version','helper','publisher','implementation','plan','publication','resource','tracker','inventory','directory'}
        and p['schema_version']=='fleet-retained-publication-verification/v1' and p['plan']==plan_binding
        and result_binding['path']==p['directory']+'/result.json'
        and p['publication']['path']==plan['journal']+'/owning-publication/result.json'
        and Path(p['directory']).parent==Path(plan['journal']).parent and p['directory']!=plan['journal'],
        'independent completion detached from original publication')
    implementation=checked(p['implementation']);require(implementation['publisher']==p['publisher'],'original publisher closure differs')
    for row in (p['helper'],p['publisher'],p['inventory'],*implementation['dependencies'].values()):
        require(binding(row['path'])==row,'independent completion implementation changed')
    outer=checked(c['guard']);require(outer['returncode']==0,'verification transport failed')
    guard=json.loads(outer['stdout']);require(guard['returncode']==0 and guard['failure']is None
        and guard['seconds']==3300 and guard['memory_bytes']==8589934592,'full guarded verification required')
    result=json.loads(guard['log'].strip().splitlines()[-1])
    require(result==dict(status='REMOTE_VERIFIED',result=result_binding,models_dispatched=0,publication_mutations=0),
        'actual verification guard result differs')
    settled=checked(c['reconciliation']);require(settled['automatic_retry']is False
        and settled['result']['state']=='abandoned','prior failed publication must remain reconciled')
    matches=[o for o in settled['after']['operations'] if o['operation_id']==settled['result']['operation_id']]
    require(len(matches)==1 and matches[0]['kind']=='publication' and matches[0]['state']=='abandoned'
        and matches[0]['workbook']==plan['workbook'],'original consumed publication operation differs')
    return c,p,matches[0]


def remote_version(plan,version_custody=None):
    from scripts.fleet.publication_launch import local_publication_version
    return local_publication_version({'version_custody':version_custody} if version_custody is not None else {},plan)


def stage(plan_binding,result_binding,*,host,output,collector,local_code_root,output_root,verification=None,version_custody=None):
    require(host in HOSTS,'authorized native host required')
    plan=checked(plan_binding);out=safe(Path(output).absolute())
    require(plan['schema_version']=='harbor-fleet-remote-publication/v1','exact selected publication plan required')
    continuation=verification_continuation(plan_binding,result_binding,verification) if verification is not None else None
    if continuation is None:require(result_binding['path']==plan['journal']+'/result.json','exact selected publication result required')
    export=Path(continuation[1]['directory'] if continuation else plan['journal'])/'export'
    require(not out.exists() and Path(local_code_root).is_dir(),'fresh staging namespace and actual local runtime required')
    require(binding(collector['path'])==collector and collector['sha256']==COLLECTOR_SHA,'exact original portable collector required')
    out.mkdir(parents=True,mode=0o700)
    common=['--project=saleseng','--zone=us-central1-a','--tunnel-through-iap','--quiet']
    script="from pathlib import Path;import hashlib;row="+repr(result_binding)+";p=Path(row['path']);assert hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256'];print(p.read_text())"
    with(out/'transfer.log').open('xb')as log:
        r=subprocess.run(['gcloud','compute','ssh',host,*common,'--command',shlex.join(['python3','-I','-B','-c',script])],stdout=subprocess.PIPE,stderr=log)
        require(r.returncode==0,'verified result fetch incomplete');result=json.loads(r.stdout)
        require(result['status']=='REMOTE_VERIFIED' and result['workbook']==plan['workbook'] and result['plan']==plan_binding and result['model_calls']==0 and result['automatic_retry']is False,'actual independently verified result required')
        if continuation:
            require(result['verification_continuation']==continuation[0]['profile']
                and result['publication']==continuation[1]['publication'],'verified continuation changed original P result')
        persist(out/'remote-result.json',dict(binding=result_binding,value=result),immutable=True)
        for key,name in [('archive','task.tar.gz'),('manifest','manifest.json')]:
            row=result['package'][key];require(Path(row['path']).is_absolute() and Path(row['path']).is_relative_to(export),'remote package outside publication export')
            rc=subprocess.run(['gcloud','compute','scp',*common,host+':'+row['path'],str(out/name)],stdout=log,stderr=subprocess.STDOUT).returncode
            require(rc==0 and binding(out/name)['sha256']==row['sha256'],'artifact transfer incomplete or changed')
    manifest=checked(binding(out/'manifest.json'));report=verify_archive(out/'task.tar.gz',manifest,plan['workbook'],collector)
    if continuation:require(report['tracker']==continuation[1]['tracker'],'independent verification changed released tracker')
    selected=persist(out/'collection-plan.json',dict(schema_version='harbor-fleet-collection-plan/v1',workbook=plan['workbook'],source_sha256=plan['source_sha256'],code_root=str(safe(Path(local_code_root))),journal=str(out/'collection-operation'),archive=str(out/'task.tar.gz'),manifest=binding(out/'manifest.json'),output_root=str(safe(Path(output_root)))),immutable=True)
    return persist(out/'staged.json',dict(status='STAGED_VERIFIED_NOT_COLLECTED',plan=selected,remote_plan=plan_binding,remote_result=result_binding,remote_version=remote_version(plan,version_custody),independent_verification=manifest['verification'],collector=collector,final_output_untouched=True,claims_changed=0,models_dispatched=0,automatic_retry=False,**({'verification_continuation':verification}if verification is not None else {}),**({'version_custody':version_custody}if version_custody is not None else {})),immutable=True)
