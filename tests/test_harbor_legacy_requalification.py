"""Real journal/CAS mechanics; owning financial/runtime I/O is a test boundary.

No test PASS callback exists in the production API. Dedicated owning tests cover
actual full validation, and a negative fixture restores that validator here.
"""
import copy
from dataclasses import replace
from pathlib import Path
import pytest
from scripts.batch import harbor_legacy_requalification as q
from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch.harbor_admission import record,read
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_complete_readiness import ReadinessSelection
from scripts.batch.harbor_runtime_readiness import RuntimeSelection,RuntimeObservation
from scripts.batch.harbor_ready_transitions import ReleaseSelection
from scripts import corpus_qualification
ACTUAL_RELEASE=q._release


def setup(tmp_path,monkeypatch):
    root=tmp_path/'harbor/workbooks';root.mkdir(parents=True)
    tracker=root/'0245.json';raw=tmp_path/'original.xlsx';raw.write_bytes(b'fixture primitive evidence')
    oldnotes=root/'old-notes.md';oldnotes.write_text('Unmodified legacy notes\n')
    stage=tmp_path/'0245-outputs';stage.mkdir();(stage/'instruction.md').write_text('fixture content')
    proof_file=tmp_path/'mechanical-boundary.json';proof_file.write_text('{"mechanical_fixture":true,"started_at_ns":25}')
    code=tmp_path/'current-code.py';code.write_text('version=1\n')
    publication=tmp_path/'publication';publication.mkdir()
    (publication/'current-release.json').write_text('{"release_id":"retained"}')
    (publication/'release-manifest.json').write_text('{}')
    gen={'id':'b'*64,'manifest_sha256':'c'*64,'path':str(tmp_path/'gen')}
    bindings={'original_raw_source':record(raw),'source_file':record(raw),'curation':record(raw),
        'source_generation':gen,'segmentation_generation':gen,'historical_extra':{'retained':True}}
    bindings.update({key:record(raw) for key in q.UPGRADES})
    t={'schema_version':2,'workbook_id':'0245','revision':118,'updated_at':20,
        'execution_state':'PROMOTED','promotion_failure':None,'current_confidence':{'level':'high'},
        'lane_state':{lane:{'state':'passed','current_confidence':'high','attempt':2} for lane in schema.LANES},
        'leases':dict.fromkeys(schema.LEASE_KINDS),'handoffs':[{'legacy':'real retained chronology'}],
        'agent_notes':dict(record(oldnotes),updated_at=20,evidence_set_sha256='a'*64,diagnostic_signature=None),
        'generation_ids':{'source':'b'*64,'segmentation':'b'*64,'task':'d'*64,'release':'e'*64},
        'promotion_ids':{'release_id':'e'*64,'task_generation_id':'d'*64,'previous_release_id':'f'*64},
        'bindings':bindings,'repairs':{'count':2},'scheduler':{'attempts':5},
        'lease_history':[{'old':'retained'}],'upstream_restart_history':[{'old':'retained'}]}
    tracker.write_bytes(lifecycle.canonical(t))
    inventory=root.parent/'0245-publication-evidence.json'
    inventory.write_bytes(lifecycle.canonical({str(proof_file):record(proof_file)['sha256']}))
    journal=root.parent/'0245-publication-journal.json'
    journal.write_bytes(lifecycle.canonical({'events':[dict(event='PROMOTED',**t['promotion_ids'])]}))
    current=copy.deepcopy(bindings);current.update({key:record(proof_file) for key in q.UPGRADES})
    content=Selection('0245',stage,'a'*64,current,tmp_path/'normalization',tmp_path/'naturalization',{}, {})
    runtime=RuntimeSelection('0245',stage,{},'plain',{})
    chosen=ReadinessSelection(content,None,runtime,RuntimeObservation(record(proof_file),'1'*32))
    published=ReleaseSelection(publication,publication,publication,publication,'e'*64,
        record(publication/'current-release.json'),record(publication/'release-manifest.json'))
    proof={'schema_version':'harbor-complete-readiness/v1','workbook':'0245','stage_sha256':'a'*64,
        'publication_ready':True,'blockers':[],'checks':{
            'naturalization_current_context':{'checked':True,'result':{'attempts_used':2}},
            'dialogue_review_provenance':{'checked':True,'result':{'round':2}}},
        'evidence':[record(proof_file)]}
    def release_boundary(before,selection,pub,*,live=True):
        assert pub == published and selection == chosen
        lifecycle.checked_record(pub.pointer);lifecycle.checked_record(pub.manifest)
        assert (stage/'instruction.md').read_text()=='fixture content'
        return {'pointer':pub.pointer,'manifest':pub.manifest,'task_path':str(publication/'immutable-task'),'published_tree_sha256':'9'*64}
    monkeypatch.setattr(q,'_release',release_boundary)
    monkeypatch.setattr(q,'complete_readiness',lambda *a,**k:copy.deepcopy(proof))
    monkeypatch.setattr(q,'_code',lambda:[record(code)])
    authority=lifecycle.RootAuthority('harbor-orchestrator',100,lambda operation,*a:operation in
        ('requalify_legacy','recover_legacy_requalification','reconcile_legacy_preparation'))
    args=dict(authority=authority,expected_revision=118,expected_sha256=record(tracker)['sha256'],
        trusted_public_key=content.trusted_public_key,historical_evidence=[record(inventory),record(journal)],clock=lambda:30)
    source=dict(workbook_id='0245',**record(raw))
    return tracker,t,chosen,published,args,source,proof,code


