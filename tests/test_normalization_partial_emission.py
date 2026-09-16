import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import normalization_partial_emission as S


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return S.binding(path)


@pytest.fixture
def fixture(tmp_path):
    home=tmp_path/'old';run=tmp_path/'run';run.mkdir();helper=save(tmp_path/'old.py',{})
    symbols=[{'module_name':'gen_normalizer','names':['coalesce_same_atom_aliases']}]
    rp=save(tmp_path/'old-runtime.json',dict(symbols=symbols))
    p=dict(ordinary_author={'path':'/original','sha256':'a'*64},helper=helper,runtime_profile=rp,
        home=str(home),fence=4,source={'path':'/source','sha256':'b'*64},workbook='0001',prepared={'path':'/prepared','sha256':'c'*64},owner='same-owner')
    previous=save(tmp_path/'old-plan.json',p)
    intent=save(home/'emit/intent.json',dict(plan=previous,helper=helper,prepared=p['prepared'],lease={},seconds=3300,memory=8*1024**3))
    log=home/'emit/step.log';log.write_text('N.validate_spec(spec)\nValueError: Duplicate explicit source id long collides with a different source definition\n')
    guard=save(home/'emit/resource.json',dict(returncode=1,failure_type=None,automatic_retry=False,intent=intent,log=S.binding(log)))
    job=save(tmp_path/'job.json',dict(driver=helper,source=p['source'],workbook='0001',inputs=[previous],command=['python','-I','-B',helper['path'],'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]))
    wi=save(tmp_path/'worker-intent.json',dict(job=job))
    outer=save(tmp_path/'outer.json',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,automatic_retry=False,publication_performed=False,result=None,intent=wi))
    outputs=[save(run/n,{'variables':[{'value':1}]})for n in S.names('0001')]
    files=[previous,guard,intent,S.binding(log),outer,wi,job,*outputs]
    stop=save(tmp_path/'stopped.json',dict(at=1,claims=0,models=0,processes=[],files=[dict(native=x,copy=x)for x in files]))
    q=dict(schema_version=S.SCHEMA,previous=previous,guard=guard,outer=outer,stopped=stop,outputs=outputs)
    p=dict(p,helper=save(tmp_path/'new.py',{}),runtime_profile=save(tmp_path/'new-runtime.json',dict(symbols=symbols+[{'module_name':'mcp_env.build','names':['source_id']}])) ,home=str(tmp_path/'new'),fence=5,partial_helper=save(tmp_path/'partial.py',{}),partial_emission=save(tmp_path/'partial.json',q))
    selection=save(tmp_path/'selection.json',dict(pipeline_bindings={}))
    calls=[]
    N=SimpleNamespace(expected_outputs=lambda *a:({},{}),validate_emission=lambda *a:calls.append('emission'),validate_spec=lambda *a:calls.append('spec'),verify_against_golden=lambda *a:[])
    selected=SimpleNamespace(run=run,workbook='0001',bindings={'source_file':{'path':'/source'}})
    P=SimpleNamespace(selection=lambda _:selected,_current=lambda *a,**k:({'selection':selection},selected,None,{},[]),N=N)
    return p,q,P,run,calls


def test_sealed_partial_emission_keeps_all_outputs_and_runs_original_validators(fixture):
    p,q,P,run,calls=fixture;before={x:S.binding(x)for x in run.iterdir()}
    assert S.validate(p,P,{})==q
    assert calls==['emission','spec']and before=={x:S.binding(x)for x in run.iterdir()}


@pytest.mark.parametrize('change',['source','fence','uncertain','live','missing-custody','advanced','output','profile','numerical'])
def test_refuses_foreign_uncertain_or_changed_partial_emission(fixture,change,tmp_path):
    p,q,P,run,calls=fixture
    if change=='source':p['source']=dict(p['source'],sha256='f'*64)
    elif change=='fence':p['fence']+=1
    elif change=='uncertain':
        value=S.read(q['outer']);value['returncode']=None;q['outer']=save(tmp_path/'uncertain.json',value)
    elif change in ('live','missing-custody'):
        value=S.read(q['stopped'])
        if change=='live':value['processes']=[{'pid':1}]
        else:value['files']=value['files'][1:]
        q['stopped']=save(tmp_path/'new-stop.json',value)
    elif change=='advanced':save(run/'phase1-result.json',{})
    elif change=='output':save(run/'normalized.json',{'variables':[{'value':2}]})
    elif change=='profile':p['runtime_profile']=save(tmp_path/'wrong-profile.json',dict(symbols=[{},{}]))
    else:P.N.verify_against_golden=lambda *a:['changed value']
    p['partial_emission']=save(tmp_path/'new-partial.json',q)
    with pytest.raises(ValueError):S.validate(p,P,{})
