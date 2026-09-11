"""Exact absent-to-present direct pre-streaming audit proof for root continuation.

This validates evidence applicability only. The caller must still run every
current owning prefix gate and commit its exact lease/CAS before further work.
"""
from dataclasses import replace
from scripts.batch.harbor_admission import read,require

KEYS=frozenset({'audit_validator_upgrade','audit_upgrade_original_code'})


def validate_selection(expected,current):
    """Preserve the whole expected task, allowing only one exact proof addition."""
    if expected==current:return []
    old=expected.content.bindings;new=current.content.bindings
    require(not (KEYS & old.keys()) and KEYS <= new.keys(),
            'only absent-to-present paired direct audit proof is supported')
    require({k:v for k,v in new.items() if k not in KEYS}==old,
            'continuation changed prior evidence or added unrelated bindings')
    require(replace(current,content=replace(current.content,bindings=old))==expected,
            'continuation changed task or semantic context')
    from xl_audit_inventory_upgrade import BEFORE_SHA256
    from xl_audit_validator_upgrade import checked
    require(new['audit_upgrade_original_code']['sha256']==BEFORE_SHA256,
            'only exact pre-streaming direct upgrade is supported')
    checked(new['audit_upgrade_original_code'])
    require(read(new['variable_source_metadata'])['generator_sha256']==BEFORE_SHA256,
            'audit is not the exact completed pre-streaming generation')
    # Reject renderer/locator wrappers: this route is the direct reviewed 56ab
    # transition, without changing the normalizer's original request locator.
    from xl_audit_validator_upgrade import SCHEMA
    require(read(new['audit_validator_upgrade'])['schema_version']==SCHEMA,
            'direct completed-audit upgrade receipt required')
    from scripts.qualified_publication import validate_audit_bindings
    records=validate_audit_bindings(new,expected_generations={
        k:new[k+'_generation']['id'] for k in ('source','segmentation')})
    return [{'path':str(path),'sha256':sha} for path,sha in sorted(records.items())]
