from pathlib import Path
import pytest
from tests.test_normalization_partial_emission import fixture,save
from scripts.fleet import normalization_partial_emission as S


def prepared(fixture,tmp_path):
    p,q,P,run,calls=fixture
    rp=S.read(p['runtime_profile']);rp.update(entrypoint=p['helper'],read_call={'args':[{'home':p['home']}]});p['runtime_profile']=save(tmp_path/'pre-profile.json',rp)
    old=save(tmp_path/'partial-plan.json',p)
    job=save(tmp_path/'interrupted-job.json',dict(workbook=p['workbook'],source=p['source'],driver=p['helper'],inputs=[old],command=['python','-I','-B',p['helper']['path'],'run','--plan',old['path'],'--plan-sha256',old['sha256']]))
    intent=save(tmp_path/'interrupted-intent.json',dict(job=job,owner=p['owner'],automatic_retry=False))
    outer=save(tmp_path/'interrupted/outcome.json',dict(status='RECONCILIATION_REQUIRED',returncode=None,failure_type='TimeoutError',result=None,automatic_retry=False,publication_performed=False,intent=intent))
    log=tmp_path/'interrupted/worker.log';log.write_bytes(b'');lr=S.binding(log)
    files=[old,job,intent,outer,lr]
    stopped=save(tmp_path/'pre-stopped.json',dict(at=1,claims=0,models=0,processes=[],files=[dict(native=x,copy=x)for x in files]))
    proof=dict(schema_version='fleet-normalization-preexecution-stop/v1',previous=old,outer=outer,stopped=stopped,log=lr)
    p=dict(p,home=str(tmp_path/'fresh'),fence=6,preexecution=save(tmp_path/'preexecution.json',proof))
    rp['read_call']['args'][0]['home']=p['home'];p['runtime_profile']=save(tmp_path/'next-profile.json',rp)
    return p,proof,P,calls


def test_stopped_before_task_preserves_original_partial_validation(fixture,tmp_path):
    p,proof,P,calls=prepared(fixture,tmp_path)
    S.validate(p,P,{})
    assert calls==['emission','spec']


@pytest.mark.parametrize('change',['owner','fence','source','model','process','home','log','output','missing','second-interruption'])
def test_preexecution_rejects_changed_or_started_work(fixture,tmp_path,change):
    p,proof,P,calls=prepared(fixture,tmp_path)
    if change in ('owner','fence','source'):
        p[change]=p['fence']+1 if change=='fence'else 'foreign'
    elif change in ('model','process','missing'):
        stop=S.read(proof['stopped'])
        if change=='model':stop['models']=1
        elif change=='process':stop['processes']=[{'pid':99}]
        else:stop['files']=stop['files'][1:]
        proof['stopped']=save(tmp_path/'bad-stop.json',stop)
    elif change=='home':Path(S.read(proof['previous'])['home']).mkdir()
    elif change=='log':Path(proof['log']['path']).write_text('task began')
    elif change=='output':
        value=S.read(proof['outer']);value['result']={'path':'/output','sha256':'a'*64};proof['outer']=save(tmp_path/'bad-outer.json',value)
    else:
        old=S.read(proof['previous']);old['preexecution']=p['preexecution'];proof['previous']=save(tmp_path/'bad-previous.json',old)
    p['preexecution']=save(tmp_path/'bad-proof.json',proof)
    with pytest.raises(ValueError):S.validate(p,P,{})
