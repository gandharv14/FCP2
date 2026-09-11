"""Real chained journal/CAS, with the same owning-I/O boundary as v1 tests."""
import copy
from dataclasses import replace
from pathlib import Path
import pytest
from scripts import harbor_legacy_refresh as q
from scripts.batch import harbor_legacy_requalification as v1
from scripts.batch.harbor_admission import read,record
from scripts.batch import harbor_tracker_lifecycle as life
from test_harbor_legacy_requalification import setup
from scripts import corpus_qualification


def case(tmp_path,monkeypatch):
    p,old,s,pub,args,source,proof,code=setup(tmp_path,monkeypatch)
    original=v1.requalify(p,s,pub,**args)
    auth=life.RootAuthority('harbor-orchestrator',100,lambda operation,target,before,request:target==str(p) and operation in ('refresh_legacy_requalification','recover_legacy_refresh'))
    def options(at=40):return dict(authority=auth,expected_revision=read(record(p))['revision'],expected_sha256=record(p)['sha256'],trusted_public_key=s.content.trusted_public_key,clock=lambda:at)
    return p,old,s,pub,source,proof,code,original,auth,options


def test_two_code_only_refreshes_preserve_v1_and_exact_history(tmp_path,monkeypatch):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch)
    v1tree={str(x):x.read_bytes() for x in Path(first['journal']['path']).parent.rglob('*') if x.is_file()}
    before=p.read_bytes();code.write_text('version=2\n')
    second=q.refresh(p,first['journal'],s,pub,**args())
    after=read(record(p));assert second['revision']==120 and after['handoffs'][-1]['binding_changes']=={}
    for k in set(old)-v1.CHANGED:assert after[k]==old[k]
    assert q.validate(source,p,trusted_public_key=s.content.trusted_public_key)['verified_promoted']
    assert {str(x):x.read_bytes() for x in Path(first['journal']['path']).parent.rglob('*') if x.is_file()}==v1tree
    code.write_text('version=3\n')
    stale=q.validate(source,p,trusted_public_key=s.content.trusted_public_key);assert stale['state']=='STALE' and not stale['verified_promoted']
    third=q.refresh(p,second['journal'],s,pub,**args(50))
    assert third['revision']==121
    result=corpus_qualification.validate_terminal(source,p.parents[2],trusted_public_key=s.content.trusted_public_key)
    assert result['verified_promoted'] and result['sequence']==2
    assert len(read(record(p))['handoffs'])==len(old['handoffs'])+3
    # Runtime started25: after original baseline20, before v1 CAS30. Unrelated
    # code changes do not fabricate a newer runtime run or change chronology.
    assert read(s.observation.execution)['started_at_ns']==25


def test_current_v1_is_not_refreshed_and_v1_api_remains_single_use(tmp_path,monkeypatch):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='not code-only stale'):q.refresh(p,first['journal'],s,pub,**args())
    with pytest.raises(ValueError,match='already requalified'):v1._baseline(read(record(p)),p)


@pytest.mark.parametrize('point',['prepared','after_tracker_cas'])
def test_exact_interrupted_cas_recovers_once(tmp_path,monkeypatch,point):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch);code.write_text('version=2')
    def fail(where):
        if where==point:raise RuntimeError('fixture interruption')
    with pytest.raises(RuntimeError):q.refresh(p,first['journal'],s,pub,**args(),fault_hook=fail)
    journal=record(next(p.parent.glob('0245-legacy-refreshes/*/transaction.json')))
    recovered=q.recover(p,journal,authority=auth,trusted_public_key=s.content.trusted_public_key)
    assert q.recover(p,journal,authority=auth,trusted_public_key=s.content.trusted_public_key)==recovered
    assert recovered['revision']==120 and q.validate(source,p,trusted_public_key=s.content.trusted_public_key)['verified_promoted']


@pytest.mark.parametrize('point',['prepared','after_tracker_cas'])
def test_stale_recovery_never_commits_unproven_before_state(tmp_path,monkeypatch,point):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch);code.write_text('version=2')
    def fail(where):
        if where==point:raise RuntimeError('fixture interruption')
    with pytest.raises(RuntimeError):q.refresh(p,first['journal'],s,pub,**args(),fault_hook=fail)
    journal=record(next(p.parent.glob('0245-legacy-refreshes/*/transaction.json')));code.write_text('version=3')
    if point=='prepared':
        before=p.read_bytes()
        with pytest.raises(ValueError,match='uncommitted stale'):q.recover(p,journal,authority=auth,trusted_public_key=s.content.trusted_public_key)
        assert p.read_bytes()==before
    else:
        result=q.recover(p,journal,authority=auth,trusted_public_key=s.content.trusted_public_key)
        assert result['state']=='STALE' and not q.validate(source,p,trusted_public_key=s.content.trusted_public_key)['verified_promoted']
        q.refresh(p,journal,s,pub,**args(50))
        assert q.validate(source,p,trusted_public_key=s.content.trusted_public_key)['verified_promoted']


