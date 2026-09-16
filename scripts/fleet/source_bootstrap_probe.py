"""Full owning bootstrap validation under an explicit read-only history adapter.

This probe writes only its selected diagnostic directory. It supplies the
already validated full namespace certificate to the pure bootstrap validator;
no fleet lease, tracker admission or model permission is synthesized.
"""
from pathlib import Path
from scripts.fleet.migration import binding,checked,retain

def validate(p,h,root,result,ledger,g,author,output):
    from scripts.fleet import source_authoring_history as N
    output=Path(output)
    if not output.is_absolute() or output.exists() or output.is_relative_to(root) or output.is_relative_to(Path(h['authoring_home'])):
        raise ValueError('fresh isolated bootstrap probe output required')
    output.mkdir(parents=True)
    base,_,_=author.imports(root);A=base.admission
    def forbidden(*a,**kw):raise ValueError('owning operation/model forbidden in source bootstrap probe')
    base.run=forbidden;base.executor.execute=forbidden
    original=N.configured
    def projected(roots,known):
        value=N.validate_certificate(p['history'],workbook=p['workbook'],root=root,live=False)
        if list(map(str,roots))!=value['history_roots'] or sorted(known)!=value['known_ids']:raise ValueError('read-only history namespace differs')
        return value
    import os
    keys=('HARBOR_FLEET_SOURCE_HISTORY_PATH','HARBOR_FLEET_SOURCE_HISTORY_SHA256','HARBOR_FLEET_WORKBOOK')
    prior={key:os.environ.get(key) for key in keys}
    os.environ.update(HARBOR_FLEET_SOURCE_HISTORY_PATH=p['history']['path'],HARBOR_FLEET_SOURCE_HISTORY_SHA256=p['history']['sha256'],HARBOR_FLEET_WORKBOOK=p['workbook']);N.configured=projected
    try:
        inventory=A.read_inventory(h['inventory']['path']);original_source=next(r for r in inventory['sources'] if r['workbook_id']==p['workbook']);segment=checked(g['segmentation']);curation=binding(Path(g['segmentation']['path']).parent/'curation.toml')
        survey=retain(output/'survey.json',dict(inventory=h['inventory'],scanned_roots=h['history_roots'],excluded=h['excluded'],histories=h['histories'],unmapped_prior_artifacts=[]))
        candidate=retain(output/'candidate.json',dict(schema_version='harbor-qualified-task-candidate-proposal/v1',requested_workbook_ids=[p['workbook']],target_new_qualified_tasks=1,denominator_for_original_rebuild=len(inventory['sources']),survey=survey,selected_candidates=[dict(original=original_source,ledger=result['ledger'],retained_targets=segment['curated_output_cells'],retained_curation=curation,exclusion_evidence=[],prior_audit_metadata=[],**({'pre_audit_history':h['pre_audit']} if h['pre_audit'] else {}))]))
        recipe=A.DependencyRecipe(tuple(binding(root/x) for x in ('requirements.txt','requirements-harbor.txt')),minimum_disk_bytes=12*1024**3,maximum_workbooks=1,maximum_models=1)
        dependency=retain(output/'dependency.json',A.dependency_report(recipe,root))
        policy=A.policy_from_candidate_manifest(candidate,batch=p['batch'],tracker_root=output/'unused-trackers',original_inventory=h['inventory'],dependency=dependency,dependencies=recipe,history_roots=tuple(map(Path,h['history_roots'])),expected_original_count=len(inventory['sources']))
        selection=base.bootstrap.Selection(candidate,policy,p['workbook'],g['source'],g['segmentation'],output/'unused-bootstrap',base.bootstrap.Policy('cms6m4urm006n07z8ecxi1oi2'))
        validation=retain(output/'validation.json',base.bootstrap.validate(selection))
        assert not (output/'unused-trackers').exists() and not (output/'unused-bootstrap').exists()
        return dict(validation=validation,authority_mode='read_only_full_history_certificate_no_claim',bootstrap_function=binding(Path(base.bootstrap.__file__)),admission_performed=False,models_dispatched=0,source_reconstruction_replayed=False)
    finally:
        N.configured=original
        for key,value in prior.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
