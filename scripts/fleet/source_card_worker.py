"""Declared worker namespace for the closed source-card correction.

This factory grants no custody. Its reader must validate the selected immutable
permit, original source/Calls, and current takeover chain. Original worker locks,
active-lease checks, phase order, uncertain-result handling and operations remain
in the cloned namespace. Ordinary workers keep the original implementation.
"""
from pathlib import Path
from types import FunctionType, SimpleNamespace
import hashlib
import inspect

from scripts.fleet import source_card_authoring as core


def install(original, reader, producer):
    namespace = dict(original.__dict__)
    facade = SimpleNamespace(**namespace)
    for name, value in tuple(namespace.items()):
        if isinstance(value, FunctionType) and value.__globals__ is original.__dict__:
            clone = FunctionType(value.__code__, namespace, value.__name__,
                                 value.__defaults__, value.__closure__)
            clone.__kwdefaults__ = value.__kwdefaults__
            namespace[name] = clone
    namespace.update(_source_cards=reader, producer=producer)
    before = inspect.getsource(original._step)
    after = before
    witnesses = []

    def change(old, new, count=1):
        nonlocal after
        core.require(after.count(old) == count, 'unreviewed source-card worker fragment')
        after = after.replace(old, new)
        witnesses.append(dict(before=old, after=new, count=count))

    change("    initial=worker.run/'initial.json'\n", "    initial=worker.run/'initial.json'\n"
           "    if _source_cards.is_card(worker):\n"
           "        require(initial.is_file(), 'source-card continuation requires original initial history')\n")
    change("""            from scripts.batch.harbor_dialogue_pre_review_repair import validate as validate_pre_review
            validate_pre_review(worker,before)""", """            if _source_cards.is_card(worker):
                _source_cards.validate(worker,before)
            else:
                from scripts.batch.harbor_dialogue_pre_review_repair import validate as validate_pre_review
                validate_pre_review(worker,before)""")
    change("""    if worker.lease_renewal is not None:
        from scripts.batch.harbor_instruction_lease_renewal import validate
        renewal = validate(worker,before)""", """    if worker.lease_renewal is not None:
        if _source_cards.is_card(worker):
            renewal = _source_cards.worker_renewal(worker,before)
        else:
            from scripts.batch.harbor_instruction_lease_renewal import validate
            renewal = validate(worker,before)""")
    change("    elif worker.code_repair is None and worker.disclosure_repair is None and renewal is None",
           "    elif not _source_cards.is_card(worker) and worker.code_repair is None and worker.disclosure_repair is None and renewal is None")
    # The ordinary one-sentence correction has already accepted its authors.
    # This separate source correction must actually execute both new authors.
    condition = "worker.dialogue_pre_review_repair is not None and worker.dialogue_semantic_retry is None and"
    change(condition, "not _source_cards.is_card(worker) and " + condition, 4)
    change("    require(checkpoint==required, 'exact root-selected preceding checkpoint hash required')",
           "    if _source_cards.is_card(worker) and next_phase=='dialogue-writer' and not pending_now:\n"
           "        required=worker.dialogue_pre_review_repair\n"
           "    require(checkpoint==required, 'exact root-selected preceding checkpoint hash required')")
    # Both the first request preparation and every same-intent pending return
    # use the permit's current lease. Never fall back to the old writer1 lease.
    change("    require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')",
           "    if _source_cards.is_card(worker) and expected=='dialogue-writer':\n"
           "        if pending_now:\n"
           "            pending_intent=read(read(checkpoint)['intent'])\n"
           "            require(pending_intent['previous']==worker.dialogue_pre_review_repair and pending_intent['code']==code(worker), 'source-card pending authority or declared implementation changed')\n"
           "        predecessor=_source_cards.owned()\n"
           "    require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')")
    old_pending = "    if renewal is not None and preceding==renewal['checkpoint'] and checkpoint==worker.lease_renewal:"
    new_pending = "    if renewal is not None and preceding==renewal['checkpoint'] and (checkpoint==worker.lease_renewal or\n            (pending_now and read(read(checkpoint)['intent'])['previous']==worker.lease_renewal)):"
    if old_pending in after:
        change(old_pending, new_pending)
    else:
        core.require(after.count(new_pending) == 1, 'unreviewed renewal pending predecessor')
    exec(compile(after, str(Path(__file__).absolute()) + '::_step', 'exec'), namespace)
    patched_step = namespace['_step']

    def selected_step(worker, selection, lease, **options):
        if not reader.is_card(worker):
            return original._step(worker, selection, lease, **options)
        return patched_step(worker, selection, lease, **options)

    def directory(worker, phase):
        if reader.is_card(worker) and phase in original.STEPS[original.STEPS.index('dialogue-writer'):]:
            return Path(reader.plan['directory'])/'steps'/phase
        return original.step_directory(worker, phase)

    namespace.update(_step=selected_step, step_directory=directory)
    facade.__dict__.update(namespace)
    facade.declared_namespace = namespace
    facade.declared_function_changes = {'_step': dict(before=before, after=after,
        before_sha256=hashlib.sha256(before.encode()).hexdigest(),
        after_sha256=hashlib.sha256(after.encode()).hexdigest(), replacements=witnesses)}
    return facade
