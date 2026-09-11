"""External guard ownership is structural, never inferred from a copied path."""
from pathlib import Path
from dataclasses import replace
import pytest
from scripts.batch import harbor_lease_takeover as T,harbor_instruction_worker as W
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_model_executor import Campaign
from scripts.batch.harbor_admission import read,record
from scripts import harbor_instruction_campaign as C,harbor_campaign as B
from test_root_lease_takeover import setup,driver_setup,save

@pytest.fixture
def external(driver_setup):
    s,d,b,lease=driver_setup;c=s.campaign_root;home=c.parent/'actual-external-operation'
    root=c.parent/'old-code';(root/'scripts').mkdir(parents=True)
    for name in ('harbor_instruction_campaign.py','harbor_campaign.py'):
        (root/'scripts'/name).write_text('Historical bytes are not executable Python.\n')
    worker=W.Worker(RootContext(c/'trackers/workbooks','key'),c/'instruction',Campaign(c/'models',c/'slots',1),root)
    selection=read(s.checkpoint)['selection'];controls=read(s.config)['controls']
    desc=dict(schema_version=C.SCHEMA,worker=C.worker_encode(worker),controls=controls,dependencies={},origin=[],code=record(root/'scripts/harbor_instruction_campaign.py'),initial=dict(selection=selection,lease=B.encode(lease)))
    descriptor=save(home/'descriptor.json',desc)
    cp=save(home/'checkpoint.json',dict(schema_version=C.SCHEMA,status='INSTRUCTION_READY',descriptor=descriptor,selection=selection,lease=B.encode(lease),checkpoint=None,evidence=[],publication_performed=False))
    intent=dict(schema_version=C.SCHEMA,action='step',arguments=dict(descriptor=descriptor,checkpoint=cp),controls=controls,code=desc['code'],base_code=record(root/'scripts/harbor_campaign.py'))
    guard=save(home/'resource.json',dict(intent=save(home/'intent.json',intent),log=save(home/'step.log',{'stopped':True}),metrics={},returncode=1,failure=None))
    return replace(s,guard=guard,checkpoint=cp),d,b,descriptor


def test_external_guard_preserves_actual_paths_and_never_executes_old_code(external):
    s,d,b,descriptor=external;old=Path(s.guard['path']).read_bytes()
    lease,completion=T.replace_expired(s,d,clock=lambda:120)
    proof=read(read(completion)['stopped_owner_proof'])
    assert s.guard in proof['evidence'] and descriptor in proof['evidence']
    assert Path(s.guard['path']).read_bytes()==old
    assert T.validate_completion(completion)[2]==lease


@pytest.mark.parametrize('fault',['action','checkpoint','base_code','code','controls','worker_run','tracker_root','descriptor_controls','extra_field'])
def test_external_guard_rejects_foreign_ownership_without_mutation(external,fault):
    s,d,b,db=external;g=read(s.guard);intent=read(g['intent']);desc=read(db)
    if fault=='action':intent['action']='start'
    elif fault=='checkpoint':intent['arguments']['checkpoint']={**s.checkpoint,'sha256':'0'*64}
    elif fault in ('base_code','code'):intent[fault]=record(Path(g['log']['path']))
    elif fault=='controls':intent['controls']={}
    else:
        if fault=='worker_run':desc['worker']['run']+='/other'
        elif fault=='tracker_root':desc['worker']['context']['tracker_root']+='/other'
        elif fault=='descriptor_controls':desc['controls']={}
        else:desc['extra']=None
        db=save(Path(db['path']),desc);cp=read(s.checkpoint);cp['descriptor']=db
        cp=save(Path(s.checkpoint['path']),cp);s=replace(s,checkpoint=cp);intent['arguments']=dict(descriptor=db,checkpoint=cp)
    g['intent']=save(Path(g['intent']['path']),intent);s=replace(s,guard=save(Path(s.guard['path']),g))
    with pytest.raises(ValueError):T.replace_expired(s,d,clock=lambda:120)
    assert read(s.tracker)==b and not d.exists()
