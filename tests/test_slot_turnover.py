"""Assignment journal tests; retirement owning proof is tested separately."""
from pathlib import Path
import copy
import importlib.util
import sys
from types import SimpleNamespace as NS
import pytest
from scripts import harbor_parallel_policy as P
from scripts.corpus import object_hash

from scripts import harbor_slot_turnover as M
FILE=Path(M.__file__)

@pytest.fixture
def campaign(tmp_path,monkeypatch):
    root=tmp_path/'campaign';root.mkdir()
    for n in ('parallel-policy','parallel-workbook-locks','model-journal','model-slots','intake-v1'):(root/n).mkdir()
    (root/'actual-workbook-slot.lock').touch();(root/'model-slots/capacity').write_bytes(b'1\n')
    contract=P.put(root/'parallel-policy/selected.json',P.proposal(root));activation=P.activate(contract)
    raw=tmp_path/'raw';raw.mkdir();sources=[]
    for i in range(1,701):
        wb=f'{i:04d}';path=raw/(wb+'.xlsx');path.write_bytes(('synthetic source '+wb).encode())
        sources.append(dict(workbook_id=wb,path=str(path),sha256=P.record(path)['sha256'],size_bytes=path.stat().st_size,format='.xlsx',corpus='synthetic'))
    inv=dict(schema_version='raw-corpus/v1',sources=sources);inv['inventory_sha256']=object_hash(inv)
    inventory=P.put(tmp_path/'inventory.json',inv)
    survey=P.put(root/'intake-v1/survey.json',dict(inventory=inventory,unmapped_prior_artifacts=[]))
    ids=list(P.WORKBOOKS)+[f'{n:04d}' for n in range(250,296)]
    rows=[dict(original=sources[int(wb)-1],history=[],prior_audit_metadata=[],exclusion_evidence=[]) for wb in ids]
    candidates=P.put(root/'intake-v1/candidates.json',dict(schema_version='new50-qualified-task-candidate-proposal/v1',target_new_qualified_tasks=50,denominator_for_original_rebuild=700,survey=survey,selected_candidates=rows))
    selected=M.Selection(contract,activation,candidates,inventory)
    # The journal tests receive a selected synthetic terminal observation, not
    # an arbitrary passed flag. Production uses the separately tested validator.
    retirement=P.put(root/'retirement.json',dict(workbook='0042',terminal='synthetic owning fixture'))
    def check(s,b,assignment):
        v=P.read(b)
        P.require(b==retirement and assignment['workbook']==v['workbook'],'foreign retirement')
        return v
    monkeypatch.setattr(M,'_retirement',check)
    return selected,root,retirement


def initialize(c):return M.initialize(c[0])
def assign(c,b,**kw):return M.assign(c[0],expected=b,lane=2,workbook='0274',retirement=c[2],**kw)


def test_exact_four_lane_turnover_retains_other_lanes_and_history(campaign):
    s,root,_=campaign;old=initialize(campaign);a=M.validate(s)
    physical=[Path(r['lock']).stat().st_ino for r in a['assignments']]
    new=assign(campaign,old);b=M.validate(s,expected=new)
    assert b['sequence']==1 and b['assignments'][1]['workbook']=='0274'
    assert b['retired']==[a['assignments'][1]]
    assert [Path(r['lock']).stat().st_ino for r in b['assignments']]==physical
    assert P.validate(s.contract,s.activation,'0035')==Path(a['assignments'][0]['lock'])
    assert (root/'model-slots/capacity').read_bytes()==b'1\n'
    assert not (root/'workbooks').exists()


@pytest.mark.parametrize('field',['stale','foreign','active','unknown','existing_work','existing_tracker','old_workbook'])
def test_assignment_rejects_ambiguous_or_reused_identity(campaign,field):
    s,root,r=campaign;b=initialize(campaign);kw=dict(expected=b,lane=2,workbook='0274',retirement=r)
    if field=='stale':kw['expected']=dict(b,sha256='0'*64)
    elif field=='foreign':kw['retirement']=P.put(root/'foreign.json',dict(workbook='0035'))
    elif field=='active':kw['workbook']='0035'
    elif field=='unknown':kw['workbook']='0700'
    elif field=='existing_work':(root/'work/0274').mkdir(parents=True)
    elif field=='existing_tracker':(root/'workbooks').mkdir();(root/'workbooks/0274.json').write_text('{}')
    elif field=='old_workbook':kw['workbook']='0042'
    with pytest.raises((ValueError,KeyError)):M.assign(s,**kw)
    assert M.validate(s)['pointer']==b


