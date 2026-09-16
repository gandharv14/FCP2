"""Declared producer namespace for source-card author2 followed by review1.

This factory is not an authorization entry point. The root-selected reader must
validate its immutable source/lease/old-call permit on every context access.
Ordinary producer functions and all existing files remain unchanged. New request
evidence explicitly includes this adapter and its independently bound reader.
"""
from pathlib import Path
from types import FunctionType, SimpleNamespace
import hashlib
import inspect

from scripts.fleet import source_card_authoring as core


def install(original, reader):
    """Build an isolated producer facade; never modify original module globals."""
    namespace = dict(original.__dict__)
    facade = SimpleNamespace(**namespace)
    for name, value in tuple(namespace.items()):
        if isinstance(value, FunctionType) and value.__globals__ is original.__dict__:
            cloned = FunctionType(value.__code__, namespace, value.__name__,
                                  value.__defaults__, value.__closure__)
            cloned.__kwdefaults__ = value.__kwdefaults__
            namespace[name] = cloned
    namespace['_source_cards'] = reader
    before = inspect.getsource(original._request)
    after = before.replace(
        "if task.dialogue_pre_review_repair is not None and ordinal==2 and kind in",
        "if not _source_cards.is_card(task) and task.dialogue_pre_review_repair is not None and ordinal==2 and kind in")
    fragment = """        from scripts.batch.harbor_dialogue_pre_review_repair import validate_task
        validate_task(task);require(ordinal==1 and limit==2,'pre-review correction changed review allowance');evidence=[*evidence,pre_review]"""
    replacement = """        if _source_cards.is_card(task):
            _source_cards.validate_task(task)
        else:
            from scripts.batch.harbor_dialogue_pre_review_repair import validate_task
            validate_task(task)
        require(ordinal==1 and limit==2,'pre-review correction changed review allowance');evidence=[*evidence,pre_review]"""
    core.require(before.count(fragment) == 1 and after.count('not _source_cards.is_card(task)') == 1,
                 'original request function differs from reviewed patch')
    after = after.replace(fragment, replacement)
    exec(compile(after, str(Path(__file__).absolute()) + '::_request', 'exec'), namespace)

    def code_evidence(task):
        existing = original.code_evidence(task)
        if reader.is_card(task):
            existing = [*existing, *reader.dependencies()]
            core.require(len({r['path'] for r in existing}) == len(existing),
                         'duplicate declared producer dependency')
        return existing

    def context(task, round, root):
        if reader.is_card(task):
            core.require(type(round) is int and round in (1, 2), 'source author round invalid')
            return reader.context(task, root=root)
        return original._dialogue(task, round, root)

    def previous(directory, round):
        if reader.owns_directory(directory):
            core.require(round == 2, 'source feedback belongs only to author generation two')
            return reader.feedback()
        return original._previous_dialogue(directory, round)

    def prepare_writer(task, round=1, *, root=original.ROOT):
        if not reader.is_card(task):
            return original.prepare_dialogue_writer(task, round, root=root)
        directory, claims, template, slots = context(task, round, root)
        authority = reader.validate_task(task)
        author = reader.author()
        body = author.author_body(template, slots, claims['writer_pack'], 'writer',
                                  previous=reader.feedback())
        evidence = [authority, *[original.record(directory/name) for name in
                    ('claims.json', 'writer_pack.json', 'draft_template.md', 'slots.json')]]
        return namespace['_request'](task, 'dialogue-writer', original.DIALOGUE, 2, 2,
            body, author.prompt(root, 'writer'), evidence, mode='json_object')

    original_prepare_para = namespace['prepare_dialogue_paraphraser']

    def prepare_para(task, round=1, *, root=original.ROOT):
        if reader.is_card(task):
            core.require(type(round) is int and round in (1, 2), 'source author round invalid')
            reader.validate_task(task)
            round = 2
        request = original_prepare_para(task, round, root=root)
        return request

    # Paraphraser's original request evidence does not include the new authority;
    # add it before its original immutable preparation, never rewrite afterward.
    prepared_request = namespace['_request']

    def request(task, kind, lane, ordinal, limit, body, prompt, evidence, **options):
        if reader.is_card(task):
            authority = reader.validate_task(task)
            expected = 1 if kind == 'dialogue-review' else 2
            core.require(kind in ('dialogue-writer', 'dialogue-paraphraser', 'dialogue-review')
                         and ordinal == expected and limit == 2
                         and options.get('structural_round') is None,
                         'source continuation cannot add another author/review attempt')
            if kind == 'dialogue-paraphraser':
                core.require(authority not in evidence, 'duplicate source certificate')
                evidence = [*evidence, authority]
        return prepared_request(task, kind, lane, ordinal, limit, body, prompt, evidence, **options)

    def review_context(task, claims, draft, round, *, root=original.ROOT):
        if not reader.is_card(task):
            return original.dialogue_review_context(task, claims, draft, round, root=root)
        authority = reader.validate_task(task)
        core.require(round == 1, 'source correction has only the unused first review')
        return (core.review_body(claims, draft, authority, reader.budget()),
                original.skill_prompt(root, 'additional-assumptions-dialogue',
                    'You are reviewing notes from a deal-team conversation') + reader.review_guidance(), [])

    def prepare_review(task, round=1, *, root=original.ROOT):
        if not reader.is_card(task):
            return original.prepare_dialogue_review(task, round, root=root)
        core.require(type(round) is int and round == 1, 'source correction has only the unused first review')
        directory, claims, _, _ = context(task, round, root)
        draft = original.record(directory/'round-02/draft.md')
        reader.verify_pair(task, original.text(draft))
        body, prompt, evidence = review_context(task, claims, original.text(draft), round, root=root)
        return request(task, 'dialogue-review', original.DIALOGUE, 1, 2, body, prompt,
                       [original.record(directory/'claims.json'), draft, *evidence])

    def consume_review(task, selected_request, writer, paraphraser, campaign, *, apply=False, root=original.ROOT):
        if not reader.is_card(task):
            return original.consume_dialogue_review(task, selected_request, writer, paraphraser,
                                                    campaign, apply=apply, root=root)
        namespace['validate_request'](task, selected_request, 'dialogue-review')
        directory, claims, _, _ = context(task, 1, root)
        run = directory/'round-02'
        draft = original.text(original.record(run/'draft.md'))
        calls = tuple(original.observed(r, campaign) for r in (writer, paraphraser))
        for r, kind in zip((writer, paraphraser), ('dialogue-writer', 'dialogue-paraphraser')):
            namespace['validate_request'](task, r, kind)
        checked_authors = reader.verify_pair(task, draft, calls=calls)
        call = original.observed(selected_request, campaign)
        _, prompt, _ = review_context(task, claims, draft, 1, root=root)
        identity = original.task_context(task)[1]
        checked = core.review_identity(call, author=reader.author(), claims=claims, draft=draft,
            authority=reader.validate_task(task), budget=reader.budget(), policy=task.policy,
            authors_checked=checked_authors, workbook=identity['workbook'],
            source_sha256=identity['original']['sha256'], prompt=prompt)
        review = checked['review']
        original.put(run/'review.json', review)
        original.put(directory/'review-call-01.json', original.asdict(call))
        validator = original.skill_module(root, 'additional-assumptions-dialogue', 'validate_dialogue')
        draft_report = validator.check_draft(draft, claims, task.stage,
                                             source_workbook=original.dialogue_source_workbook(task))
        passed = validator.review_passed(review, claims, draft_text=draft) and draft_report['passed'] is True
        result = dict(passed=passed, round=1, author_generation=2, draft_report=draft_report,
            review_faults=validator.review_faults(review, claims, draft_text=draft),
            review=original.record(run/'review.json'), draft=original.record(run/'draft.md'),
            call=original.asdict(call), publication_ready=False)
        warnings = validator.review_warnings(review, claims, draft_text=draft)
        if warnings:
            result['warnings'] = warnings
        original.put(run/'review-result.json', result)
        if apply:
            core.require(passed, 'source-corrected dialogue still fails independent review or mechanics')
            validator.apply(task.stage, run/'draft.md', claims, review, require_review_pass=True,
                            skip_smoke=True, source_workbook=original.dialogue_source_workbook(task))
            result.update(applied=True, current_runtime_required=True)
        return result

    namespace.update(code_evidence=code_evidence, _dialogue=context,
        _previous_dialogue=previous, _request=request, prepare_dialogue_writer=prepare_writer,
        prepare_dialogue_paraphraser=prepare_para, prepare_dialogue_review=prepare_review,
        dialogue_review_context=review_context, consume_dialogue_review=consume_review)
    facade.__dict__.update(namespace)
    facade.declared_function_changes = {'_request': dict(
        before_sha256=hashlib.sha256(before.encode()).hexdigest(),
        after_sha256=hashlib.sha256(after.encode()).hexdigest(), before=before, after=after)}
    return facade
