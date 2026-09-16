"""Read-only provenance for a separately declared, independently reviewed derivative.

An original writer pair remains generation two. Review three is independently
journaled under a cumulative reservation. No Call, draft, review, or policy is
rewritten. The caller supplies its current source/claims/task and owning context.
"""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_model_executor as E,harbor_dialogue_authoring as author
from scripts.batch.harbor_model_review_provenance import Call,Policy,validate_call,require_independent
from scripts.batch.harbor_readiness import skill_module
from scripts.batch import harbor_continuation_allowance as allowance

SCHEMA='harbor-reviewed-source-derivative-history/v1'


def retained_reader(profile,result,*,root,claims,draft,workbook,original_source,golden,policy,task_dir,warning_reader,archive=None,archive_cache=None):
    """A failed original consumer is history, never a fabricated checkpoint."""
    import importlib.util,sys
    require(warning_reader is not None,'explicit retained derivative warning reader required')
    require(record(warning_reader['path'])==warning_reader,'retained warning reader changed')
    spec=importlib.util.spec_from_file_location('_retained_derivative_warning_history',warning_reader['path'])
    reader=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reader;spec.loader.exec_module(reader)
    require(reader.SCHEMA=='harbor-dialogue-source-revision-warning/v1','foreign retained warning reader')
    p=read(profile);r=read(result)
    if archive is not None:
        require(set(archive)in({'helper','plan'},{'helper','plan','receipt'})and record(archive['helper']['path'])==archive['helper'],'closed declared historical archive required')
        spec=importlib.util.spec_from_file_location('_retained_derivative_archive',archive['helper']['path']);A=importlib.util.module_from_spec(spec);sys.modules[spec.name]=A;spec.loader.exec_module(A)
        chosen,_=A.load(archive['plan']);require(chosen['selection']==p['reader_selection']and chosen['context_helper']==p['context_helper'],'archive belongs to another historical consumer')
        require(isinstance(archive_cache,dict),'owning invocation archive cache required')
        original_loader=reader.module
        def loader(row):
            loaded=original_loader(row)
            if row!=p['helper']:return loaded
            context_loader=loaded.module
            def selected_context(name,bound):
                original=context_loader(name,bound)
                return A.context_consumer(original,archive['plan'],archive_cache,receipt=archive.get('receipt'))if bound==p['context_helper']else original
            loaded.module=selected_context
            return loaded
        reader.module=loader
    else:require(archive_cache is None,'historical cache without declared archive')
    proof=reader.inspect(profile,result)
    selected=read(p['reader_selection']);revision=read(selected['revision']);rejected=read(selected['rejection'])
    snapshot=read(selected['snapshot']);original_root=Path(snapshot.get('snapshot',snapshot)['root'])
    require(p['workbook']==workbook and p['source']==original_source and revision['source']==golden
        and Path(root)==original_root and claims==revision['claims']and draft==p['candidate'],
        'retained derivative owning selection differs')
    from scripts.batch import harbor_transition_codec as codec
    old_selection=codec.decode(read(rejected['original_intent'])['selection'])
    require(old_selection.content.stage==Path(task_dir) and old_selection.content.model_policy==policy
        and old_selection.content.bindings['original_raw_source']==original_source,
        'retained derivative owning task differs')
    call=Call(**r['call']);actual=validate_call(call,read(call.request),policy=policy)
    require(actual['review']==r['review'],'retained third review interpretation differs')
    pair=tuple(Call(**row['call'])for row in rejected['authors'])
    require(len(pair)==2 and [row['request_frame']for row in rejected['authors']]
        ==[record(Path(row['request_frame']['path']))for row in rejected['authors']],
        'retained author pair differs')
    # inspect authenticates their original uncorrected draft, every author Call,
    # the deterministic SUM derivative and all three independent reviewer Calls.
    evidence=[profile,result,warning_reader,p['helper'],p['context_helper'],p['executor'],p['allowance'],
        p['allowance_module'],p['reader_selection'],selected['reader'],selected['rejection'],selected['revision'],
        selected['snapshot'],claims,draft,original_source,golden,proof['template'],
        r['reservation'],r['outcome'],*proof['original_review_outcomes'],*([archive['helper'],archive['plan'],*archive.get('receipt',{}).values()]if archive else [])]
    return dict(schema_version=SCHEMA,profile=profile,result=result,claims=proof['claim_count'],round=3,
        author_generation=2,provenance=actual,writer_calls=pair,source_proof=proof['derivative'],
        original_reviews=proof['original_review_outcomes'],reservation=r['reservation'],
        source_warning_proof=proof,evidence=evidence)


