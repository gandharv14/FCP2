"""Closed source-only reconstruction after an opaque terminal legacy package.

Every old namespace and model reservation is retained. No old quality verdict is
adopted and this certificate cannot authorize a new model or publication.
"""
from pathlib import Path
import time
from . import harbor_legacy_research_history as L
SCHEMA='harbor-legacy-staged-source/v1'
AUTHOR_SCHEMA='harbor-legacy-staged-authoring/v1'
MCP_SCHEMA='harbor-legacy-staged-mcp-source/v1'
FIELDS={'schema_version','authorization','workbook','original','tracker','curations','audit_directory','audit_files','source_manifest','segmentation_manifest','baseline','baseline_sidecar','history_roots','known_ids','excluded','histories','retained_opaque_research_calls','new_source_reconstruction_ordinal','new_research_max_calls','new_research_max_batches','new_research_attempts_per_batch','home','created_at_ns','expires_at_ns','staged_history'}
EXTRAS={'disclosure-faithfulness.md','grader-smoke/output/answers.json','grader-smoke/output/reward-details.json','grader-smoke/output/reward.json','grader-smoke/output/reward.txt','grader-smoke/output/score_details.json','grader-smoke/workspace/answers.json'}

def terminal_empty_disclosure_evidence(actual):
    """Retain the closed empty-disclosure review and its old diagnostic logs.

    These historical bytes grant no fresh quality or model authorization.
    Partial inventories and nonempty reviews require a different reader.
    """
    names={'disclosure-faithfulness-validation.json','disclosure-faithfulness.json','disclosure-sentence-manifest.json','grader-process.log','oracle-build.log','oracle-container-id.log','oracle-container.log','oracle-process.log'}
    if not names.intersection(actual):return set()
    L.require(names<=set(actual),'complete terminal disclosure diagnostics required')
    validation=L.read(actual['disclosure-faithfulness-validation.json']);review=L.read(actual['disclosure-faithfulness.json']);manifest=L.read(actual['disclosure-sentence-manifest.json'])
    L.require(validation.get('schema_version')=='harbor-disclosure-faithfulness-validation/v1'and validation.get('valid')is True and validation.get('faults')==[]and validation.get('manifest_sha256')==actual['disclosure-sentence-manifest.json']['sha256']and validation.get('review_sha256')==actual['disclosure-faithfulness.json']['sha256'],'terminal disclosure validation differs')
    L.require(manifest.get('schema_version')=='harbor-disclosure-sentence-manifest/v1'and manifest.get('agent_record_ids')==[]and manifest.get('sentences')==[],'only empty historical disclosure supported')
    L.require(review.get('schema_version')=='harbor-disclosure-faithfulness/v1'and review.get('passed')is True and review.get('sentences')==[]and review.get('blocking_findings')==[]and review.get('non_blocking_flags')==[]and review.get('manifest_sha256')==actual['disclosure-sentence-manifest.json']['sha256']and review.get('input_hashes')==manifest.get('input_hashes')and bool(manifest.get('input_hashes')),'terminal empty disclosure review differs')
    import re
    for name in names:
        L.require(L.binding(actual[name]['path'])==actual[name],'terminal diagnostic bytes changed')
    L.require(Path(actual['grader-process.log']['path']).read_text().strip()=='exact-answer grader score: 1.0'and Path(actual['oracle-process.log']['path']).read_text().strip()=='HTTP oracle PASS','terminal diagnostic completion required')
    L.require(re.fullmatch('[0-9a-f]{64}',Path(actual['oracle-container-id.log']['path']).read_text().strip())and all(Path(actual[n]['path']).stat().st_size>0 for n in ('oracle-build.log','oracle-container.log')),'retained oracle diagnostic identity required')
    return names

