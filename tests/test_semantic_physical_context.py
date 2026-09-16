import copy,json,sys
from pathlib import Path
import pytest
from scripts.fleet import instruction_semantic_continue as M

@pytest.fixture
def physical(tmp_path):
 def put(name,v):
  p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return M.binding(p)
 helper=put('physical.py',{});old_snapshot=put('old-snapshot.json',{});snapshot=put('snapshot.json',{'root':str(tmp_path/'code')});before={'code':old_snapshot,'workbook':'0079','run_root':str(tmp_path/'authoring')};oldconfig=put('oldconfig.json',before);config=put('config.json',dict(before,code=snapshot));dispatcher=put('dispatcher.py',{});freshrow=put('fresh.json',{'schema_version':'harbor-fleet-fresh-plan/v1','workbook':'0079','dispatcher':dispatcher});fresh=M.read(freshrow);original=dict(config=oldconfig,driver=dispatcher,publish=False,models=True,commands=True,containers=True);originalrow=put('original-job.json',original);home=tmp_path/'physical';source=put('source.xlsx',{})
 q=dict(schema_version='fleet-disclosure-payload-continuation/v1',output=str(home),original_job=originalrow,workbook='0079',source=source,fresh_plan=freshrow,config=config,old_snapshot=old_snapshot,snapshot=snapshot,helper=helper);qr=put('physical-plan.json',q);intent=put('physical/intent.json',dict(plan=qr,helper=helper,automatic_retry=False));log=put('physical/permit.log',{});first=put('physical/first-review/outcome.json',{})
 guard=dict(intent=intent,command=[sys.executable,'-I','-B',helper['path'],'permit-child','--plan',qr['path'],'--sha256',qr['sha256']],returncode=0,failure=None,seconds=3300,memory_bytes=8589934592,models=0,automatic_retry=False,log=log);put('physical/permit-resource.json',guard)
 job=dict(original,config=config,outcome=first,operations=str(tmp_path/'authoring/dispatcher-v2/physical'));jr=put('physical/authoring-job.json',job);p=dict(physical_context=qr,original_job=jr,workbook='0079',source=source,fresh_plan=freshrow)
 return p,fresh,job,guard,put

def test_completed_physical_review_preserves_original_policy(physical):
 p,f,j,g,put=physical;root=M.physical_authoring(p,f,j)[2];assert root.name=='code'
 assert M.read(p['original_job'])==j

@pytest.mark.parametrize('mutation',['nonzero','uncertain','different-plan','larger-guard','different-job','missing-guard','different-source'])
def test_partial_or_changed_physical_handoff_cannot_authorize_semantic_retry(physical,mutation):
 p,f,j,g,put=physical
 if mutation=='nonzero':g['returncode']=1
 elif mutation=='uncertain':g['failure']='TimeoutError'
 elif mutation=='different-plan':g['command'][-1]='0'*64
 elif mutation=='larger-guard':g['seconds']=6600
 elif mutation=='different-job':j=dict(j,maximum_steps=999);p['original_job']=put('physical/authoring-job.json',j)
 elif mutation=='missing-guard':Path(g['log']['path']).unlink()
 elif mutation=='different-source':p['source']=put('other-source.xlsx',{})
 put('physical/permit-resource.json',g)
 with pytest.raises((ValueError,FileNotFoundError)):M.physical_authoring(p,f,j)