def validate(profile,result,*,root,claims,draft,workbook,original_source,golden,policy,task_dir,warning_reader=None,archive=None,archive_cache=None):
    """Recheck all three independent Calls and the exact mathematical text delta."""
    p=read(profile);r=read(result)
    if p['schema_version']=='fleet-rejected-reader-review/v1':
        return retained_reader(profile,result,root=root,claims=claims,draft=draft,workbook=workbook,
            original_source=original_source,golden=golden,policy=policy,task_dir=task_dir,warning_reader=warning_reader,archive=archive,archive_cache=archive_cache)
    require(warning_reader is None and archive is None and archive_cache is None,'ordinary source derivative cannot select retained-reader warnings')
    if p['schema_version']=='fleet-dialogue-remaining-derivative-review/v1':
        import importlib.util,sys
        from scripts.batch import harbor_tracker_lifecycle as life
        life.checked_record(p['helper'])
        spec=importlib.util.spec_from_file_location('_remaining_derivative_history',p['helper']['path']);reader=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reader;spec.loader.exec_module(reader)
        return reader.validate_review(profile,result,root=root,claims=claims,draft=draft,workbook=workbook,original_source=original_source,golden=golden,policy=policy,task_dir=task_dir)
    require(p['schema_version']=='fleet-dialogue-source-revision-review/v1' and p['workbook']==workbook
        and p['source']==original_source and p['golden']==golden,'derivative source authority differs')
    require(r['plan']==profile and result==record(Path(p['home'])/'result.json')
        and r['status']in('SOURCE_DERIVATIVE_REVIEW_REJECTED','SOURCE_DERIVATIVE_REVIEW_PASSED_NOT_ADOPTED')
        and r['prior_consumed']==2 and r['cumulative_consumed']==3 and r['task_adopted']is False and r['qualified']is False,'actual completed additional review required')
    claims_value=read(claims);require(claims==p['claims'] and draft==p['candidate'],'derivative claims/draft selection changed')
    from scripts.batch import harbor_tracker_lifecycle as life
    for row in (original_source,golden,p['draft'],p['candidate'],p['revision'],p['helper'],p['proof'],p['selection']):life.checked_record(row)
    import importlib.util,sys
    spec=importlib.util.spec_from_file_location('_historical_source_derivative',p['revision']['path']);revision=importlib.util.module_from_spec(spec);sys.modules[spec.name]=revision;spec.loader.exec_module(revision)
    fixed,source_proof=revision.repair(Path(p['draft']['path']).read_text(),claims_value,golden['path'],read(p['selection']))
    require(fixed==Path(draft['path']).read_text() and source_proof==read(p['proof']),'source-corrected bytes differ')
    directory=Path(p['claims']['path']).parent
    scope=dict(workbook_id=workbook,original_sha256=original_source['sha256'],lane='harbor-dialogue-author',budget_id='dialogue-review')
    from scripts.batch import harbor_instruction_producer as producer,harbor_dialogue_operand_evidence as operands
    base_prompt=producer.skill_prompt(root,'additional-assumptions-dialogue','You are reviewing notes from a deal-team conversation')
    prior=[];writers=[];current_authors=None
    for number in(1,2):
        pair=tuple(Call(**read(record(directory/(kind+'-call-%02d.json'%number))))for kind in('writer','paraphraser'))
        authored=author.verify_pair(pair,policy=policy,claims=claims_value,draft=(directory/('round-%02d/draft.md'%number)).read_text(),round=number,root=root,workbook=workbook,original_sha256=original_source['sha256'])
        call=Call(**read(record(directory/('review-call-%02d.json'%number))))
        plan=read(record(Path(call.submitted['path']).parent/'plan.json'))
        require(plan['scope']==scope and plan['ordinal']==number and plan['limit']==2 and plan['policy']==asdict(policy),'retained review scope changed')
        campaign=E.Campaign(Path(plan['campaign_root']),Path(plan['slot_root']),plan['model_slots'])
        prepared=E.PreparedCall(plan['request'],policy,E.SemanticScope(**scope),number,2,plan['attempt_id'],tuple(plan['budget_evidence']))
        observed=E.inspect(prepared,campaign)
        require(observed.status=='completed' and observed.call==call,'uncertain retained review')
        actual=validate_call(call,read(call.request),policy=policy);require_independent(actual,authored)
        require(actual['review']==read(record(directory/('round-%02d/review.json'%number))) and actual['review']['round']==number and actual['review']['passed']is False,'original rejected review changed')
        require(read(call.request)['messages'][0]==dict(role='system',content=base_prompt+(operands.GUIDANCE if number==2 else '')),'retained reviewer prompt changed')
        body=json.loads(read(call.request)['messages'][1]['content']);require(body['claims.json']==claims_value and body['draft.md']==(directory/('round-%02d/draft.md'%number)).read_text() and body['round']==number,'retained full review context differs')
        prior.append((observed,actual));writers.extend(authored)
        if number==2:current_authors=pair
    reservation=read(r['reservation']);require(reservation['prior_records']==[x[0].outcome for x in prior]
        and reservation['allowance']==p['allowance'] and reservation['original_limit']==2,'cumulative review history differs')
    allowance.validate_review_reservation(r['reservation'],scope,3,3)
    saved=read(record(Path(p['home'])/'prepared.json'))
    prepared=E.PreparedCall(saved['request'],Policy(**saved['policy']),E.SemanticScope(**saved['scope']),saved['ordinal'],saved['limit'],saved['attempt_id'],tuple(saved['budget_evidence']),review_reservation=saved['review_reservation'])
    require(asdict(prepared.policy)==asdict(policy) and asdict(prepared.scope)==scope and prepared.ordinal==prepared.limit==3 and prepared.review_reservation==r['reservation'] and profile in prepared.budget_evidence,'additional call authority differs')
    observed=E.inspect(prepared,campaign);call=Call(**r['call'])
    require(observed.status=='completed' and observed.call==call and observed.outcome==r['outcome'],'additional review did not actually complete')
    payload=read(call.request);body=json.loads(payload['messages'][1]['content'])
    require(set(body)=={'claims.json','draft.md','round','operand_evidence','source_revision'} and body['claims.json']==claims_value and body['draft.md']==fixed and body['round']==3,'exact third review context changed')
    expected_prompt=(base_prompt+operands.GUIDANCE).replace('<1 or 2>','3')+'\nThis is explicitly authorized cumulative review round 3. Return round: 3. The draft is a separately declared deterministic derivative of author round 2, not a new author response. Independently review all claims and all source formulas. Recheck prior entailment findings; they can be wrong. The source_revision evidence proves a text transformation, not semantic approval. Do not waive accuracy, sign, range, coverage, leakage or naturalness requirements.'
    require(payload['messages'][0]==dict(role='system',content=expected_prompt),'additional reviewer prompt weakened')
    if 'operand_projection'in p:
        life.checked_record(p['operand_projection'])
        spec=importlib.util.spec_from_file_location('_historical_selected_operands',p['operand_projection']['path']);selected=importlib.util.module_from_spec(spec);sys.modules[spec.name]=selected;spec.loader.exec_module(selected)
        require(selected.GUIDANCE==operands.GUIDANCE,'reviewed operand guidance changed')
        operands=selected
    require(body['operand_evidence']==operands.project(claims_value,fixed,source=golden,answer_key=record(Path(task_dir)/'tests/answer_key.json'),task_dir=Path(task_dir),root=root),'third-review operand evidence changed')
    require(body['source_revision']==dict(proof=source_proof,original_review_rounds_consumed=2,derived_from_author_round=2,generated_author_calls_added=0,prior_review=prior[1][1]['review']),'review lost derivative history')
    actual=validate_call(call,payload,policy=policy);require_independent(actual,[*writers,*(x[1]for x in prior)])
    require(actual['review']==r['review'] and actual['review']['round']==3,'additional review response differs')
    return dict(schema_version=SCHEMA,profile=profile,result=result,claims=len(claims_value['claims']),round=3,author_generation=2,provenance=actual,writer_calls=current_authors,source_proof=source_proof,original_reviews=[x[0].outcome for x in prior],reservation=r['reservation'],evidence=[profile,result,p['source'],p['golden'],p['claims'],p['draft'],p['candidate'],p['proof'],p['selection'],p['revision'],p['helper'],p['allowance'],r['reservation'],r['outcome'],*([p['operand_projection']]if 'operand_projection'in p else[])])
