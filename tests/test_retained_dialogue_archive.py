from pathlib import Path
import json,subprocess,sys
from scripts.fleet import retained_dialogue_archive as A
import pytest


def guarded(tmp_path,body):
 source=tmp_path/'source';source.write_text('retained bytes');lock=tmp_path/'scope.lock';lock.touch()
 code="import importlib.util,json,os,sys\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location('archive',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nroot=Path(sys.argv[2]); verify=m.install_guard()\n"+body
 return subprocess.run([sys.executable,'-I','-B','-c',code,A.__file__,str(tmp_path)],capture_output=True,text=True)


def test_guard_reads_exact_inputs_and_existing_lock_without_write_permission(tmp_path):
 r=guarded(tmp_path,"assert (root/'source').read_text()=='retained bytes'\nfd=os.open(root/'scope.lock',os.O_RDWR|os.O_NOFOLLOW);os.close(fd)\nprint(json.dumps(verify()))\n")
 assert r.returncode==0,r.stderr
 rows=json.loads(r.stdout);assert A.binding(tmp_path/'source')in rows and A.binding(tmp_path/'scope.lock')in rows
 assert (tmp_path/'scope.lock').read_bytes()==b''


def test_guard_rejects_new_files_mutation_network_and_processes(tmp_path):
 r=guarded(tmp_path,"""import socket,subprocess
attempts=[lambda:(root/'new').write_text('bad'),lambda:(root/'source').write_text('bad'),lambda:os.mkdir(root/'newdir'),lambda:os.remove(root/'source'),lambda:subprocess.run(['true']),lambda:socket.socket().connect(('127.0.0.1',1))]
for operation in attempts:
 try:operation()
 except ValueError:pass
 else:raise AssertionError('forbidden effect allowed')
assert (root/'source').read_text()=='retained bytes'
(root).mkdir(exist_ok=True)
print('PASS')
""")
 assert r.returncode==0,r.stderr
 assert r.stdout.strip()=='PASS'and not(tmp_path/'new').exists()and not(tmp_path/'newdir').exists()


def test_guard_rejects_writable_non_lock_and_credentials(tmp_path):
 (tmp_path/'model.env').write_text('not a real credential')
 r=guarded(tmp_path,"""for operation in [lambda:os.open(root/'source',os.O_RDWR),lambda:(root/'model.env').read_text()]:
 try:operation()
 except ValueError:pass
 else:raise AssertionError('forbidden access allowed')
print('PASS')
""")
 assert r.returncode==0,r.stderr
 assert r.stdout.strip()=='PASS'


def test_guard_records_directory_membership(tmp_path):
 r=guarded(tmp_path,"list(root.iterdir())\nprint(json.dumps(verify.directories()))\n")
 assert r.returncode==0,r.stderr
 assert dict(path=str(tmp_path),members=['scope.lock','source'])in json.loads(r.stdout)


def cached_fixture(tmp_path,monkeypatch):
 stage=tmp_path/'live';stage.mkdir();copy=tmp_path/'archive';copy.mkdir()
 (stage/'notes').write_text('new task');(copy/'notes').write_text('historical task')
 tracker=tmp_path/'tracker.json';tracker.write_text('new tracker')
 oldtracker=tmp_path/'old-tracker.json';oldtracker.write_text('original tracker')
 other=tmp_path/'request.json';other.write_text('sealed request')
 failed=tmp_path/'failed';failed.mkdir();intent=failed/'intent.json';intent.write_text('{}')
 rejection=tmp_path/'rejection.json';rejection.write_text(json.dumps(dict(original_intent=A.binding(intent))))
 selection=tmp_path/'selection.json';selection.write_text(json.dumps(dict(rejection=A.binding(rejection))))
 bound=dict(path=str(tmp_path/'plan.json'),sha256='test')
 p=dict(selection=A.binding(selection),uid=123,gid=456)
 a=dict(stage=dict(path=str(stage),archive=str(copy)),tracker=dict(original=dict(path=str(tracker)),archive=dict(path=str(oldtracker))))
 monkeypatch.setattr(A,'load',lambda b:(p,a)if b==bound else pytest.fail('wrong archive'))
 value=dict(schema_version=A.SCHEMA,plan=bound,uid=123,gid=456,mount_namespace='child',parent_namespace='parent',models_dispatched=0,task_mutations=0,publication=False,payload=dict(selection=p['selection'],original=True),read_only_binds=[[str(copy),str(stage)],[str(oldtracker),str(tracker)]],inputs=[dict(A.binding(copy/'notes'),path=str(stage/'notes')),dict(A.binding(oldtracker),path=str(tracker)),A.binding(other)],input_directories=[dict(path=str(stage),members=['notes'])])
 return bound,value,copy,other,failed