@pytest.mark.parametrize('point',['prepared','pointer'])
def test_explicit_interrupted_recovery_has_single_event(campaign,point):
    s,root,_=campaign;b=initialize(campaign)
    def crash(p):
        if p==point:raise RuntimeError('crash')
    with pytest.raises(RuntimeError):assign(campaign,b,_fault=crash)
    tx=M.record(M._pending(s)[0])
    result=M.recover(s,tx);state=M.validate(s,expected=result)
    assert state['sequence']==1 and state['assignments'][1]['workbook']=='0274'
    assert len(list((M.home(s)/'events').iterdir()))==2
    with pytest.raises(ValueError):M.recover(s,tx)


def test_pending_transaction_never_blind_retries(campaign):
    s,root,_=campaign;b=initialize(campaign)
    with pytest.raises(RuntimeError):assign(campaign,b,_fault=lambda p:(_ for _ in ()).throw(RuntimeError()) if p=='prepared' else None)
    with pytest.raises(ValueError,match='pending'):assign(campaign,b)


@pytest.mark.parametrize('change',['other_lane','history','new_budget','pointer','extra','before','five_lanes'])
def test_corrupt_pending_transition_rejects_before_pointer_mutation(campaign,change):
    s,root,_=campaign;b=initialize(campaign)
    with pytest.raises(RuntimeError):assign(campaign,b,_fault=lambda p:(_ for _ in ()).throw(RuntimeError()) if p=='prepared' else None)
    txpath=M._pending(s)[0];tx=P.read(P.record(txpath));state=P.read(tx['after'])
    if change=='other_lane':state['assignments'][0]['workbook']='0275'
    elif change=='history':state['retired']=[]
    elif change=='new_budget':state['assignments'][1]=copy.deepcopy(M.validate(s)['assignments'][0])
    elif change=='pointer':state['sequence']=4
    elif change=='extra':state['allowed']=True
    elif change=='before':tx['before']['sha256']='0'*64
    elif change=='five_lanes':state['assignments'].append(copy.deepcopy(state['assignments'][0]))
    Path(tx['after']['path']).write_bytes(P.canonical(state));tx['after']=P.record(tx['after']['path'])
    txpath.write_bytes(P.canonical(tx))
    with pytest.raises((ValueError,KeyError)):M.recover(s,P.record(txpath))
    assert P.record(root/'parallel-policy/turnover/current.json')==b


