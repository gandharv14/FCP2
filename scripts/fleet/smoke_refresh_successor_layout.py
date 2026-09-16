"""Closed 0270 successor custody: immutable authoring code and Linux runtime differ."""
from pathlib import Path
from scripts.fleet.migration import binding,checked

SCHEMA='harbor-0270-refresh-successor-layout/v1'
PRIOR_VERSION='73b5681a505d6d3ec0865109e1bcfa6b63ac48f9c0f68a47f94ed28df31f3071'
OLD_RELEASE='5d3b09ac15928bcb163405f8fd950a84d7ff3d5f56b5c01eb2a47e8f810e9057'
REMOTE=Path('/home/gmahajan_labelbox_com/synthetic-data-fleet')

def require(value,message):
    if not value:raise ValueError(message)

def validate(layout_binding,*,expected_sha256,remote=False):
    require(layout_binding['sha256']==expected_sha256,'unreviewed successor layout')
    v=checked(layout_binding)
    require(set(v)=={'schema_version','workbook','source','prior_outcome','root_plan','worker_job','authoring_snapshot',
        'authoring_version','profile','native_probe','original_config','runtime_release','runtime_manifest',
        'runtime_manifest_copy','runtime_python','runtime_worker','runtime_probe'},'closed successor layout required')
    require(v['schema_version']==SCHEMA and v['workbook']=='0270','only exact smoke successor supported')
    require(v['runtime_release']==OLD_RELEASE and v['authoring_version']!=OLD_RELEASE,'distinct successor authoring identity required')
    runtime=REMOTE/'releases'/OLD_RELEASE
    require(v['runtime_python']==str(runtime/'venv/bin/python') and v['runtime_worker']['path']==str(runtime/'code/scripts/fleet/worker.py'),'reviewed native runtime differs')
    require(v['runtime_manifest']['path']==str(runtime/'code/FLEET_CODE_MANIFEST.json')
        and v['runtime_manifest']['sha256']==v['runtime_manifest_copy']['sha256'],'native manifest projection changed')
    manifest=checked(v['runtime_manifest'] if remote else v['runtime_manifest_copy'])
    require(manifest['release']==OLD_RELEASE and manifest['files']['scripts/fleet/worker.py']['sha256']==v['runtime_worker']['sha256'],'native runtime worker differs')
    snapshot=checked(v['authoring_snapshot']);root=Path(snapshot['root'])
    require(root.name==v['authoring_version'] and root.is_absolute() and '..' not in root.parts,'exact successor snapshot identity required')
    seen=set()
    for row in snapshot['files']:
        name=Path(row['relative']);require(not name.is_absolute() and '..' not in name.parts and str(name) not in seen,'invalid snapshot inventory')
        seen.add(str(name));require(binding(root/name)['sha256']==row['sha256'],'successor code bytes changed')
    require('xl_mcp_oracle.py' in seen and 'scripts/batch/harbor_environment_worker.py' in seen,'incomplete owning snapshot')
    profile=checked(v['profile']);probe=checked(v['native_probe']);runtime_probe=checked(v['runtime_probe'])
    require(probe.get('status')=='PASS' and probe.get('models_dispatched')==0
        and probe.get('result',{}).get('plan')==v['root_plan'],'exact no-model native probe required')
    require(runtime_probe['schema_version']=='harbor-fleet-successor-runtime-probe/v1'
        and runtime_probe['runtime_release']==OLD_RELEASE and runtime_probe['executable']==v['runtime_python']
        and runtime_probe['runtime_manifest']==v['runtime_manifest'] and runtime_probe['runtime_worker']==v['runtime_worker']
        and runtime_probe['authoring_snapshot']==v['authoring_snapshot'] and runtime_probe['models_dispatched']==0
        and runtime_probe['python_version']=='3.12.3','native runtime proof differs')
    if remote:
        import sys
        require(sys.executable==v['runtime_python'] and binding(Path(sys.executable).resolve())==runtime_probe['native_executable'],'actual native interpreter changed')
    require(binding(v['source']['path'])==v['source'],'source bytes changed')
    old=checked(v['original_config']);prior=checked(v['prior_outcome']);plan=checked(v['root_plan']);job=checked(v['worker_job'])
    require(profile['schema_version']=='0270-oracle-gate-successor/v1' and profile['models_replayed'] is False
        and profile['semantic_budget_reset'] is False and profile['original_snapshot']==old['code'],'preserved original lineage required')
    require(plan['profile']==v['profile'] and plan['snapshot']==v['authoring_snapshot'],'root plan lineage differs')
    require(plan['schema_version']=='0270-oracle-refresh-continuation/v1' and 'native_lease' in plan
        and 'lease_renewal' not in plan, 'exact retained-refresh continuation required')
    parent=checked(profile['parent_successor'])
    require(Path(parent['root']).name==PRIOR_VERSION and v['authoring_version']!=PRIOR_VERSION, 'parent successor identity differs')
    require(old['workbook']==plan['workbook']==job['workbook']=='0270' and job['source']==v['source'],'successor workbook/source differs')
    require(prior['status']=='RECONCILIATION_REQUIRED' and prior['automatic_retry'] is False,'prior hold must remain retained')
    require(job['version']==v['authoring_version'] and job['cwd']==str(runtime/'code')
        and job['command'][0]==v['runtime_python'] and job['inputs'][0]==v['root_plan'],'job must keep separate authoring/runtime bindings')
    require(v['profile'] in job['inputs'] and v['authoring_snapshot'] in job['inputs'],'worker must bind reviewed lineage and full snapshot')
    return v


def configuration(layout,new_binding):
    old=checked(layout['original_config']);new=checked(new_binding)
    require(new['code']==layout['authoring_snapshot'],'publication config does not select successor snapshot')
    require({k:v for k,v in new.items() if k!='code'}=={k:v for k,v in old.items() if k!='code'},'continuation changed original config semantics')
    return new
