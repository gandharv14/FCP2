import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import pre_dispatch_continue as P
from scripts.fleet import completed_pre_dispatch_publication as M
from scripts.fleet import fresh_publication as F


def fixture(tmp_path):
    def keep(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return P.binding(path)
    ops=tmp_path/'ops';ops.mkdir();jobrow=keep(tmp_path/'job.json',{});driver=keep(tmp_path/'driver.json',{})
    descriptor=keep(tmp_path/'descriptor.json',{});cp=keep(tmp_path/'old-result.json',dict(status='LEASE_RENEWAL_REQUIRED',descriptor=descriptor))
    old=keep(tmp_path/'old-outcome.json',dict(result=cp));job=dict(operations=str(ops),outcome=old,driver=driver,maximum_steps=100)
    jobrow=keep(tmp_path/'job.json',job);keep(ops/'job.json',job);ready=dict(job=jobrow)
    for i,status in enumerate(('RETRY_READY','PUBLICATION_READY')):
        d=ops/('%03d'%i);d.mkdir()
        intent=dict(job=jobrow,checkpoint=cp,seconds=900,driver=driver)if i==0 else dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=cp))
        ir=keep(d/'intent.json',intent);gr=keep(d/'resource.json',dict(intent=ir));cp=keep(d/'result.json',dict(status=status,descriptor=descriptor));outcome=keep(d/'outcome.json',dict(status='COMPLETED_OPERATION',resource=gr,result=cp))
    ready.update(outcome=outcome,checkpoint=cp);keep(ops/'hold.json',dict(outcome=outcome))
    pub=SimpleNamespace(require=P.require,binding=P.binding,checked=P.read)
    calls=[]
    def successful(row,env):
        calls.append(row);v=P.read(row);cp=v['result'];return cp,P.read(cp)
    return pub,dict(operations=str(ops)),SimpleNamespace(successful=successful),job,ready,keep,calls


def test_every_original_guard_and_final_ready_remain_authority(tmp_path):
    pub,p,D,job,ready,keep,calls=fixture(tmp_path)
    M.completed_tail(pub,p,D,(),job,ready)
    assert len(calls)==3 and calls[-1]==ready['outcome']


@pytest.mark.parametrize('fault',['skip','wrong_driver','different_first_job','nonrenewal_first','extra_child','wrong_ready'])
def test_completed_tail_rejects_unrelated_or_missing_successors(tmp_path,fault):
    pub,p,D,job,ready,keep,_=fixture(tmp_path);ops=Path(p['operations'])
    if fault=='extra_child':(ops/'002').mkdir()
    elif fault=='wrong_ready':ready['outcome']=job['outcome']
    else:
        path=ops/('001'if fault=='skip'else'000')/'intent.json';v=P.read(P.binding(path))
        if fault=='skip':v['arguments']['checkpoint']=job['outcome']
        elif fault=='wrong_driver':v['driver']=job['outcome']
        elif fault=='different_first_job':v['job']=job['outcome']
        else:v=dict(action='step',arguments={})
        ir=keep(path,v);gr=keep(path.parent/'resource.json',dict(intent=ir));ov=P.read(P.binding(path.parent/'outcome.json'));ov['resource']=gr;out=keep(path.parent/'outcome.json',ov)
        if path.parent.name=='001':ready['outcome']=out;keep(ops/'hold.json',dict(outcome=out))
    with pytest.raises((ValueError,FileNotFoundError)):M.completed_tail(pub,p,D,(),job,ready)


def test_dispatch_refuses_mixed_continuation_authorities():
    pub=SimpleNamespace(require=P.require)
    with pytest.raises(ValueError,match='mix'):
        F.dispatcher(pub,None,dict(completed_pre_dispatch={},semantic_continuation={}),{}, {})


def test_dependencies_exclude_mutated_pre_renewal_tracker():
    rows={k:dict(path='/'+k,sha256=k)for k in ('helper','original_worker_job','original_job','outer','failed','custody','tracker','dispatcher','completed')}
    p=dict(rows,renewal_setup={k:rows[k]for k in ('dispatcher','completed')});edge=dict(reader={'path':'/reader','sha256':'r'},plan={'path':'/plan','sha256':'p'})
    deps=M.dependencies(SimpleNamespace(checked=lambda _:p),dict(completed_pre_dispatch=edge))
    assert rows['tracker']not in deps and rows['completed']in deps and rows['failed']in deps


def test_preparer_keeps_actual_root_launch_when_selecting_completed_history(monkeypatch):
    from scripts.fleet import prepare_fresh_publication as Q
    freshrow={'path':'fresh','sha256':'f'};history={'path':'continued','sha256':'c'}
    rootrow={'path':'root','sha256':'r'};jobrow={'path':'job','sha256':'j'};ir={'path':'intent','sha256':'i'}
    source={'path':'source','sha256':'s'};helper={'path':'helper','sha256':'h'}
    root={'job':jobrow,'host':'hhu-server-7'}
    job={'workbook':'0295','cwd':'/code/v','version':'v','source':source,'driver':helper,'inputs':[history]}
    original=dict(job,inputs=[freshrow]);fresh={'schema_version':'harbor-fleet-fresh-plan/v1','workbook':'0295','code_root':'/code/v'}
    continued={'schema_version':'fleet-pre-dispatch-continuation/v1','original_worker_job':'original','renewal_setup':{},'helper':helper,'workbook':'0295'}
    process={'root_intent':ir,'job':jobrow};launch={'intent':ir,'remote':process}
    values={'launch':launch,'intent':{'plan':rootrow},'root':root,'job':job,'fresh':fresh,'continued':continued,'original':original}
    monkeypatch.setattr(Q,'checked',lambda row:values[row['path']if isinstance(row,dict)else row])
    selected=Q.selection({'path':'launch'},freshrow,completed_pre_dispatch=history)
    assert selected['launch'] is root and selected['job'] is job


def test_preparer_preserves_original_legacy_source_authority():
    from scripts.fleet.prepare_fresh_publication import source_history
    row={'path':'legacy','sha256':'h'}
    assert source_history({}, {'legacy_research_history':row})=={'legacy_research_history':row}
    assert source_history({'history':row},{})=={'history':row}
    with pytest.raises(ValueError,match='mixed'):source_history({'history':row},{'legacy_research_history':row})


def test_completed_probe_keeps_native_source_history_dispatch_order():
    from scripts.fleet.prepare_fresh_publication import source_history_probe
    template="before\nfrom scripts.fleet import history as H\nold_survey()\ncp,v=D.successful(x,y)\nafter"
    text=source_history_probe(template)
    assert 'old_survey'not in text and text.endswith('after')
    assert text.index('history_survey(')<text.index('dispatcher(')<text.index('pub.publication_only()')<text.index('D.setup(')<text.index('D.successful(')