def qualify(path,source,key):
    return corpus_qualification.validate_terminal(source,path.parents[2],trusted_public_key=key)


def test_transaction_qualifier_preserves_legacy_and_release(tmp_path,monkeypatch):
    p,old,s,pub,args,source,proof,code=setup(tmp_path,monkeypatch)
    old_notes=Path(old['agent_notes']['path']).read_bytes();pointer=Path(pub.pointer['path']).read_bytes()
    result=q.requalify(p,s,pub,**args);current=read(record(p))
    assert result['revision']==119 and current['promotion_ids']==old['promotion_ids']
    assert current['handoffs'][:-1]==old['handoffs']
    for key in set(old)-q.CHANGED:assert current[key]==old[key]
    assert Path(old['agent_notes']['path']).read_bytes()==old_notes
    assert Path(pub.pointer['path']).read_bytes()==pointer
    assert qualify(p,source,args['trusted_public_key'])['verified_promoted'] is True
    assert qualify(p,source,args['trusted_public_key'])['output_path']==str(pub.publication_root/'immutable-task')
    assert not any(e.get('schema_version')==lifecycle.CREATE_SCHEMA for e in current['handoffs'])
    with pytest.raises(Exception):q.requalify(p,s,pub,**dict(args,expected_revision=119,expected_sha256=record(p)['sha256']))


@pytest.mark.parametrize('where',['prepared','after_tracker_cas'])
def test_recover_interrupted_cas_exactly_once(tmp_path,monkeypatch,where):
    p,old,s,pub,args,source,*_=setup(tmp_path,monkeypatch)
    def fail(point):
        if point==where:raise RuntimeError('interrupted fixture')
    with pytest.raises(RuntimeError):q.requalify(p,s,pub,**args,fault_hook=fail)
    journal=record(next(p.parent.glob('0245-legacy-requalifications/*/transaction.json')))
    with pytest.raises(Exception):qualify(p,source,args['trusted_public_key'])
    first=q.recover(p,journal,authority=args['authority'],trusted_public_key=args['trusted_public_key'])
    second=q.recover(p,journal,authority=args['authority'],trusted_public_key=args['trusted_public_key'])
    assert first==second and first['revision']==119
    assert qualify(p,source,args['trusted_public_key'])['verified_promoted'] is True


@pytest.mark.parametrize('change',['old_history','after_history','old_notes','new_notes','code','noncode','stage',
    'selection','release_pointer','complete','journal_seal','post_tracker_change','original_source'])
def test_qualification_rejects_tampered_evidence(tmp_path,monkeypatch,change):
    p,old,s,pub,args,source,proof,code=setup(tmp_path,monkeypatch)
    result=q.requalify(p,s,pub,**args);tx=Path(result['journal']['path']).parent
    if change=='old_history':(tx/'before.json').write_text('{}')
    elif change=='after_history':(tx/'after.json').write_text('{}')
    elif change=='old_notes':Path(old['agent_notes']['path']).write_text('changed')
    elif change=='new_notes':(tx/'notes.md').write_text('changed')
    elif change=='code':code.write_text('version=2')
    elif change=='noncode':Path(proof['evidence'][0]['path']).write_text('changed')
    elif change=='stage':(s.content.stage/'instruction.md').write_text('changed')
    elif change=='selection':
        d=read(record(tx/'transaction.json'));d['selection']={'type':'execute','value':'untrusted'}
        (tx/'transaction.json').write_bytes(lifecycle.canonical(d))
    elif change=='release_pointer':Path(pub.pointer['path']).write_text('changed')
    elif change=='complete':(tx/'complete.json').write_text('{}')
    elif change=='original_source':Path(source['path']).write_bytes(b'changed')
    else:
        t=read(record(p))
        if change=='journal_seal':t['handoffs'][-1]['journal']['sha256']='0'*64
        else:t['scheduler']['attempts']=0
        p.write_bytes(lifecycle.canonical(t))
    before=p.read_bytes()
    if change=='code':
        result=qualify(p,source,args['trusted_public_key'])
        assert result['state']=='STALE' and result['prior_promotion'] and not result['verified_promoted']
    else:
        with pytest.raises(Exception):qualify(p,source,args['trusted_public_key'])
    assert p.read_bytes()==before