def terminal_masked_scan_evidence(p,actual):
    """Retain an additional completed scan, cross-bound to the old oracle.

    This authenticates a historical report; fresh input masking is still run.
    """
    names={'masked-leakscan-report.json','mcp-input-leakscan.json'}&set(actual)
    if not names:return set()
    L.require(len(names)==1,'ambiguous additional masked scan')
    report=L.read(actual[next(iter(names))]);oracle=L.read(actual['oracle-report.json'])
    fields={'allowlist_errors','approved_value_leaks','manifest_errors','mask_cells','masked_cells_not_blank','unapproved_value_leaks','unused_allowlist_entries','valid','workbook_scanned'}
    L.require(set(report)==fields and report['valid']is True and type(report['mask_cells'])is int and report['mask_cells']>0 and all(report[k]==[]for k in fields-{'mask_cells','valid','workbook_scanned'}),'additional masked scan has findings')
    L.require(report['workbook_scanned']=='inputs_out_mcp/'+p['workbook']+'-inputs.xlsx'and oracle.get('workbook_checks')=={k:v for k,v in report.items()if k!='workbook_scanned'},'additional masked scan is detached from the original oracle')
    if 'leak_patch.json'in actual:
        patch=L.read(actual['leak_patch.json'])
        L.require(set(patch)=={'generated_at','proposals','source_report_sha256','workbook'}and patch['proposals']==[]and patch['source_report_sha256']==actual['leakscan_report.json']['sha256']and patch['workbook']=='inputs_out/'+p['workbook']+'-inputs.xlsx'and isinstance(patch['generated_at'],str),'nonempty or detached historical leak patch')
        names.add('leak_patch.json')
    return names

def notes_from_retained_draft(draft,notes):
    """Exact original claim-comment removal; never a new model verdict."""
    import re
    for row in (draft,notes):L.require(L.binding(row['path'])==row,'retained draft or notes bytes changed')
    body=re.sub(r'<!--\s*claim:([^>]+?)\s*-->','',Path(draft['path']).read_text())
    body=re.sub(r'\n{3,}','\n\n',body).strip()+'\n'
    L.require(body.encode('utf-8')==Path(notes['path']).read_bytes(),'delivered notes are not the exact retained draft projection')

def terminal_disclosure_review_evidence(actual):
    """Retain the complete old disclosure review without reusing its verdict."""
    names={'disclosure-faithfulness-validation.json','disclosure-faithfulness.json','disclosure-sentence-manifest.json'}
    if not names.intersection(actual):return set()
    L.require(names<=set(actual),'complete original disclosure review required')
    validation=L.read(actual['disclosure-faithfulness-validation.json']);review=L.read(actual['disclosure-faithfulness.json']);manifest=L.read(actual['disclosure-sentence-manifest.json'])
    L.require(validation.get('schema_version')=='harbor-disclosure-faithfulness-validation/v1' and validation.get('valid')is True and validation.get('faults')==[] and validation.get('manifest_sha256')==actual['disclosure-sentence-manifest.json']['sha256'] and validation.get('review_sha256')==actual['disclosure-faithfulness.json']['sha256'],'original disclosure validation differs')
    L.require(manifest.get('schema_version')=='harbor-disclosure-sentence-manifest/v1' and review.get('schema_version')=='harbor-disclosure-faithfulness/v1' and review.get('passed')is True and review.get('blocking_findings')==[] and review.get('manifest_sha256')==actual['disclosure-sentence-manifest.json']['sha256'] and review.get('input_hashes')==manifest.get('input_hashes') and bool(manifest.get('input_hashes')),'original disclosure review is detached')
    return names


