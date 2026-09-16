import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import harbor_publish as P
from scripts.fleet import undispatched_publication_child as A


def saved(path, value):
    path.write_text(json.dumps(value)); return A.binding(path)


@pytest.fixture
def selected(tmp_path, monkeypatch):
    plan = saved(tmp_path/'reader.json', {'fixture': True})
    wrapper = tmp_path/'child.py'
    wrapper.write_text('''import sys,json\nfrom pathlib import Path\nsys.path.insert(0,REPO)\nfrom scripts import harbor_publish as P\nfrom scripts.fleet import undispatched_publication_child as A\na=sys.argv;arg=lambda k:a[a.index(k)+1]\nplan=dict(path=arg('--plan'),sha256=arg('--plan-sha256'));intent=dict(path=arg('--intent'),sha256=arg('--intent-sha256'))\nA.validate(P,reader_plan=plan,child_wrapper=A.binding(__file__),reader_evidence=[],intent=intent,sidecar=arg('--adapter'))\nA.retain(Path(intent['path']).parent/'result.json',dict(status='FIXTURE_ONLY',models=0,publication=0))\n'''.replace('REPO', repr(str(Path(P.__file__).parents[1]))))
    @contextmanager
    def locks(_): yield ()
    raw = b'fixture tracker'
    before = dict(revision=1, leases={'task_agent': {}}, workbook_id='0173')
    request = SimpleNamespace(lease=SimpleNamespace(revision=1,sha256=P.schema.sha(raw),owner='fixture',token='fixture'),output=tmp_path/'output')
    monkeypatch.setattr(P,'locks',locks)
    monkeypatch.setattr(P,'_paths',lambda *a:(tmp_path/'tracker',None,None))
    monkeypatch.setattr(P,'_checkpoint',lambda *a:None)
    monkeypatch.setattr(P,'_tracker',lambda *a:(raw,before))
    monkeypatch.setattr(P,'_live',lambda *a:None)
    monkeypatch.setattr(P,'encode',lambda _: {'fixture': True})
    monkeypatch.setattr(P,'_tree',lambda _:None)
    run=A.select(P,reader_plan=plan,child_wrapper=A.binding(wrapper),reader_evidence=[])
    return run,request,tmp_path,plan,wrapper


def test_real_original_publication_run_child_dependency_and_no_replay(selected):
    run,request,root,plan,wrapper=selected
    original_run=P.run;before=Path(P.__file__).read_bytes()
    outcome=A.checked(run(request,root/'journal'))
    assert outcome['status']=='PROMOTED'  # Only the explicitly labeled fixture boundary.
    assert A.checked(outcome['result'])==dict(status='FIXTURE_ONLY',models=0,publication=0)
    resource=A.checked(outcome['resource']);intent=A.checked(resource['intent'])
    assert intent['code']==P._codes()
    sidecar=A.checked(A.binding(Path(resource['log']['path']).parent/'declared-publication-reader.json'))
    assert sidecar['original_command'][2]==str(Path(P.__file__).resolve())
    assert sidecar['selected_command'][3]==str(wrapper)
    assert sidecar['timeout_seconds']==3300 and sidecar['models_allowed']is False
    assert P.run is original_run and Path(P.__file__).read_bytes()==before
    with pytest.raises(ValueError,match='explicit exact resume'):run(request,root/'journal')


def test_lost_child_response_retains_intent_and_requires_reconciliation(selected,monkeypatch):
    _,request,root,plan,wrapper=selected;calls=[]
    def lost(command,**options):
        calls.append(command);assert Path(command[-1]).is_file();raise TimeoutError('fixture lost response')
    monkeypatch.setattr(P.guard,'_run_in_process_group',lost)
    run=A.select(P,reader_plan=plan,child_wrapper=A.binding(wrapper),reader_evidence=[])
    result=A.checked(run(request,root/'lost'))
    assert result['status']=='RECONCILIATION_REQUIRED'
    assert A.checked(result['resource'])['failure']['type']=='TimeoutError'
    with pytest.raises(ValueError):run(request,root/'lost')
    assert len(calls)==1 and (root/'lost/intent.json').is_file()


@pytest.mark.parametrize('change',['wrapper','plan','guard','code','argv','foreign-invocation'])
def test_mutation_blocks_before_spawn(selected,change,monkeypatch):
    run,_,root,plan,wrapper=selected
    journal=root/'manual';invocation=journal/'invocation-fixture';invocation.mkdir(parents=True)
    value=dict(schema_version=P.SCHEMA,request={},queued_tracker={},initial_output_sha256=None,code=P._codes())
    if change=='code':value['code']={}
    bound=saved(journal/'intent.json',value)
    cmd=[sys.executable,'-B',str(Path(P.__file__).resolve()),'_child',bound['path'],bound['sha256']]
    if change=='wrapper':wrapper.write_text('changed')
    if change=='plan':Path(plan['path']).write_text('{}')
    if change=='argv':cmd[3]='another-child'
    if change=='foreign-invocation':invocation=root/'invocation-foreign';invocation.mkdir()
    with (invocation/'publication.log').open('xb')as log:
        with pytest.raises(ValueError):run.declared_runner(cmd,stdout=log,timeout_seconds=3301 if change=='guard' else 3300,
          memory_limit_bytes=8*1024**3,grace_seconds=3,cwd=P.ROOT)
    assert not (invocation/'declared-publication-reader.json').exists()
