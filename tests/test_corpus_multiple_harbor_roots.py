"""Namespace selection is independent of financial qualification.

The strict promotion fixture retains its explicit mocked financial boundary;
these tests do not claim synthetic workbooks passed production financial gates.
"""
import json
from pathlib import Path
import shutil
import pytest
from scripts import corpus_qualification as q, corpus_report as stage
from scripts.corpus import digest
from test_harbor_promotion_evidence import promoted


def inventory(tmp_path, count=700):
    return {'inventory_sha256':'frozen-originals','sources':[
        {'workbook_id':f'{i:04d}','path':str(tmp_path/'raw'/f'{i:04d}.xlsx'),
         'sha256':f'{i:064x}','format':'xlsx'} for i in range(1,count+1)]}


def namespace(tmp_path,name='new50-20260909'):
    root=tmp_path/'harbor'/name;(root/'workbooks').mkdir(parents=True)
    return root


def write_tracker(root,wb='0001',state='PUBLICATION_READY'):
    p=root/'workbooks'/(wb+'.json');p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps({'workbook_id':wb,'execution_state':state,'promotion_ids':{}}))
    return p


def test_default_is_backward_compatible_and_new_ready_is_never_promoted(tmp_path):
    manifest=inventory(tmp_path);extra=namespace(tmp_path);write_tracker(extra)
    default=q.build_report(manifest,tmp_path)
    assert default['states']=={'NOT_RUN':700} and 'harbor_roots' not in default
    selected=q.build_report(manifest,tmp_path,harbor_roots=[extra])
    assert selected['raw_denominator']==700 and selected['verified_promoted']==0
    assert selected['states']=={'RUNNING':1,'NOT_RUN':699}
    assert selected['harbor_roots']==[str(tmp_path/'harbor'),str(extra)]
    report=stage.build_report(manifest,tmp_path,harbor_roots=[extra])
    assert report['verified_promoted'] is None and report['harbor_yield'] is None
    assert report['workbooks'][0]['harbor_state']=='PUBLICATION_READY'


def test_new_namespace_uses_full_strict_promotion_qualifier_once(tmp_path,monkeypatch):
    path,state,source,selection,proof=promoted(tmp_path/'harbor/campaign-parent',monkeypatch)
    extra=path.parent.parent;manifest=inventory(tmp_path)
    manifest['sources'][244]={**source,'format':'xlsx'}
    report=q.build_report(manifest,tmp_path,harbor_roots=[extra])
    assert report['raw_denominator']==700 and report['verified_promoted']==1
    assert report['harbor_yield']==1/700 and len(report['workbooks'])==700
    row=next(r for r in report['workbooks'] if r['workbook_id']=='0245')
    assert row['release_id']=='a'*64 and row['harbor_root']==str(extra)
    # The current owning proof is still mandatory after correct namespace routing.
    from scripts.batch import harbor_ready_transitions
    monkeypatch.setattr(harbor_ready_transitions,'_proof',lambda *a:(_ for _ in ()).throw(ValueError('current owning gate failed')))
    assert q.build_report(manifest,tmp_path,harbor_roots=[extra])['verified_promoted']==0


@pytest.mark.parametrize('identical',[False,True])
def test_conflicting_originals_are_not_cherry_picked_or_counted_twice(tmp_path,identical):
    manifest=inventory(tmp_path);extra=namespace(tmp_path)
    a=write_tracker(tmp_path/'harbor',state='PROMOTED');b=write_tracker(extra,state='STOPPED')
    if identical:b.write_bytes(a.read_bytes())
    report=q.build_report(manifest,tmp_path,harbor_roots=[extra])
    assert report['raw_denominator']==700 and report['verified_promoted']==0
    row=report['workbooks'][0]
    assert row['state']=='EVIDENCE_INVALID' and row['failure_class']=='ambiguous_original_tracker'
    assert row['candidates']==[
        {'harbor_root':str(p.parent.parent),'path':str(p),'sha256':digest(p)} for p in (a,b)]
    assert stage.build_report(manifest,tmp_path,harbor_roots=[extra])['workbooks'][0]['harbor_failure']=='ambiguous_original_tracker'


def test_conflict_binding_preserves_history_and_clears_stale_promoted_flag(tmp_path):
    manifest=inventory(tmp_path,1);extra=namespace(tmp_path)
    write_tracker(tmp_path/'harbor');write_tracker(extra)
    directory=tmp_path/'workbooks/0001';directory.mkdir(parents=True)
    ledger=directory/'ledger.json';old={'state':'PROMOTED','verified_promoted':True}
    ledger.write_text(json.dumps({'source':manifest['sources'][0],'harbor':old,'promoted':True,'history':['keep']}))
    report=q.build_report(manifest,tmp_path,harbor_roots=[extra],bind_ledgers=True)
    saved=json.loads(ledger.read_text())
    assert saved['harbor_history']==[old] and saved['promoted'] is False and saved['history']==['keep']
    assert saved['harbor']['failure_class']=='ambiguous_original_tracker'
    assert report['verified_promoted']==0


