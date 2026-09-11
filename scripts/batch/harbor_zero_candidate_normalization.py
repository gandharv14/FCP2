"""Read-only proof that a fresh normalization has no mechanically eligible cell.

No semantic Call is fabricated. Plain eligibility and current complete audit /
source / segmentation / exact emission remain mandatory owning gates.
"""
from dataclasses import asdict
from pathlib import Path

from scripts.batch import harbor_normalization_producer as producer
from scripts.batch.harbor_admission import read,record,require
from scripts.batch.harbor_review_context import json_value,normalization_body
from scripts.batch.harbor_model_review_provenance import Policy
from scripts.batch.harbor_tracker_lifecycle import checked_record


def validate_no_candidates(selection,*,rows=None,draft=None):
    s=selection;b=s.bindings;run=s.normalization_run
    require(s.normalization_call is None,'zero-candidate branch cannot consume a model Call')
    require(not b.get('normalization_prepared_request'),'zero candidates cannot carry a submitted review request')
    require(not b.get('normalization_audit_applicability') and not b.get('normalization_original_locator_code'),
            'no-candidate proof does not reinterpret a historical review')
    prepared=s.normalization_prepared;checked_record(prepared);manifest=read(prepared)
    require(manifest.get('schema_version')=='prepared-normalization-review/v1','fresh supported no-candidate preparation required')
    checked_record(manifest['selection']);plan=read(manifest['selection'])
    require(Path(manifest['selection']['path'])==run/'production-selection.json','foreign preparation recipe')
    require(plan['workbook']==s.workbook and Path(plan['run'])==run,'foreign no-candidate workbook/run')
    require(all(b.get(key)==value for key,value in plan['bindings'].items()),'no-candidate source/audit selection changed')
    budget=plan['budget']
    recipe=producer.Selection(s.workbook,plan['bindings'],run,Policy(**plan['policy']),
        producer.Budget(budget['ordinal'],budget['limit'],budget['attempt_id'],tuple(budget['evidence'])),plan['first_normalization'])
    require(recipe.policy==s.model_policy,'no-candidate root policy differs')
    current,_=producer.selection_data(recipe)
    require(current==plan,'no-candidate current code/budget/generation evidence changed')
    checked_record(b['normalization_draft'])
    actual_draft,actual_rows=producer.analyse(recipe)
    require(draft is None or actual_draft==draft,'owning draft differs from independent reanalysis')
    require(rows is None or actual_rows==rows,'owning rows differ from independent reanalysis')
    require(actual_rows and all(row['clean'] is False for row in actual_rows),'empty or mechanically eligible candidates require review')
    require(actual_draft==producer.import_table(Path(b['variable_source_audit']['path'])),'no-candidate audit import is incomplete')
    draft_binding=record(run/'draft.json')
    require(b['normalization_draft']==draft_binding,'no-candidate draft binding differs')
    expected_body=dict(source=b['source_file'],baseline=b['baseline_inputs'],draft=draft_binding,
        source_sha256=b['source_file']['sha256'],baseline_sha256=b['baseline_inputs']['sha256'],draft_sha256=draft_binding['sha256'],
        candidates=[],context={},candidate_primitive_checks=[],audit=b['variable_source_audit'],
        current_source_generation=b['source_generation'],current_segmentation_generation=b['segmentation_generation'])
    if b.get('audit_equivalence') is not None:expected_body['audit_equivalence']=b['audit_equivalence']
    checked_record(manifest['evidence']);checked_record(manifest['analysis'])
    require(Path(manifest['evidence']['path'])==run/'review-evidence.json' and Path(manifest['analysis']['path'])==run/'analysis.json',
            'no-candidate evidence belongs to another run')
    require(read(manifest['evidence'])==expected_body and read(manifest['analysis'])==json_value(actual_rows),
            'no-candidate evidence/analysis differs from current complete reconstruction')
    normalization_body(expected_body,source=b['source_file'],baseline=b['baseline_inputs'],draft=draft_binding,rows=actual_rows,bindings=b)
    expected=dict(schema_version='prepared-normalization-review/v1',status='no_maskable_candidates',request=None,
        evidence=manifest['evidence'],analysis=manifest['analysis'],draft=draft_binding,selection=manifest['selection'],
        candidate_count=0,candidate_references=[],policy=asdict(recipe.policy),budget=asdict(recipe.budget),semantic_budget_reset=False)
    require(manifest==json_value(expected),'no-candidate manifest is not exact fresh preparation')
    for name in ('review-request.json','normalization-review.json','oracle_forced_exclusions.json','source-display-review.json'):
        require(not (run/name).exists(),'no-candidate preparation contains post-hoc review/exclusion evidence')
    # Do not create or repair missing files during readiness inspection.
    for key,suffix in [('variable_source_metadata','.metadata.json'),('variable_source_inventory','.inventory.json')]:
        path=run/(s.workbook+'-inputs-variable-sources'+suffix)
        require(path.is_file() and path.read_bytes()==producer.raw(b[key]),'no-candidate inventory floor evidence changed')
    first=record(run/'first_normalization.json')
    first_count=read(first).get('atomic_variables')
    require(type(first_count) is int and first_count==0,'original nonempty normalization cannot become plain')
    mode=producer.plain_eligibility.evaluate(run)
    require(mode['mode']=='plain' and mode['eligible'] is True,'plain eligibility rejects no-candidate normalization')
    return {'semantic_review_required':False,'candidate_count':0,'analyzed_cells':len(actual_rows),
        'evidence':[prepared,manifest['selection'],manifest['evidence'],manifest['analysis'],draft_binding,first,
                    record(Path(__file__)),*recipe.budget.evidence],
        'semantic_attempts_added':0,'mode':'plain'}
