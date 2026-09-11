"""Isolated two-edge evidence consumer; never installs an inventory approval."""
from pathlib import Path
import json
import xl_table_recalc_quarantine as quarantine
import xl_presentation_tables as presentation
import xl_source_health as health
from xl_seg import publication

POLICY='quarantined-table-presentation-chain/v1'
SCHEMA='source-quarantined-table-lineage/v1'

class LineageError(ValueError):
    pass

def record(path):
    p=Path(path).absolute()
    return {'path':str(p),'sha256':quarantine.file_hash(p),'size_bytes':p.stat().st_size}

def load(path):
    def unique(items):
        value={}
        for key,item in items:
            if key in value:raise LineageError('duplicate JSON field')
            value[key]=item
        return value
    return json.loads(Path(path).read_text(),object_pairs_hook=unique)

def current_code():
    # Location is not semantic identity: frozen snapshots may relocate identical
    # reviewed code. Bind explicit logical modules and the complete verifier set;
    # artifact/input paths elsewhere in this contract remain exact.
    modules = {'xl_table_quarantine_lineage.py': Path(__file__),
               'xl_table_recalc_quarantine.py': Path(quarantine.__file__),
               'xl_presentation_tables.py': Path(presentation.__file__),
               'xl_source_health.py': Path(health.__file__)}
    entries = {}
    for name, path in modules.items():
        if path.name != name:
            raise LineageError('unexpected logical module identity')
        entries[name] = quarantine.file_hash(path)
    root = Path(publication.__file__).resolve().parent.parent
    for path in publication.verifier_paths():
        path = Path(path)
        try:
            relative = path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise LineageError('verifier path outside publication repository') from exc
        name = relative.as_posix()
        if not (name in {'xl_ast_graph.py', 'xl_graph_store.py', 'xl_segment.py'}
                or (relative.parent.as_posix() == 'xl_seg' and relative.suffix == '.py')):
            raise LineageError('unexpected verifier identity')
        if name in entries:
            raise LineageError('duplicate logical code identity')
        entries[name] = quarantine.file_hash(path)
    if 'xl_seg/publication.py' not in entries:
        raise LineageError('publication implementation missing from verifier identity')
    return [{'name':name,'sha256':value} for name,value in sorted(entries.items())]

