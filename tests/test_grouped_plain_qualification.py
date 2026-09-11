import copy,json
from pathlib import Path
import pytest
import plain_eligibility as P
from scripts.batch import harbor_grouped_plain_qualification as G
from scripts.batch.harbor_admission import record


def fixture_rows():
    rows=[dict(reference='S!A1',clean=True,cells=[['S','A1',1]]),dict(reference='S!A2',clean=False,cells=[['S','A2',2]])]
    prepared=dict(full_candidate_references=['S!A1'],partitions=[dict(references=['S!A1'])])
    aggregate=dict(review=dict(cells={'S!A1':dict(eligible=False)}),dependency_reconciliation=dict(candidate_ids={'S!A1':'stable'},review_decisions_changed=False))
    return rows,prepared,aggregate


def test_full_atomic_coverage_with_independent_complete_references():
    rows,m,a=fixture_rows();assert G.coverage(rows,copy.deepcopy(rows),m,a)==dict(audit_atoms=2,unique_audited_references=2,reviewed_candidate_references=1,partitions=1)

@pytest.mark.parametrize('change',['missing_atom','missing_review','missing_partition','positive_review','duplicate_id'])
def test_coverage_fails_closed(change):
    rows,m,a=fixture_rows();fresh=copy.deepcopy(rows)
    if change=='missing_atom':rows.pop()
    if change=='missing_review':a['review']['cells'].clear()
    if change=='missing_partition':m['partitions'].clear()
    if change=='positive_review':a['review']['cells']['S!A1']['eligible']=True
    if change=='duplicate_id':a['dependency_reconciliation']['candidate_ids']['foreign']='stable'
    with pytest.raises(ValueError):G.coverage(rows,fresh,m,a)


def make_run(tmp_path):
    run=tmp_path/'0184-variable-sources';run.mkdir()
    values={'draft.json':dict(rows=[dict(variable_text='a'),dict(variable_text='b'),dict(variable_text='c')]),
      'normalized.json':dict(variables=[]),'first_normalization.json':dict(atomic_variables=0),
      'normalization_report.json':dict(workbook='0184',dispositions=[dict(status='excluded',code='not_research_input',reason='provided context') for _ in range(3)]),
      'exclusions.json':dict(exclusions=[]),'0184-inputs-variable-sources.metadata.json':dict(inventory_rows=1000)}
    for name,value in values.items():(run/name).write_text(json.dumps(value,indent=2,ensure_ascii=False,sort_keys=True)+'\n')
    return run


def test_only_authenticated_coverage_replaces_grouped_floor(tmp_path,monkeypatch):
    run=make_run(tmp_path);assert P.evaluate(run)['mode']=='fail'
    (run/G.SIDECAR).write_text('{}')
    with pytest.raises(ValueError):P.evaluate(run)
    monkeypatch.setattr(G,'validate_sidecar',lambda r:dict(coverage=dict(audit_atoms=100,reviewed_candidate_references=100)))
    assert P.evaluate(run)['mode']=='plain'
    assert P.evaluate(run,allow_grouped=False)['mode']=='fail'

@pytest.mark.parametrize('change',['prior_positive','forced','changed_exclusion','nonempty_spec'])
def test_coverage_never_waives_other_plain_conditions(tmp_path,monkeypatch,change):
    run=make_run(tmp_path);(run/G.SIDECAR).write_text('{}')
    monkeypatch.setattr(G,'validate_sidecar',lambda r:dict(coverage=dict(audit_atoms=100,reviewed_candidate_references=100)))
    if change=='prior_positive':(run/'first_normalization.json').write_text('{"atomic_variables":1}')
    if change=='forced':(run/'oracle_forced_exclusions.json').write_text('["S!A1"]')
    if change=='changed_exclusion':(run/'normalization_report.json').write_text(json.dumps(dict(workbook='0184',dispositions=[dict(status='included',reason='changed')])))
    if change=='nonempty_spec':(run/'normalized.json').write_text('{"variables":[{}]}')
    assert P.evaluate(run)['mode']!='plain'


def test_receipt_is_recomputed_not_boolean_permission(tmp_path,monkeypatch):
    expected=dict(original_guard={'path':'guard','sha256':'hash'},witnesses=[],run=str(tmp_path),coverage=dict(audit_atoms=100))
    monkeypatch.setattr(G,'project',lambda *a:copy.deepcopy(expected))
    path=tmp_path/'proof.json';path.write_text(json.dumps(expected));assert G.validate(record(path),run=tmp_path)==expected
    value=copy.deepcopy(expected);value['coverage']['audit_atoms']=10000;path.write_text(json.dumps(value))
    with pytest.raises(ValueError):G.validate(record(path),run=tmp_path)

