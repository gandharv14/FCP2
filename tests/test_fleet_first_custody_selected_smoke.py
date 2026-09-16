import ast,copy,json,subprocess,sys
from pathlib import Path
import pytest
from scripts.fleet import first_custody_selected_smoke as X
from scripts.fleet.migration import binding,checked,retain
R=Path(__file__).resolve().parents[1]
def test_authority_and_operation_functions_unchanged():
 old=ast.parse((R/'diagnostics/vm-fleet-20260912/control/first-custody-003/first_custody_concurrent.py').read_text())
 current=ast.parse(Path(X.__file__).read_text())
 names=('original_authority','native_bindings','normalization_authority','dialogue_authority','authority','operation_admission','central_gate','local_custody','remote_preflight','launch')
 for name in names:
  def node(t):return next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name==name)
  assert ast.dump(node(old))==ast.dump(node(current)),name
@pytest.mark.parametrize('w',['0073','0173','0303','0077'])
def test_actual_source_authority_preserved(w):
 p=checked(binding(R/'diagnostics/vm-fleet-20260912/control/first-custody-002'/f'{w}-launch-template.json'));p['schema_version']=X.SCHEMA;p['smoke_workbook']='0029'
 assert X.authority(p,checked(p['job']))['workbook']==w

def gate_fixture(tmp_path,monkeypatch):
 verification=dict(schema_version='independent-published-task-copy-check/v1',workbook='0029',passed=True,byte_identical_to_immutable_generation=True,current_qualification_verified=True,model_calls=0,runtime_operations=0,task_manifest={'sha256':'m'})
 vb=retain(tmp_path/'v.json',verification);files={'generation-manifest.json':{'sha256':'m'}}
 collection=dict(schema_version='harbor-fleet-collection/v1',status='COLLECTED',workbook='0029',local_byte_integrity_verified=True,independent_remote_verification=vb,output=str(tmp_path/'output'),files=files)
 p=dict(smoke_workbook='0029',smoke_verification=vb,smoke_collection=retain(tmp_path/'c.json',collection))
 monkeypatch.setattr(X,'operation_admission',lambda *a:None);monkeypatch.setattr(X.F,'files',lambda p:files)
 return p,dict(workbooks=[dict(workbook='0029',state='complete')]),verification,collection

def test_selected_real_smoke(tmp_path,monkeypatch):
 p,s,v,c=gate_fixture(tmp_path,monkeypatch);X.smoke_gate(p,s,{'workbook':'0073'})
@pytest.mark.parametrize('target,key,value',[('plan','smoke_workbook','0270'),('verification','workbook','0270'),('verification','passed',False),('verification','current_qualification_verified',False),('verification','model_calls',1),('collection','workbook','0270'),('collection','status','STAGED'),('central','state','hold')])
def test_smoke_evidence_cannot_be_substituted(tmp_path,monkeypatch,target,key,value):
 p,s,v,c=gate_fixture(tmp_path,monkeypatch)
 if target=='plan':p[key]=value
 elif target=='central':s['workbooks'][0][key]=value
 else:
  item=v if target=='verification' else c;item[key]=value;p['smoke_'+('verification' if target=='verification' else 'collection')]=retain(tmp_path/'changed.json',item)
 with pytest.raises(ValueError):X.smoke_gate(p,s,{'workbook':'0073'})

def test_frozen_copy_survives_foreign_pythonpath_and_rejects_extra_import(tmp_path):
 from scripts.fleet import freeze_first_custody as B
 out=tmp_path/'frozen';result=B.freeze(out,Path(X.__file__));manifest=checked(result['manifest'])
 assert next(x for x in manifest['originals'] if Path(x['original']['path']).name=='prepare_queue.py')['recovered_from']['sha256']=='e1b0c3acc90b3776ad1eea97b1b6fa5906fb9c8c2d32ccbdaedcd197980968db'
 import os
 poisoned=tmp_path/'poison/scripts/fleet';poisoned.mkdir(parents=True);(poisoned.parent/'__init__.py').write_text("raise RuntimeError('live package leaked')")
 env=dict(os.environ,PYTHONPATH=str(tmp_path/'poison'),PYTHONDONTWRITEBYTECODE='1')
 r=subprocess.run([sys.executable,'-I','-B',result['wrapper']['path'],'--help'],capture_output=True,env=env)
 assert r.returncode==0,r.stderr
 extra=Path(manifest['root'])/'scripts/fleet/extra.py';extra.write_text('')
 r=subprocess.run([sys.executable,'-I','-B',result['wrapper']['path'],'--help'],capture_output=True,env=env)
 assert r.returncode!=0 and b'inventory changed' in r.stderr

def test_unreviewed_custody_edit_cannot_freeze(tmp_path):
 from scripts.fleet import freeze_first_custody as B
 p=tmp_path/'selected.py';p.write_text(Path(X.__file__).read_text().replace("w['state']=='hold' and w['fence']==0", "w['state']=='hold' and w['fence']>=0"))
 with pytest.raises(ValueError,match='unreviewed'):B.freeze(tmp_path/'frozen',p)
 assert not (tmp_path/'frozen').exists()

def test_any_changed_dependency_can_use_exact_retained_bytes(tmp_path,monkeypatch):
 from scripts.fleet import freeze_first_custody as B
 live=tmp_path/'live/publish.py';live.parent.mkdir();live.write_text('old publication')
 row=B.binding(live);retained=tmp_path/'retained';retained.mkdir();(retained/live.name).write_bytes(live.read_bytes())
 live.write_text('new prospective publication');monkeypatch.setattr(B,'RECOVERY_ROOTS',(retained,))
 assert B.recover_dependency(row)==retained/live.name
 live.unlink();assert B.recover_dependency(row)==retained/live.name

@pytest.mark.parametrize('fault',['bytes','symlink'])
def test_dependency_recovery_cannot_invent_or_follow_evidence(tmp_path,monkeypatch,fault):
 from scripts.fleet import freeze_first_custody as B
 live=tmp_path/'live/publish.py';live.parent.mkdir();live.write_text('original');row=B.binding(live)
 retained=tmp_path/'retained';retained.mkdir();saved=retained/live.name
 if fault=='bytes':saved.write_text('wrong')
 else:saved.symlink_to(live)
 monkeypatch.setattr(B,'RECOVERY_ROOTS',(retained,));live.write_text('changed')
 with pytest.raises(ValueError,match='unavailable'):B.recover_dependency(row)