def _validated_edges(*, original, staged, intermediate, candidate, curation,
                     request_path, result_path, materialization_path,
                     expected_workbook, expected_original_sha256,
                     expected_curation_sha256, expected_ordered_outputs,
                     producer_sha256, public_key):
    files={name:record(path) for name,path in {
        'original':original,'staged':staged,'intermediate':intermediate,'candidate':candidate,
        'curation':curation,'request':request_path,'result':result_path,
        'materialization':materialization_path}.items()}
    if len({Path(files[k]['path']).resolve() for k in ('original','staged','intermediate','candidate')})!=4:
        raise LineageError('chain must preserve four distinct original/staging/intermediate/final files')
    quarantine.same(files['original']['sha256'],quarantine.sha(expected_original_sha256),'caller original identity')
    quarantine.same(files['curation']['sha256'],quarantine.sha(expected_curation_sha256),'caller retained curation')
    if not isinstance(expected_ordered_outputs,list) or not expected_ordered_outputs or any(not isinstance(x,str) or '!' not in x for x in expected_ordered_outputs) or len(set(expected_ordered_outputs))!=len(expected_ordered_outputs):
        raise LineageError('complete unique ordered curated output identity required')
    request=load(request_path);result=load(result_path);materialization=load(materialization_path)
    quarantine.same(request.get('workbook'),expected_workbook,'original workbook identity')
    for key in ('intermediate','candidate'):
        if Path(files[key]['path']).stem!=expected_workbook:
            raise LineageError('chain filenames must preserve original workbook identity')
    first=quarantine.validate_result(result,request,source=original,staged=staged,output=intermediate,
                                     producer_sha256=producer_sha256,public_key=public_key)
    quarantine.same(first['original_sha256'],expected_original_sha256,'first edge original')
    quarantine.same(first['intermediate_sha256'],files['intermediate']['sha256'],'first edge output')
    quarantine.same(materialization.get('original_sha256'),first['intermediate_sha256'],'joined edge')
    quarantine.same(materialization.get('candidate_sha256'),files['candidate']['sha256'],'final candidate')
    quarantine.same(materialization.get('curation_origin'),'retained','curation provenance')
    quarantine.same(materialization.get('curation_sha256'),expected_curation_sha256,'second edge curation')
    quarantine.same(materialization.get('ordered_outputs'),expected_ordered_outputs,'unchanged ordered targets')
    # Existing validator independently rechecks current AST builder, generation
    # manifest, current verifier fingerprints, reconstruction, all-table cone
    # exclusion, package mutation whitelist and fresh answer-leak scan.
    second=presentation.verify_materialization(intermediate,candidate,curation,materialization)
    generation=Path(materialization['inspection_generation'])
    quarantine.same(quarantine.file_hash(generation/'curation.toml'),expected_curation_sha256,'proof curation bytes')
    payload=load(generation/'segments.json')
    quarantine.same(payload['verification'].get('ordered_output_cells'),expected_ordered_outputs,'proof targets')
    # Force strict proof classification independently; a receipt's passed label
    # is insufficient, and no cached target is supplied to this validator.
    tables=presentation.table_inventory(presentation._package(Path(intermediate))[1])
    outputs,cone=presentation.check_exclusion(tables,payload)
    quarantine.same(outputs,expected_ordered_outputs,'complete proof targets')
    final_health=health.inspect_workbook(candidate)
    if final_health.get('route') not in {'pass','restricted_pass'}:
        raise LineageError('final candidate is not currently source-inspectable')
    if quarantine.observer.observe_data_tables(Path(candidate))['tables']:
        raise LineageError('final candidate still contains a data table')
    # Hash curation bytes preserves every convention and tolerance, including
    # deliberate custom entries; current verifier fingerprints bind defaults.
    evidence={'schema_version':SCHEMA,'policy_version':POLICY,'scope':'validated_chain_evidence_only',
              'source_approval':False,'task_approval':False,'workbook':expected_workbook,
              'files':files,'original_sha256':expected_original_sha256,
              'intermediate_sha256':files['intermediate']['sha256'],
              'candidate_sha256':files['candidate']['sha256'],
              'curation_sha256':expected_curation_sha256,'ordered_outputs':expected_ordered_outputs,
              'quarantine_profile':quarantine.PROFILE,'quarantine_claims_sha256':first['claims_sha256'],
              'quarantine_request_sha256':first['request_sha256'],'quarantine_result_sha256':first['result_sha256'],
              'materialization_policy':presentation.POLICY,'materialization_manifest_sha256':second['manifest_sha256'],
              'inspection_generation':str(generation.absolute()),
              'inspection_manifest_sha256':quarantine.file_hash(generation/'generation-manifest.json'),
              'verification_sha256':quarantine.digest(payload['verification']),
              'proof_sha256':quarantine.digest(payload['proof']),
              'excluded_cone_sha256':quarantine.digest(cone),
              'final_health_sha256':quarantine.digest(final_health),'current_code':current_code()}
    # Retained evidence must remain stable throughout both independently
    # validated edges, not merely at the beginning of their separate checks.
    for name,item in files.items():
        quarantine.same(record(item['path']),item,name+' changed during chain validation')
    return evidence

def propose_lineage(**bindings):
    """Return a fully checked proposal; does not write or approve it."""
    result=_validated_edges(**bindings)
    result['lineage_sha256']=quarantine.digest(result)
    return result

def validate_lineage(lineage,**bindings):
    """Revalidate both edges against caller-selected immutable identities."""
    expected=propose_lineage(**bindings)
    quarantine.same(lineage,expected,'two-edge lineage')
    return {'status':'verified_chain_evidence','policy_version':POLICY,
            'lineage_sha256':expected['lineage_sha256'],
            'original_sha256':expected['original_sha256'],
            'candidate_sha256':expected['candidate_sha256'],
            'source_approval':False,'task_approval':False}
