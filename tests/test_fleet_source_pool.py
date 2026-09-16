from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sys
import pytest
from scripts.fleet import pool_v3 as P, fresh as F, migration as M


@pytest.fixture(autouse=True)
def no_native_workers(monkeypatch):
    monkeypatch.setattr(P,'native_workers',lambda host:set())


def queue(tmp_path):
    jobs=[]
    for wb in ['0001','0002','0003']:
        jobs.append(({'path':str(tmp_path/(wb+'.json')),'sha256':'a'*64},dict(workbook=wb,source={'sha256':'b'*64},version='c'*64,cwd=str(tmp_path),run=str(tmp_path/wb),memory_limit_bytes=16*1024**3,minimum_disk_bytes=12*1024**3)))
    status={'workbooks':[dict(workbook=j['workbook'],state='ready',source_sha256='b'*64,version='c'*64)for _,j in jobs],'operations':[]}
    return jobs,status


def healthy(_):return dict(memory_available_bytes=64*1024**3,disk_free_bytes=64*1024**3,cpus=16,load_average=[0,0,0])


def test_pending_dispatches_close_stale_status_two_slot_race(tmp_path,monkeypatch):
    jobs,status=queue(tmp_path);monkeypatch.setattr(P,'resources',healthy)
    with ThreadPoolExecutor(max_workers=3) as x:
        result=list(x.map(lambda i:P.reserve(tmp_path,jobs,status,i,'hhu-server'),range(3)))
    assert sum(r is not None for r in result)==2
    assert len(list((tmp_path/'dispatches').iterdir()))==2


def test_existing_oneoff_occupies_slot_and_completed_source_releases_it(tmp_path,monkeypatch):
    jobs,status=queue(tmp_path);monkeypatch.setattr(P,'resources',healthy)
    status['workbooks'].append(dict(workbook='0190',state='active',owner='hhu-server:slot-2:old'))
    first=P.reserve(tmp_path,jobs,status,0,'hhu-server');assert first
    assert P.reserve(tmp_path,jobs,status,1,'hhu-server') is None
    run=Path(first[1]['run']);run.mkdir();M.retain(run/'outcome.json',dict(status='RECONCILIATION_REQUIRED'))
    assert P.reserve(tmp_path,jobs,status,1,'hhu-server')[1]['workbook']=='0002'
    assert P.owner_host('hhu-server-8/slot-0/generation')=='hhu-server-8'


@pytest.mark.parametrize('fault',['hold','wrongversion','memory','unknownowner'])
def test_admission_blocks_unsafe_capacity_and_identity(tmp_path,monkeypatch,fault):
    jobs,status=queue(tmp_path);monkeypatch.setattr(P,'resources',healthy)
    if fault=='hold':status['operations']=[dict(state='hold')]
    elif fault=='wrongversion':
        for r in status['workbooks']:r['version']='d'*64
    elif fault=='memory':monkeypatch.setattr(P,'resources',lambda p:{**healthy(p),'memory_available_bytes':19*1024**3})
    else:
        status['workbooks'].append(dict(workbook='0190',state='active',owner='unregistered'))
        with pytest.raises(ValueError,match='registered'):P.reserve(tmp_path,jobs,status,0,'hhu-server')
        return
    assert P.reserve(tmp_path,jobs,status,0,'hhu-server') is None
    assert not (tmp_path/'dispatches').exists()


def source_fixture(tmp_path,monkeypatch,fault=None):
    from scripts.batch import harbor_continuation_allowance as A, hardened_runner as H
    code=tmp_path/'code';helper=code/'scripts/fleet/source_first.py';helper.parent.mkdir(parents=True);helper.write_text('pass\n')
    home=tmp_path/'author';home.mkdir();sourcehome=tmp_path/'source';wb='0001'
    source=dict(workbook=wb,inventory={},history_roots=[],snapshot=M.retain(tmp_path/'snapshot.json',dict(root=str(code))),helper=M.binding(helper),next_attempt=1,cumulative_reconstruction_limit=1,models=0,automatic_retry=False,allowance={},source={'sha256':'b'*64},seconds=7200,memory_bytes=16*1024**3,home=str(sourcehome))
    bound=M.retain(tmp_path/'source-plan.json',source)
    plan=dict(home=str(home),workbook=wb,inventory={},history_roots=[],code_root=str(code),queue={},reconstruction=dict(mode='source-first',plan=bound))
    monkeypatch.setattr(A,'validate',lambda *a,**k:dict(limits=dict(stage_seconds=7200,memory_bytes=16*1024**3)))
    calls=[]
    def child(argv,**kw):
        calls.append(argv);assert argv[:3]==[sys.executable,'-I','-B'];assert kw['timeout_seconds']==7650 and kw['memory_limit_bytes']==16*1024**3
        sourcehome.mkdir();corpus=sourcehome/'corpus'
        for name,folder in [('source','sources'),('segmentation','segments')]:
            g=corpus/folder/wb/'generations'/name;g.mkdir(parents=True)
            M.retain(g/'generation-manifest.json',dict(curated_output_cells=['S!A1']))
            if name=='segmentation':(g/'curation.toml').write_text('outputs=["S!A1"]\n')
        ledger=M.retain(corpus/'ledger.json',dict(workbook_id=wb,build=dict(state='VERIFIED',attempts=2 if fault=='counter' else 1,bindings=dict(curation_sha256=None),source_generation_id='source',segmentation_generation_id='segmentation')))
        M.retain(sourcehome/'result.json',dict(status='RECONSTRUCTION_HELD' if fault=='held' else 'RECONSTRUCTION_VERIFIED',plan=bound,ledger=ledger,models_dispatched=0,automatic_retry=False))
        return 1 if fault=='returncode' else 0
    monkeypatch.setattr(H,'_run_in_process_group',child)
    return plan,calls


