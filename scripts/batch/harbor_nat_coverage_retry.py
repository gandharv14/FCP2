"""Two corrective reviews after an incomplete first NAT review.

The candidate and writer budget stay unchanged. Every correction carries the
exact prior model response and owning coverage errors. Semantic rejection,
invented evidence and interrupted requests never become approval. After three incomplete reviews, reject the candidate while retaining all
responses. No coverage exception can authorize acceptance.
"""
from dataclasses import asdict
import json

from scripts.batch import harbor_instruction_producer as p
from scripts.batch.harbor_admission import read, record, require

MAX_REVIEWS = 3


def coverage_feedback(source, candidate, review, *, root):
    source_lines = p.editable_claims(root, source)
    candidate_lines = p.editable_claims(root, candidate)
    checks = review['clause_checks']
    actual = [row['source_claim'] for row in checks]
    quoted = [row['candidate_claim'] for row in checks]
    return dict(
        code='incomplete_clause_coverage',
        missing_source_lines=[line for line in source_lines if line not in actual],
        unexpected_source_lines=[line for line in actual if line not in source_lines],
        duplicate_source_lines=list(dict.fromkeys(line for line in actual if actual.count(line)>1)),
        source_order_matches=actual == source_lines,
        required_source_lines_in_order=source_lines,
        missing_candidate_lines=[line for line in candidate_lines if not any(line in q for q in quoted)],
        instruction='Return the complete review again. Quote every source line once in its original order. '
                    'Use verbatim candidate excerpts covering every nonempty editable candidate line. '
                    'Include unchanged lines and bullets. Do not summarize quotations. '
                    'Keep any substantive semantic findings; coverage correction is not permission to approve a mismatch.')


def prepare(task, original, previous, failure, campaign, *, ordinal, root):
    require(type(ordinal) is int and ordinal in (1,2),'only two coverage corrections are permitted')
    require(failure.get('coverage_retry_required') is True and failure.get('passed') is False,
            'only a completed coverage rejection permits correction')
    context=p.validate_request(task,original,'naturalization-review')
    writer_round=context['writer_round']
    expected=('naturalization-review' if ordinal==1 else
              'naturalization-coverage-review-writer-%02d'%writer_round)
    prior_context=p.validate_request(task,previous,expected)
    require(previous.prepared.ordinal == (1 if ordinal==1 else ordinal-1),'prior correction ordinal differs')
    call=p.observed(previous,campaign)
    require(call==failure['call'],'feedback is not bound to the completed prior call')
    payload=read(original.prepared.request)
    body=json.loads(payload['messages'][1]['content'])
    require(json.loads(read(previous.prepared.request)['messages'][1]['content'])==body,
            'coverage correction changed source/candidate')
    review=p.validate_call(call,read(previous.prepared.request),policy=task.policy)['review']
    feedback=coverage_feedback(body['source'],body['candidate'],review,root=root)
    require(feedback==failure['feedback'],'coverage feedback changed')
    evidence=p.put(task.run/('coverage-review-feedback-%02d-%02d.json'%(writer_round,ordinal)),
        dict(schema_version='harbor-nat-coverage-feedback/v1',original=asdict(original),
             previous=asdict(previous),call=asdict(call),feedback=feedback,
             total_attempt=ordinal+1,total_limit=MAX_REVIEWS))
    prompt=payload['messages'][0]['content']+'\nYour previous review was incomplete. Owning validator feedback:\n'+json.dumps(feedback,ensure_ascii=False)
    prompt+='\nExact previous review:\n'+json.dumps(review,ensure_ascii=False)
    return p._request(task,'naturalization-coverage-review-writer-%02d'%writer_round,p.NAT,
        ordinal,2,body,prompt,[original.context,previous.context,evidence])


def run(worker, task, original, writer, directory, *, models, credential):
    """Return a result/pending status without discarding an existing request."""
    from scripts.batch import harbor_instruction_worker as w
    request=original
    history=[]
    for number in range(MAX_REVIEWS):
        pending=w._model(worker,task,request,models,credential)
        if pending:return None,pending
        result=p.consume_naturalization_review(task,request,writer,worker.campaign,
            apply=True,root=worker.root,defer_coverage=True)
        if not result.get('coverage_retry_required'):
            return dict(result,request=request),None
        history.append(dict(request=request.prepared.request,call=asdict(result['call']),feedback=result['feedback']))
        if number==MAX_REVIEWS-1:
            # Consume the final completed response through the strict rejection
            # path. This records the failure and retains the remaining writer
            # attempt; an incomplete reviewer response never becomes approval.
            rejected=p.consume_naturalization_review(task,request,writer,worker.campaign,
                apply=False,root=worker.root,defer_coverage=False)
            return dict(rejected,request=request,coverage_exhausted=True),None
        path=directory/('coverage-review-%02d.json'%(number+1))
        request=w.request_saved(path) if path.exists() else w.request_saved(path,
            prepare(task,original,request,result,worker.campaign,ordinal=number+1,root=worker.root))
    raise AssertionError('bounded coverage loop did not terminate')


def validate_exception(directory, selected_call, *, writer_call, writer_request, writer_bodies, policy, root):
    """Reject legacy exception receipts; coverage must always be complete."""
    require(not (directory/'coverage-exception.json').exists(),
            'legacy incomplete coverage exception cannot authorize naturalization')
    return None
