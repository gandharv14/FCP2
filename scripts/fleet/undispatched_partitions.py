"""Declared owning-reader facade for a proven undispatched partition.

The retained partition manifest and all original producer bytes stay unchanged.
The facade binds its added executor dependency in every resulting aggregate.
It uses the original full source, semantic review, clause coverage, independence,
round ordering and emission functions; it never creates a semantic verdict.
"""
from types import FunctionType, ModuleType, SimpleNamespace
import inspect
import sys


def facade(partition, recovery, prepared, *, partition_code, continuation_evidence=()):
    U = sys.modules[recovery.__class__.__module__]
    U.require(U.binding(partition.__file__) == partition_code, 'owning partition producer changed')
    E = recovery.E
    # Validate the exact retained selection through the original owning API.
    manifest, selected, campaign, draft, rows = partition._current(prepared, allow_emitted=True)
    matches = [i for i in range(len(manifest['partitions']))
               if partition._prepared_call(prepared, i, 1) == recovery.prepared]
    U.require(len(matches) == 1 and campaign == recovery.campaign,
              'recovery does not select exactly one retained first partition')
    index = matches[0]
    # Already completed lanes remain genuine original Calls. Merely reading
    # their verdicts does not spend a new review or silently promote a warning.
    for i in range(index):
        first = partition._review(prepared, i, 1, campaign, rows)
        U.require(first['valid'], 'earlier rejected partition needs its owning remaining round')
    for evidence in continuation_evidence:
        U.require(U.binding(evidence['path']) == evidence, 'continuation authority changed')
    witness = dict(schema_version='fleet-undispatched-partition-reader/v1',
        adapter=U.binding(__file__), original_partition=partition_code,
        prepared=prepared, partition_index=index, recovery=recovery.witness,
        continuation_evidence=list(continuation_evidence),
        original_manifest_unchanged=True, source_validation_unchanged=True,
        semantic_validator_unchanged=True, semantic_budget_reset=False)

    def is_selected(pc, c):
        return pc == recovery.prepared and c == recovery.campaign

    def location(pc, c):
        return recovery.root if is_selected(pc, c) else E.location(pc, c)

    def inspect_call(pc, c, **kwargs):
        U.require(not kwargs or not is_selected(pc, c), 'unsupported recovered reader options')
        return recovery.inspect() if is_selected(pc, c) else E.inspect(pc, c, **kwargs)

    def execute_call(pc, c, **kwargs):
        if is_selected(pc, c):
            return recovery.execute(**kwargs)
        if pc.scope == recovery.prepared.scope:
            # Until a real failed first review exists, a second logical round
            # cannot be constructed by this facade. The unchanged P.dispatch
            # has already validated all first-round results before this call.
            U.require(c == recovery.campaign and pc.ordinal == pc.limit == 2,
                      'only the existing remaining second round can follow')
            first = recovery.inspect()
            U.require(first.status in {'completed', 'response_rejected'}, 'uncertain first transport')
            source = inspect.getsource(E.execute)
            source = U.patch_once(source,
                "            prior = root.parent / ('attempt-%02d' % number)",
                "            prior = recovery_first_root if number == 1 else root.parent / ('attempt-%02d' % number)")
            source = U.patch_once(source, "        save(root/'submitted.json', submitted)",
                "        submitted['undispatched_predecessor'] = recovery_first_witness\n        save(root/'submitted.json', submitted)")
            namespace = dict(E.__dict__)
            namespace.update(recovery_first_root=recovery.root,
                             recovery_first_witness=U.binding(recovery.root/'undispatched-recovery.json'))
            original_inspect = E.inspect_locked
            namespace['inspect_locked'] = lambda root, *a, **k: (
                recovery._inspect(root, *a, **k) if root == recovery.root else original_inspect(root, *a, **k))
            exec(compile(source, __file__ + ':declared-second-round', 'exec'), namespace)
            return namespace['execute'](pc, c, **kwargs)
        return E.execute(pc, c, **kwargs)

    proxy = SimpleNamespace(**E.__dict__)
    proxy.location, proxy.inspect, proxy.execute = location, inspect_call, execute_call
    clone = ModuleType(partition.__name__)
    clone.__dict__.update(partition.__dict__)
    clone.E = proxy
    # Rebind unchanged function bytecode to the explicitly declared dependency
    # namespace. No original module object or immutable file is modified.
    for name, value in partition.__dict__.items():
        if isinstance(value, FunctionType) and value.__globals__ is partition.__dict__:
            function = FunctionType(value.__code__, clone.__dict__, value.__name__, value.__defaults__, value.__closure__)
            function.__kwdefaults__ = value.__kwdefaults__
            clone.__dict__[name] = function
    original_aggregate = clone.aggregate

    def aggregate(selected_prepared):
        U.require(selected_prepared == prepared, 'reader is bound to one retained partition manifest')
        recovery.validate_original()
        value = original_aggregate(selected_prepared)
        files = partition.records(witness)
        files.update({b['path']:b['sha256'] for b in recovery.proof['sealed_files']})
        for path, digest in files.items():
            U.require(U.binding(path)['sha256'] == digest, 'declared reader lineage changed')
        value['checked_files'] = dict(sorted({**value['checked_files'], **files}.items()))
        return value

    clone.aggregate = aggregate
    clone.undispatched_reader_witness = witness
    return clone


def install(partition, selected_facade):
    """Explicit process-local selection for subsequent owning validation imports."""
    if selected_facade.__name__ != partition.__name__:
        raise ValueError('partition module identity differs')
    parent, _, name = partition.__name__.rpartition('.')
    if sys.modules.get(partition.__name__) is not partition:
        raise ValueError('another partition reader was already selected')
    sys.modules[partition.__name__] = selected_facade
    setattr(sys.modules[parent], name, selected_facade)
