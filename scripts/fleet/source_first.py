"""One source-first reconstruction using the ordinary corpus owning gates.

Initial history is checked centrally and on the assigned VM. Qualification is
only the actual execute_one result; source screening never grants authoring.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse,hashlib,json,os,sys,time

def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)),'canonical regular file required')
    with p.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(path=str(p),sha256=sha)
def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'selected bytes changed')
    return json.loads(Path(row['path']).read_bytes())
def keep(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(p.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)
    return binding(p)
def history(value,workbook,roots):
    require(value['schema_version']=='fleet-source-initial-history/v1' and value['roots']==roots,'exact initial namespace survey required')
    require(workbook in value['known_ids'] and workbook not in value['excluded'] and not value['histories'].get(workbook),'historical workbook cannot start fresh')

def retain_legacy_curation(selected, workbook, root):
    """Carry the authenticated historical target selection into the new build."""
    prior=read(selected)
    rows=prior['curations']
    require(prior['workbook']==workbook and rows and len({r['sha256'] for r in rows})==1,
            'ambiguous historical target selection')
    for row in rows:require(binding(row['path'])==row,'historical curation changed')
    data=Path(rows[0]['path']).read_bytes()
    require(hashlib.sha256(data).hexdigest()==rows[0]['sha256'],'historical curation changed during read')
    target=Path(root)/workbook/'curation.toml'
    require(target.is_absolute() and not any(x.is_symlink() for x in (target,*target.parents)),
            'unsafe retained curation destination')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    fd=os.open(target.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)
    copied=binding(target)
    require(copied['sha256']==rows[0]['sha256'],'retained curation copy changed')
    return keep(Path(root).parent/'retained-curation.json',dict(legacy_research=selected,original=rows,selected=copied))
def load(selected):
    p=read(selected)
    cache=p.get('schema_version')=='fleet-source-first/v2'
    legacy=p.get('schema_version')=='fleet-source-first/v3'
    require(set(p)==({'schema_version','workbook','helper','snapshot','source','inventory','history','history_roots','native_history_roots','home','allowance','seconds','memory_bytes','next_attempt','cumulative_reconstruction_limit','models','automatic_retry'}|({'cache_policy'}if cache else set())|({'legacy_research'}if legacy else set())),'closed source-first plan required')
    require(p['schema_version']in('fleet-source-first/v1','fleet-source-first/v2','fleet-source-first/v3') and p['helper']==binding(__file__),'selected source-first helper required')
    ordinal=2 if legacy else 1
    if legacy:
        prior_selected=read(p['legacy_research'])
        if prior_selected.get('schema_version')=='harbor-legacy-opaque-source/v1':ordinal=prior_selected['new_source_reconstruction_ordinal']
    require(type(ordinal)is int and p['next_attempt']==p['cumulative_reconstruction_limit']==ordinal and p['models']==0 and p['automatic_retry'] is False,'initial attempt one only; models forbidden')
    snap=read(p['snapshot']);root=Path(snap['root'])
    actual={str(x.relative_to(root)):binding(x)for x in root.rglob('*')if x.is_file()}
    require(actual==snap['bindings'],'full source implementation changed')
    sys.path.insert(0,str(root))
    from scripts import corpus,corpus_build as B
    from scripts.batch import harbor_continuation_allowance as A
    require(Path(B.__file__).resolve().is_relative_to(root) and corpus.code_hash()==snap['fingerprint'],'foreign source builder loaded')
    policy=A.validate(p['allowance'],expected_scope=A.scope(p['workbook'],p['source']['sha256']),at=time.time_ns())
    require(0<p['seconds']<=policy['limits']['stage_seconds'] and 0<p['memory_bytes']<=policy['limits']['memory_bytes'],'selected resource allowance exceeded')
    sources=read(p['inventory'])['sources'];require([x for x in sources if x['workbook_id']==p['workbook']]==[p['source']],'ambiguous original source inventory')
    require(binding(p['source']['path'])=={k:p['source'][k]for k in ('path','sha256')},'original source changed')
    if cache:
        from xl_source_cache_equivalence import policy
        policy(p['cache_policy'],p['source']['path'])
    if legacy:
        from scripts.batch import harbor_legacy_research_history as L
        prior=L.validate(p['legacy_research'],workbook=p['workbook'],original={k:p['source'][k]for k in('path','sha256')},history_roots=p['history_roots'],known_ids={x['workbook_id']for x in sources})
        observed=read(p['history'])
        require(observed['roots']==p['history_roots']and observed['known_ids']==prior['known_ids']and observed['excluded']==prior['excluded']and observed['histories']==prior['histories'],'complete actual legacy survey differs')
        require(Path(p['home'])==Path(prior['home'])/'source','source authority cannot select another namespace')
    else:history(read(p['history']),p['workbook'],p['history_roots'])
    home=Path(p['home']);require(home.is_absolute() and not home.is_relative_to(root) and not home.exists(),'consumed or invalid source namespace')
    from scripts.batch.harbor_admission import survey_namespaces
    excluded,histories=survey_namespaces(tuple(map(Path,p['native_history_roots'])),set(x['workbook_id']for x in sources))
    require(p['workbook'] not in excluded and not histories.get(p['workbook']),'native history requires continuation')
    return p,snap,corpus,B

def eligible_source(health,source):
    """Admit only an existing source builder route, never numerical approval."""
    if health['route']=='pass' or set(health['reason_codes'])=={'data_tables_present'}:return 'existing'
    from xl_presentation_tables import composable_source
    if composable_source(health, source):return 'verified_table_remediation_plan'
    reasons=set(health['reason_codes'])
    require(bool(reasons) and reasons<={'unsupported_volatile_formulas_present','external_links_present'},'source requires another explicit supported route')
    from xl_volatile_formula_remediation import build_plan
    plan=build_plan(source)
    require(plan['status']=='eligible' and not plan['unresolved'],'source transformation remains unresolved')
    return 'verified_rewrite_plan'


def inspect(selected):
    p,snap,C,B=load(selected)
    from xl_source_health import inspect_workbook
    h=inspect_workbook(p['source']['path'])
    require(h['source_sha256']==p['source']['sha256'],'source inspection changed')
    # Data-table-only input is eligible to attempt the existing lossless
    # materializer, which still proves exclusion, source validity and no leaks.
    if p['schema_version']=='fleet-source-first/v2':
        from xl_source_cache_equivalence import preview
        preview(p['source']['path'],p['cache_policy'])
    else:eligible_source(h,p['source']['path'])
    return dict(status='SOURCE_FIRST_PREFLIGHT_PASS',plan=selected,source=p['source'],health=h,source_qualified=False,models_dispatched=0,attempts_dispatched=0)

def run(selected):
    p,snap,C,B=load(selected)
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'],'live workbook custody required')
    claim=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
    require(claim['source_sha256']==p['source']['sha256'] and claim['version']==snap['fingerprint'] and claim['fence']==1,'initial exact source claim required')
    legacy=p['schema_version']=='fleet-source-first/v3'
    ordinal=p['next_attempt']
    home=Path(p['home']);home.mkdir(parents=True)
    keep(home/'intent.json',dict(plan=selected,allowance=p['allowance'],ordinal=ordinal,models=False,automatic_retry=False,created_at_ns=time.time_ns()))
    keep(Path(p['allowance']['path']).parent/('reconstruction-%s-%06d.json'%(p['workbook'],ordinal)),dict(plan=selected,ordinal=ordinal,cumulative_limit=ordinal,legacy_research=p.get('legacy_research'),automatic_retry=False))
    run_root=home/'corpus';run_root.mkdir();os.chdir(snap['root'])
    screen=C.screen_one(p['source'],run_root,snap['fingerprint'],300);keep(home/'screen.json',screen)
    require(not screen.get('build') and not screen.get('build_history'),'initial reconstruction already consumed')
    args=SimpleNamespace(inventory=p['inventory']['path'],run_root=str(run_root),curation_root=str(home/'initial-curations'),code_root=snap['root'],source_inventory=None,inventory_batch_id=None,recalc_root=None,trusted_public_key='/etc/harbor/excel-runner-public.pem',timeout=p['seconds'],memory_mb=p['memory_bytes']//1024**2,require_source_generation=None)
    if legacy:retain_legacy_curation(p['legacy_research'],p['workbook'],args.curation_root)
    if p['schema_version']=='fleet-source-first/v2':
        args.cache_equivalence_policy=p['cache_policy']['path'];args.cache_equivalence_policy_sha256=p['cache_policy']['sha256']
    result=B.execute_one(args,p['source'],snap['fingerprint'])
    ledger=read(binding(run_root/'workbooks'/p['workbook']/'ledger.json'))
    require(ledger['build']['attempts']==1 and not ledger.get('build_history'),'initial attempt counter changed')
    receipt=keep(home/'result.json',dict(status='RECONSTRUCTION_VERIFIED' if result.get('state')=='VERIFIED' else 'RECONSTRUCTION_HELD',plan=selected,result=result,ledger=binding(run_root/'workbooks'/p['workbook']/'ledger.json'),models_dispatched=0,publication_performed=False,automatic_retry=False,**({'legacy_research':p['legacy_research'],'legacy_source_generation_retained':True,'cumulative_reconstruction_ordinal':ordinal}if legacy else {})))
    require(result.get('state')=='VERIFIED','source-first reconstruction held; preserve evidence')
    return receipt
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['inspect','run']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);x=a.parse_args();print(json.dumps(globals()[x.action](dict(path=str(Path(x.plan).absolute()),sha256=x.sha256))))
