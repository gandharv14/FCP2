from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.batch import harbor_disclosure_rejection_cache as C

def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return C.binding(path)

def test_presence_witness_includes_absent_preparations_and_restores_functions(tmp_path):
    target=tmp_path/'later-attempt';old=Path.exists
    with C.presence_witness()as witness:
        assert not target.exists()and not target.is_dir()and not target.is_file()and not target.is_symlink()
        rows=witness()
    assert Path.exists is old
    assert {r['predicate']for r in rows}=={'exists','is_dir','is_file','is_symlink'}
    assert all(r['value']is False for r in rows)
    with C.presence_witness():
        assert not target.exists();target.mkdir()
        with pytest.raises(ValueError,match='topology changed'):target.exists()

@pytest.fixture
def proof(tmp_path):
    input_file=put(tmp_path/'original-review.json',{'blocked_indices':[4]})
    journal=tmp_path/'attempts';journal.mkdir();(journal/'attempt-01').mkdir()
    absent=tmp_path/'later-model-call'
    code=put(tmp_path/'original-reader.json',{});source=put(tmp_path/'source.json',{})
    run=put(tmp_path/'run.json',dict(workbook='0111',source=source))
    rejection=put(tmp_path/'rejection.json',dict(implementation=code,round=3,run_plan=run))
    custody=put(tmp_path/'custody.json',dict(run_plan=run))
    scope=dict(workbook_id='0111',original_sha256=source['sha256'],lane='harbor-disclosure-writer',budget_id='disclosure-review')
    profile=put(tmp_path/'profile.json',dict(schema_version=C.SCHEMA,helper=C.binding(C.__file__),rejection=rejection,
        custody=dict(helper=code,profile=custody),parents=None,reader=code,functions=dict(closed_rejection='sha',_cold_validate='sha'),
        scope=scope,ordinal=3,python='/original/python',guard=code))
    value=dict(schema_version=C.SCHEMA,plan=profile,rejection=rejection,scope=scope,ordinal=3,
        completed_rejection=True,semantic_approval=False,current_custody=False,models_dispatched=0,task_mutations=0,
        inputs=[input_file],input_directories=[dict(path=str(journal),members=['attempt-01'])],
        path_predicates=[dict(path=str(absent),predicate='exists',value=False)])
    home=tmp_path/'native';inspection=put(home/'inspection.json',value);log=put(home/'probe.log',value)
    resource=put(home/'resource.json',dict(inspection=inspection,returncode=0,failure_type=None,models=0,claims=0,
        memory_limit_bytes=8*1024**3,timeout_seconds=1800,elapsed_seconds=12,peak_rss_bytes=1000,
        command=['/original/python','-I','-B',C.__file__,'probe','--plan',profile['path'],'--sha256',profile['sha256']],log=log))
    return profile,dict(resource=resource,inspection=inspection),rejection,scope,3

def test_exact_completed_rejection_never_grants_approval(proof):
    assert C.cached(*proof)is True
    value=C.read(proof[1]['inspection']);assert value['semantic_approval']is value['current_custody']is False

@pytest.mark.parametrize('fault',['bytes','directory','absent','scope','ordinal','plan','guard','approval','models','log'])
def test_cached_rejection_is_invalidated_by_any_changed_evidence(proof,fault):
    profile,receipt,rejection,scope,ordinal=proof;value=C.read(receipt['inspection'])
    if fault=='bytes':Path(value['inputs'][0]['path']).write_text('{}')
    elif fault=='directory':(Path(value['input_directories'][0]['path'])/'attempt-02').mkdir()
    elif fault=='absent':Path(value['path_predicates'][0]['path']).touch()
    elif fault=='scope':scope=dict(scope,workbook_id='foreign')
    elif fault=='ordinal':ordinal=4
    elif fault=='plan':Path(profile['path']).write_text('{}')
    elif fault=='guard':
        guard=C.read(receipt['resource']);guard['returncode']=1;receipt['resource']=put(Path(receipt['resource']['path']),guard)
    elif fault=='log':Path(C.read(receipt['resource'])['log']['path']).write_text('{}')
    else:
        value['semantic_approval'if fault=='approval'else'models_dispatched']=True
        receipt['inspection']=put(Path(receipt['inspection']['path']),value)
        guard=C.read(receipt['resource']);guard['inspection']=receipt['inspection'];guard['log']=put(Path(guard['log']['path']),value)
        receipt['resource']=put(Path(receipt['resource']['path']),guard)
    with pytest.raises(ValueError):C.cached(profile,receipt,rejection,scope,ordinal)