def terminal_reviewed_mcp_evidence(actual):
    """Authenticate completed nonempty historical reviews and exact log shapes.

    The original verdict is retained, not adopted by the new source attempt.
    """
    import re
    names=terminal_disclosure_review_evidence(actual)
    L.require(len(names)==3,'complete terminal MCP disclosure review required')
    manifest=L.read(actual['disclosure-sentence-manifest.json'])
    review=L.read(actual['disclosure-faithfulness.json'])
    sentences=manifest.get('sentences');reviewed=review.get('sentences')
    L.require(isinstance(sentences,list) and bool(sentences) and isinstance(reviewed,list) and len(sentences)==len(reviewed),'nonempty complete historical review required')
    ids=[s['sentence_id']for s in sentences]
    L.require(len(ids)==len(set(ids)) and review.get('non_blocking_flags')==[],'ambiguous historical disclosure review')
    for expected,observed in zip(sentences,reviewed):
        L.require(all(observed.get(k)==expected.get(k)for k in ('sentence_id','text','record_ids')) and observed.get('verdict')=='pass' and observed.get('findings')==[],'historical sentence review differs')
    logs={'grader-process.log','oracle-build.log','oracle-container-id.log','oracle-container.log','oracle-process.log'}&set(actual)
    L.require(logs in ({'oracle-container.log'},{'grader-process.log','oracle-build.log','oracle-container-id.log','oracle-container.log','oracle-process.log'}),'unsupported historical diagnostic inventory')
    for name in names|logs:L.require(L.binding(actual[name]['path'])==actual[name],'historical diagnostic bytes changed')
    L.require(Path(actual['oracle-container.log']['path']).stat().st_size>0,'empty historical oracle log')
    if len(logs)>1:
        L.require(Path(actual['grader-process.log']['path']).read_text().strip()=='exact-answer grader score: 1.0' and Path(actual['oracle-process.log']['path']).read_text().strip()=='HTTP oracle PASS','historical diagnostic completion required')
        L.require(re.fullmatch('[0-9a-f]{64}',Path(actual['oracle-container-id.log']['path']).read_text().strip()) and Path(actual['oracle-build.log']['path']).stat().st_size>0,'historical oracle identity required')
    return names|logs


def reviewed_artifacts_without_summary(wb,aafiles,files,old,upper_bound):
    """Read a completed historical review from artifacts when its summary is absent."""
    L.require(old.get('additional_assumptions')is None,'existing dialogue summary must use its original reader')
    reviewed_dialogue_artifacts(wb,aafiles,files,upper_bound)


def reviewed_terminal_artifact_summary(wb,aafiles,files,old,upper_bound):
    """Bind the older verdict/count summary to its actual applied artifacts."""
    aa=old.get('additional_assumptions')
    required={'apply','claims','notes','review','review_round','review_verdict'}
    L.require(isinstance(aa,dict) and required<=set(aa)<=required|{'docker_copy','main_image_smoke'},'closed terminal artifact summary required')
    L.require(aa['apply']==aafiles['apply.json']['path'] and aa['review']==aafiles['review.json']['path'] and aa['notes']==files['environment/additional-assumptions.md']['path'],'terminal artifact summary detached')
    claims=L.read(aafiles['claims.json']);review=L.read(aafiles['review.json'])
    L.require(type(aa['claims'])is int and aa['claims']==len(claims['claims']) and type(aa['review_round'])is int and aa['review_round']==review['round'] and aa['review_verdict']=='pass','terminal artifact count or verdict differs')
    L.require('main_image_smoke'not in aa or aa['main_image_smoke']=='pass','contradictory historical image result')
    L.require('docker_copy'not in aa or aa['docker_copy']=='COPY additional-assumptions.md /app/additional-assumptions.md','contradictory historical notes copy')
    reviewed_dialogue_artifacts(wb,aafiles,files,upper_bound)


def terminal_style_application(wb,aafiles,files,old,upper_bound):
    """Retain a historically applied, failed style review; grant no new verdict."""
    aa=old.get('additional_assumptions')
    fields={'accuracy','applied','apply','claims','copy_semantics_smoke','dockerfile_copy','environment_hygiene','main_image_smoke','model','naturalness','notes','review','round','run'}
    L.require(isinstance(aa,dict) and set(aa)==fields,'closed terminal style application summary required')
    L.require(aa['applied']is True and aa['accuracy']=='pass' and aa['naturalness']=='fail (permitted after round 2 by closed policy)' and type(aa['round'])is int and aa['round']==2,'original failed style verdict must be retained')
    L.require(aa['model']=='gpt-5.6-sol-high' and aa['environment_hygiene']=='pass' and all(aa[k]is True for k in ('copy_semantics_smoke','dockerfile_copy','main_image_smoke')),'terminal application diagnostics differ')
    L.require(aa['run']==str(Path(aafiles['review.json']['path']).parent) and aa['notes']==files['environment/additional-assumptions.md']['path'] and all(aa[k]==aafiles[k+'.json']['path']for k in ('apply','claims','review')),'terminal style summary detached')
    reviewed_dialogue_artifacts(wb,aafiles,files,upper_bound,style_failure=True)


