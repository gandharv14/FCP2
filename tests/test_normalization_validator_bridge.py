"""Hermetic isolation boundary tests; real producer validation has its own suite."""
from dataclasses import dataclass,asdict
from pathlib import Path
import hashlib,json,subprocess,sys
import pytest
from scripts.batch import harbor_normalization_validator_bridge as B
from scripts.batch.harbor_admission import record

@dataclass
class Policy:
    model:str='fixture'

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    root=tmp_path/'active';batch=root/'scripts/batch';batch.mkdir(parents=True)
    for p in [root/'scripts/__init__.py',batch/'__init__.py']:p.write_text('')
    (batch/'harbor_normalization_partitioned.py').write_text('''from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass
import json,hashlib
@dataclass
class Policy:
    model:str='fixture'
def selection(prepared):
    m=json.loads(Path(prepared['path']).read_text())
    return SimpleNamespace(policy=Policy(),analysis=m['legacy']['analysis.json'])
def validate(binding,**kw):
    raw=Path(binding['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==binding['sha256']
    value=json.loads(raw)
    assert kw['source']==value['source'] and kw['baseline']==value['source'] and kw['draft']==value['source']
    assert kw['policy']==Policy() and kw['bindings']=={'source_file':value['source']}
    return {'review':value['review'],'semantic_attempt':1,'checked_files':{binding['path']:binding['sha256']}}
''')
    (batch/'harbor_normalization_producer.py').write_text('''import json
from pathlib import Path
def analyse(s):return {},json.loads(Path(s.analysis['path']).read_text())
''')
    def save(name,v):
        p=tmp_path/name;p.write_text(json.dumps(v));return record(p)
    source=save('source.json',{'source':'fixture'})
    rows=[{'reference':'Sheet!A1','clean':True,'cells':[['Sheet','A1',5]],'duplicate_refs':['Sheet!A1']}]
    analysis=save('analysis.json',rows)
    files=[dict(relative=str(p.relative_to(root)),sha256=record(p)['sha256']) for p in root.rglob('*') if p.is_file()]
    snapshot=save('normalization-snapshot.json',{'snapshot':{'root':str(root),'files':files}})
    prepared=save('prepared.json',dict(code={str(batch/'harbor_normalization_partitioned.py'):record(batch/'harbor_normalization_partitioned.py')['sha256']},legacy={'analysis.json':analysis}))
    binding=save('review.json',{'source':source,'review':{'cells':{'Sheet!A1':{'eligible':True}}}})
    monkeypatch.setattr(B,'APPROVED_SNAPSHOT_SHA256',snapshot['sha256'])
    child=tmp_path/'bridge.py';child.write_text(Path(B.__file__).read_text().replace('9876445cfce18daceb5cfffd0736a4c6a15d5117b5f99a1d844cbcb8e7dea79b',snapshot['sha256']))
    monkeypatch.setattr(B,'__file__',str(child))
    # Exercise the real fresh interpreter and hash-bound request, while leaving
    # process-group guardian mechanics to their independent regression suite.
    def guarded(command,*,stdout,**kwargs):
        return subprocess.run(command,stdout=stdout,stderr=stdout,cwd=kwargs['cwd'],env=kwargs['env'],timeout=20).returncode
    from scripts.batch import hardened_runner
    monkeypatch.setattr(hardened_runner,'_run_in_process_group',guarded)
    args=dict(binding=binding,prepared=prepared,source=source,baseline=source,draft=source,rows=rows,policy=Policy(),bindings={'source_file':source})
    return args,root,snapshot,analysis


def test_explicit_active_snapshot_validates_in_isolated_process(isolated):
    args,root,snapshot,_=isolated
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result=B.validate(**args)
    assert result['review']['cells']['Sheet!A1']['eligible'] is True
    assert result['checked_files'][snapshot['path']]==snapshot['sha256']
    assert all(p.read_bytes()==raw for p,raw in before.items())

@pytest.mark.parametrize('target',['source','snapshot','code','rows','untrusted_root','extra_module','symlink'])
def test_tampered_or_untrusted_context_fails_closed(isolated,target):
    args,root,snapshot,analysis=isolated
    if target=='source':args['source']={**args['source'],'sha256':'0'*64}
    elif target=='snapshot':Path(snapshot['path']).write_text('{}')
    elif target=='code':(root/'scripts/batch/harbor_normalization_producer.py').write_text('raise AssertionError("untrusted")')
    elif target=='rows':args['rows'][0]['cells'][0][-1]=999
    elif target=='extra_module':(root/'sitecustomize.py').write_text('raise RuntimeError("must never execute")')
    elif target=='symlink':(root/'injected.py').symlink_to(root/'scripts/__init__.py')
    else:
        value=json.loads(Path(args['prepared']['path']).read_text());path=next(iter(value['code']));value['code']={path.replace('/active/','/untrusted/'):value['code'][path]}
        Path(args['prepared']['path']).write_text(json.dumps(value));args['prepared']=record(args['prepared']['path'])
    with pytest.raises((ValueError,FileNotFoundError)):B.validate(**args)