@pytest.fixture
def complete_case(tmp_path,monkeypatch):
    from dataclasses import asdict
    run=make_run(tmp_path)
    def keep(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return record(path)
    draft=json.loads((run/'draft.json').read_text())
    rows=[dict(reference=f'S!A{i}',clean=True,cells=[['S',f'A{i}',i]]) for i in range(1,25)]
    refs=[r['reference'] for r in rows]
    m=dict(full_candidate_references=refs,partitions=[dict(references=refs)])
    a=dict(review=dict(cells={r:dict(eligible=False) for r in refs}),dependency_reconciliation=dict(candidate_ids={r:r for r in refs},review_decisions_changed=False))
    outputs={name:record(run/name) for name in ['normalized.json','normalization_report.json','exclusions.json']}
    binding=dict(source_file={'path':str(tmp_path/'source.xlsx'),'sha256':'x'},baseline_inputs={'path':str(tmp_path/'inputs.xlsx'),'sha256':'y'},source_generation=dict(path=str(tmp_path/'sources/generations/a'),id='a'),segmentation_generation=dict(path=str(tmp_path/'segments/generations/b'),id='b'))
    selection=keep(tmp_path/'selection.json',dict(run=str(run),workbook='0184',bindings=binding,pipeline_bindings={'verified':'fixture'},policy=asdict(G.N.Policy('fixture'))))
    m.update(selection=selection,legacy={'draft.json':record(run/'draft.json'),'analysis.json':keep(tmp_path/'analysis.json',rows)})
    prepared=keep(tmp_path/'prepared.json',m)
    phase=keep(run/'phase1-result.json',dict(status='normalization_rejected',semantic_budget_reset=False,workbook='0184',prepared=prepared,normalization_partitioned_review=keep(tmp_path/'aggregate.json',a),outputs=outputs,phase1=keep(run/'normalized.phase1.json',{'variables':[]}),first_normalization=record(run/'first_normalization.json'),plain_eligibility=keep(run/'plain-eligibility.json',P.evaluate(run))))
    directory=tmp_path/'guard'
    intent=keep(directory/'intent.json',dict(action='emit',workbook='0184',plan=keep(tmp_path/'plan.json',dict(workbooks={'0184':dict(prepared=prepared)}))))
    keep(directory/'resource.json',dict(returncode=0,failure=None,intent=intent))
    guard=keep(directory/'result.json',dict(status='phase1',phase1=phase))
    monkeypatch.setattr(G.N.gen,'analyse',lambda *a,**k:(copy.deepcopy(draft),copy.deepcopy(rows)))
    monkeypatch.setattr(G.B,'validate',lambda *args,**kw:dict(review=copy.deepcopy(a['review']),checked_files={}))
    monkeypatch.setattr(G.N,'resolve_build_context',lambda *a,**k:dict(bindings={'verified':'fixture'}))
    projection={run/name:json.loads((run/name).read_text()) for name in outputs}
    monkeypatch.setattr(G.N,'expected_outputs',lambda *a,**k:('',copy.deepcopy(projection)))
    return run,guard,projection


def test_owning_successor_preserves_rejected_receipt_and_phase2_witnesses(complete_case,tmp_path):
    run,guard,projection=complete_case
    original=(run/'phase1-result.json').read_bytes()
    binding=G.prepare(guard,tmp_path/'successor')
    successor=G.install(binding)
    assert G.validate_successor(successor,original_guard=guard)['status']=='phase1_evidence_ready'
    forged=G.read(successor);forged['prepared']={'path':'foreign','sha256':'0'*64}
    bad=tmp_path/'forged-successor.json';bad.write_text(json.dumps(forged))
    with pytest.raises(ValueError):G.validate_successor(record(bad),original_guard=guard)
    assert (run/'phase1-result.json').read_bytes()==original
    # The existing phase2 may add its catalog while retaining an empty spec;
    # the original phase1 bytes remain in exact-hash immutable witnesses.
    projection[run/'normalized.json']['source_profiles']=[]
    projection[run/'normalization_report.json']['phase2_fixture']=True
    assert P.evaluate(run)['mode']=='plain' # catalog added, outputs still all phase1
    (run/'normalized.json').write_text(json.dumps(projection[run/'normalized.json'],indent=2,ensure_ascii=False,sort_keys=True)+'\n')
    with pytest.raises(ValueError):P.evaluate(run) # reject mixed old/new outputs
    (run/'normalization_report.json').write_text(json.dumps(projection[run/'normalization_report.json'],indent=2,ensure_ascii=False,sort_keys=True)+'\n')
    assert P.evaluate(run)['mode']=='plain'
    (run/'exclusions.json').write_text('{"exclusions":["forged"]}')
    with pytest.raises(ValueError):P.evaluate(run)


def test_missing_atomic_evidence_fails_owning_preparation(complete_case,tmp_path):
    run,guard,_=complete_case
    phase=G.read(G.read(guard)['phase1']);m=G.read(phase['prepared'])
    path=Path(m['legacy']['analysis.json']['path']);path.write_text('[]')
    with pytest.raises(ValueError):G.prepare(guard,tmp_path/'must-not-qualify')
    assert not (run/G.SIDECAR).exists()


def test_duplicate_relationships_cannot_inflate_coverage_floor():
    rows,m,a=fixture_rows();rows=[copy.deepcopy(rows[0]) for _ in range(100)]
    with pytest.raises(ValueError,match='distinct reviewed'):
        G.coverage(rows,copy.deepcopy(rows),m,a,minimum=20)

@pytest.mark.parametrize('tamper',[None,'snapshot','prior_bytes','other_current_code','proof','witness','extra_file'])
def test_only_pinned_snapshot_single_validator_repair_can_relocate(tmp_path,monkeypatch,tamper):
    prior=tmp_path/'prior';current=tmp_path/'current'
    names=[G.SELF,'plain_eligibility.py','scripts/batch/harbor_normalization_validator_bridge.py']
    for root in [prior,current]:
        for name in names:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(root.name if name==G.SELF else 'same reviewed bytes')
    snapshot=tmp_path/'downstream-snapshot-v3.json'
    snapshot.write_text(json.dumps(dict(snapshot=dict(root=str(prior),files=[dict(relative=n,sha256=record(prior/n)['sha256']) for n in names]))))
    monkeypatch.setattr(G,'PRIOR_SNAPSHOT_SHA256',record(snapshot)['sha256']);monkeypatch.setattr(G,'__file__',str(current/G.SELF))
    old=dict(code={str(prior/n):record(prior/n)['sha256'] for n in names[:2]},checked_files={str(prior/names[2]):record(prior/names[2])['sha256']},immutable={'witness':'original'})
    new=dict(code={str(current/n):record(current/n)['sha256'] for n in names[:2]},checked_files={str(current/names[2]):record(current/names[2])['sha256']},immutable={'witness':'original'})
    if tamper=='snapshot':snapshot.write_text('{}')
    if tamper=='prior_bytes':(prior/G.SELF).write_text('forged prior')
    if tamper=='other_current_code':(current/'plain_eligibility.py').write_text('unreviewed policy')
    if tamper=='proof':old['immutable']['witness']='changed'
    if tamper=='witness':old['checked_files']['foreign']='0'*64
    if tamper=='extra_file':(current/'sitecustomize.py').write_text('untrusted')
    if tamper is None:G.validate_code_relocation(old,new)
    else:
        with pytest.raises(ValueError):G.validate_code_relocation(old,new)


@pytest.mark.parametrize('tamper',[None,'unapproved_refresh','boundary_inventory','boundary_record'])
def test_exact_refresh_chain_successor_preserves_grouped_proof(tmp_path,monkeypatch,tamper):
    prior=tmp_path/'prior';boundary=tmp_path/'boundary';current=tmp_path/'current'
    names=[G.SELF,'plain_eligibility.py',G.REFRESH]
    for root in [prior,boundary,current]:
        for name in names:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(root.name if name==G.SELF else ('new refresh' if root==current and name==G.REFRESH else 'same bytes'))
    for version,root,constant in [('v3',prior,'PRIOR_SNAPSHOT_SHA256'),('v4',boundary,'BOUNDARY_SNAPSHOT_SHA256')]:
        path=tmp_path/f'downstream-snapshot-{version}.json'
        path.write_text(json.dumps(dict(snapshot=dict(root=str(root),files=[dict(relative=n,sha256=record(root/n)['sha256']) for n in names]))))
        monkeypatch.setattr(G,constant,record(path)['sha256'])
    monkeypatch.setattr(G,'REFRESH_SUCCESSOR_SHA256',record(current/G.REFRESH)['sha256'])
    monkeypatch.setattr(G,'__file__',str(current/G.SELF))
    old=dict(code={str(prior/n):record(prior/n)['sha256'] for n in names[:2]},checked_files={},immutable='retained')
    new=dict(code={str(current/n):record(current/n)['sha256'] for n in names[:2]},checked_files={},immutable='retained')
    if tamper=='unapproved_refresh':(current/G.REFRESH).write_text('unreviewed')
    if tamper=='boundary_inventory':(boundary/'injected.py').write_text('unreviewed')
    if tamper=='boundary_record':(tmp_path/'downstream-snapshot-v4.json').write_text('{}')
    if tamper is None:G.validate_code_relocation(old,new)
    else:
        with pytest.raises(ValueError):G.validate_code_relocation(old,new)