def reviewed_dialogue_artifacts(wb,aafiles,files,upper_bound,*,style_failure=False):
    """Validate original review, application, and exact delivered draft bytes."""
    required={'apply.json','review.json','claims.json','draft.md','writer_pack.json','draft-check.json'}
    L.require(required<=set(aafiles),'complete original dialogue artifacts required')
    applied=L.read(aafiles['apply.json']);review=L.read(aafiles['review.json']);claims=L.read(aafiles['claims.json']);draft=L.read(aafiles['draft-check.json'])
    L.require(claims.get('workbook')==wb and claims.get('task')==wb+'-outputs' and claims.get('empty')is False and bool(claims.get('claims')),'original nonempty workbook claims differ')
    L.require(review.get('passed')is (not style_failure) and review.get('accuracy',{}).get('verdict')=='pass' and review.get('naturalness',{}).get('verdict')==('fail'if style_failure else 'pass') and type(review.get('round'))is int and 1<=review['round']<=2,'original terminal reviewed round required')
    if style_failure:
        expected=[r['record_id']for r in claims['claims']];observed=review['accuracy'].get('claims',[])
        L.require(review['round']==2 and len(expected)==len(set(expected)) and [r.get('record_id')for r in observed]==expected and all(r.get('must_say')=='entailed' and r.get('findings',[])==[]for r in observed) and review['accuracy'].get('extras')==[] and review['accuracy'].get('cell_refs_in_senior_turns')==[],'terminal style custody cannot conceal semantic findings')
        findings=review['naturalness'].get('findings')
        L.require(isinstance(findings,list) and bool(findings) and all(isinstance(x,str)and x.strip()for x in findings) and aafiles['review.r2.json']['sha256']==aafiles['review.json']['sha256'],'complete original failed second review required')
    L.require(draft.get('passed')is True and draft.get('faults')==[] and draft.get('accuracy_faults')==[] and draft.get('cast_faults')==[],'original draft check differs')
    count=max(review['round'],_reserved(aafiles,r'\.r(\d+)\.'))
    L.require(count<=2 and type(upper_bound)is int and upper_bound==count,'original dialogue reservations cannot reset')
    notes=files['environment/additional-assumptions.md'];marker_row=files['tests/dialogue-applied.json'];marker=L.read(marker_row)
    L.require(applied.get('applied')is True and applied.get('review_passed')is (not style_failure) and applied.get('draft_passed')is True and applied.get('draft_faults')==[] and applied.get('instruction_sha256')==files['instruction.md']['sha256'] and applied.get('notes')==notes['path'] and applied.get('marker')==marker_row['path'],'original completed dialogue application differs')
    L.require(marker.get('skill')=='additional-assumptions-dialogue' and marker.get('applied')is True and marker.get('review_passed')is (not style_failure) and marker.get('draft_passed')is True and marker.get('notes')=='environment/additional-assumptions.md' and marker.get('instruction_sha256')==files['instruction.md']['sha256'],'original dialogue marker differs')
    notes_from_retained_draft(aafiles['draft.md'],notes)


def inventory(path):
    p=Path(path)
    return {'':L.binding(p)}if p.is_file()else L.inventory(p)

def _reserved(files,prefix):
    import re
    values=[int(m.group(1))for n in files if(m:=re.search(prefix,n))]
    return max(values,default=0)

