import json
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
import pytest
from scripts.fleet import normalization_completed_emission as S
from tests.test_normalization_partial_emission import fixture,save


def prepared(fixture,tmp_path):
    old,_,P,run,calls=fixture;old=dict(old,preexecution={'retained':'preexecution'})
    old['ordinary_author']=save(tmp_path/'ordinary.json',dict(research={'path':'/research','sha256':'a'*64}))
    old['tracker']=save(tmp_path/'tracker.json',{})
    partition=save(tmp_path/'partition.json',{'previous':None});old['prepared']=partition
    profile=S.read(old['runtime_profile']);profile.update(entrypoint=old['helper'],read_call={'args':[{'home':old['home']}]});old['runtime_profile']=save(tmp_path/'current-runtime.json',profile)
    previous=save(tmp_path/'previous.json',old);home=Path(old['home'])
    phase=save(run/'normalized.phase1.json',dict(variables=[]));emitted=save(run/'phase1-result.json',dict(status='phase1_evidence_ready',prepared=partition,phase1=phase))
    ei=save(home/'emit/intent.json',dict(plan=previous,helper=old['helper'],prepared=partition,lease={},seconds=3300,memory=8*1024**3))
    log=home/'emit/step.log';log.write_text('actual emission passed');eg=save(home/'emit/resource.json',dict(intent=ei,returncode=0,failure_type=None,automatic_retry=False,log=S.binding(log)))
    er=save(home/'emit/result.json',dict(emitted=emitted,lease={},runtime=old['runtime_profile'],models_dispatched=0))
    arguments={k:save(home/(k+'.json'),{})for k in ('legacy','current','lease')};arguments['checkpoint']=S.read(old['ordinary_author'])['research']
    ri=save(home/'refresh/intent.json',dict(action='refresh',arguments=arguments));rl=home/'refresh/step.log';rl.write_text("manifest['previous']['prior_plan']\nTypeError: 'NoneType' object is not subscriptable\n")
    rg=save(home/'refresh/resource.json',dict(intent=ri,returncode=1,failure=None,log=S.binding(rl)))
    job=save(tmp_path/'completed-job.json',dict(driver=old['helper'],source=old['source'],workbook=old['workbook'],inputs=[previous],command=['python','-I','-B',old['helper']['path'],'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]))
    wi=save(tmp_path/'completed-wi.json',dict(owner=old['owner'],job=job));outer=save(tmp_path/'completed-outer.json',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,automatic_retry=False,publication_performed=False,result=None,intent=wi))
    rows=[previous,er,eg,ei,S.binding(log),emitted,rg,ri,S.binding(rl),outer,wi,job]
    stop=save(tmp_path/'completed-stop.json',dict(at=1,claims=0,models=0,processes=[],files=[dict(native=x,copy=x)for x in rows]))
    q=dict(schema_version=S.SCHEMA,previous=previous,emission_result=er,emission_guard=eg,refresh_guard=rg,outer=outer,stopped=stop)
    p=dict(old,home=str(tmp_path/'next'),fence=old['fence']+1,completed_emission=save(tmp_path/'completion.json',q),completed_helper={'selected':'helper'})
    profile['read_call']['args'][0]['home']=p['home'];profile['symbols'].append({'module_name':'scripts.batch.harbor_environment_refresh','names':['unchanged']});p['runtime_profile']=save(tmp_path/'next-runtime.json',profile)
    P.aggregate=lambda _:calls.append('aggregate')
    return p,q,P,calls,home


def test_completed_phase_one_is_read_not_reemitted(fixture,tmp_path):
    p,q,P,calls,home=prepared(fixture,tmp_path)
    value=S.validate(p,P,{})
    assert value['status']=='phase1_evidence_ready' and calls==['aggregate'] and S.result(p)==value

@pytest.mark.parametrize('kind',['fence','source','model','phase','advanced','runtime','different-failure'])
def test_completed_emission_refuses_changed_or_advanced_boundary(fixture,tmp_path,kind):
    p,q,P,calls,home=prepared(fixture,tmp_path)
    if kind=='fence':p['fence']+=1
    elif kind=='source':p['source']={'foreign':True}
    elif kind=='model':
        s=S.read(q['stopped']);s['models']=1;q['stopped']=save(tmp_path/'badstop.json',s)
    elif kind=='phase':save(P.selection(p['prepared']).run/'normalized.phase1.json',{'changed':True})
    elif kind=='advanced':save(home/'refresh/result.json',{})
    elif kind=='runtime':
        rp=S.read(p['runtime_profile']);rp['symbols'][0]={'changed':True};p['runtime_profile']=save(tmp_path/'badruntime.json',rp)
    else:(home/'refresh/step.log').write_text('another failure')
    p['completed_emission']=save(tmp_path/'newcompletion.json',q)
    with pytest.raises(ValueError):S.validate(p,P,{})
