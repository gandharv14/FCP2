"""Install one explicitly selected source-card reader in a fresh guarded process.

No source files or original function globals are changed. The caller verifies
the full original snapshot and all adapter bytes before calling this function;
each owning child independently repeats that verification from its sidecar.
"""
from types import SimpleNamespace
import sys

from scripts.fleet import source_card_authoring as core
from scripts.fleet import source_card_producer, source_card_worker, source_card_readiness


NAMES = {
    'producer': 'scripts.batch.harbor_instruction_producer',
    'worker': 'scripts.batch.harbor_instruction_worker',
    'semantic': 'scripts.batch.harbor_semantic_readiness',
    'static': 'scripts.batch.harbor_readiness',
    'complete': 'scripts.batch.harbor_complete_readiness',
    'renewal': 'scripts.batch.harbor_instruction_lease_renewal',
    'campaign': 'scripts.harbor_instruction_campaign',
}


def install(originals, reader):
    core.require(set(originals) == set(NAMES), 'complete declared source-card module selection required')
    for key, name in NAMES.items():
        core.require(sys.modules.get(name) is originals[key], 'foreign cached source-card dependency: '+name)
    reader.dependencies()
    producer = source_card_producer.install(originals['producer'], reader)
    worker = source_card_worker.install(originals['worker'], reader, producer)
    reader.worker = worker
    semantic = source_card_readiness.semantic(originals['semantic'], reader)
    static = source_card_readiness.static(originals['static'], reader)
    scope = source_card_readiness.namespace(originals['complete'])
    scope.update(static=static, dialogue_review=semantic.dialogue_review)
    complete = SimpleNamespace(**scope)
    worker.declared_namespace.update(static=static, complete_readiness=complete.complete_readiness)
    worker.__dict__.update(worker.declared_namespace)
    campaign = source_card_readiness.campaign(originals['campaign'], reader, worker)
    renewal = source_card_readiness.renewal(originals['renewal'], reader)
    selected = dict(producer=producer, worker=worker, semantic=semantic, static=static,
                    complete=complete, renewal=renewal, campaign=campaign)
    # Publish the selected namespace only after every closed source patch was
    # constructed successfully. Classes/field schemas retain original identity.
    for key, name in NAMES.items():
        package, leaf = name.rsplit('.', 1)
        sys.modules[name] = selected[key]
        setattr(sys.modules[package], leaf, selected[key])
    return selected