def validate(proof,*,workbook=None,original=None,history_roots=None,known_ids=None,at_ns=None):
    p=L.read(proof)
    if p.get('schema_version')==AUTHOR_SCHEMA:
        before=authoring(p,workbook=workbook,original=original,history_roots=history_roots,known_ids=known_ids,at_ns=at_ns)
        return p
    exhausted=p.get('staged_history',{}).get('dialogue_custody_kind')=='terminal_nat_exhausted'
    if exhausted:from . import harbor_legacy_nat_exhausted as N
    mcp=p.get('schema_version')==MCP_SCHEMA
    L.require(set(p)==FIELDS|({'completed_builds'}if mcp else set())and p['schema_version']in {SCHEMA,MCP_SCHEMA},'closed staged source authority required')
    now=time.time_ns()if at_ns is None else at_ns
    L.require(p['authorization']['sha256']==L.AUTH_SHA and L.binding(p['authorization']['path'])==p['authorization'],'exact staged source authorization required')
    L.require(type(p['created_at_ns'])is int and type(p['expires_at_ns'])is int and p['created_at_ns']<=now<p['expires_at_ns'] and p['expires_at_ns']-p['created_at_ns']<=24*3600*10**9,'staged source authority expired')
    wb=p['workbook'];L.require(wb in p['known_ids']and len(wb)==4 and wb.isdigit(),'known staged workbook required')
    for expected,actual in((workbook,wb),(original,p['original']),(list(map(str,history_roots))if history_roots is not None else None,p['history_roots']),(sorted(known_ids)if known_ids is not None else None,p['known_ids'])):L.require(expected is None or expected==actual,'staged source selection differs')
    L.require(L.binding(p['original']['path'])==p['original']and Path(p['original']['path']).stem==wb,'staged original changed')
    old=L.read(p['tracker']);L.require(old['workbook_id']==wb and old['source_candidate']==p['original']['path']and old['source_health']['source_sha256']==p['original']['sha256'],'old staged source identity differs')
    if exhausted:N.status(p,old)
    else:L.require(old['status']=='staged_ready'and old['mode']==('mcp'if mcp else 'plain')and old['first_blocker']is None and old['promotion_attempted']is False,'only terminal unpromoted plain stage supported')
    L.require(all(old['gates'].get(k)=='pass'for k in L.GATES),'old prerequisite source/research gates differ')
    actual=L.inventory(p['audit_directory']);expected=L.FILES|{f'{wb}-inputs-variable-sources'+suffix for suffix in('.md','.inventory.json','.metadata.json')}|{f'normalize_{wb}.py'}|(set()if exhausted else EXTRAS)
    if not mcp and p['staged_history'].get('dialogue_custody_kind')=='reviewed_applied_without_summary':expected|=terminal_disclosure_review_evidence(actual)
    diagnostic_kind=p['staged_history'].get('disclosure_custody_kind')
    L.require(diagnostic_kind is None or mcp and diagnostic_kind=='reviewed_terminal','unsupported historical disclosure custody')
    if mcp:
        expected|=L.completed_builds(p,actual)
        if exhausted:
            L.require(diagnostic_kind is None,'exhausted NAT cannot carry later review diagnostics')
            expected|=N.extra_evidence(p,actual)
        else:expected|={'oracle-report.json'}|(terminal_reviewed_mcp_evidence(actual)if diagnostic_kind else terminal_empty_disclosure_evidence(actual))|terminal_masked_scan_evidence(p,actual)
    L.require(actual==p['audit_files']and set(actual)==expected,'complete retained research namespace changed')
    meta=L.read(actual[f'{wb}-inputs-variable-sources.metadata.json']);L.require(meta.get('status')=='complete'and meta.get('workbook')==wb and meta.get('model')=='openai/gpt-5.6-sol'and meta.get('prompt_version')=='variable-source-audit-v2'and meta.get('finish_reasons')and all(x=='stop'for x in meta['finish_reasons']),'legacy research not terminal')
    L.require(meta.get('artifact')in((p['baseline']['path'],f'inputs_out/{wb}-inputs.xlsx',f'../inputs_out/{wb}-inputs.xlsx')if mcp else(p['baseline']['path'],f'inputs_out/{wb}-inputs.xlsx')),'staged research baseline detached')
    L.require(type(meta['api_calls'])is int and 0<meta['api_calls']==p['retained_opaque_research_calls']<=64,'opaque research count changed')
    L.require(old['baseline_inputs']==p['baseline'],'old baseline identity changed')
    for row in(p['baseline'],p['baseline_sidecar'],*p['curations']):L.require(L.binding(row['path'])==row,'staged financial evidence changed')
    source=L.retained_generation(p['source_manifest']);seg=L.retained_generation(p['segmentation_manifest'])
    L.require(source['generation_id']==old['source_generation_id']and source['bindings']['source_sha256']==p['original']['sha256']and seg['generation_id']==old['segmentation_generation_id'],'staged generation identities differ')
    L.require(p['curations']and all(r['sha256']==old['curation']['sha256_before']==old['curation']['sha256_after']for r in p['curations'])and old['curation']['preserved']is True and old['curation']['recurate_used']is False,'staged curation changed')
    h=p['staged_history'];L.require(set(h)=={'schema_version','namespaces','gate_inventory','package','naturalization_reservations_upper_bound','dialogue_rounds_upper_bound','new_authoring_authorized'}|({'dialogue_custody_kind'}if mcp or h.get('dialogue_custody_kind')=='reviewed_applied_without_summary' else set())|({'disclosure_custody_kind'}if diagnostic_kind else set())and h['schema_version']=='harbor-terminal-legacy-stage-custody/v1'and h['new_authoring_authorized']is False,'source-only staged custody required')
    L.require(set(h['namespaces'])==set(p['excluded'][wb])and h['gate_inventory']==old['gates'],'complete excluded stages/gates must be retained')
    for root,files in h['namespaces'].items():L.require(inventory(root)==files,'retained staged namespace changed')
    package=Path(h['package']);L.require(str(package)==old['staged_path']and package.name==wb+'-outputs'and str(package)in h['namespaces'],'staged package detached')
    files=h['namespaces'][str(package)];b=L.read(files['tests/pipeline_bindings.json']);L.require(b['source_generation_id']==source['generation_id']and b['source_manifest_sha256']==p['source_manifest']['sha256']and b['segmentation_generation_id']==seg['generation_id']and b['segmentation_manifest_sha256']==p['segmentation_manifest']['sha256'],'staged package generation differs')
    if exhausted:
        N.history(p,old,files)
        allowed={r['path']:r for r in [p['tracker'],*actual.values()]}
        for row in p['histories'][wb]:L.require(allowed.get(row['path'])==row and L.binding(row['path'])==row,'unaccounted exhausted NAT model history')
        L.require(p['tracker']in p['histories'][wb],'old exhausted NAT tracker missing')
        L.require(p['new_source_reconstruction_ordinal']==2 and p['new_research_max_calls']==16 and p['new_research_max_batches']==8 and p['new_research_attempts_per_batch']==2,'bounded prospective counts changed')
        home=Path(p['home']);L.require(home.is_absolute()and home.name==wb and '..'not in home.parts,'closed exhausted NAT source root required')
        return p
    if mcp:
        oracle=L.read(actual['oracle-report.json']);L.require(oracle.get('valid')is True and oracle['bundle']==str(package)and oracle['mcp_authoring_bundle']==str(Path(p['audit_directory'])/'mcp')and oracle['manifest_errors']==[],'retained MCP oracle identity differs')
    roots={Path(r).name:(r,files)for r,files in h['namespaces'].items()if Path(r).name!=wb+'-outputs'}
    L.require(str(Path(p['tracker']['path']))in h['namespaces'],'old staged tracker omitted')
    natroot,natfiles=roots[wb+'-instruction-naturalization'];state=L.read(natfiles['state.json']);L.require(state['status']=='applied'and state['attempt_limit']==2 and state['instruction_path']==str(package/'instruction.md'),'legacy NAT is not terminal applied')
    nat=max(len(state['attempts']),_reserved(natfiles,r'^attempt-(\d+)/'));L.require(0<nat<=2 and type(h['naturalization_reservations_upper_bound'])is int and h['naturalization_reservations_upper_bound']==nat,'legacy NAT reservations cannot reset')
    aaroot,aafiles=roots[wb+'-additional-assumptions'];aa=old.get('additional_assumptions')
    kind=h.get('dialogue_custody_kind','reviewed_applied')
    L.require(kind in {'terminal_style_applied','reviewed_terminal_artifacts','reviewed_applied_without_summary','reviewed_applied','reviewed_applied_artifacts','reviewed_applied_draft_projection','empty_not_launched','empty_terminal_artifacts'},'closed dialogue custody kind required')
    expected_roots={str(package),p['tracker']['path'],natroot,aaroot,str(Path(natroot).parent/'disclosure'/(wb+'-outputs'))}
    L.require(set(h['namespaces'])==expected_roots,'unsupported additional legacy namespace')
    if kind=='terminal_style_applied':
        L.require(mcp,'terminal style summary requires MCP custody')
        terminal_style_application(wb,aafiles,files,old,h['dialogue_rounds_upper_bound'])
    elif kind=='reviewed_terminal_artifacts':
        L.require(mcp,'terminal artifact summary requires MCP custody')
        reviewed_terminal_artifact_summary(wb,aafiles,files,old,h['dialogue_rounds_upper_bound'])
    elif kind=='reviewed_applied_without_summary':
        reviewed_artifacts_without_summary(wb,aafiles,files,old,h['dialogue_rounds_upper_bound'])
    elif kind in {'empty_not_launched','empty_terminal_artifacts'}:
        L.require(set(aafiles)=={'claims.json','writer_pack.json'}and aa.get('result')=='n/a (no agent_records)','empty dialogue requires terminal metadata and complete namespace')
        if kind=='empty_not_launched':
            L.require(aa.get('writer_launched')is False and aa.get('reviewer_launched')is False,'empty dialogue requires explicit no-launch metadata and complete namespace')
        else:
            # Missing legacy launch fields never become an invented zero count.
            # Keep the entire old two-round allowance as a conservative bound.
            L.require(mcp and all(k not in aa or aa[k]is False for k in ('writer_launched','paraphraser_launched','reviewer_launched'))and all(k not in aa or aa[k]is None for k in ('apply','review','notes')),'contradictory terminal empty dialogue summary')
        claims=L.read(aafiles['claims.json']);pack=L.read(aafiles['writer_pack.json'])
        L.require(claims['workbook']==wb and claims['task']==wb+'-outputs'and claims['empty']is True and claims['claims']==[]and pack['claims']==[]and aa['claims']==aafiles['claims.json']['path'],'empty dialogue records differ')
        L.require(type(h['dialogue_rounds_upper_bound'])is int and h['dialogue_rounds_upper_bound']==(0 if kind=='empty_not_launched'else 2),'empty dialogue cannot conceal prior rounds')
    else:
        artifact_summary=kind in {'reviewed_applied_artifacts','reviewed_applied_draft_projection'}
        if artifact_summary:
            L.require(mcp and aa.get('result')=='applied','artifact summary requires terminal MCP application')
            for key,expected in {'run':aaroot,'review_passed':True,'draft':aafiles['draft.md']['path']}.items():
                L.require(key not in aa or type(aa[key])is type(expected)and aa[key]==expected,'contradictory original dialogue summary')
            for key in ('apply','review','claims'):L.require(aa[key]==aafiles[key+'.json']['path'],'original artifact summary detached')
        else:L.require(aa['run']==aaroot and aa['review_passed']is True,'old dialogue not terminal reviewed')
        rounds=_reserved(aafiles,r'\.r(\d+)\.')
        recorded=next((aa[k]for k in('round','rounds','review_round')if k in aa),None)
        L.require(type(recorded)is int and 1<=recorded<=2,'old dialogue round count required');rounds=max(rounds,recorded)
        L.require(rounds<=2 and type(h['dialogue_rounds_upper_bound'])is int and h['dialogue_rounds_upper_bound']==rounds,'old dialogue reservations cannot reset')
        applied=L.read(aafiles['apply.json']);review=L.read(aafiles['review.json'])
        L.require(applied.get('applied')is True and applied.get('review_passed')is True and applied.get('instruction_sha256')==files['instruction.md']['sha256'],'completed dialogue application differs from retained package')
        L.require(review.get('passed')is True and review.get('accuracy',{}).get('verdict')=='pass'and review.get('naturalness',{}).get('verdict')=='pass','legacy dialogue terminal review differs')
        for key in('apply','review','draft'):
            if artifact_summary and key=='draft'and key not in aa:continue
            L.require(aa[key]in {r['path']for r in aafiles.values()},'old dialogue evidence detached')
        if artifact_summary:
            notes=files['environment/additional-assumptions.md'];marker=L.read(files['tests/dialogue-applied.json'])
            L.require(applied.get('draft_passed')is True and applied.get('draft_faults')==[]and aa['notes']==applied.get('notes')==notes['path'],'terminal draft or notes identity differs')
            if kind=='reviewed_applied_draft_projection':
                L.require('notes_sha256'not in aa,'existing historical notes digest requires its original reader')
                notes_from_retained_draft(aafiles['draft.md'],notes)
            else:L.require(aa['notes_sha256']==notes['sha256'],'terminal draft or notes identity differs')
            L.require(marker.get('skill')=='additional-assumptions-dialogue'and marker.get('applied')is True and marker.get('review_passed')is True and marker.get('draft_passed')is True and marker.get('notes')=='environment/additional-assumptions.md'and marker.get('instruction_sha256')==files['instruction.md']['sha256'],'original application marker differs')
    allowed={r['path']:r for r in [p['tracker'],*actual.values()]}
    for row in p['histories'][wb]:L.require(allowed.get(row['path'])==row and L.binding(row['path'])==row,'unaccounted staged model history')
    L.require(p['tracker']in p['histories'][wb],'old staged tracker missing')
    L.require(p['new_source_reconstruction_ordinal']==2 and p['new_research_max_calls']==16 and p['new_research_max_batches']==8 and p['new_research_attempts_per_batch']==2,'bounded prospective counts changed')
    home=Path(p['home']);L.require(home.is_absolute()and home.name==wb and '..'not in home.parts,'closed staged source root required')
    return p


