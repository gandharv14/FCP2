"""Credential relocation preserves real exclusion, sources and prior checkpoints."""
import copy
from pathlib import Path
from dataclasses import replace
import pytest
from scripts.batch import harbor_lease_takeover as T
from scripts.batch.harbor_admission import read,record
from test_root_lease_takeover import setup,driver_setup,save

@pytest.mark.parametrize('mode',['valid','undeclared','source','slot','config','executed','authorization','schema','extra','relative'])
def test_actual_replacement_requires_exact_credential_migration(driver_setup,mode):
    s,d,b,old=driver_setup
    config=read(s.config);config['controls']['value']['env_file']={'type':'path','value':'/mac/project/.env'}
    s=replace(s,config=save(Path(s.config['path']),config));guard=read(s.guard);intent=read(guard['intent'])
    intent['controls']=copy.deepcopy(config['controls']);intent['controls']['value']['env_file']['value']='/linux/private/model.env'
    guard['intent']=save(Path(guard['intent']['path']),intent);s=replace(s,guard=save(Path(s.guard['path']),guard))
    auth=save(s.campaign_root/'authorization.json',{'user_authorized_credential_relocation':True})
    migration=dict(schema_version='harbor-credential-path-migration/v1',config=s.config,workbook=s.workbook,
        source_sha256=b['bindings']['original_raw_source']['sha256'],original=config['controls'],executed=intent['controls'],authorization=auth,reason='Relocate the credential path; preserve original evidence and lock identity.')
    if mode=='source':migration['source_sha256']='f'*64
    elif mode=='slot':migration['executed']['value']['slot_lock']={'type':'path','value':'/different/workbook.lock'}
    elif mode=='config':migration['config']={**s.config,'sha256':'e'*64}
    elif mode=='executed':migration['executed']['value']['env_file']['value']='/unselected/private.env'
    elif mode=='authorization':migration['authorization']={**auth,'sha256':'e'*64}
    elif mode=='schema':migration['schema_version']='unknown'
    elif mode=='extra':migration['arbitrary_relaxation']=True
    elif mode=='relative':migration['executed']['value']['env_file']['value']='relative.env'
    row=save(s.campaign_root/'credential-migration.json',migration)
    if mode!='undeclared':s=replace(s,controls_migration=row)
    kept={x['path']:Path(x['path']).read_bytes()for x in (s.config,s.guard,s.checkpoint)}
    if mode=='valid':
        lease,complete=T.replace_expired(s,d,clock=lambda:120)
        assert T.validate_completion(complete)[2]==lease
        assert row in read(read(complete)['stopped_owner_proof'])['evidence']
    else:
        with pytest.raises((ValueError,FileNotFoundError)):T.replace_expired(s,d,clock=lambda:120)
        assert not d.exists() and read(s.tracker)==b
    assert all(Path(p).read_bytes()==raw for p,raw in kept.items())


def test_legacy_selection_omits_unselected_migration(driver_setup):
    s,*_=driver_setup
    assert 'controls_migration' not in T._selection_value(s)