def test_registry_matches_exact_prior_record_and_restores_original(proof,tmp_path):
    profile,receipt,rejection,scope,ordinal=proof;calls=[]
    def original(*args):calls.append(args);return 'original HTTP terminal proof'
    Q=SimpleNamespace(closed_rejection=original)
    registry=put(tmp_path/'registry.json',dict(schema_version=C.REGISTRY,helper=C.binding(C.__file__),entries=[dict(rejection=rejection,profile=profile,receipt=receipt)]))
    with C.installed(Q,registry):
        assert Q.closed_rejection(rejection,scope,ordinal)is True
        foreign=dict(rejection,sha256='different')
        assert Q.closed_rejection(foreign,scope,ordinal)=='original HTTP terminal proof'
        with pytest.raises(ValueError):Q.closed_rejection(rejection,scope,ordinal+1)
    assert Q.closed_rejection is original and calls==[(foreign,scope,ordinal)]

@pytest.mark.parametrize('wrapped',[False,True])
def test_historical_producer_check_reads_archive_and_keeps_other_validation(tmp_path,wrapped):
    import importlib.util
    code=tmp_path/'rounds.py';code.write_text('''from pathlib import Path
def record(path):
 import hashlib
 return dict(path=str(path),sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
class W:
 @staticmethod
 def task(worker,selection,tracker):
  assert Path(tracker['path']).read_bytes()==b'original tracker'
  return type('Task',(),{'run':worker.run})()
def validate(inputs,worker,selection):
 assert inputs['valid_source'], 'substantive source check'
 assert Path(inputs['producer_run']) == W.task(worker,selection,record(worker.context.tracker_root/(inputs['workbook']+'.json'))).run
 return 'validated'
''')
    spec=importlib.util.spec_from_file_location('test_original_rounds',code);Q=importlib.util.module_from_spec(spec);spec.loader.exec_module(Q)
    archive=tmp_path/'archive.json';archive.write_bytes(b'original tracker')
    live=tmp_path/'live';live.mkdir();(live/'0111.json').write_bytes(b'new owner must stay live')
    worker=SimpleNamespace(context=SimpleNamespace(tracker_root=live),run=tmp_path/'producer')
    run=dict(workbook='0111',modules=dict(rounds=C.binding(code)))
    if wrapped:
        selected=tmp_path/'selected.py';selected.write_text(code.read_text())
        spec=importlib.util.spec_from_file_location('test_selected_rounds',selected);S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)
        scope=dict(Q.__dict__);exec(compile(selected.read_text(),str(selected),'exec'),scope)
        wrapper=tmp_path/'wrapper.py';wrapper.write_text('''def wrap(actual,original,selected,value):
 def checked(*args,**kwargs):
  assert original.record(original.__file__)==value['original']
  assert original.record(selected.__file__)==value['selected']
  return actual(*args,**kwargs)
 return checked
''')
        spec=importlib.util.spec_from_file_location('test_wrapper',wrapper);M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
        value=dict(original=C.binding(code),selected=C.binding(selected));profile=tmp_path/'profile.json';profile.write_text(json.dumps(value))
        run['round_reader']=dict(runtime=C.binding(wrapper),profile=C.binding(profile))
        Q.validate=M.wrap(scope['validate'],Q,S,value)
    inputs=dict(workbook='0111',producer_run=str(worker.run),valid_source=True);original=Q.validate
    with C.archived_round_validation(Q,run,worker,C.binding(archive)):
        assert Q.validate(inputs,worker,None)=='validated'
        with pytest.raises(AssertionError,match='substantive source'):Q.validate(dict(inputs,valid_source=False),worker,None)
        with pytest.raises(ValueError):Q.validate(dict(inputs,workbook='foreign'),worker,None)
    assert Q.validate is original and (live/'0111.json').read_bytes()==b'new owner must stay live'
    with pytest.raises(AssertionError):Q.validate(inputs,worker,None)