def authoring(p,**selection):
    """Append fresh bounded authors only after the new source pair completes."""
    L.require(set(p)==FIELDS|{'authoring'}|({'completed_builds'}if 'completed_builds'in p else set()) and p['schema_version']==AUTHOR_SCHEMA,'closed staged author successor required')
    a=p['authoring'];L.require(set(a)=={'source_authority','source_result','snapshot','new_nat_maximum_attempts','cumulative_nat_upper_bound','new_dialogue_reviews','cumulative_dialogue_reviews_upper_bound'},'closed staged author allowance required')
    before=validate(a['source_authority'],**selection)
    L.require(before['schema_version']in {SCHEMA,MCP_SCHEMA},'staged author must follow source-only custody')
    L.require({k:v for k,v in p.items()if k not in {'schema_version','authoring'}}=={k:v for k,v in before.items()if k!='schema_version'},'staged author changed prior evidence or source authority')
    for key,value in {'new_nat_maximum_attempts':2,'new_dialogue_reviews':2,'cumulative_nat_upper_bound':before['staged_history']['naturalization_reservations_upper_bound']+2,'cumulative_dialogue_reviews_upper_bound':before['staged_history']['dialogue_rounds_upper_bound']+2}.items():
        L.require(type(a[key])is int and a[key]==value,'new staged budget cannot reset prior consumption')
    result=L.read(a['source_result']);L.require(result['status']=='RECONSTRUCTION_VERIFIED'and result['legacy_research']==a['source_authority']and result['models_dispatched']==0 and result['cumulative_reconstruction_ordinal']==2 and result['automatic_retry']is False,'completed new source-only result required')
    plan=L.read(result['plan']);L.require(plan['legacy_research']==a['source_authority']and plan['home']==p['home']+'/source'and plan['source']['sha256']==p['original']['sha256'],'staged source completion belongs to another selection')
    snapshot=L.read(a['snapshot']);L.require(Path(snapshot['root']).name==snapshot['fingerprint'],'staged author snapshot namespace differs')
    return before
