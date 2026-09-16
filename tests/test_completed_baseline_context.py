"""Completed migrations use retained owning receipts, never fresh eligibility."""
from types import SimpleNamespace as N
from pathlib import Path
import pytest
from scripts.fleet import baseline_continue as F
from scripts.batch import harbor_baseline_continuation as B


def selection(tmp,monkeypatch):
 home=tmp/'execution';home.mkdir();dest=tmp/'destination';dest.mkdir()
 source={'path':'/source','sha256':'source'};snap=F.keep(tmp/'snapshot.json',{'root':str(tmp/'root')})
 config=F.keep(tmp/'oldconfig.json',{});newconfig=F.keep(dest/'config.json',{})
 boundary=F.keep(tmp/'boundary.json',dict(new_code=snap,source=source,workbook='0186',config=config))
 original_job=F.keep(tmp/'oldjob.json',dict(config=config,driver={'path':'/driver','sha256':'driver'},models=True,publish=False,outcome={'path':'/original-before-migration','sha256':'retained'}))
 runtime={'path':'/oldhelper','sha256':'original'};stdlib={'path':'/stdlib','sha256':'stdlib'}
 old=dict(schema_version=F.SCHEMA,entry=runtime,workbook='0186',source=source,stdlib=stdlib,snapshot=snap,boundary=boundary,original_job=original_job,home=str(home),operations=str(dest/'dispatcher/first'))
 original_plan=F.keep(tmp/'originalplan.json',old);migration=F.keep(tmp/'migration.json',{})
 baseline=F.keep(home/'baseline/outcome.json',{});job=F.keep(home/'authoring-job.json',{**F.checked(original_job),'config':newconfig,'operations':old['operations'],'outcome':baseline})
 seen=[];env=('exact-retained-environment',)
 D=N(setup=lambda j:seen.append(('setup',j))or env,successful=lambda o,e:seen.append(('successful',o,e))or(None,dict(status='STEP_COMPLETE',step='baseline')))
 original=N(module=lambda n,r:N(preload_inspect=lambda:None)if r==stdlib else D,retained_history=lambda *a,**k:seen.append(('history',a,k)),validate_migration=lambda *a:seen.append(('migration',a))or dict(config=newconfig),load=lambda *a,**k:pytest.fail('fresh admission must not run'))
 monkeypatch.setattr(F,'module',lambda n,r:original)
 monkeypatch.setattr(B,'selected_code',lambda s:seen.append(('snapshot',s)))
 p=dict(schema_version='fleet-completed-baseline-context/v1',entry=F.binding(F.__file__),original_runtime=runtime,original_plan=original_plan,migration=migration,job=job,workbook='0186',source=source)
 return p,seen,D,env


def test_completed_context_validates_original_history_migration_and_dispatcher(tmp_path,monkeypatch):
 p,seen,D,env=selection(tmp_path,monkeypatch);bound=F.keep(tmp_path/'context.json',p)
 assert F.context(bound)==(p,D,env)
 assert [x[0]for x in seen]==['snapshot','history','migration','setup','successful']
 assert seen[1][2]=={'probe':True}
 assert seen[2][1][1:3]==(p['original_plan'],p['migration'])

@pytest.mark.parametrize('field',['source','workbook','runtime','job','foreign_field'])
def test_completed_context_rejects_cross_binding(tmp_path,monkeypatch,field):
 p,seen,D,env=selection(tmp_path,monkeypatch)
 if field=='source':p['source']={'path':'/other','sha256':'other'}
 elif field=='workbook':p['workbook']='foreign'
 elif field=='runtime':p['original_runtime']={'path':'/other','sha256':'other'}
 elif field=='job':p['job']=F.keep(tmp_path/'foreignjob.json',F.checked(p['job']))
 else:p['fresh_admission']=True
 with pytest.raises(ValueError):F.context(F.keep(tmp_path/'context.json',p))
 assert not any(x[0]=='setup'for x in seen)


def normalization_selection(tmp_path,monkeypatch):
 p,seen,D,env=selection(tmp_path,monkeypatch)
 old=F.checked(p['original_plan']);auth=dict(path='/declared/authorization.md',sha256='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0')
 actual_binding=F.binding
 monkeypatch.setattr(F,'binding',lambda path:auth if str(path)==auth['path']else actual_binding(path))
 prepared=F.keep(tmp_path/'prepared.json',{'original':'retained-source'})
 n=dict(schema_version='harbor-normalization-snapshot-selection/v1',prepared=prepared,snapshot=old['snapshot'],authorization=auth)
 return p,n,seen


def test_explicit_normalization_uses_original_bridge_and_retained_snapshot(tmp_path,monkeypatch):
 from scripts.batch import harbor_normalization_validator_bridge as N
 p,n,seen=normalization_selection(tmp_path,monkeypatch);calls=[]
 monkeypatch.setenv('HARBOR_FLEET_NORMALIZATION_PROFILE','')
 monkeypatch.setattr(N,'_approved',lambda prepared:calls.append((prepared,F.os.environ['HARBOR_FLEET_NORMALIZATION_PROFILE'])))
 p['normalization_profile']=F.keep(tmp_path/'normalization.json',n)
 F.context(F.keep(tmp_path/'context.json',p))
 assert calls==[(n['prepared'],F.json.dumps(p['normalization_profile'],sort_keys=True))]

@pytest.mark.parametrize('change',['snapshot','authorization','prepared'])
def test_normalization_context_rejects_foreign_or_changed_selection(tmp_path,monkeypatch,change):
 from scripts.batch import harbor_normalization_validator_bridge as N
 p,n,seen=normalization_selection(tmp_path,monkeypatch)
 monkeypatch.setattr(N,'_approved',lambda prepared:pytest.fail('changed normalization reached original reader'))
 if change=='snapshot':n['snapshot']=F.keep(tmp_path/'foreign-snapshot.json',{'root':'/foreign'})
 elif change=='authorization':n['authorization']={**n['authorization'],'sha256':'0'*64}
 else:Path(n['prepared']['path']).write_text('{}')
 p['normalization_profile']=F.keep(tmp_path/'normalization.json',n)
 with pytest.raises(ValueError):F.context(F.keep(tmp_path/'context.json',p))
