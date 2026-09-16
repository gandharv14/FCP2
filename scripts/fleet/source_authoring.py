"""First authoring from a verified, source-preserving reconstruction continuation.

This entrypoint has no reconstruction command and never resets a prior attempt.
Existing owning admission, model reviews, resource limits and publication gates
remain mandatory. Publication stays a separately verified coordinator operation.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse,hashlib,importlib.util,json,os,sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.fleet.migration import binding,checked,retain
from scripts.fleet import source_authoring_history as N
SCHEMA='fleet-source-to-first-authoring-plan/v1'

def require(v,m):
    if not v:raise ValueError(m)

def module(name,row):
    require(binding(row['path'])==row,'selected authoring helper changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def selected(row,*,live):
    p=checked(row);require(set(p)=={'schema_version','workbook','history','snapshot','code_lineage','preparer','dispatcher','queue','taxonomy','env_file','batch','owner','publication_root','output_root'},'closed source-authoring plan required')
    require(p['schema_version']==SCHEMA and p['workbook'] in N.WORKBOOKS,'unreviewed first-authoring workbook')
    snapshot=checked(p['snapshot']);root=Path(snapshot['root']);require(Path(__file__).resolve()==root/'scripts/fleet/source_authoring.py','run only immutable selected authoring code')
    actual={str(x.relative_to(root)):hashlib.sha256(x.read_bytes()).hexdigest() for x in root.rglob('*') if x.is_file() and x.name!='.pipeline-code.json' and not {'__pycache__','.pytest_cache'}&set(x.relative_to(root).parts)}
    require(actual==snapshot['files'] and root.name==snapshot['fingerprint'],'authoring inventory changed')
    for x in root.rglob('*'):require(not x.is_symlink(),'authoring code alias')
    lineage=checked(p['code_lineage']);require(lineage['snapshot']==p['snapshot'] and lineage['history']==p['history'],'source/code lineage detached')
    history=N.validate_certificate(p['history'],workbook=p['workbook'],root=root,live=live)
    for name,digest in lineage['source_producer_files'].items():require(actual[name]==digest,'original source producer changed')
    for name,digest in lineage['source_algorithm_files'].items():require(actual[name]==digest,'successful source algorithm changed')
    for key in ('preparer','dispatcher','taxonomy'):require(binding(p[key]['path'])==p[key],'authoring dependency changed')
    q=checked(p['queue']);item=next(x for x in q['items'] if x['workbook_id']==p['workbook']);require({k:item['original_source'][k] for k in ('path','sha256')}==history['source'],'source queue identity differs')
    if history['pre_audit'] is None:require(item['history_class']=='no_history' and not item['preserved_history'] and not item['preserved_curations'],'unexpected prior authoring history')
    else:
        proof=checked(history['pre_audit']);require([{'path':x['path'],'sha256':x['sha256']} for x in item['preserved_curations']]==proof['curations'],'retained queue curations differ')
        require([{'path':x['path'],'sha256':x['sha256']} for x in item['preserved_history']]==[proof['tracker']],'retained queue history differs')
    result=checked(history['source_result']);ledger=checked(result['ledger']);corpus=Path(result['ledger']['path']).parents[2]
    generations={}
    for kind,folder in [('source','sources'),('segmentation','segments')]:
        path=corpus/folder/p['workbook']/'generations'/ledger['build'][kind+'_generation_id']/'generation-manifest.json';bound=binding(path);value=checked(bound)
        require(bound['sha256']==ledger['build'][kind+'_manifest_sha256'] and value['generation_id']==ledger['build'][kind+'_generation_id'],'completed source generation differs')
        generations[kind]=bound
    segment=checked(generations['segmentation']);curation=binding(Path(generations['segmentation']['path']).parent/'curation.toml')
    require(curation['sha256']==ledger['build']['bindings']['curation_sha256'] and segment['artifacts']['curation.toml']['sha256']==curation['sha256'],'retained successful curation differs')
    return p,history,root,result,ledger,generations

def inspect(row,probe_output=None):
    p,h,root,result,ledger,g=selected(row,live=False)
    require(not Path(h['authoring_home']).exists(),'first authoring namespace already consumed')
    author=module('source_authoring_probe_preparer',p['preparer']);dispatcher=module('source_authoring_probe_dispatcher',p['dispatcher'])
    from xl_source_publication import validate_source_generation
    from xl_seg.publication import validate_generation_directory
    source=validate_source_generation(Path(g['source']['path']).parent,expected_generation_id=ledger['build']['source_generation_id'])
    # Publication validation verifies the retained artifact byte inventory;
    # all current owning source/financial gates still execute after admission.
    segment=validate_generation_directory(Path(g['segmentation']['path']).parent,expected_generation_id=ledger['build']['segmentation_generation_id'],expected_manifest_sha256=ledger['build']['segmentation_manifest_sha256'],require_pass=True,source_generation_dir=Path(g['source']['path']).parent)
    require(probe_output is not None,'full owning bootstrap probe output required')
    from scripts.fleet.source_bootstrap_probe import validate as full_bootstrap
    bootstrap=full_bootstrap(p,h,root,result,ledger,g,author,probe_output)
    return dict(status='SOURCE_TO_FIRST_AUTHORING_PREFLIGHT_PASS',plan=row,history=p['history'],snapshot=p['snapshot'],source_result=h['source_result'],bootstrap=bootstrap,source_generation=source['generation_id'],segmentation_generation=segment['generation_id'],reconstruction_attempts=2,reconstruction_replayed=False,original_history_preserved=True,models_dispatched=0,tracker_mutations=0,qualification_claimed=False)

def run(row):
    require(not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'read-only probe cannot author')
    require(not any(os.environ.get(k) for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH')),'foreign history projection cannot authorize this first authoring')
    p,h,root,result,ledger,g=selected(row,live=True);home=Path(h['authoring_home']);require(not home.exists(),'first authoring consumed; reconcile remaining checkpoint')
    home.mkdir(parents=True,mode=0o700);retain(home/'intent.json',dict(plan=row,driver=binding(__file__),source_result=h['source_result'],reconstruction_replayed=False,automatic_retry=False))
    os.environ['HARBOR_FLEET_SOURCE_HISTORY_PATH']=p['history']['path'];os.environ['HARBOR_FLEET_SOURCE_HISTORY_SHA256']=p['history']['sha256']
    try:
        author=module('source_selected_preparer',p['preparer']);dispatcher=module('source_selected_dispatcher',p['dispatcher'])
        args=SimpleNamespace(code_root=str(root),run_root=str(home/'authoring'),workbook=p['workbook'],ledger=result['ledger']['path'],inventory=h['inventory']['path'],history_root=h['history_roots'],pre_audit_history=h['pre_audit']['path'] if h['pre_audit'] else None,generated_curation=None,source_manifest=g['source']['path'],segmentation_manifest=g['segmentation']['path'],taxonomy=p['taxonomy']['path'],env_file=p['env_file'],batch=p['batch'],owner=p['owner'],slot_root=str(dispatcher.BASE/'root/workbook-slots'/p['workbook']),model_slot_root=str(dispatcher.MODEL),queue_lock=str(dispatcher.QUEUE_LOCK),publication_root=p['publication_root'],output_root=p['output_root'],project_id='cms6m4urm006n07z8ecxi1oi2')
        config_binding=author.prepare(args);config=checked(config_binding);base,_,_=author.imports(root);controls=base.decode(config['controls']);intake=base.decode(checked(config['intake']));require(intake.campaign.model_slots==1,'globalmodel limit changed');base._campaign(intake.campaign)
        outcome=base.run('admit',{'intake':config['intake']},controls,home/'authoring/admission-001')
        job=dict(schema_version=dispatcher.SCHEMA,driver=p['dispatcher'],queue=p['queue'],config=config_binding,outcome=outcome,operations=str(home/'authoring/dispatcher-v2/first'),maximum_steps=100,models=True,commands=True,containers=True,publish=False);job_binding=retain(home/'authoring-job.json',job)
        dispatcher.successful(outcome,dispatcher.setup(job));dispatcher.run(job_binding);held=checked(binding(Path(job['operations'])/'hold.json'));checkpoint,value=dispatcher.successful(held['outcome'],dispatcher.setup(job));require(value['status']=='PUBLICATION_READY','authoring requires owning checkpoint reconciliation')
        return retain(home/'result.json',dict(status='PUBLICATION_READY',workbook=p['workbook'],plan=row,job=job_binding,config=config_binding,outcome=held['outcome'],checkpoint=checkpoint,source_history=p['history'],publication_performed=False,automatic_retry=False))
    except Exception as e:
        retain(home/'failure.json',dict(status='RECONCILIATION_REQUIRED',plan=row,error_type=type(e).__name__,publication_performed=False,reconstruction_replayed=False,automatic_retry=False));raise

def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['inspect','run']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);a.add_argument('--probe-output');v=a.parse_args();row=dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256);require(v.action=='inspect' or v.probe_output is None,'probe output cannot authorize owning run');print(json.dumps(inspect(row,v.probe_output) if v.action=='inspect' else run(row)))
if __name__=='__main__':main()