@pytest.mark.parametrize('change',['bad_hash','bad_revision','authority','live_lease','fake_creation',
    'missing_runtime','failed_gate','changed_curation','reset_budget'])
def test_rejects_before_tracker_write(tmp_path,monkeypatch,change):
    p,old,s,pub,args,source,proof,code=setup(tmp_path,monkeypatch)
    if change=='bad_hash':args['expected_sha256']='0'*64
    elif change=='bad_revision':args['expected_revision']=117
    elif change=='authority':args['authority']=lifecycle.RootAuthority('harbor-orchestrator',100,lambda *a:False)
    elif change in ('live_lease','fake_creation'):
        t=read(record(p))
        if change=='live_lease':t['leases']['task_agent']={'owner':'active'}
        else:t['handoffs'].append({'schema_version':lifecycle.CREATE_SCHEMA})
        p.write_bytes(lifecycle.canonical(t));args['expected_sha256']=record(p)['sha256']
    elif change=='missing_runtime':s=replace(s,runtime=None)
    elif change=='failed_gate':proof['publication_ready']=False
    elif change=='changed_curation':s.content.bindings['curation']=record(code)
    elif change=='reset_budget':proof['checks']['naturalization_current_context']['result']['attempts_used']=1
    before=p.read_bytes()
    with pytest.raises(Exception):q.requalify(p,s,pub,**args)
    assert p.read_bytes()==before


def test_actual_owning_checker_rejects_mechanical_fixture(tmp_path,monkeypatch):
    actual=q.complete_readiness
    p,old,s,pub,args,*_=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(q,'complete_readiness',actual)
    with pytest.raises(Exception):q.requalify(p,s,pub,**args)
    assert read(record(p))['revision']==118


@pytest.mark.parametrize('change',['valid_absent','existing','bound','typed_allow','other_omission'])
def test_omitted_unbound_allowlist_is_narrow_and_legacy_is_preserved(tmp_path,monkeypatch,change):
    p,old,s,pub,args,*_=setup(tmp_path,monkeypatch)
    missing=tmp_path/'oracle-allowlist.json'
    old['bindings']['oracle_allowlist']={'path':str(missing),'sha256':None}
    if change=='existing':missing.write_text('{}')
    if change=='bound':old['bindings']['oracle_allowlist']['sha256']='a'*64
    if change=='typed_allow':s=replace(s,content=replace(s.content,allowlist={'path':str(missing),'sha256':'a'*64}))
    if change=='other_omission':s.content.bindings.pop('historical_extra')
    if change=='valid_absent':
        delta=q._delta(old,s)
        assert set(delta)==q.UPGRADES and old['bindings']['oracle_allowlist']['sha256'] is None
    else:
        with pytest.raises(Exception):q._delta(old,s)


def test_historical_noncode_tree_and_journal_are_required(tmp_path,monkeypatch):
    p,old,s,pub,args,*_=setup(tmp_path,monkeypatch)
    folder=tmp_path/'old-tree';folder.mkdir();(folder/'data.txt').write_text('retained')
    inv=Path(args['historical_evidence'][0]['path'])
    inv.write_bytes(lifecycle.canonical({str(folder):q.release._tree_hash(folder)[0]}))
    args['historical_evidence'][0]=record(inv)
    q._historical(old,p,args['historical_evidence'])
    (folder/'data.txt').write_text('altered')
    with pytest.raises(Exception,match='non-code'):q._historical(old,p,args['historical_evidence'])
    with pytest.raises(Exception,match='inventory and journal'):q._historical(old,p,[])


def test_registered_code_drift_retained_only_as_historical(tmp_path,monkeypatch):
    p,old,s,pub,args,source,proof,code=setup(tmp_path,monkeypatch)
    inv=Path(args['historical_evidence'][0]['path']);inv.write_bytes(lifecycle.canonical({str(code):'0'*64}))
    args['historical_evidence'][0]=record(inv)
    result=q._historical(old,p,args['historical_evidence'])
    assert result['historical_code_changes']==[{'path':str(code),'previous_sha256':'0'*64,'current_sha256':record(code)['sha256']}]


