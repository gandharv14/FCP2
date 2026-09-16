"""One explicitly authorized pre-source proof continuation; no model or publication.

The original corpus is immutable. A separate clone inherits every consumed build
before execute_one appends exactly one attempt. This route requires that no production source generation exists yet.
The original selected curation and source-producer bytes remain unchanged.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse,copy,fcntl,hashlib,json,os,shutil,sys,time

def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)), 'regular canonical file required')
    with p.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(path=str(p),sha256=sha)
def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'bound bytes changed')
    return json.loads(Path(row['path']).read_bytes())
def keep(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
    return binding(p)
def inventory(root):
    root=Path(root);rows={}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(),'corpus symlink forbidden')
        if p.is_file() and p.name!='.lock':rows[str(p.relative_to(root))]=binding(p)
    return rows

def corpus_inventory(root):
    """Record regular generation bytes and exact internal relative pointers."""
    root=Path(root);require(root.is_absolute() and not any(p.is_symlink()for p in(root,*root.parents)),'canonical corpus root required');files={};links={}
    for p in sorted(root.rglob('*')):
        name=str(p.relative_to(root))
        if p.is_symlink():
            target=p.readlink();resolved=p.resolve(strict=True)
            require(not target.is_absolute() and resolved.is_relative_to(root),'corpus pointer escapes closure')
            links[name]=str(target)
        elif p.is_file() and p.name!='.lock':files[name]=binding(p)
    return dict(files=files,links=links)

def closure_matches(p,closure):
    if 'links' not in closure:return inventory(p['corpus'])==closure['files']
    require(not closure['links'] or p['restriction_inventory'] is not None,'internal generation pointers require retained restricted materialization')
    actual=corpus_inventory(p['corpus'])
    return actual=={k:closure[k]for k in ('files','links')}


def validate_boundary(p,ledger,policy):
    old=ledger['build'];wb=p['workbook']
    require(ledger['workbook_id']==wb and ledger['source']==p['source'],'original workbook/source changed')
    remediation=p.get('schema_version')=='fleet-pre-source-continuation/v2'
    require(ledger['state']=='STOPPED' and ledger['failure']=='source_policy_unsupported'
            and set(ledger['reason_codes'])==({'external_links_present'} if remediation else {'data_tables_present'}),'explicit retained pre-source materialization boundary required')
    restricted=p.get('restriction_inventory') is not None
    if remediation:
        require(not restricted and p.get('inventory_batch_id') is None
                and p['expected_failure']=='RemediationError:remediation left unsupported volatile or external references'
                and not old.get('normalization'),'only the exact terminal external-package normalization failure is supported')
    if restricted:
        require(p['expected_failure']=='RecalculationError:restricted pass requires an approved batch inventory' and bool(old.get('normalization')),'only retained missing-inventory boundary supported')
    require(old['state']=='STOPPED' and old['stage']==('normalization' if remediation else 'source' if restricted else 'presentation_tables') and old['exit_code']==1
            and old['failure']==p['expected_failure'],'exact retained failed proof required')
    require(not old.get('source_checkpoint') and not old.get('source_generation_id') and p['generation'] is None,'no completed source generation may be discarded')
    require(old['attempts']==p['previous_attempt'] and p['next_attempt']==p['previous_attempt']+1
            and p['next_attempt']<=p['cumulative_reconstruction_limit'],'monotonic reconstruction allowance exceeded')
    require(len(ledger.get('build_history',[]))==p['previous_attempt']-1,'retained history incomplete')
    require(p['seconds']<=policy['limits']['stage_seconds'] and p['memory_bytes']<=policy['limits']['memory_bytes'],'resource allowance exceeded')
    require(p['models']==0 and p['automatic_retry'] is False,'zero-model once-only continuation required')

def load(selected):
    p=read(selected)
    remediation=p.get('schema_version')=='fleet-pre-source-continuation/v2'
    require(set(p)==({'schema_version','workbook','helper','snapshot','source','ledger','closure','corpus','inventory','restriction_inventory','inventory_batch_id','curation_root','home','allowance','previous_attempt','next_attempt','cumulative_reconstruction_limit','generation','seconds','memory_bytes','models','automatic_retry','prior_snapshot','retained_curation','expected_failure'}|({'remediation'} if remediation else set())),'closed resource plan required')
    require(p['schema_version']in('fleet-pre-source-continuation/v1','fleet-pre-source-continuation/v2') and p['helper']==binding(__file__),'selected helper changed')
    snap=read(p['snapshot']);root=Path(snap['root']);require(inventory(root)==snap['bindings'],'complete selected code changed')
    sys.path.insert(0,str(root))
    from scripts.batch import harbor_continuation_allowance as A
    policy=A.validate(p['allowance'],expected_scope=A.scope(p['workbook'],p['source']['sha256']),at=time.time_ns())
    ledger=read(p['ledger']);validate_boundary(p,ledger,policy)
    require(binding(p['source']['path'])=={k:p['source'][k] for k in ('path','sha256')},'source bytes changed')
    closure=read(p['closure']);require(closure['corpus']==p['corpus'] and closure_matches(p,closure),'original corpus changed')
    require(closure['files']['workbooks/'+p['workbook']+'/ledger.json']==p['ledger'],'ledger detached from corpus')
    read(p['inventory']);require(bool(p['restriction_inventory'])==bool(p['inventory_batch_id']),'restriction inventory and batch must be selected together')
    home=Path(p['home']);require(home.is_absolute() and not home.is_relative_to(root) and not home.is_relative_to(Path(p['corpus'])),'disjoint continuation home required')
    from scripts import corpus,corpus_build as B
    require(Path(B.__file__).resolve().is_relative_to(root) and corpus.code_hash()==snap['fingerprint'],'foreign builder loaded')
    prior=read(p['prior_snapshot']);require(inventory(prior['root'])==prior['bindings'],'original code closure changed')
    for name in ['xl_ast_graph.py','xl_graph_store.py']:
        require(binding(Path(prior['root'])/name)['sha256']==binding(root/name)['sha256'],'original source producer changed')
    if remediation:validate_remediation(p,ledger,prior,snap)
    validate_curation(p,ledger)
    if p['restriction_inventory'] is not None:validate_restriction(p,ledger)
    return p,ledger,closure,B,snap

def validate_remediation(p,ledger,prior,snap):
    """Only a selected dead-external-graph policy changes the source snapshot."""
    import xl_volatile_formula_remediation as M
    names=set(prior['bindings']);require(names==set(snap['bindings']),'source code member set changed')
    changed={n for n in names if prior['bindings'][n]['sha256']!=snap['bindings'][n]['sha256']}
    require(changed=={'xl_volatile_formula_remediation.py','.pipeline-code.json'},'only the remediator source delta is authorized')
    value=p['remediation'];require(set(value)=={'module','plan'},'closed source remediation selection required')
    require(binding(M.__file__)==value['module']==binding(Path(snap['root'])/'xl_volatile_formula_remediation.py'),'foreign remediator loaded')
    plan=read(value['plan']);M.validate_plan(plan,p['source']['path'])
    require(plan['status']=='eligible' and not plan['unresolved'] and any(a['kind']=='prune_dead_external_package' for a in plan['actions']),'actual dead external package plan required')

def validate_curation(p,ledger):
    selected=p['retained_curation']
    if selected is not None:
        require(binding(selected['path'])==selected,'retained original selection changed')
    else:
        require(p['schema_version']=='fleet-pre-source-continuation/v2'
                and ledger['build']['bindings']['curation_sha256'] is None
                and not Path(ledger['build']['curation_path']).exists()
                and not (Path(p['curation_root'])/p['workbook']/'curation.toml').exists(),'an original target selection may not be discarded')

def validate_restriction(p,ledger):
    """Native existing transformation plus original pinned inventory approval."""
    from xl_source_inventory import validate_inventory_manifest
    from xl_source_recalc import create_restriction_documents
    from xl_source_health import inspect_workbook
    from xl_presentation_tables import verify_materialization
    inventory=read(p['restriction_inventory']);validate_inventory_manifest(inventory)
    rows=[r for r in inventory['workbooks']if r['workbook_id']==p['workbook']]
    require(len(rows)==1,'selected restricted inventory workbook missing')
    n=ledger['build']['normalization'];row=rows[0];manifest=read(dict(path=n['manifest'],sha256=n['manifest_sha256']))
    require(binding(n['candidate'])['sha256']==n['candidate_sha256']==row['sha256'],'restricted candidate differs')
    lineage=row.get('transformation')or{}
    require(lineage.get('schema_version')=='source-transformation-lineage/v1' and lineage.get('original_path')==p['source']['path'] and lineage.get('original_source',{}).get('sha256')==p['source']['sha256'],'restricted original lineage differs')
    require(lineage.get('candidate_path')==n['candidate'] and lineage.get('curation_path')==p['retained_curation']['path'] and lineage.get('receipt')==manifest,'restricted transformation differs')
    verify_materialization(p['source']['path'],n['candidate'],p['retained_curation']['path'],manifest)
    health=inspect_workbook(n['candidate']);require(health['route']=='restricted_pass','only native restricted source supported')
    request,result=create_restriction_documents(n['candidate'],health,inventory,batch_id=p['inventory_batch_id'],workbook_id=p['workbook'])
    return dict(request_schema=request['schema_version'],result_schema=result['schema_version'],inventory=p['restriction_inventory'],batch=p['inventory_batch_id'],candidate_sha256=row['sha256'])


def arguments(p,run):
    return SimpleNamespace(inventory=p['inventory']['path'],run_root=str(run),curation_root=p['curation_root'],code_root=read(p['snapshot'])['root'],source_inventory=(p['restriction_inventory']['path'] if p['restriction_inventory'] else None),inventory_batch_id=p['inventory_batch_id'],recalc_root=None,trusted_public_key='/etc/harbor/excel-runner-public.pem',timeout=p['seconds'],memory_mb=p['memory_bytes']//1024**2,require_source_generation=p['generation'])

def inspect(selected_plan):
    p,ledger,closure,B,snap=load(selected_plan);require(not Path(p['home']).exists(),'continuation consumed; reconcile')
    selected=B.retained_curation(arguments(p,p['corpus']),p['source'],ledger)
    if p['retained_curation'] is None:require(not selected.exists(),'actual builder found a retained target selection')
    else:require(binding(selected)==p['retained_curation'],'actual builder would select different retained targets')
    require(closure_matches(p,closure),'read-only probe changed original corpus')
    return dict(status='PRE_SOURCE_CONTINUATION_PREFLIGHT_PASS',plan=selected_plan,
        previous_attempt=p['previous_attempt'],next_attempt=p['next_attempt'],
        retained_curation=p['retained_curation'],source_generation_absent=True,
        models_dispatched=0,attempts_dispatched=0)

def active(p,snap):
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'],'explicit workbook custody required')
    claim=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
    require(claim['source_sha256']==p['source']['sha256'] and claim['version']==snap['fingerprint'],'claim source/code differs')

def clone(p,closure):
    dest=Path(p['home'])/'corpus';require(not dest.exists(),'clone consumed; reconcile')
    for name,b in closure['files'].items():
        if name.endswith('/build.log'):continue
        require(binding(b['path'])==b,'original changed during clone')
        target=dest/name;target.parent.mkdir(parents=True,exist_ok=True)
        with Path(b['path']).open('rb') as src,target.open('xb') as out:shutil.copyfileobj(src,out,1024*1024);out.flush();os.fsync(out.fileno())
        require(binding(target)['sha256']==b['sha256'],'clone bytes differ')
    for name,target in closure.get('links',{}).items():
        link=dest/name;link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(target)
    if 'links' in closure:
        copied=corpus_inventory(dest);require(copied['links']==closure['links'] and {n:r['sha256']for n,r in copied['files'].items()}=={n:r['sha256']for n,r in closure['files'].items()if not n.endswith('/build.log')},'cloned generation closure differs')
    return dest

def run(selected):
    p,ledger,closure,B,snap=load(selected);active(p,snap)
    home=Path(p['home']);require(not home.exists(),'consumed namespace cannot replay')
    require(shutil.disk_usage(p['corpus']).free>=12*1024**3+sum(Path(b['path']).stat().st_size for b in closure['files'].values()),'source clone would breach disk reserve')
    home.mkdir(parents=True);keep(home/'intent.json',dict(plan=selected,previous=p['ledger'],allowance=p['allowance'],next_attempt=p['next_attempt'],automatic_retry=False,created_at_ns=time.time_ns()))
    # Original corpus lock also prevents competing selected copies from cloning the same boundary.
    with (Path(p['corpus'])/'workbooks'/p['workbook']/'.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(closure_matches(p,closure),'original changed before clone')
        reservation=Path(p['allowance']['path']).parent/('reconstruction-%s-%06d.json'%(p['workbook'],p['next_attempt']))
        keep(reservation,dict(plan=selected,previous=p['ledger'],ordinal=p['next_attempt'],cumulative_limit=p['cumulative_reconstruction_limit'],automatic_retry=False,created_at_ns=time.time_ns()))
        dest=clone(p,closure);active(p,snap);os.chdir(snap['root'])
        result=B.execute_one(arguments(p,dest),p['source'],snap['fingerprint'])
        after=json.loads((dest/'workbooks'/p['workbook']/'ledger.json').read_bytes())
        require(after['build_history']==ledger.get('build_history',[])+[ledger['build']] and after['build']['attempts']==p['next_attempt'],'consumed history was rewritten')
        require(closure_matches(p,closure),'original corpus changed')
        receipt=keep(home/'result.json',dict(status='RECONSTRUCTION_VERIFIED' if result.get('state')=='VERIFIED' else 'RECONSTRUCTION_HELD',result=result,ledger=binding(dest/'workbooks'/p['workbook']/'ledger.json'),plan=selected,models_dispatched=0,publication_performed=False,automatic_retry=False))
        validate_curation(p,ledger)
        require(result.get('state')=='VERIFIED','reconstruction held; inspect retained result')
        return receipt

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['inspect','run']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);x=a.parse_args();print(json.dumps(globals()[x.action](dict(path=str(Path(x.plan).absolute()),sha256=x.sha256))))
