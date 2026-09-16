import pytest
from pathlib import Path
from dataclasses import dataclass,asdict
from pathlib import Path
from scripts.fleet.unadmitted_continue import continued_intake

@dataclass(frozen=True)
class Policy:
 dependency: dict
 campaign_evidence:tuple
 dependencies:dict
 tracker_root:Path
 history_roots:tuple
 mode:str='new'
@dataclass(frozen=True)
class Bootstrap:
 policy:Policy
 run:Path
 model_policy:dict
 source_manifest:dict
 segmentation_manifest:dict
 workbook:str
@dataclass(frozen=True)
class Intake:
 bootstrap:Bootstrap
 environment_run:Path
 campaign:dict
 inherited_audit_budget:dict
 owner:str

def test_new_pretracker_selection_preserves_entire_source_and_campaign():
 p=Policy({'path':'old-dependency','sha256':'a'*64},({'path':'candidate','sha256':'b'*64},),{'maximum_models':1,'maximum_workbooks':2},Path('/old/trackers'),('/actual/history',))
 s=Bootstrap(p,Path('/old/bootstrap'),{'model':'original','reasoning_effort':'high'},{'sha256':'c'*64},{'sha256':'d'*64},'0315')
 i=Intake(s,Path('/old/environment'),{'writer_rounds':2,'review_rounds':2,'physical_retries':0},{'attempts':0,'source_history':['original-diagnostic']},'original-native-owner')
 before=asdict(i);dep={'path':'new-dependency','sha256':'e'*64};history=({'path':'old-prepared','sha256':'f'*64},)
 actual=continued_intake(None,i,dep,Path('/unused'),history)
 expected=asdict(i);expected['bootstrap']['run']=Path('/unused/bootstrap');expected['bootstrap']['policy']['dependency']=dep;expected['bootstrap']['policy']['campaign_evidence']=p.campaign_evidence+history
 assert asdict(actual)==expected and asdict(i)==before
 assert actual.campaign is i.campaign and actual.inherited_audit_budget is i.inherited_audit_budget

def test_canonical_binding_rejects_parent_symlink(tmp_path):
 import pytest
 from scripts.fleet.unadmitted_continue import binding
 real=tmp_path/'real';real.mkdir();(real/'evidence').write_text('original');alias=tmp_path/'alias';alias.symlink_to(real,target_is_directory=True)
 with pytest.raises(ValueError):binding(alias/'evidence')

def test_live_custody_failure_precedes_any_new_namespace(tmp_path,monkeypatch):
 from types import SimpleNamespace
 import pytest
 from scripts.fleet import unadmitted_continue as U
 policy=SimpleNamespace(original_inventory={'path':'original'},history_roots=('original',))
 def refuse(*a):raise ValueError('no real owning heartbeat')
 adm=SimpleNamespace(read_inventory=lambda p:{'sources':[{'workbook_id':'0315'}]},survey_namespaces=refuse)
 monkeypatch.setattr(U,'context',lambda r:({},None,None,SimpleNamespace(admission=adm),SimpleNamespace(bootstrap=SimpleNamespace(policy=policy)),{}))
 target=tmp_path/'unused'
 with pytest.raises(ValueError,match='no real owning heartbeat'):U.prepare({},target)
 assert not target.exists()

@pytest.mark.parametrize('fault',[None,'old_job','config','outcome','old_operations','foreign_namespace','used','changed_workbook'])
def test_completed_admission_changes_only_original_dispatcher_namespace(tmp_path,fault):
    from scripts.fleet import unadmitted_continue as U
    old=dict(home=str(tmp_path/'completed'),workbook='0315');cfg=dict(run_root=str(tmp_path/'original'),workbook='0315')
    job=dict(config={'path':old['home']+'/config.json'},outcome={'path':old['home']+'/admission-001/outcome.json'},operations=old['home']+'/dispatcher-v2/first',models=True,maximum_steps=100)
    p=dict(workbook='0315',authoring_job={'path':old['home']+'/authoring-job.json'},operations=str(tmp_path/'original/dispatcher-v2/resume-001'))
    if fault=='old_job':p['authoring_job']['path']='/foreign/job.json'
    if fault=='config':job['config']['path']='/foreign/config.json'
    if fault=='outcome':job['outcome']['path']='/foreign/outcome.json'
    if fault=='old_operations':job['operations']='/other'
    if fault=='foreign_namespace':p['operations']=str(tmp_path/'foreign/resume')
    if fault=='used':Path(job['operations']).mkdir(parents=True)
    if fault=='changed_workbook':cfg['workbook']='0496'
    if fault:
        with pytest.raises(ValueError):U.completed_selection(p,old,job,cfg)
    else:
        selected=U.completed_selection(p,old,job,cfg)
        assert selected==dict(job,operations=p['operations']) and selected['models']is True
