"""Prepare the existing held-custody controller for a stopped environment gate.

Only data, immutable reader copies and an explicit bounded lease allowance are
created here. The original native takeover owns expiry, stopped locks and CAS.
"""
from pathlib import Path
import time
from scripts.fleet.migration import binding,checked,retain

def prepare(root,workbook,custody,output):
 from scripts.fleet.coordinator import Client
 from scripts.batch import harbor_continuation_allowance as A
 root=Path(root).absolute();out=Path(output).absolute();out.mkdir();m=checked(custody);rows=m['files'];home='/home/gmahajan_labelbox_com/synthetic-data-fleet/source-pool-opaque-003/'+workbook
 def row(suffix):return next(r for r in rows if r['native']['path']==home+suffix)
 config=row('/authoring/authoring/config.json');v=checked(config['copy']);tracker=row('/authoring/authoring/trackers/workbooks/'+workbook+'.json');t=checked(tracker['copy']);assert t['workbook_id']==workbook;raw=t['bindings']['original_raw_source'];C=root/'diagnostics/vm-fleet-20260912/control';co=binding(C/'coordinator-process-v4.json');receipt=checked(co);status=Client(receipt['url'],str(Path(receipt['private_directory'])/'admin.token')).call('status');w=next(w for w in status['workbooks']if w['workbook']==workbook);assert w['state']=='hold'and w['source_sha256']==raw['sha256']and not any(o['workbook']==workbook and o['state']in('active','hold')for o in status['operations']);retain(out/'central.json',dict(at=status['at'],workbook=w))
 shared=out/'shared';shared.mkdir();bindings={}
 for key,path in [('lifecycle','scripts/batch/harbor_tracker_lifecycle.py'),('takeover','scripts/batch/harbor_lease_takeover.py'),('allowance','scripts/batch/harbor_continuation_allowance.py'),('stdlib','scripts/fleet/stdlib_imports.py')]:
  target=shared/(key+'.py');target.write_bytes((root/path).read_bytes());bindings[key]=binding(target)
 runtime=shared/'runtime.py';runtime.write_bytes((root/'diagnostics/bulk-production/0078-terminal-continuation-001/takeover-001/shared/runtime.py').read_bytes());controller=shared/'takeover_control.py';controller.write_bytes((root/'scripts/fleet/takeover_control.py').read_bytes())
 auth=binding(root/'diagnostics/bulk-production/authorization-20260913.md');assert auth['sha256']=='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0';directory=out/'allowance';directory.mkdir();limits=dict(takeovers=4,lease_duration_ns=4*3600*10**9,stage_seconds=3300,memory_bytes=8589934592,review_attempts=2,transport_retries=0)
 allowance=A.issue(directory,expected_previous=None,selected_scope=A.scope(workbook,raw['sha256']),limits=limits,authorization=auth,reason='Explicit user-authorized four-hour lease after sealed normalization HTTP400. Preserve consumed native replacements and reviews; partition review allowance is separately bound to the terminal request. No automatic transport retry.',expires_at_ns=time.time_ns()+86400*10**9,approve=lambda proposal:proposal['limits']==limits and proposal['authorization']==auth)
 selection=dict(campaign_root=v['run_root'],workbook=workbook,worker_kind='environment',tracker=tracker['native'],checkpoint=row('/authoring/authoring/dispatcher-v2/first/003/result.json')['native'],guard=row('/authoring/authoring/dispatcher-v2/first/003/resource.json')['native'],owner='harbor-orchestrator-'+workbook+'-'+m['host']+':terminal001',duration_ns=limits['lease_duration_ns'],prior_completion=None,config=config['native'],allowance=allowance)
 profile=retain(out/'profile.json',dict(schema_version='harbor-allowance-takeover-direct/v1',original_snapshot=v['code'],shared=bindings,allowance=allowance,selection=selection))
 py='/home/gmahajan_labelbox_com/synthetic-data-fleet/releases/5d3b09ac15928bcb163405f8fd950a84d7ff3d5f56b5c01eb2a47e8f810e9057/venv/bin/python';p=retain(out/'plan.json',dict(schema_version='fleet-held-native-takeover/v1',helper=binding(controller),coordinator_receipt=co,runtime=binding(runtime),profile=profile,host=m['host'],python=py,workbook=workbook,expected_owner=w['owner'],expected_fence=w['fence'],source_sha256=w['source_sha256'],version=w['version'],native_directory=str(Path(v['run_root'])/'lease-takeovers/terminal-normalization-001')))
 # A frozen standalone controller lives outside the repository package tree.
 # Preload stdlib inspection before the explicitly selected repository imports.
 bootstrap='import inspect,sys,runpy;sys.path.insert(0,'+repr(str(root))+');sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")'
 cmd=[str(root/'.venv/bin/python'),'-I','-B','-c',bootstrap,str(controller),'--plan',p['path'],'--plan-sha256',p['sha256']];retain(out/'commands.json',dict(inspect=cmd+['--journal',str(out/'preflight-001')],execute=cmd+['--journal',str(out/'execute-001'),'--execute']))
 files=[*bindings.values(),binding(runtime),binding(controller),allowance,auth,profile];q=out/'verify.py';q.write_text('from pathlib import Path\nimport hashlib,json\nrows='+repr(files)+'\nfor r in rows:assert hashlib.sha256(Path(r["path"]).read_bytes()).hexdigest()==r["sha256"]\nprint("EXACT_TAKEOVER_INPUTS_VERIFIED")\n');retain(out/'stage-files.json',[r['path']for r in files]+[str(q)]);return p
