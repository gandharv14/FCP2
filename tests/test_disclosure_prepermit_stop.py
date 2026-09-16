from pathlib import Path
import json,copy
import pytest
from scripts.fleet import disclosure_command_continue as D

def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return D.binding(path)

def case(tmp_path):
 code=tmp_path/'old.py';code.write_text('retained original')
 home=tmp_path/'failed';worker=tmp_path/'worker';instruction=tmp_path/'instruction';instruction.mkdir();initial=write(instruction/'initial.json',{})
 profile=write(tmp_path/'profile.json',dict(initial=initial));source=tmp_path/'source';source.write_text('source')
 old=dict(helper=D.binding(code),owner='old-owner',fence=11,output=str(home),runtime_profile={'old':'runtime'},workbook='0496',source=D.binding(source),profile=profile,tracker={'unchanged':'tracker'},lease=None)
 row=write(tmp_path/'old.json',old);parent=write(home/'intent.json',dict(plan=row,helper=old['helper'],automatic_retry=False))
 job=dict(run=str(worker),command=['python','-I','-B',str(code),'run'],driver=old['helper'],inputs=[row],source=old['source'],workbook='0496',version='version',result=str(home/'result.json'))
 j=write(tmp_path/'job.json',job);intent=write(worker/'intent.json',dict(job=j,owner=old['owner'],claim_id='actual-claim',automatic_retry=False))
 claim=write(worker/'claim.json',dict(claim_id='actual-claim',owner=old['owner'],fence=11,workbook='0496',source_sha256=old['source']['sha256'],version='version'))
 outcome=write(worker/'outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,publication_performed=False,intent=intent))
 process=write(tmp_path/'process.json',dict(job=j,automatic_retry=False,pid=99999999))
 log=home/'permit.log';log.write_text('in permit_child\n U.custody(p,w.root)\n in custody\nValueError: unreconciled model effect or quarantined custody\n')
 guard=write(home/'permit-resource.json',dict(intent=parent,returncode=1,failure=None,seconds=3300,memory_bytes=8589934592,models_dispatched=0,log=D.binding(log),command=['python','-I','-B',str(code),'permit-child','--plan',row['path'],'--sha256',row['sha256']]))
 selected=write(tmp_path/'failure.json',dict(plan=row,job=j,process=process,outcome=outcome,claim=claim,guard=guard))
 return dict(old,helper=D.binding(Path(D.__file__)),owner='new-owner',fence=12,output=str(tmp_path/'unused'),runtime_profile={'new':'runtime'},prepermit_failure=selected),old

def test_sealed_admission_refusal_keeps_exact_scope(tmp_path):
 p,old=case(tmp_path);assert D.prepermit_stop(p)==old
 assert not Path(p['output']).exists()

@pytest.mark.parametrize('fault',['permit','later','source','lease','fence','claim','passed_guard','wrong_error','marker','output','second_recovery'])
def test_any_effect_or_detached_custody_refuses_before_authorize(tmp_path,fault):
 p,old=case(tmp_path);home=Path(old['output']);a=D.read(p['prepermit_failure'])
 if fault in ('permit','later'):(home/fault).mkdir()
 elif fault in ('source','lease'):p[fault]={'foreign':'scope'}
 elif fault=='fence':p['fence']+=1
 elif fault=='output':p['output']=old['output']
 elif fault=='marker':write(Path(D.read(old['profile'])['initial']['path']).parent/'disclosure-command-repair.json',{})
 elif fault=='wrong_error':(home/'permit.log').write_text('other failure')
 else:
  if fault=='claim':v=D.read(a['claim']);v['claim_id']='foreign';a['claim']=write(Path(a['claim']['path']),v)
  elif fault=='passed_guard':v=D.read(a['guard']);v['returncode']=0;a['guard']=write(Path(a['guard']['path']),v)
  else:v=D.read(a['plan']);v['prepermit_failure']=p['prepermit_failure'];a['plan']=write(Path(a['plan']['path']),v)
  p['prepermit_failure']=write(Path(p['prepermit_failure']['path']),a)
 with pytest.raises(ValueError):D.prepermit_stop(p)

def test_later_owning_permit_is_only_accepted_as_explicit_history(tmp_path):
 p,_=case(tmp_path);marker=Path(D.read(p['profile'])['initial']['path']).parent/'disclosure-command-repair.json'
 write(marker,dict(schema_version='harbor-disclosure-command-repair/v1',profile=p['profile'],directory=str(Path(p['output'])/'permit'),automatic_retry=False))
 with pytest.raises(ValueError):D.prepermit_stop(p)
 assert D.prepermit_stop(p,continued=True)['owner']=='old-owner'

@pytest.mark.parametrize('fault',[None,'source_context','directory','helper','old_renewal'])
def test_explicit_renewal_only_changes_declared_context_lease_reader(tmp_path,fault):
 p,old=case(tmp_path);a=D.read(p['prepermit_failure']);old['renewal_module']=None
 c=dict(schema_version='fleet-failed-package-first-step-context/v1',helper=old['helper'],previous={'path':'/original','sha256':'same'},failure={'sealed':'failure'},bridge=old['helper'],stopped={'actual':'custody'},pair_scope={'unchanged':'scope'})
 cb=write(tmp_path/'old-context.json',c);old['context']=dict(helper=old['helper'],plan=cb,function='context',manager='contextmanager',kwargs={},projection={'dispatcher':2,'environment':3})
 if fault=='old_renewal':old['renewal_module']={'foreign':'old'}
 a['plan']=write(Path(a['plan']['path']),old)
 job=D.read(a['job']);job['inputs']=[a['plan']];a['job']=write(Path(a['job']['path']),job)
 outer=D.read(a['outcome']);intent=D.read(outer['intent']);intent['job']=a['job'];outer['intent']=write(Path(outer['intent']['path']),intent);a['outcome']=write(Path(a['outcome']['path']),outer)
 process=D.read(a['process']);process['job']=a['job'];a['process']=write(Path(a['process']['path']),process)
 # Changing the old plan also requires the authentic original intent/job/guard.
 home=Path(old['output']);write(home/'intent.json',dict(plan=a['plan'],helper=old['helper'],automatic_retry=False));g=D.read(a['guard']);g['intent']=D.binding(home/'intent.json');g['command'][-1]=a['plan']['sha256'];a['guard']=write(Path(a['guard']['path']),g)
 p.update(context=old['context'],renewal_module={'path':'/selected-L','sha256':'original'},prepermit_failure=write(Path(p['prepermit_failure']['path']),a))
 new=dict(c,helper=p['helper'],bridge=p['helper'],lease_renewal=dict(module=p['renewal_module'],directory=str(Path(p['output'])/'renewal')))
 if fault=='source_context':new['previous']={'foreign':'checkpoint'}
 if fault=='directory':new['lease_renewal']['directory']='/foreign/renewal'
 if fault=='helper':new['helper']=old['helper']
 newrow=write(tmp_path/'new-context.json',new);p['context']=dict(old['context'],helper=p['helper'],plan=newrow)
 if fault:
  with pytest.raises(ValueError):D.prepermit_stop(p)
 else:assert D.prepermit_stop(p)==old
