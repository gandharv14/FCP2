import ast
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import normalization_remaining as N, normalization_single_review as S


def test_only_single_mode_changes_memory_and_retains_original_default():
    assert N.emission_memory({})==8*1024**3
    assert N.emission_memory({'single_author':{}})==12*1024**3
    assert N.SINGLE_FIELDS-N.ORDINARY_FIELDS=={'single_author','single_helper','memory_policy'}


def test_single_runtime_cannot_change_other_functions(tmp_path):
    root=tmp_path/'root';f=root/'scripts/batch/harbor_review_context.py';f.parent.mkdir(parents=True);f.write_text('def normalization_review(): return 1\ndef other(): return 2\n')
    selected=tmp_path/'selected.py';selected.write_text('def normalization_review(): return 3\ndef other(): return 2\n')
    p={'runtime':{'path':'/runtime','sha256':'a'*64}}
    profile=dict(runtime=p['runtime'],symbols=[dict(module_name='scripts.batch.harbor_review_context',names=['normalization_review'],original=N.binding(f),selected=N.binding(selected))])
    p['runtime_profile']=N.keep(tmp_path/'profile.json',profile)
    S.delta(N,p,root)
    selected.write_text('def normalization_review(): return 3\ndef other(): return 4\n')
    profile['symbols'][0]['selected']=N.binding(selected);p['runtime_profile']=N.keep(tmp_path/'changed.json',profile)
    with pytest.raises(ValueError,match='other functions'):S.delta(N,p,root)


@pytest.mark.parametrize('key,value',[('memory_bytes',16*1024**3),('seconds',7200),('models_repeated',1),('original_outputs_preserved',False),('source',{'sha256':'e'*64}),('workbook','wrong')])
def test_resource_policy_rejects_broader_or_foreign_scope(key,value):
    auth={'path':'/auth','sha256':S.AUTH};guard={'path':'/guard','sha256':'b'*64};source={'path':'/source','sha256':'c'*64}
    policy=dict(schema_version='fleet-normalization-memory-continuation/v1',authorization=auth,workbook='0316',source=source,failed_guard=guard,memory_bytes=S.MEMORY,seconds=S.SECONDS,models_repeated=0,original_outputs_preserved=True)
    policy[key]=value
    helper=SimpleNamespace(read=lambda row:policy,binding=lambda path:auth)
    with pytest.raises(ValueError):S.resource_policy(helper,dict(memory_policy={},source=source,workbook='0316'),dict(failed_guard=guard))


def test_original_allowance_must_cover_memory_without_new_model_budget():
    auth={'path':'/auth','sha256':S.AUTH};guard={};source={'sha256':'c'*64}
    policy=dict(schema_version='fleet-normalization-memory-continuation/v1',authorization=auth,workbook='0316',source=source,failed_guard=guard,memory_bytes=S.MEMORY,seconds=S.SECONDS,models_repeated=0,original_outputs_preserved=True)
    allowance=dict(authorization=auth,scope=dict(workbook='0316',original_sha256='c'*64),limits=dict(memory_bytes=16*1024**3,stage_seconds=7200))
    values={'policy':policy,'takeover':{'allowance':'allowance'},'allowance':allowance}
    helper=SimpleNamespace(read=values.__getitem__,binding=lambda path:auth);p=dict(memory_policy='policy',takeover_plan='takeover',source=source,workbook='0316')
    S.resource_policy(helper,p,dict(failed_guard=guard))
    allowance['limits']['memory_bytes']=8*1024**3
    with pytest.raises(ValueError):S.resource_policy(helper,p,dict(failed_guard=guard))


def test_completed_runtime_groups_both_refresh_symbols_and_preserves_other_code(tmp_path):
    root=tmp_path/'root';directory=root/'scripts/batch';directory.mkdir(parents=True)
    review=directory/'harbor_review_context.py';review.write_text('def normalization_review(): return 1\n')
    refresh=directory/'harbor_environment_refresh.py';refresh.write_text('def unchanged(): return 1\ndef prepare(): return 2\ndef advance(): return 3\n')
    selected=tmp_path/'selected.py';selected.write_text('def unchanged(): return 4\ndef prepare(): return 5\ndef advance(): return 3\n')
    p={'runtime':{'path':'/runtime','sha256':'a'*64},'completed_emission':{}}
    profile=dict(runtime=p['runtime'],symbols=[dict(module_name='scripts.batch.harbor_review_context',names=['normalization_review'],original=N.binding(review),selected=N.binding(review)),dict(module_name='scripts.batch.harbor_environment_refresh',names=['unchanged','prepare'],original=N.binding(refresh),selected=N.binding(selected))])
    p['runtime_profile']=N.keep(tmp_path/'profile.json',profile);S.delta(N,p,root)
    profile['symbols'][1]['names']=['unchanged'];p['runtime_profile']=N.keep(tmp_path/'missing.json',profile)
    with pytest.raises(ValueError):S.delta(N,p,root)
    profile['symbols'][1]['names']=['unchanged','prepare'];selected.write_text('def unchanged(): return 4\ndef prepare(): return 5\ndef advance(): return 6\n');profile['symbols'][1]['selected']=N.binding(selected);p['runtime_profile']=N.keep(tmp_path/'other.json',profile)
    with pytest.raises(ValueError,match='other functions'):S.delta(N,p,root)


def test_prepared_refresh_requires_coherent_whole_module_with_same_narrow_delta(tmp_path):
    root=tmp_path/'root';directory=root/'scripts/batch';directory.mkdir(parents=True)
    review=directory/'harbor_review_context.py';review.write_text('def normalization_review(): return 1\n')
    refresh=directory/'harbor_environment_refresh.py';refresh.write_text('def unchanged(): return 1\ndef prepare(): return 2\ndef validate(): return __file__\ndef commit(): return validate()\n')
    selected=tmp_path/'selected.py';selected.write_text(refresh.read_text().replace('return 1','return 3').replace('return 2','return 4'))
    p={'runtime':{'path':'/runtime','sha256':'a'*64},'completed_emission':{},'prepared_refresh':{}}
    profile=dict(runtime=p['runtime'],symbols=[dict(module_name='scripts.batch.harbor_review_context',names=['normalization_review'],original=N.binding(review),selected=N.binding(review))],dependencies=[dict(module_name='scripts.batch.harbor_environment_refresh',original=N.binding(refresh),selected=N.binding(selected))])
    p['runtime_profile']=N.keep(tmp_path/'profile.json',profile);S.delta(N,p,root)
    unselected=dict(p);unselected.pop('prepared_refresh')
    with pytest.raises(ValueError):S.delta(N,unselected,root)
    selected.write_text(selected.read_text().replace('return validate()','return 0'));profile['dependencies'][0]['selected']=N.binding(selected);p['runtime_profile']=N.keep(tmp_path/'changed.json',profile)
    with pytest.raises(ValueError,match='other functions'):S.delta(N,p,root)