@pytest.mark.parametrize('fault',[None,'prepared','inventory','authority','extra','root'])
def test_declared_existing_snapshot_remains_exact_in_cold_validator(isolated,monkeypatch,tmp_path,fault):
    args,root,legacy,_=isolated
    original=json.loads(Path(legacy['path']).read_bytes())['snapshot']
    snapshot=tmp_path/'existing-inventory.json';snapshot.write_text(json.dumps(original))
    authority=tmp_path/'authority.md';authority.write_text('Explicit prospective execution authorization; retain all original attempts.')
    p=dict(schema_version='harbor-normalization-snapshot-selection/v1',prepared=args['prepared'],snapshot=record(snapshot),authorization=record(authority))
    if fault=='prepared':p['prepared']={**args['prepared'],'sha256':'0'*64}
    elif fault=='inventory':snapshot.write_text('{}')
    elif fault=='authority':authority.write_text('changed')
    elif fault=='extra':p['skip_validation']=True
    elif fault=='root':
        value={**original,'root':str(root/'other')};snapshot.write_text(json.dumps(value));p['snapshot']=record(snapshot)
    profile=tmp_path/'selected-profile.json';profile.write_text(json.dumps(p));bound=record(profile)
    monkeypatch.setenv('HARBOR_FLEET_NORMALIZATION_PROFILE',json.dumps(bound))
    # No fabricated legacy approval exists at the old inferred path.
    Path(legacy['path']).unlink()
    if fault:
        with pytest.raises((ValueError,FileNotFoundError)):B.validate(**args)
    else:
        result=B.validate(**args)
        assert result['review']['cells']['Sheet!A1']['eligible'] is True
        for row in (bound,p['snapshot'],p['authorization']):assert result['checked_files'][row['path']]==row['sha256']
        assert not Path(legacy['path']).exists()


def test_failed_cold_validator_preserves_exact_request_and_response(isolated,monkeypatch,tmp_path):
    args,_,_,_=isolated
    from scripts.batch import hardened_runner
    def failed(command,*,stdout,**kwargs):
        stdout.write(b'Traceback: exact selected normalization defect\n');return 7
    monkeypatch.setattr(hardened_runner,'_run_in_process_group',failed)
    monkeypatch.setattr(B.tempfile,'tempdir',str(tmp_path))
    with pytest.raises(ValueError,match='exact selected normalization defect'):B.validate(**args)
    kept=list(tmp_path.glob('normalization-validation-failed-*/failure.json'));assert len(kept)==1
    v=json.loads(kept[0].read_bytes());assert v['returncode']==7 and v['automatic_retry'] is False
    for row in (v['request'],v['response']):assert record(row['path'])==row
    assert json.loads(Path(v['request']['path']).read_bytes())['prepared']==args['prepared']
    assert (kept[0].stat().st_mode & 0o777)==0o600

@pytest.mark.parametrize('fault',[None,'loader_hash','plan_prepared','foreign_facade','no_witness'])
def test_completed_partition_reader_is_explicit_and_exact_in_child(isolated,monkeypatch,tmp_path,fault):
    args,root,old,_=isolated
    snapshot=tmp_path/'existing-code.json';snapshot.write_text(json.dumps(json.loads(Path(old['path']).read_bytes())['snapshot']))
    authority=tmp_path/'authority.md';authority.write_text('Explicit retained partition history selection.')
    loader=tmp_path/'completed-reader.py';loader.write_text('''from types import SimpleNamespace
from pathlib import Path
import json
def load(bound):
 from scripts.batch import harbor_normalization_partitioned as P
 p=json.loads(Path(bound['path']).read_bytes())
 facade=SimpleNamespace(__name__=P.__name__,undispatched_reader_witness={'retained':True},selection=P.selection,validate=P.validate)
 return p,None,None,None,facade,None
'''.replace("__name__=P.__name__","__name__='foreign'" if fault=='foreign_facade' else "__name__=P.__name__").replace("{'retained':True}","None" if fault=='no_witness' else "{'retained':True}"))
    plan=tmp_path/'reader-plan.json';plan.write_text(json.dumps(dict(prepared={**args['prepared'],'sha256':'0'*64} if fault=='plan_prepared' else args['prepared'],version=root.name)))
    profile=tmp_path/'profile.json';profile.write_text(json.dumps(dict(schema_version='harbor-normalization-snapshot-selection/v1',prepared=args['prepared'],snapshot=record(snapshot),authorization=record(authority),retained_partition={'loader':record(loader),'plan':record(plan)})))
    monkeypatch.setenv('HARBOR_FLEET_NORMALIZATION_PROFILE',json.dumps(record(profile)))
    monkeypatch.setattr(B.tempfile,'tempdir',str(tmp_path))
    if fault=='loader_hash':loader.write_text(loader.read_text()+'\n')
    if fault:
        with pytest.raises(ValueError):B.validate(**args)
    else:
        result=B.validate(**args)
        assert result['review']['cells']['Sheet!A1']['eligible'] is True
        assert result['checked_files'][str(loader)]==record(loader)['sha256']
        assert result['checked_files'][str(plan)]==record(plan)['sha256']
