"""Bounded synthetic checks of shared evidence and complete review coverage."""
import copy
import json
import tracemalloc
from dataclasses import replace
import pytest
from scripts.batch import harbor_normalization_compact as K
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_partitioned as P
from scripts.batch.harbor_admission import record,read
from test_normalization_compact import evidence
from test_harbor_normalization_producer import setup


def test_repeated_relationships_remain_linear_without_expanding_millions():
    refs=['Sheet!A'+str(i) for i in range(2000)]
    shared={'draft_id':'one','notes':'unchanged'}
    body=dict(candidates=[dict(reference=r,cells=[['Sheet',r.split('!')[1],1]],
        duplicate_refs=refs,row=shared,clean=True) for r in refs],candidate_primitive_checks=[])
    tracemalloc.start()
    projected=K.json_value(body);compact=K.compact(projected);expanded=K.expand(compact)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert peak<16*1024*1024
    assert len(compact['duplicate_reference_groups'])==1
    assert len({id(r['duplicate_refs']) for r in expanded['candidates']})==1
    assert K.equal(expanded,projected)
    assert K.encoded_size(projected)>20*1024*1024
    assert K.encoded_size(compact)<1024*1024


def test_size_estimation_matches_utf8_and_escaping_without_serialization():
    common=['é','\\','\n',None,True,1,1.2]
    value={'nested':[common]*8,'𝛼':{}}
    assert K.encoded_size(value)==len(K.encoded(value))


def test_indexed_disk_projection_and_streaming_canonical_bytes(tmp_path,monkeypatch):
    value={'a':[1,2],'unicode':'é'}
    target=tmp_path/'small.json';N.keep(target,value)
    assert target.read_bytes()==N.executor.canonical(value)
    monkeypatch.setattr(N,'MAX_CONTEXT_BYTES',1)
    body,_=evidence(tmp_path)
    bound=N.store_evidence(tmp_path/'indexed.json',body)
    assert read(bound)['schema_version']=='normalization-indexed-artifact/v1'
    assert K.equal(N.expand_artifact(read(bound)),K.json_value(body))


def test_overlapping_audit_rows_retained_in_single_physical_partition(tmp_path):
    body,_=evidence(tmp_path)
    extra=copy.deepcopy(body['candidates'][0]);extra['row']['draft_id']='second-audit-row'
    body['candidates'].append(extra)
    body['candidate_primitive_checks'].append(copy.deepcopy(body['candidate_primitive_checks'][0]))
    parts=K.partitions(body,lambda c,r:c,max_references=1)
    assert len(parts)==4
    assert sum(len(K.expand(c)['candidates']) for _,c in parts)==5
    assert K.expand(parts[0][1])['candidates'][-1]['row']['draft_id']=='second-audit-row'


def test_cross_batch_dependency_exclusion_closure_and_exact_coverage(tmp_path):
    body,_=evidence(tmp_path);refs=[r['reference'] for r in body['candidates']]
    body['candidates'][0]['duplicate_refs']=[refs[0],refs[2]]
    decisions={r:{'eligible':True} for r in refs};decisions[refs[2]]={'eligible':False}
    receipt=K.reconcile(body,decisions)
    assert refs[0] not in receipt['eligible_references']
    assert receipt['duplicate_exclusion_rounds']==[[refs[0]]]
    assert decisions[refs[0]]['eligible'] is True
    assert len(set(receipt['candidate_ids'].values()))==4
    with pytest.raises(ValueError,match='coverage'):K.reconcile(body,{**decisions,'Foreign!A1':{'eligible':True}})
    with pytest.raises(ValueError,match='coverage'):K.reconcile(body,{r:v for r,v in decisions.items() if r!=refs[2]})


@pytest.mark.parametrize('incomplete',[False,True])
def test_unsubmitted_old_code_transition_preserves_budget_and_old_evidence(setup,tmp_path,incomplete,monkeypatch):
    s,campaign,*_=setup()
    old_module=tmp_path/'old_generator.py';old_module.write_text('raise RuntimeError("must never execute")\n')
    with monkeypatch.context() as patch:
        patch.setattr(N,'code_bindings',lambda:{str(old_module):record(old_module)['sha256']})
        N.prepare(s)
    plan_path=s.run/'production-selection.json';old=read(record(plan_path))
    if incomplete:
        for name in ('analysis.json','review-evidence.json','review-request.json','prepared.json'):(s.run/name).unlink()
    original={p:p.read_bytes() for p in s.run.iterdir() if p.is_file()}
    new=replace(s,run=tmp_path/'new-runs'/s.run.name)
    plan,draft,rows,body=P.full_evidence(new)
    transition=P.transition(new,record(plan_path),plan,body,rows)
    assert transition['original_budget']==K.json_value(old['budget'])
    assert transition['semantic_attempts_added']==0
    assert transition['prior_analysis_available'] is not incomplete
    assert all(p.read_bytes()==b for p,b in original.items())
    old_module.write_text('changed')
    with pytest.raises(ValueError):P.transition(new,record(plan_path),plan,body,rows)
