from pathlib import Path
import tempfile,json,ast
import pytest
from scripts.fleet.offline_bindings import readers,binding
from scripts.fleet import launch_job_v2 as V2,launch_job_v3 as V3

def make(tmp):
 p=tmp/'copy.json';p.write_text('{"path":"/home/original","lease":"retained"}\n');copy=binding(p);native=dict(path='/home/gmahajan_labelbox_com/synthetic-data-fleet/plan.json',sha256=copy['sha256']);manifest=tmp/'manifest.json';manifest.write_text(json.dumps(dict(schema_version='fleet-offline-bindings/v1',copies=[dict(native=native,copy=copy)])));return binding(manifest),native,copy

def test_exact_bytes_only_no_path_rewrite(tmp_path):
 selected,native,copy=make(tmp_path);check,bind=readers(selected);assert bind(native['path'])==native;assert check(native)['path']=='/home/original'
 Path(copy['path']).write_text('{}')
 with pytest.raises(ValueError):check(native)

@pytest.mark.parametrize('change',['sha','native_path','copy_sha','duplicate','extra'])
def test_invalid_projection_rejected(tmp_path,change):
 selected,native,copy=make(tmp_path);p=Path(selected['path']);v=json.loads(p.read_bytes());r=v['copies'][0]
 if change=='sha':r['native']['sha256']='0'*64
 if change=='native_path':r['native']['path']='/Users/not-native'
 if change=='copy_sha':r['copy']['sha256']='0'*64
 if change=='duplicate':v['copies'].append(r)
 if change=='extra':r['ignore']=True
 p.write_text(json.dumps(v))
 with pytest.raises(ValueError):readers(binding(p))

def test_wrong_requested_digest_rejected(tmp_path):
 selected,native,copy=make(tmp_path);check,bind=readers(selected)
 with pytest.raises(ValueError):check(dict(native,sha256='f'*64))

def test_remote_custody_and_mutation_functions_unchanged():
 # central_gate has a separately tested explicit quarantine admission reader.
 for name in ['remote_preflight','remote_spawn','launch']:
  assert ast.dump(ast.parse(V2.function_source(name)))==ast.dump(ast.parse(V3.function_source(name)))


def test_ssh_diagnostics_preserve_transport_invocation():
 # Error reporting is separately exercised by test_fleet_launch_diagnostics.
 def invocation(module):
  tree=ast.parse(module.function_source("ssh"))
  return [ast.dump(n) for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="run"]
 assert invocation(V2)==invocation(V3)
