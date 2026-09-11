"""Explicit adoption of the first immutable, automatically generated curation.

The original successful corpus ledger remains unchanged, including its null input
curation binding. This proof supplies a separately named output-curation binding.
"""
from pathlib import Path
from scripts.batch import harbor_admission as A
SCHEMA='harbor-generated-curation-adoption/v1'
KEYS={'schema_version','workbook','queue','ledger','source_manifest','segmentation_manifest','curation','ordered_targets','history_roots'}

def validate(proof,*,workbook,original,history_roots,known_ids):
    p=A.read(proof);A.require(set(p)==KEYS and p['schema_version']==SCHEMA and p['workbook']==workbook,'generated curation proof schema/identity differs')
    A.require(p['history_roots']==[str(x) for x in history_roots],'generated curation history scope differs')
    q=A.read(p['queue']);A.require(q['schema_version']=='authoring-request-queue/v1','unknown generated curation queue')
    rows=[r for r in q['items'] if r['workbook_id']==workbook]
    A.require(len(rows)==1,'generated curation queue identity ambiguous');row=rows[0]
    A.require(row['history_class']=='no_history' and row['preserved_curations']==[] and row['preserved_history']==[],'prior curation/history cannot use generated adoption')
    A.require({k:row['original_source'][k] for k in ('path','sha256')}==original,'generated curation original differs')
    A.require(A.record(original['path'])==original,'generated curation source changed')
    excluded,history=A.survey_namespaces(tuple(Path(x) for x in history_roots),set(known_ids))
    A.require(workbook not in excluded and not history.get(workbook),'generated curation has prior task evidence')
    ledger=A.read(p['ledger']);build=ledger['build']
    A.require(ledger['schema_version']=='corpus-workbook/v1' and ledger['workbook_id']==workbook and ledger['source']==row['original_source'],'generated curation ledger identity differs')
    A.require(build['state']=='VERIFIED' and build['attempts']==1 and not ledger.get('build_history') and build['bindings']['curation_sha256'] is None,'only first successful generated curation may be adopted')
    input_path=Path(build['curation_path'])
    A.require(not input_path.exists() and not input_path.is_symlink(),'original curation input now exists')
    for kind in ('source','segmentation'):
        bound=p[kind+'_manifest'];value=A.read(bound);directory=Path(bound['path']).parent
        A.require(Path(bound['path']).name=='generation-manifest.json' and directory.parent.name=='generations' and directory.parent.parent.name==workbook,'generated curation generation path differs')
        A.require(directory.name==value['generation_id']==build[kind+'_generation_id'] and bound['sha256']==build[kind+'_manifest_sha256'],'generated curation generation differs from retained ledger')
    segment=A.read(p['segmentation_manifest']);curation=A.record(Path(p['segmentation_manifest']['path']).parent/'curation.toml')
    A.require(curation==p['curation'] and segment['artifacts']['curation.toml']['sha256']==curation['sha256'],'generated curation differs from immutable output artifact')
    A.require(segment['artifacts']['curation.toml']['size_bytes']==Path(curation['path']).stat().st_size,'generated curation artifact size differs')
    A.require(p['ordered_targets']==segment['curated_output_cells'] and bool(p['ordered_targets']['ordered']),'generated curation ordered targets differ')
    return p