@pytest.mark.parametrize('expression,expected',[('600',600),('COLD_VALIDATION_SECONDS',900),('None',None)])
def test_original_cold_timeout_literal_or_named_policy(tmp_path,expression,expected):
    import importlib.util
    source=tmp_path/'reader.py';source.write_text('import subprocess\nCOLD_VALIDATION_SECONDS=900\ndef closed_rejection():\n return subprocess.run([],timeout='+expression+')\n')
    spec=importlib.util.spec_from_file_location('cold_timeout',source);reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    if expected is None:
        with pytest.raises(ValueError):C.original_cold_seconds(reader)
    else:assert C.original_cold_seconds(reader)==expected

def test_original_cold_deadline_is_enforced_and_restored():
    import signal,time
    previous=signal.getsignal(signal.SIGALRM)
    with pytest.raises(TimeoutError):
        with C.original_cold_deadline(.01):time.sleep(.1)
    assert signal.getsignal(signal.SIGALRM)==previous and signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)

@pytest.mark.parametrize('change',[None,'authority','function'])
def test_unmodified_interrupted_reader_keeps_immutable_producer_check(tmp_path,monkeypatch,change):
    import importlib.util,sys
    source=tmp_path/'rounds.py';source.write_text('''def validate(inputs,worker):
 from scripts.batch.harbor_disclosure_command_repair import producer_run
 assert inputs['valid'], 'substantive check'
 assert inputs['producer']==producer_run(worker), 'producer changed'
 return 'original result'
''')
    spec=importlib.util.spec_from_file_location('original_immutable_producer',source);Q=importlib.util.module_from_spec(spec);spec.loader.exec_module(Q)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_disclosure_command_repair',SimpleNamespace(producer_run=lambda worker:worker.run))
    runtime=tmp_path/'reserved.py';runtime.write_text('SCHEMA="fleet-disclosure-reserved-round/v1"\n')
    prior=put(tmp_path/'prior.json',dict(modules=dict(rounds=C.binding(source))))
    stopped=put(tmp_path/'stopped.json',dict(schema_version='fleet-disclosure-interrupted-reservation/v1',plan=prior))
    run=dict(workbook='0315',modules=dict(rounds=C.binding(source)),reserved_round=stopped,round_reader=dict(runtime=C.binding(runtime),profile=None))
    archive=tmp_path/'archive';archive.write_text('original tracker')
    worker=SimpleNamespace(run='immutable producer');original=Q.validate
    if change=='authority':
        value=C.read(prior);value['modules']['rounds']['sha256']='foreign';prior=put(tmp_path/'foreign.json',value)
        run['reserved_round']=put(tmp_path/'foreign-stopped.json',dict(schema_version='fleet-disclosure-interrupted-reservation/v1',plan=prior))
    if change=='function':Q.validate=lambda *a:True
    if change:
        with pytest.raises(ValueError):
            with C.archived_round_validation(Q,run,worker,C.binding(archive)):pass
    else:
        with C.archived_round_validation(Q,run,worker,C.binding(archive)):
            assert Q.validate is original
            assert Q.validate(dict(valid=True,producer=worker.run),worker)=='original result'
            with pytest.raises(AssertionError,match='substantive'):Q.validate(dict(valid=False,producer=worker.run),worker)
            with pytest.raises(AssertionError,match='producer'):Q.validate(dict(valid=True,producer='foreign'),worker)
        assert Q.validate is original
