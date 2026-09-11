import dataclasses,json
from pathlib import Path
import pytest
from test_harbor_instruction_producer import setup,ROOT
from scripts.batch import harbor_instruction_producer as p
from scripts.batch.harbor_admission import record


def test_valid_second_writer_after_mechanical_failure_gets_one_real_review(setup):
    task,campaign,respond,calls=setup;p.initialize_naturalization(task,root=ROOT)
    source=p.text(record(task.run/'naturalization/writer_source.md'));spans=p.skill_module(ROOT,'naturalize-finance-task-instruction','instruction_spans');a,b=spans.extract_editable_bodies(source.encode(),spans.scan_instruction(source.encode()))
    first=p.prepare_naturalization_writer(task,root=ROOT)
    respond(first,{'preamble_body':a.decode(),'input_body':b.decode().replace('[[F','[[BROKEN')})
    bad=p.consume_naturalization_writer(task,first,campaign,root=ROOT)
    assert bad['validation']['valid'] is False
    assert p.read(bad['state'])['status']=='retry_ready'
    with pytest.raises(ValueError,match='mechanical'):p.prepare_naturalization_review(task,root=ROOT)
    writer=p.prepare_naturalization_writer(task,root=ROOT);assert writer.prepared.ordinal==2
    respond(writer,{'preamble_body':a.decode(),'input_body':b.decode()});assert p.consume_naturalization_writer(task,writer,campaign,root=ROOT)['validation']['valid']
    review=p.prepare_naturalization_review(task,root=ROOT)
    body=json.loads(p.read(review.prepared.request)['messages'][1]['content']);lines=p.editable_claims(ROOT,body['source'])
    respond(review,{'valid':True,'blocking_findings':[],'clause_checks':[dict(source_claim=line,candidate_claim=line,preserved=True)for line in lines]})
    result=p.consume_naturalization_review(task,review,writer,campaign,root=ROOT)
    assert result['passed'] and result['attempts_used']==2 and len(calls)==3
    assert review.prepared.ordinal==1 and review.prepared.limit==1
    assert review.prepared.scope.budget_id=='naturalization-review-writer-02'
    assert not (task.run/'requests/naturalization-review-writer-01').exists()
    assert p.observed(review,campaign)==result['call'] and len(calls)==3
    with pytest.raises(ValueError):p.prepare_naturalization_writer(task,root=ROOT)


@pytest.mark.parametrize('change',['review_ordinal','review_scope','writer_round'])
def test_review_budget_identity_cannot_be_reassigned(setup,change):
    from test_harbor_instruction_producer import nat_writer
    task,campaign,respond,calls=setup;nat_writer(setup);review=p.prepare_naturalization_review(task,root=ROOT)
    if change=='review_ordinal':
        altered=dataclasses.replace(review,prepared=dataclasses.replace(review.prepared,ordinal=2))
    elif change=='review_scope':
        altered=dataclasses.replace(review,prepared=dataclasses.replace(review.prepared,scope=dataclasses.replace(review.prepared.scope,budget_id='naturalization-review-writer-02')))
    else:
        path=Path(review.context['path']);value=p.read(review.context);value['writer_round']=2;path.write_text(json.dumps(value));altered=dataclasses.replace(review,context=record(path))
    with pytest.raises(ValueError):p.validate_request(task,altered,'naturalization-review')
    assert len(calls)==1


@pytest.mark.parametrize('change',[None,'wrong_path_id','wrong_manifest_id','changed_manifest'])
def test_disclosure_plan_consumes_canonical_lane_note_bindings(setup,tmp_path,change):
    task,campaign,respond,calls=setup;tracker=Path(task.tracker['path']);value=p.read(task.tracker);value['lane_state'][p.DISCLOSURE]['state']='pending'
    # Exact binding keys emitted by _source/check_gate in harbor_lane_notes:
    # source generation/file/health/ast and segmentation generation/curation.
    for kind,digit in [('source','a'),('segmentation','b')]:
        directory=tmp_path/(kind+'-root')/'0245/generations'/(digit*64);directory.mkdir(parents=True);manifest=directory/'generation-manifest.json';manifest.write_text(json.dumps({'generation_id':directory.name}))
        value['bindings'][kind+'_generation']={'path':str(directory),'id':directory.name,'manifest_sha256':record(manifest)['sha256']}
        if kind=='source':
            original=Path(value['raw_source']['path']);source=directory/'0245.xlsx';source.write_bytes(original.read_bytes());health=directory/'health.json';health.write_text('{}');ast=directory/'ast/0245';ast.mkdir(parents=True)
            value['bindings'].update(source_file=record(source),source_health=record(health),ast_workbook_root={'path':str(ast)})
        else:
            curation=directory/'curation.toml';curation.write_text('outputs = []');value['bindings']['curation']=record(curation)
    assert 'source_generation_root' not in value['bindings'] and 'segmentation_root' not in value['bindings']
    if change=='wrong_path_id':value['bindings']['source_generation']['id']='c'*64
    elif change in ('wrong_manifest_id','changed_manifest'):
        binding=value['bindings']['source_generation'];manifest=Path(binding['path'])/'generation-manifest.json';manifest.write_text(json.dumps({'generation_id':'c'*64}))
        if change=='wrong_manifest_id':binding['manifest_sha256']=record(manifest)['sha256']
    tracker.write_text(json.dumps(value));task=dataclasses.replace(task,tracker=record(tracker))
    if change:
        with pytest.raises(ValueError):p.disclosure_command_plan(task)
    else:
        plan=p.disclosure_command_plan(task);command=plan['before_role_resolution'][0]
        assert command[command.index('--source-generation-root')+1]==str(tmp_path/'source-root')
        assert command[command.index('--seg-root')+1]==str(tmp_path/'segmentation-root')
        assert command[command.index('--source-generation-id')+1]=='a'*64
        assert command[command.index('--segmentation-generation-id')+1]=='b'*64
        assert plan['publication_ready'] is False
    assert calls==[]


def test_existing_old_reviewer_scope_cannot_silently_reset_budget(setup):
    from test_harbor_instruction_producer import nat_writer
    task,campaign,respond,calls=setup;nat_writer(setup)
    old=task.run/'requests/naturalization-review/attempt-01';old.mkdir(parents=True);(old/'retained-evidence.json').write_text('{}')
    with pytest.raises(ValueError,match='cannot reset'):p.prepare_naturalization_review(task,root=ROOT)
    assert len(calls)==1 and (old/'retained-evidence.json').read_text()=='{}'
