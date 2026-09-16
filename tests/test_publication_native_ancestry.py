"""Native filesystem/process checks after the separate full ancestry validator."""
from pathlib import Path
import json,time
import pytest
from scripts.fleet import publication_launch as L


@pytest.mark.parametrize('defect',[None,'oldest-log','oldest-owning-intent','oldest-process','intermediate-process','wrong-ready-owner'])
def test_two_native_journals_preserve_original_ready_owner(tmp_path,monkeypatch,defect):
    def put(path,v):
        path=tmp_path/path;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(v));return L.binding(path)
    ready=put('ready.json',dict(status='PUBLICATION_READY',publication_performed=False))
    publisher=[put(f'publisher{i}.py',{}) for i in range(3)]
    plans=[put(f'plan{i}.json',dict(workbook='0162',source_sha256='source',code_root='/code',ready=ready,journal=str(tmp_path/f'journal{i}'))) for i in range(3)]
    profiles=[dict(plan=plans[i],expected_owner=('author'if i==0 else f'publisher{i-1}'),owner=f'publisher{i}',publisher=publisher[i]) for i in range(3)]
    for i in(1,2):profiles[i]['preowning_recovery']=dict(path=f'/edge{i}',sha256=str(i))
    observed=[]
    for i in(0,1):
        directory=tmp_path/f'journal{i}';directory.mkdir()
        put(f'journal{i}/intent.json',dict(plan=plans[i],driver=publisher[i],operation_id=f'op{i}',owner=f'publisher{i}'))
        for name in('acquired.json','controller.log','reconciliation.json'):put(f'journal{i}/{name}',{})
        observed.append(dict(files=[L.binding(x)for x in directory.iterdir()]))
    def validated_edge(p,plan,read,bind):
        index=int(p['owner'][-1]);assert index in(1,2)
        return profiles[index-1],observed[index-1],dict(operation_id=f'op{index-1}'),{}
    monkeypatch.setattr(L,'preowning',validated_edge)
    # Ancestry validation itself is exercised with authentic two-edge journals
    # in test_publication_preowning_ancestry; this fixture tests native rechecks.
    job=put('job.json',dict(workbook='0162',version='code',source=dict(sha256='source'),inputs=[],run=str(tmp_path/'author'),result=ready['path']))
    intent=put('worker-intent.json',dict(job=job,owner='publisher1'if defect=='wrong-ready-owner'else'author'))
    outcome=put('outcome.json',dict(returncode=0,failure_type=None,result=ready,intent=intent))
    process=put('process.json',dict(pid=999999999,start_ticks='1'))
    tracker=put('tracker.json',dict(leases=dict(task_agent=dict(expires_at=time.time_ns()+9000*10**9))))
    proof=put('proof.json',dict(tracker=tracker,paths=dict(journal=str(tmp_path/'journal2'),output=str(tmp_path/'output'),publication_root=str(tmp_path/'publication'))))
    p=dict(profiles[2],proof=proof,implementation=put('manifest.json',dict(publisher=publisher[2],dependencies={})),worker_outcome=outcome,process=process)
    if defect=='oldest-log':(tmp_path/'journal0/controller.log').write_text('changed')
    elif defect=='oldest-owning-intent':(tmp_path/'journal0/owning-publication').mkdir()
    fake=[]
    if defect in('oldest-process','intermediate-process'):
        proc=tmp_path/'proc/999999998';proc.mkdir(parents=True)
        (proc/'stat').write_text('999999998 (worker) '+' '.join(['S']+['0']*18+['123']))
        target=str(tmp_path/'journal0')if defect=='oldest-process'else publisher[1]['path']
        (proc/'cmdline').write_bytes(('python\0'+target+'\0').encode());fake=[proc]
    original=Path.iterdir
    monkeypatch.setattr(Path,'iterdir',lambda p:iter(fake)if str(p)=='/proc'else original(p))
    if defect:
        with pytest.raises(ValueError):L.native(dict(profile=p))
    else:
        assert L.native(dict(profile=p))['status']=='PUBLICATION_VALIDATED_NOT_STARTED'
        assert p['expected_owner']=='publisher1'  # Current fenced owner stays distinct.
