import ast
from pathlib import Path
from scripts.fleet import build_retained_warning_publication as B

def test_generic_publisher_preserves_owning_control_pack_and_collection(tmp_path):
 original=Path('diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py').resolve()
 B.build(tmp_path/'publisher',original)
 old={n.name:ast.dump(n,include_attributes=False)for n in ast.parse(original.read_text()).body if isinstance(n,ast.FunctionDef)}
 new={n.name:ast.dump(n,include_attributes=False)for n in ast.parse((tmp_path/'publisher/publish.py').read_text()).body if isinstance(n,ast.FunctionDef)}
 changed={name for name in old if old[name]!=new[name]}
 assert changed=={'history_binding','publication_environment','remote_child'}
 assert old['controlled']==new['controlled'] and old['collect']==new['collect'] and old['pack']==new['pack']

def test_builder_refuses_unreviewed_publisher(tmp_path):
 import pytest
 p=tmp_path/'wrong.py';p.write_text('# foreign\n')
 with pytest.raises(AssertionError):B.build(tmp_path/'out',p)
 assert not (tmp_path/'out').exists()

def test_publication_uses_exact_existing_guarded_child_adapter(tmp_path):
 import json,hashlib
 B.build(tmp_path/'publisher',Path('diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py').resolve())
 manifest=json.loads((tmp_path/'publisher/manifest.json').read_text())
 child=manifest['dependencies']['child']
 assert child['sha256']==hashlib.sha256(Path('scripts/fleet/undispatched_publication_child.py').read_bytes()).hexdigest()
 assert manifest['owning_code_changed'] is False and manifest['model_dispatch_allowed'] is False


def test_relocated_status_has_closed_live_capacity_dependency(tmp_path):
 import json,hashlib,importlib.util,pytest
 bound=B.build(tmp_path/'publisher',Path('diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py').resolve())
 manifest=json.loads(Path(bound['path']).read_text())
 relocated=B.closure_successor(tmp_path/'relocated',bound)
 selected=json.loads(Path(relocated['path']).read_text())
 assert not (tmp_path/'relocated/capacity_policy.py').exists()
 spec=importlib.util.spec_from_file_location('relocated_status',selected['dependencies']['status']['path']);s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
 assert s.CAPACITY_POLICY==manifest['dependencies']['capacity']
 plan=dict(workbook='WB',code_root='/code/version');row=dict(path='/plan',sha256='b'*64)
 auth=dict(owner='pub-owner',fence=2)
 identity=hashlib.sha256(json.dumps(dict(workbook='WB',request_sha256=row['sha256']),sort_keys=True,separators=(',',':')).encode()).hexdigest()
 wb=dict(workbook='WB',state='active',owner='pub-owner',fence=2,expires=200,version='version',source_sha256='source')
 status=dict(at=100,model_limit=1,publication_limit=1,workbooks=[wb],operations=[dict(kind='publication',state='active',workbook='WB',owner='pub-owner',fence=2,expires=200,identity=identity)])
 def require(ok,message):
  if not ok:raise ValueError(message)
 assert s.validate_status(status,auth,row,plan,'source',now=100,require=require)==wb
 oldfuncs={n.name:ast.dump(n,include_attributes=False)for n in ast.parse(Path(manifest['publisher']['path']).read_text()).body if isinstance(n,ast.FunctionDef)}
 newfuncs={n.name:ast.dump(n,include_attributes=False)for n in ast.parse(Path(selected['publisher']['path']).read_text()).body if isinstance(n,ast.FunctionDef)}
 assert newfuncs==oldfuncs
 Path(s.CAPACITY_POLICY['path']).write_text('# changed')
 with pytest.raises(ValueError,match='policy changed'):s.validate_status(status,auth,row,plan,'source',now=100,require=require)
 with pytest.raises(AssertionError):B.closure_successor(tmp_path/'foreign',relocated)
 assert not(tmp_path/'foreign').exists()


def test_capacity_binding_rejects_wrong_hash_or_declared_policy(tmp_path):
 import hashlib,pytest
 p=tmp_path/'policy.py';p.write_text('x=1\n');row=dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 with pytest.raises(AssertionError):B.bind_capacity("CAPACITY_POLICY=None\nCAPACITY_POLICY_SHA='wrong'\n",row)
 with pytest.raises(AssertionError):B.bind_capacity('CAPACITY_POLICY=None\nCAPACITY_POLICY_SHA='+repr(row['sha256'])+'\n',dict(row,sha256='f'*64))


def test_generated_environment_uses_reviewed_live_warning_history(tmp_path,monkeypatch):
 import os,sys,json
 original=Path('diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py').resolve()
 B.build(tmp_path/'publisher',original)
 generated=ast.parse((tmp_path/'publisher/publish.py').read_text())
 selected=next(n for n in generated.body if isinstance(n,ast.FunctionDef) and n.name=='publication_environment')
 current=next(n for n in ast.parse(Path('scripts/fleet/publish.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='publication_environment')
 assert ast.dump(selected,include_attributes=False)==ast.dump(current,include_attributes=False)
 records={}
 def put(name,value):
  row={'path':'/'+name,'sha256':name};records[row['path']]=value;return row
 source={'path':'/source','sha256':'s'}
 legacy=put('legacy',{'workbook':'WB','original':source})
 fresh=put('fresh',{'workbook':'WB','legacy_research_history':legacy})
 renewal=put('renewal',{'workbook':'WB','source':source,'fresh_plan':fresh})
 prior=put('prior',{'workbook':'WB','source_sha256':'s','live_renewal':{'plan':renewal}})
 plan={'workbook':'WB','source_sha256':'s','code_root':'/code','retained_warning':{'continuation_plan':prior}}
 def require(ok,message):
  if not ok:raise ValueError(message)
 ns=dict(os=os,sys=sys,Path=Path,require=require,checked=lambda r:records[r['path']],history_binding=lambda p:prior)
 exec(compile(ast.Module(body=[selected],type_ignores=[]),'<generated-publication-environment>','exec'),ns)
 env=ns['publication_environment'](plan,dict(workbook='WB',owner='publisher',token='fixture',fence=3),action='remote',url='http://coordinator',token_file='/token')
 assert env['HARBOR_FLEET_LEGACY_RESEARCH_PATH']==legacy['path']
 assert env['HARBOR_FLEET_LEGACY_RESEARCH_SHA256']==legacy['sha256']
 assert 'HARBOR_FLEET_HISTORY_PATH' not in env