def test_actual_source_receipts_gate_generated_curation_handoff(tmp_path,monkeypatch):
    plan,calls=source_fixture(tmp_path,monkeypatch)
    bindings,proof=F.reconstruct({},plan)
    assert len(calls)==1 and M.checked(proof)['ledger']==bindings['ledger']
    assert M.checked(proof)['ordered_targets']==['S!A1']
    with pytest.raises(FileExistsError):F.reconstruct({},plan)
    assert len(calls)==1


@pytest.mark.parametrize('fault',['held','counter','returncode'])
def test_failed_or_changed_source_never_enters_author_handoff(tmp_path,monkeypatch,fault):
    plan,calls=source_fixture(tmp_path,monkeypatch,fault)
    with pytest.raises(ValueError):F.reconstruct({},plan)
    assert len(calls)==1 and not (Path(plan['home'])/'reconstruction/handoff.json').exists()


def test_authenticated_collection_is_not_an_author_slot(tmp_path,monkeypatch):
    jobs,status=queue(tmp_path);monkeypatch.setattr(P,'resources',healthy)
    claim=dict(workbook='0190',state='active',owner='local:collection:0190',fence=7,source_sha256='s',version='v',expires=123)
    status['workbooks'].append(claim)
    status['operations']=[dict(claim,kind='publication')]
    assert P.reserve(tmp_path,jobs,status,0,'hhu-server')
    status['operations'][0]['fence']=6
    with pytest.raises(ValueError,match='authenticated'):P.reserve(tmp_path,jobs,status,1,'hhu-server')


def test_held_but_live_workers_still_occupy_host_capacity(tmp_path,monkeypatch):
    jobs,status=queue(tmp_path);monkeypatch.setattr(P,'resources',healthy)
    monkeypatch.setattr(P,'native_workers',lambda host:{'0190','0010'})
    assert P.reserve(tmp_path,jobs,status,0,'hhu-server') is None
    assert not (tmp_path/'dispatches').exists()


def test_native_publication_does_not_occupy_second_author_slot(tmp_path,monkeypatch):
    jobs,status=queue(tmp_path);monkeypatch.setattr(P,'resources',healthy)
    status['workbooks'].append(dict(workbook='0125',state='active',owner='hhu-server:slot-1:source'))
    claim=dict(workbook='0303',state='active',owner='hhu-server:publication:0303:001',fence=7,source_sha256='s',version='v',expires=123)
    status['workbooks'].append(claim);status['operations']=[dict(claim,kind='publication')]
    assert P.reserve(tmp_path,jobs,status,0,'hhu-server')
    assert P.reserve(tmp_path,jobs,status,1,'hhu-server') is None


@pytest.mark.parametrize('fault',['fence','owner','source_sha256','version','expires','workbook','missing','duplicate','wrong_owner_workbook'])
@pytest.mark.parametrize('module',['pool_v3','pool_v4'])
def test_native_publication_exclusion_requires_exact_operation(module,fault):
    import importlib
    pool=importlib.import_module('scripts.fleet.'+module)
    claim=dict(workbook='0303',state='active',owner='hhu-server-5:publication:0303:001',fence=7,source_sha256='s',version='v',expires=123)
    op=dict(claim,kind='publication');status=dict(workbooks=[claim],operations=[op])
    assert pool.author_claims(status)==[]
    if fault=='missing':status['operations']=[]
    elif fault=='duplicate':status['operations'].append(dict(op))
    elif fault=='wrong_owner_workbook':claim['owner']=op['owner']='hhu-server-5:publication:0190:001'
    else:op[fault]='changed'
    with pytest.raises(ValueError):pool.author_claims(status)