@pytest.mark.parametrize('change',['outside','missing','duplicate','default_duplicate','symlink','workbook_symlink','traversal'])
def test_bad_namespace_configuration_rejects(tmp_path,change):
    extra=namespace(tmp_path);roots=[extra]
    if change=='outside':roots=[tmp_path/'outside'];(roots[0]/'workbooks').mkdir(parents=True)
    if change=='missing':roots=[tmp_path/'harbor/absent']
    if change=='duplicate':roots=[extra,extra]
    if change=='default_duplicate':roots=[tmp_path/'harbor']
    if change=='symlink':
        link=tmp_path/'harbor/link';link.symlink_to(extra,target_is_directory=True);roots=[link]
    if change=='workbook_symlink':
        (extra/'workbooks').rmdir();(extra/'workbooks').symlink_to(tmp_path/'harbor',target_is_directory=True)
    if change=='traversal':roots=[tmp_path/'harbor/..'/'harbor/new50-20260909']
    with pytest.raises(ValueError):q.build_report(inventory(tmp_path,1),tmp_path,harbor_roots=roots)


def test_tracker_symlink_does_not_alias_another_original(tmp_path):
    extra=namespace(tmp_path);actual=write_tracker(tmp_path/'harbor')
    (extra/'workbooks/0001.json').symlink_to(actual)
    report=q.build_report(inventory(tmp_path,1),tmp_path,harbor_roots=[extra])
    assert report['states']=={'EVIDENCE_INVALID':1} and report['verified_promoted']==0


def test_relative_namespace_is_explicitly_bound_to_run(tmp_path):
    extra=namespace(tmp_path)
    assert q.selected_harbor_roots(tmp_path,['harbor/new50-20260909'])==(tmp_path/'harbor',extra)


def test_foreign_ready_workbook_identity_is_not_adopted(tmp_path):
    extra=namespace(tmp_path);path=write_tracker(extra)
    path.write_text(json.dumps({'workbook_id':'0099','execution_state':'PUBLICATION_READY'}))
    report=q.build_report(inventory(tmp_path,1),tmp_path,harbor_roots=[extra])
    assert report['states']=={'EVIDENCE_INVALID':1} and report['verified_promoted']==0


@pytest.mark.parametrize('where',['harbor','harbor/workbooks'])
def test_malformed_default_root_is_not_silently_ignored(tmp_path,where):
    path=tmp_path/where;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('not a directory')
    with pytest.raises(ValueError,match='not a directory'):q.build_report(inventory(tmp_path,1),tmp_path)


@pytest.mark.parametrize('kind',['id','path'])
def test_invalid_original_manifest_is_not_silently_deduplicated(tmp_path,kind):
    manifest=inventory(tmp_path,2)
    manifest['sources'][1]['workbook_id' if kind=='id' else 'path']=manifest['sources'][0]['workbook_id' if kind=='id' else 'path']
    for fn in (q.build_report,stage.build_report):
        with pytest.raises(ValueError,match='unique'):fn(manifest,tmp_path)


def test_foreign_original_cannot_borrow_a_valid_new_namespace_promotion(tmp_path,monkeypatch):
    path,state,source,*_=promoted(tmp_path/'harbor/campaign-parent',monkeypatch)
    manifest={'inventory_sha256':'frozen','sources':[{**source,'sha256':'0'*64}]}
    report=q.build_report(manifest,tmp_path,harbor_roots=[path.parent.parent])
    assert report['states']=={'EVIDENCE_INVALID':1} and report['verified_promoted']==0


def test_duplicate_appearing_during_qualification_invalidates_snapshot(tmp_path,monkeypatch):
    path,state,source,*_=promoted(tmp_path,monkeypatch);extra=namespace(tmp_path)
    from scripts.batch import harbor_promotion_evidence
    original=harbor_promotion_evidence.validate
    def late(*args,**kwargs):
        result=original(*args,**kwargs)
        shutil.copyfile(path,extra/'workbooks/0245.json')
        return result
    monkeypatch.setattr(harbor_promotion_evidence,'validate',late)
    report=q.build_report({'inventory_sha256':'frozen','sources':[source]},tmp_path,harbor_roots=[extra])
    assert report['states']=={'EVIDENCE_INVALID':1} and report['verified_promoted']==0


@pytest.mark.parametrize('module',[q,stage])
def test_cli_passes_all_explicit_roots(module,tmp_path,monkeypatch,capsys):
    extra=namespace(tmp_path);other=namespace(tmp_path,'second');seen={}
    monkeypatch.setattr(module,'read_inventory',lambda _:inventory(tmp_path,1))
    def report(manifest,run,**kwargs):
        seen.update(kwargs)
        return {'workbooks':[],'states':{}}
    monkeypatch.setattr(module,'build_report',report)
    monkeypatch.setattr('sys.argv',['report','--inventory','frozen.json','--run-root',str(tmp_path),
        '--harbor-root',str(extra),'--harbor-root',str(other)])
    module.main();assert seen['harbor_roots']==[str(extra),str(other)]
