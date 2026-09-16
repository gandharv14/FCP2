from pathlib import Path
import copy,json
import pytest
from scripts.fleet import source_authoring_history as N
from scripts.fleet.migration import binding,checked
H=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/control/source-to-authoring-001'

@pytest.fixture
def retained(tmp_path,monkeypatch):
 p=copy.deepcopy(checked(binding(H/'0200-history-002.json')));old=copy.deepcopy(checked(p['preparation_predecessor']));proof=copy.deepcopy(checked(p['preparation_failure']));projection=checked(binding(H/'failed-preparation-001/0200/read-projection.json'))
 home=tmp_path/'source-0200-first-authoring-001';oldhome=Path(proof['old_home']);rows=[];values={}
 for row in proof['files']:
  native=Path(row['path']);local=Path(projection[row['path']]['path']);target=home/native.relative_to(oldhome) if native.is_relative_to(oldhome) else tmp_path/'worker'/native.name
  target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(local.read_bytes());rows.append(binding(target));values[row['path']]=json.loads(local.read_bytes()) if native.suffix=='.json' else None
 (home/'authoring/trackers/workbooks').mkdir(parents=True)
 old['authoring_home']=str(home);proof['old_home']=str(home);proof['files']=rows;p['authoring_home']=str(tmp_path/'source-0200-first-authoring-002')
 job=copy.deepcopy(checked(proof['failed_worker_job']));job['run']=str(tmp_path/'worker')
 values.update({p['preparation_predecessor']['path']:old,p['preparation_failure']['path']:proof,proof['failed_worker_job']['path']:job})
 def read(row):return values[row['path']] if row['path'] in values else checked(row)
 monkeypatch.setattr(N,'checked',read);monkeypatch.setitem(N.PREPARATION_FAILURES,'0200',p['preparation_failure'])
 return p,proof,home,job

def test_stopped_pre_admission_preparation_can_continue_without_reset(retained):
 p,proof,home,job=retained;assert N.preparation_failure(p,'0200')==proof
 assert not (Path(p['authoring_home'])).exists()

@pytest.mark.parametrize('case',['admitted','model','tracker','newfile','changedfile','modeljournal','wrongcode','wrongfence','wrongowner','wrongreason','source','newnamespace','wrongproof','losthistory'])
def test_no_inflight_or_consumed_budget_can_be_reprepared(case,retained):
 p,proof,home,job=retained
 if case=='admitted':proof['admission_started']=True
 if case=='model':proof['model_calls_submitted']=1
 if case=='tracker':(home/'authoring/trackers/workbooks/0200.json').write_text('{}')
 if case=='newfile':(home/'Call.json').write_text('{}')
 if case=='changedfile':(home/'authoring/candidate.json').write_text('{}')
 if case=='modeljournal':(Path(job['run'])/'model-calls').mkdir()
 if case=='wrongcode':proof['code_version']='other'
 if case=='wrongfence':proof['central']['fence']=3
 if case=='wrongowner':proof['central']['owner']='other'
 if case=='wrongreason':proof['central']['reason']='{}'
 if case=='source':p['source']={**p['source'],'sha256':'b'*64}
 if case=='newnamespace':p['authoring_home']=str(home.parent/'other')
 if case=='wrongproof':p['preparation_failure']={**p['preparation_failure'],'sha256':'c'*64}
 if case=='losthistory':p['histories']={}
 with pytest.raises(ValueError):N.preparation_failure(p,'0200')