@pytest.mark.parametrize('change',['none','task_bytes','task_id','source_id','prior_release','selected_source','curation'])
def test_real_release_identity_and_tree_checker(tmp_path,monkeypatch,change):
    p,old,s,pub,args,*_=setup(tmp_path,monkeypatch)
    identity={'workbook_id':'0245','prior_release_id':old['promotion_ids']['previous_release_id']}
    identity.update({k+'_generation':{'generation_id':old['generation_ids'][k]} for k in ('task','source','segmentation')})
    directory=pub.publication_root/'releases'/pub.release_id;directory.mkdir(parents=True)
    (directory/'release-manifest.json').write_bytes(lifecycle.canonical({'identity':identity}))
    pub=replace(pub,manifest=record(directory/'release-manifest.json'))
    task=pub.task_root/'task-generations'/old['generation_ids']['task'];task.mkdir(parents=True)
    (task/'instruction.md').write_bytes((s.content.stage/'instruction.md').read_bytes())
    (task/'generation-manifest.json').write_text('{}')
    s=replace(s,content=replace(s.content,stage_sha256=q.release._tree_hash(s.content.stage)[0]))
    monkeypatch.setattr(q.release,'resolve_current_release',lambda *a,**k:(directory,{'identity':identity}))
    if change=='task_bytes':(task/'instruction.md').write_text('different task')
    if change=='task_id':identity['task_generation']['generation_id']='0'*64
    if change=='source_id':identity['source_generation']['generation_id']='0'*64
    if change=='prior_release':identity['prior_release_id']='0'*64
    if change=='selected_source':s.content.bindings['source_generation']={'id':'0'*64}
    if change=='curation':s.content.bindings['curation']={'path':'different','sha256':'0'*64}
    if change=='none':assert ACTUAL_RELEASE(old,s,pub)['task_path']==str(task)
    else:
        with pytest.raises(Exception):ACTUAL_RELEASE(old,s,pub)


def test_recovery_rejects_tracker_cas_divergence(tmp_path,monkeypatch):
    p,old,s,pub,args,*_=setup(tmp_path,monkeypatch)
    def fail(point):
        if point=='prepared':raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):q.requalify(p,s,pub,**args,fault_hook=fail)
    journal=record(next(p.parent.glob('0245-legacy-requalifications/*/transaction.json')))
    mutated=read(record(p));mutated['revision']=120;p.write_bytes(lifecycle.canonical(mutated))
    prior=p.read_bytes()
    with pytest.raises(Exception,match='diverged'):q.recover(p,journal,authority=args['authority'],trusted_public_key=args['trusted_public_key'])
    assert p.read_bytes()==prior


def test_runtime_must_postdate_preserved_legacy_baseline(tmp_path,monkeypatch):
    p,old,s,pub,args,*_=setup(tmp_path,monkeypatch)
    # Boundary proof shape remains passing; real producer chronology is checked too.
    old['updated_at']=26;p.write_bytes(lifecycle.canonical(old));args['expected_sha256']=record(p)['sha256']
    with pytest.raises(Exception,match='predates'):q.requalify(p,s,pub,**args)


def test_code_drift_never_masks_noncode_corruption(tmp_path,monkeypatch):
    p,old,s,pub,args,source,proof,code=setup(tmp_path,monkeypatch)
    q.requalify(p,s,pub,**args);code.write_text('version=2')
    Path(proof['evidence'][0]['path']).write_text('corrupted')
    with pytest.raises(Exception,match='non-code'):qualify(p,source,args['trusted_public_key'])


def test_pre_descriptor_interruption_is_retained_and_new_preparation_allowed(tmp_path,monkeypatch):
    p,old,s,pub,args,source,*_=setup(tmp_path,monkeypatch)
    initial=p.read_bytes()
    def fail(point):
        if point=='before_descriptor':raise RuntimeError('partial preparation')
    with pytest.raises(RuntimeError):q.requalify(p,s,pub,**args,fault_hook=fail)
    tx=next(p.parent.glob('0245-legacy-requalifications/*'))
    prior={f.name:f.read_bytes() for f in tx.iterdir()}
    with pytest.raises(Exception,match='explicit recovery'):q.requalify(p,s,pub,**args)
    receipt=q.reconcile_preparation(p,tx,authority=args['authority'],expected_revision=118,
        expected_sha256=args['expected_sha256'],clock=lambda:40)
    saved=read(receipt);assert saved['qualification'] is False and saved['budget_reset'] is False
    assert p.read_bytes()==initial and not tx.exists()
    for name,raw in prior.items():assert (Path(saved['retained_directory'])/name).read_bytes()==raw
    assert q.requalify(p,s,pub,**args)['revision']==119


def test_completed_descriptor_cannot_be_relabelled_as_incomplete(tmp_path,monkeypatch):
    p,old,s,pub,args,*_=setup(tmp_path,monkeypatch)
    def fail(point):
        if point=='prepared':raise RuntimeError('complete descriptor')
    with pytest.raises(RuntimeError):q.requalify(p,s,pub,**args,fault_hook=fail)
    tx=next(p.parent.glob('0245-legacy-requalifications/*'))
    with pytest.raises(Exception,match='durable transaction'):
        q.reconcile_preparation(p,tx,authority=args['authority'],expected_revision=118,
            expected_sha256=args['expected_sha256'])