def test_cache_checks_archived_inputs_while_live_task_changes(tmp_path,monkeypatch):
 bound,value,copy,other,failed=cached_fixture(tmp_path,monkeypatch)
 assert A.validate_cached(bound,value)==value['payload']
 (tmp_path/'live'/'notes').write_text('later adopted task')
 assert A.validate_cached(bound,value)==value['payload']
 (copy/'notes').write_text('corrupted history')
 with pytest.raises(ValueError,match='cached historical bytes changed'):A.validate_cached(bound,value)


@pytest.mark.parametrize('mutation',['request','membership','checkpoint'])
def test_cache_rejects_changed_external_input_directory_or_original_checkpoint(tmp_path,monkeypatch,mutation):
 bound,value,copy,other,failed=cached_fixture(tmp_path,monkeypatch)
 if mutation=='request':other.write_text('different request')
 elif mutation=='membership':(copy/'new').touch()
 else:(failed/'complete.json').write_text('{}')
 with pytest.raises(ValueError):A.validate_cached(bound,value)


def test_context_adapter_reuses_only_exact_validated_cold_request(tmp_path,monkeypatch):
 import importlib.util
 module_path=tmp_path/'context.py'
 module_path.write_text('''import subprocess,json
def load(p,caller):
 script='m.cold_payload(json.loads(sys.argv[2]))'
 return subprocess.run([p['python'],'-I','-B','-c',script,__file__,json.dumps(p['selection'])],capture_output=True,text=True,timeout=1500,env={'HARBOR_FLEET_CONTINUATION_READ_ONLY':'1'})
''')
 spec=importlib.util.spec_from_file_location('test_cold_context',module_path);original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
 bound=dict(path=str(tmp_path/'plan.json'),sha256='test');p=dict(context_helper=A.binding(module_path),python=sys.executable,selection=dict(path='selected',sha256='exact'))
 monkeypatch.setattr(A,'load',lambda b:(p,{}))
 runs=[];validations=[]
 def run(b):runs.append(b);return {'payload':'sealed'}
 def validate(b,v):validations.append((b,v));return dict(selection=p['selection'])
 monkeypatch.setattr(A,'run',run);monkeypatch.setattr(A,'validate_cached',validate)
 selected=A.context_consumer(original,bound,{})
 for _ in range(2):assert json.loads(selected.load(p,None).stdout)==dict(selection=p['selection'])
 assert len(runs)==1 and len(validations)==2
 assert original.subprocess is subprocess and selected.__file__==original.__file__
 with pytest.raises(ValueError,match='different consumer'):selected.load(dict(p,selection={'path':'foreign'}),None)
 assert len(runs)==1 and len(validations)==2


@pytest.mark.parametrize('fault',[None,'failed','argv','output','limit'])
def test_persisted_cache_requires_actual_guarded_output(tmp_path,monkeypatch,fault):
 def put(name,value):
  path=tmp_path/name;path.write_text(json.dumps(value));return A.binding(path)
 bound=dict(path=str(tmp_path/'plan.json'),sha256='exact');p=dict(python=sys.executable,helper=dict(path=str(tmp_path/'helper.py')))
 monkeypatch.setattr(A,'load',lambda b:(p,{}))
 verified=[];monkeypatch.setattr(A,'validate_cached',lambda b,v:verified.append((b,v)))
 value={'retained':'proof'};inspection=put('inspection.json',value);log=put('probe.log',value if fault!='output'else {'changed':True})
 r=dict(inspection=inspection,returncode=1 if fault=='failed'else 0,failure_type=None,models=0,claims=0,memory_limit_bytes=8*1024**3,timeout_seconds=900 if fault=='limit'else 1800,elapsed_seconds=123,peak_rss_bytes=1234,command=[p['python'],'-I','-B',p['helper']['path'],'inspect','--plan',bound['path'],'--sha256',bound['sha256']],log=log)
 if fault=='argv':r['command'][4]='run'
 receipt=dict(resource=put('resource.json',r),inspection=inspection)
 if fault:
  with pytest.raises(ValueError):A.guarded_cache(bound,receipt)
  assert not verified
 else:
  assert A.guarded_cache(bound,receipt)==value
  assert verified==[(bound,value)]
