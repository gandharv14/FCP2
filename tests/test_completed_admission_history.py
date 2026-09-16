from pathlib import Path
from types import ModuleType,SimpleNamespace
import json
import pytest
from scripts.fleet import unadmitted_continue as U

def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return U.binding(path)

def case(tmp_path):
 old=dict(home=str(tmp_path/'completed'),workbook='0315');cfg=dict(run_root=str(tmp_path/'original'),workbook='0315')
 config=write(Path(old['home'])/'config.json',cfg)
 job=dict(driver={'original':'driver'},config=config,outcome={'path':old['home']+'/admission-001/outcome.json'},operations=old['home']+'/dispatcher-v2/first',models=True,maximum_steps=100)
 p=dict(workbook='0315',authoring_job={'path':old['home']+'/authoring-job.json'},operations=str(tmp_path/'original/dispatcher-v2/resume-001'),output=str(tmp_path/'actual-output'))
 Path(p['operations']).mkdir(parents=True);selected=dict(job,operations=p['operations']);write(Path(p['output'])/'authoring-job.json',selected)
 return p,old,job,cfg,selected

@pytest.mark.parametrize('fault',[None,'missing','changed','competing','symlink'])
def test_history_requires_actual_emitted_job_and_original_namespace(tmp_path,fault):
 p,old,job,cfg,selected=case(tmp_path)
 if fault=='missing':(Path(p['output'])/'authoring-job.json').unlink()
 if fault=='changed':write(Path(p['output'])/'authoring-job.json',dict(selected,models=False))
 if fault=='competing':Path(job['operations']).mkdir(parents=True)
 if fault=='symlink':
  alias=tmp_path/'alias';alias.symlink_to(p['output'],target_is_directory=True);p['output']=str(alias)
 if fault:
  with pytest.raises((ValueError,FileNotFoundError)):U.completed_selection(p,old,job,cfg,historical=True)
 else:
  assert U.completed_selection(p,old,job,cfg,historical=True)==selected
  with pytest.raises(ValueError,match='already started'):U.completed_selection(p,old,job,cfg)

@pytest.mark.parametrize('fault',[None,'original-check','owning-intent','namespace'])
def test_original_context_runs_without_dispatch_or_freshness_override_elsewhere(tmp_path,monkeypatch,fault):
 p,old,job,cfg,selected=case(tmp_path);p['helper']={'original':'helper'};row=write(tmp_path/'plan.json',p);cp={'original':'admission'}
 write(Path(p['output'])/'intent.json',dict(plan=row,admission=cp,automatic_retry=fault=='owning-intent'))
 original=ModuleType('original');calls=[]
 def load(r,n):
  assert r==job['driver'];calls.append('original-loader')
  return SimpleNamespace(__file__='/driver',setup=lambda value:calls.append('original-setup')or(),successful=lambda value,env:(cp,{}))
 original.__dict__.update(dict(read=U.read,module=load,p=p,old=old,job=job,cfg=cfg,require=U.require,fail=fault=='original-check',foreign=fault=='namespace',calls=calls))
 exec("def completed_context(row):\n require(not fail,'original check refused')\n selected=completed_selection(p,old,job,cfg)\n D=module(job['driver'],'selected_completed_admission_dispatcher')\n env=D.setup(selected)\n checkpoint,_=D.successful(job['outcome'],env)\n D.operation_namespace(cfg,'/foreign' if foreign else selected['operations'])\n return p,D,env,selected,checkpoint\n",original.__dict__)
 monkeypatch.setattr(U,'module',lambda r,n:original)
 if fault:
  with pytest.raises(ValueError):U.completed_history(row)
 else:
  result=U.completed_history(row);assert result[3]==selected and result[4]==cp
  assert calls==['original-loader','original-setup']