def test_original_byte_change_and_fifty_manifest_changes_reject(campaign):
    s,root,_=campaign;b=initialize(campaign)
    row=M.config(s)[2]['0274'];Path(row['original']['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):assign(campaign,b)
    Path(s.candidates['path']).write_bytes(b'{}')
    with pytest.raises(ValueError):M.validate(s)


def test_existing_model_plan_without_tracker_still_blocks_reset(campaign):
    s,root,_=campaign;b=initialize(campaign);p=root/'model-journal/orphan/attempt-01';p.mkdir(parents=True)
    P.put(p/'plan.json',dict(scope=dict(workbook_id='0274',original_sha256=M.identity(s,'0274')['original']['sha256'])))
    with pytest.raises(ValueError,match='model attempts'):assign(campaign,b)
    assert M.validate(s)['pointer']==b


def test_untouched_selected_assignment_survives_other_lane_turnover(campaign):
    s,root,_=campaign;b=initialize(campaign)
    retained=M.assignment(s,expected=b,workbook='0035');old=M.assignment(s,expected=b,workbook='0042')
    new=assign(campaign,b)
    assert M.validate_assignment(s,retained)['workbook']=='0035'
    with M.lane_lock(s,selected=retained) as fd:
        assert __import__('os').fstat(fd).st_ino==Path(retained['row']['lock']).stat().st_ino
    with pytest.raises(ValueError,match='retired'):M.validate_assignment(s,old)
    fresh=M.assignment(s,expected=new,workbook='0274')
    with M.lane_lock(s,selected=fresh):pass


def test_wrong_selected_assignment_never_obtains_lock(campaign):
    s,root,_=campaign;b=initialize(campaign);a=M.assignment(s,expected=b,workbook='0035')
    a['row']['workbook']='0274'
    with pytest.raises(ValueError):
        with M.lane_lock(s,selected=a):pytest.fail('foreign assignment acquired')


def test_preserved_candidate_history_is_not_reset(campaign):
    s,root,_=campaign;b=initialize(campaign);state=M.validate(s)
    assert state['assignments'][1]['candidate']==M.config(s)[2]['0042']
    # The module never fabricates consumed counts, leases, targets or curation.
    assert set(state)=={'schema_version','configuration','sequence','previous','assignments','retired','operation','pointer'}


def lock_holder(path,ready,release):
    import fcntl
    with open(path,'r+b') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);ready.set();release.wait(15)


@pytest.mark.parametrize('lock_name',['publication_queue_lock','lane'])
def test_real_existing_process_prevents_turnover(campaign,lock_name):
    import multiprocessing
    s,root,_=campaign;b=initialize(campaign);policy=M.config(s)[0]
    path=policy['lanes'][1]['lock'] if lock_name=='lane' else policy[lock_name]
    ctx=multiprocessing.get_context('spawn');ready=ctx.Event();release=ctx.Event()
    child=ctx.Process(target=lock_holder,args=(path,ready,release));child.start()
    try:
        assert ready.wait(10)
        with pytest.raises(BlockingIOError):assign(campaign,b)
        assert M.validate(s)['pointer']==b
    finally:
        release.set();child.join(15)
        if child.is_alive():child.kill();child.join()
    assert child.exitcode==0


def competing_assignment(s,expected,retirement,start,result):
    # Only the owning semantic fixture is substituted. Actual journal, locks,
    # candidate hashes, pointer CAS and mutation code are the production path.
    def retire(selection,binding,row):
        P.require(binding==retirement and P.read(binding)['workbook']==row['workbook'],'fixture retirement differs')
    M._retirement=retire
    start.wait(15)
    try:
        value=M.assign(s,expected=expected,lane=2,workbook='0274',retirement=retirement)
        result.put(('committed',value))
    except (ValueError,BlockingIOError) as error:
        result.put(('rejected',type(error).__name__))


def test_two_real_processes_cannot_both_assign_slot(campaign):
    import multiprocessing
    s,root,r=campaign;b=initialize(campaign);ctx=multiprocessing.get_context('spawn')
    start=ctx.Event();result=ctx.Queue();children=[ctx.Process(target=competing_assignment,args=(s,b,r,start,result)) for _ in range(2)]
    try:
        for child in children:child.start()
        start.set();out=[result.get(timeout=15) for _ in children]
        assert sorted(x[0] for x in out)==['committed','rejected']
    finally:
        for child in children:
            child.join(15)
            if child.is_alive():child.kill();child.join()
    assert all(c.exitcode==0 for c in children)
    assert M.validate(s)['sequence']==1


def test_new_controls_keep_exact_physical_lane_and_resource_policy(campaign):
    s,root,_=campaign;b=assign(campaign,initialize(campaign));selected=M.assignment(s,expected=b,workbook='0274')
    controls=M.controls(s,selected=selected)
    assert controls.slot_lock==root/'parallel-workbook-locks/0042.lock'
    assert controls.env_file is None
    assert not (root/'parallel-workbook-locks/0274.lock').exists()


@pytest.mark.parametrize('corruption',['transaction','completion'])
def test_post_pointer_recovery_revalidates_entire_committed_predecessor(campaign,corruption):
    s,root,_=campaign;before=initialize(campaign)
    initial=P.read(before)['head'];initial_dir=Path(initial['path']).parent
    def crash(point):
        if point=='pointer':raise RuntimeError('after pointer, before completion')
    with pytest.raises(RuntimeError):assign(campaign,before,_fault=crash)
    pending=M.record(M._pending(s)[0]);pointer=M.record(M.home(s)/'current.json')
    target=initial_dir/('transaction.json' if corruption=='transaction' else 'complete.json')
    value=P.read(P.record(target))
    if corruption=='transaction':value['before']={'path':str(root/'foreign'),'sha256':'0'*64}
    else:value['pointer_sha256']='0'*64
    target.write_bytes(P.canonical(value))
    with pytest.raises(ValueError):M.recover(s,pending)
    assert not (Path(pending['path']).parent/'complete.json').exists()
    assert M.record(M.home(s)/'current.json')==pointer