@pytest.mark.parametrize('change',['original','notes','proof','pointer','v1_journal_whitespace','noncode_after_code'])
def test_all_prior_noncode_checked_before_stale(tmp_path,monkeypatch,change):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch);before=p.read_bytes();code.write_text('version=2')
    path={'original':Path(source['path']),'notes':Path(old['agent_notes']['path']),'proof':Path(proof['evidence'][0]['path']),'pointer':Path(pub.pointer['path']),'v1_journal_whitespace':Path(first['journal']['path']),'noncode_after_code':Path(s.observation.execution['path'])}[change]
    path.write_bytes(path.read_bytes()+b' ')
    previous=record(path) if change=='v1_journal_whitespace' else first['journal']
    with pytest.raises((ValueError,AssertionError)):q.refresh(p,previous,s,pub,**args())
    assert p.read_bytes()==before


@pytest.mark.parametrize('change',['counts','chronology','false_check','actual_owning_validator','binding','wrong_head','CAS'])
def test_current_owning_proof_budget_identity_and_cas_required(tmp_path,monkeypatch,change):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch);code.write_text('version=2');options=args();previous=first['journal'];before=p.read_bytes()
    if change=='counts':proof['checks']['naturalization_current_context']['result']['attempts_used']=1
    if change=='chronology':
        fresh=Path(s.observation.execution['path']).with_name('new-runtime.json');fresh.write_text('{"started_at_ns":20}');s=replace(s,observation=replace(s.observation,execution=record(fresh)))
        original_release=v1._release;monkeypatch.setattr(v1,'_release',lambda a,selection,published,**kw:original_release(a,replace(selection,observation=replace(selection.observation,execution=record(Path(proof['evidence'][0]['path'])))),published,**kw))
    if change=='false_check':proof['checks']['dialogue_review_provenance']['checked']=False
    if change=='actual_owning_validator':
        from scripts.batch.harbor_complete_readiness import complete_readiness
        monkeypatch.setattr(v1,'complete_readiness',complete_readiness)
    if change=='binding':s=replace(s,content=replace(s.content,bindings={**s.content.bindings,'curation':{'path':'/missing','sha256':'0'*64}}))
    if change=='wrong_head':previous={**previous,'sha256':'0'*64}
    if change=='CAS':options['expected_revision']-=1
    with pytest.raises((ValueError,AssertionError,FileNotFoundError,KeyError)):q.refresh(p,previous,s,pub,**options)
    assert p.read_bytes()==before


def test_unfinished_or_orphan_transaction_blocks_new_refresh(tmp_path,monkeypatch):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch);code.write_text('version=2')
    root=p.parent/'0245-legacy-refreshes';root.mkdir();(root/'unreconciled').mkdir()
    with pytest.raises(ValueError,match='incomplete refresh'):q.refresh(p,first['journal'],s,pub,**args())


@pytest.mark.parametrize('changed',['before.json','after.json','notes.md','proof.json','complete.json','transaction.json'])
def test_refresh_link_integrity_is_required_even_when_current_code_is_stale(tmp_path,monkeypatch,changed):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch)
    code.write_text('version=2')
    second=q.refresh(p,first['journal'],s,pub,**args());tx=Path(second['journal']['path']).parent
    code.write_text('version=3');path=tx/changed
    path.write_bytes(b'{}' if changed=='complete.json' else path.read_bytes()+b' ')
    # Hash-bound artifacts reject whitespace changes; the completion marker
    # instead checks its exact expected semantic content.
    # Rebinding the requested journal cannot change its sealed tracker event.
    selected=record(path) if changed=='transaction.json' else second['journal']
    before=p.read_bytes()
    with pytest.raises(ValueError):q.refresh(p,selected,s,pub,**args(50))
    assert p.read_bytes()==before


def test_changed_runtime_validator_requires_fresh_runtime_selection(tmp_path,monkeypatch):
    p,old,s,pub,source,proof,code,first,auth,args=case(tmp_path,monkeypatch)
    code.write_text('version=2');before=p.read_bytes()
    from scripts.batch import harbor_runtime_readiness as runtime
    # Exercise the real owning runtime input validator (no Docker). The fixture
    # boundary supplies an exact stage tree but deliberately retains old code.
    selected=replace(s.runtime,stage_tree=runtime.tree(s.runtime.stage),code={'old-runtime.py':'0'*64})
    def owning_boundary(*_args,**_kwargs):
        runtime._inputs(selected,runtime.ROOT)
        return copy.deepcopy(proof)
    monkeypatch.setattr(v1,'complete_readiness',owning_boundary)
    with pytest.raises(ValueError,match='runtime validator code changed'):
        q.refresh(p,first['journal'],s,pub,**args())
    assert p.read_bytes()==before
