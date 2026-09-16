"""Declared current readiness reader; old Calls and modules stay unchanged."""
from pathlib import Path
from types import FunctionType, SimpleNamespace
import hashlib
import inspect
from scripts.fleet import source_card_authoring as core


def namespace(original):
    selected = dict(original.__dict__)
    for name, value in tuple(selected.items()):
        if isinstance(value, FunctionType) and value.__globals__ is original.__dict__:
            clone = FunctionType(value.__code__, selected, value.__name__, value.__defaults__, value.__closure__)
            clone.__kwdefaults__ = value.__kwdefaults__
            selected[name] = clone
    return selected


def revised(original, name, changes, dependencies):
    scope = namespace(original)
    scope.update(dependencies)
    before = inspect.getsource(getattr(original, name)); after = before
    for old, new in changes:
        core.require(after.count(old) == 1, 'unreviewed source-card readiness fragment: '+name)
        after = after.replace(old, new)
    exec(compile(after, str(Path(__file__).absolute())+'::'+name, 'exec'), scope)
    selected = SimpleNamespace(**scope)
    selected.declared_namespace = scope
    selected.declared_function_changes = {name: dict(before=before, after=after,
        before_sha256=hashlib.sha256(before.encode()).hexdigest(),
        after_sha256=hashlib.sha256(after.encode()).hexdigest())}
    return selected


def semantic(original, reader):
    changes = [
        ("    repaired=set(body)=={'claims.json','draft.md','round','source_proved_correction'}",
         "    source_cards=set(body)=={'claims.json','draft.md','round','source_card_authoring'}\n"
         "    require(not source_cards or pre_review_repair==_source_cards.authority, 'unselected source-card readiness authority')\n"
         "    repaired=source_cards or set(body)=={'claims.json','draft.md','round','source_proved_correction'}"),
        ("""        if repaired:
            from scripts.batch import harbor_dialogue_pre_review_repair as Q
            require(body['source_proved_correction']==Q.review_projection(pre_review_repair,prior,body['draft.md'],root=root),'source-proved correction context differs')
            prompt+=Q.REVIEW_GUIDANCE""",
         """        if source_cards:
            require(body['source_card_authoring']==_source_cards.review_projection(pre_review_repair,prior,body['draft.md']), 'source-card generation/review projection differs')
            prompt+=_source_cards.review_guidance()
        elif repaired:
            from scripts.batch import harbor_dialogue_pre_review_repair as Q
            require(body['source_proved_correction']==Q.review_projection(pre_review_repair,prior,body['draft.md'],root=root),'source-proved correction context differs')
            prompt+=Q.REVIEW_GUIDANCE"""),
        ("""        if repaired:
            writers=Q.verify_pair(pre_review_repair,writer_calls,policy=policy,claims=current,draft=body['draft.md'],round=body['round'],root=root,workbook=workbook,original_sha256=original_sha256);Q.validate_review_call(pre_review_repair,call)""",
         """        if source_cards:
            writers=_source_cards.review_pair(pre_review_repair,writer_calls,policy=policy,claims=current,draft=body['draft.md'],root=root,workbook=workbook,original_sha256=original_sha256)
            _source_core.authority_evidence(pre_review_repair,submitted['budget_evidence'])
        elif repaired:
            writers=Q.verify_pair(pre_review_repair,writer_calls,policy=policy,claims=current,draft=body['draft.md'],round=body['round'],root=root,workbook=workbook,original_sha256=original_sha256);Q.validate_review_call(pre_review_repair,call)"""),
    ]
    return revised(original, 'dialogue_review', changes, {'_source_cards': reader, '_source_core': core})


def static(original, reader):
    old = "    extraction=skill_module(root,'additional-assumptions-dialogue','extract_claims')"
    new = ("    if s.dialogue_claims==_source_cards.plan['corrected_claims']:\n"
           "        _source_cards.owned()\n"
           "        extraction=_source_cards.extractor()\n"
           "    else:\n" + '    '+old)
    selected = revised(original, 'prose_content', [(old, new)], {'_source_cards': reader})
    before = inspect.getsource(original.context_bindings)
    witness = '        scope_records=validate_selected(selection,root=ROOT)'
    core.require(before.count(witness)==1, 'unreviewed scope context boundary')
    after = before.replace(witness, '        scope_records=_source_scope(selection,validate_selected,_source_cards,root=ROOT)')
    scope = selected.declared_namespace
    scope['_source_scope'] = source_scope
    exec(compile(after,str(Path(__file__).absolute())+'::context_bindings','exec'),scope)
    selected.declared_function_changes['context_bindings'] = dict(before=before,after=after,
        before_sha256=hashlib.sha256(before.encode()).hexdigest(),after_sha256=hashlib.sha256(after.encode()).hexdigest())
    selected.__dict__.update(scope)
    return selected


def source_scope(selection, original, reader, *, root):
    if selection.dialogue_claims != reader.plan['corrected_claims']:
        return original(selection,root=root)
    from dataclasses import replace
    from scripts.batch import harbor_transition_codec as codec
    from scripts.batch.harbor_admission import read, record
    p=reader.owned();old=codec.decode(p['old_selection']).content
    core.require(selection.dialogue_scope_receipt==old.dialogue_scope_receipt and
        selection.scope_evidence==old.scope_evidence and selection.bindings['source_file']==p['source'] and
        selection.pre_dialogue_witness is not None,'source-card scope authority differs')
    historical=original(replace(selection,dialogue_claims=p['original_claims']),root=root)
    actual=reader.extractor().extract(selection.pre_dialogue_witness.stage)
    core.require(actual==read(p['claims']),'corrected source-card scope does not reproduce')
    return [*historical,reader.authority,p['source_proof'],p['original_claims'],p['claims']]



def campaign(original, reader, worker):
    old = "        if completion.get('schema_version')=='harbor-dialogue-pre-review-repair/v1':"
    new = ("        if completion.get('schema_version')==_source_core.SCHEMA:\n"
           "            configured=worker_decode(read(descriptor)['worker'])\n"
           "            require(configured.dialogue_pre_review_repair==bound==_source_cards.authority, 'source-card checkpoint detached from selected worker')\n"
           "            _source_cards.validate(configured,read(record(configured.context.tracker_root/'0200.json')))\n"
           "            permit=completion\n"
           "        elif completion.get('schema_version')=='harbor-dialogue-pre-review-repair/v1':")
    return revised(original, '_prior', [(old, new)], {'_source_cards': reader, '_source_core': core, 'worker': worker})


def renewal(original, reader):
    """Keep original renew CAS; distinguish the source author pair from old Q."""
    old = """                from scripts.batch.harbor_dialogue_pre_review_repair import validate as validate_pre_review
                validate_pre_review(worker,before)"""
    new = """                if _source_cards.is_card(worker):
                    _source_cards.validate(worker,before)
                else:
                    from scripts.batch.harbor_dialogue_pre_review_repair import validate as validate_pre_review
                    validate_pre_review(worker,before)"""
    selected = revised(original, 'authorize', [(old, new)], {'_source_cards': reader})
    scope = selected.declared_namespace
    before = inspect.getsource(original._boundary)
    old = "        if worker.dialogue_pre_review_repair is not None and worker.dialogue_semantic_retry is None and phase=='dialogue-paraphraser':"
    core.require(before.count(old) == 1, 'unreviewed source-card renewal boundary')
    after = before.replace(old, old.replace('if worker.', 'if not _source_cards.is_card(worker) and worker.', 1))
    exec(compile(after, str(Path(__file__).absolute())+'::_boundary', 'exec'), scope)
    selected.declared_function_changes['_boundary'] = dict(before=before, after=after,
        before_sha256=hashlib.sha256(before.encode()).hexdigest(), after_sha256=hashlib.sha256(after.encode()).hexdigest())
    ordinary = scope['_retained_lease_adapter']

    def retained(worker, before):
        if reader.is_card(worker) and worker.lease_renewal == reader.owned()['prior_continuations']['lease_renewal']:
            return reader.historical_lease(worker, before)
        return ordinary(worker, before)

    scope['_retained_lease_adapter'] = retained
    selected.__dict__.update(scope)
    reader.current_renewal = selected
    return selected
