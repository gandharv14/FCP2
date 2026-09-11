"""Current synthetic normalization/profile gates; no actual HTTP or container."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import json
import re
import test_harbor_normalization_producer as fixture_module

import pytest
from scripts.batch import harbor_environment_producer as P
from scripts.batch import harbor_profile_producer as profile
from scripts.batch import harbor_readiness as R
from scripts.batch import harbor_normalization_producer as N
from scripts.batch.harbor_admission import record,read
from test_harbor_normalization_producer import setup,mock_review
import xl_output_task as builder
import xl_variable_source_audit as audit
from grader.tests.test_variable_source_audit import GOOD_MODEL_TABLE,write_two_variable_fixture


def current_case(setup,monkeypatch):
    table=re.sub(r'https?://[^\s|)]+', 'internal://supplied-workbook/model', fixture_module.GOOD_MODEL_TABLE)
    monkeypatch.setattr(fixture_module,'GOOD_MODEL_TABLE',table)
    n,campaign,*_=setup();prepared=N.prepare(n);mock_review(monkeypatch,n)
    call=N.dispatch(n,prepared,campaign,credential='fixture-unused').call
    completed=N.read(N.emit_phase1(n,prepared,call))
    b=dict(n.bindings)
    for key,name in [('normalization_draft','draft.json'),('normalized_final','normalized.json'),('normalization_report','normalization_report.json'),('normalization_exclusions','exclusions.json')]:b[key]=record(n.run/name)
    b['normalized_phase1']=completed['phase1']
    content=R.Selection(n.workbook,n.run.parent.parent/'stage'/(n.workbook+'-outputs'),'0'*64,b,n.run,n.run.parent/'nat',{}, {},normalization_prepared=prepared,normalization_call=call,model_policy=n.policy)
    root=n.run.parent.parent
    P.journal.save(root/'captures.json',{'schema_version':'harbor-profile-captures/v1','sources':[]})
    P.journal.save(root/'profile-input.json',{'schema_version':profile.INPUT_SCHEMA,'workbook_id':n.workbook,'original_source':b['original_raw_source'],'phase1':b['normalized_phase1'],'inventory':b['variable_source_inventory'],'captures':record(root/'captures.json'),'policy':asdict(n.policy),'campaign':{'root':str(campaign.root),'slot_root':str(campaign.slot_root),'model_slots':2}})
    result=profile.run(profile.prepare(record(root/'profile-input.json'),root/'profile-prepared'))
    assert result['status']=='COMPLETE'
    return content,result['selection'],root


def test_phase2_uses_reviewed_profile_and_preserves_first_phase(setup,monkeypatch):
    content,profiles,root=current_case(setup,monkeypatch)
    raw=Path(content.bindings['normalized_phase1']['path']).read_bytes()
    final,receipt=P.finish_normalization(content,profiles,root/'phase2')
    assert Path(content.bindings['normalized_phase1']['path']).read_bytes()==raw
    assert read(final.bindings['normalized_final'])['variables']==read(content.bindings['normalized_phase1'])['variables']
    assert read(receipt)['lane_approval'] is False
    assert P.finish_normalization(content,profiles,root/'phase2')[1]==receipt


def test_interrupted_phase2_replays_only_known_old_or_new_bytes(setup,monkeypatch):
    content,profiles,root=current_case(setup,monkeypatch)
    def stop(name):
        if name=='normalized.json':raise RuntimeError('simulated process loss')
    with pytest.raises(RuntimeError,match='process loss'):P.finish_normalization(content,profiles,root/'phase2',fault_hook=stop)
    assert not (root/'phase2/complete.json').exists()
    final,receipt=P.finish_normalization(content,profiles,root/'phase2')
    assert read(receipt)['outputs']['normalized.json']==final.bindings['normalized_final']


@pytest.mark.parametrize('damage',['current','snapshot','profile','phase1'])
def test_phase2_corruption_cannot_be_recovered_as_interruption(setup,monkeypatch,damage):
    content,profiles,root=current_case(setup,monkeypatch)
    def stop(name):raise RuntimeError('process loss')
    with pytest.raises(RuntimeError):P.finish_normalization(content,profiles,root/'phase2',fault_hook=stop)
    path={'current':content.normalization_run/'exclusions.json','snapshot':root/'phase2/exclusions.json','profile':Path(profiles.profiles['path']),'phase1':Path(content.bindings['normalized_phase1']['path'])}[damage]
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises((ValueError,KeyError)):P.finish_normalization(content,profiles,root/'phase2')
    assert not (root/'phase2/complete.json').exists()


def test_packaging_pins_custom_roots_and_has_no_model_generation_mode(setup,monkeypatch):
    content,profiles,root=current_case(setup,monkeypatch)
    _,args=P.packaging_arguments(content,root/'package',{'path':str(root/'taxonomy.json')})
    args=list(map(str,args))
    for flag in ['--require-cached-variable-source-audit','--no-naturalize','--source-generation-id','--segmentation-generation-id']:assert flag in args
    assert args[args.index('--source-generation-root')+1]==str(Path(content.bindings['source_generation']['path']).parents[2])
    assert args[args.index('--seg-root')+1]==str(Path(content.bindings['segmentation_generation']['path']).parents[2])
    assert '--no-variable-source-audit' not in args


def args(**changes):
    values=dict(require_cached_variable_source_audit=True,no_naturalize=True,no_variable_source_audit=False,refresh_variable_source_audit=False,env_file='must-not-read.env',model='model',project_id='project')
    values.update(changes);return SimpleNamespace(**values)


def test_cached_packaging_never_reads_credentials(monkeypatch):
    monkeypatch.setattr(builder,'read_env_key',lambda *a:pytest.fail('credential read'))
    assert builder.model_config(args())['api_key']==''
    assert builder.model_config(args())['enabled'] is False


@pytest.mark.parametrize('change',[{'no_naturalize':False},{'no_variable_source_audit':True},{'refresh_variable_source_audit':True}])
def test_cached_packaging_rejects_conflicting_generation_or_audit_skip(change):
    with pytest.raises(ValueError,match='cached-only'):builder.model_config(args(**change))


def test_cached_packaging_full_audit_validation_has_no_transport_fallback(tmp_path,monkeypatch):
    source,seg=write_two_variable_fixture(tmp_path);run=tmp_path/'audit'
    monkeypatch.setattr(audit,'_call_model',lambda *a,**k:(GOOD_MODEL_TABLE,'stop'))
    audit.generate_audit('0001',source,seg,run,'synthetic-fixture')
    monkeypatch.setattr(audit,'_call_model',lambda *a,**k:pytest.fail('unexpected model dispatch'))
    key=builder.model_config(args())['api_key']
    assert audit.generate_audit('0001',source,seg,run,key)['cache_hit']
    (run/'0001-inputs-variable-sources.md').write_text('changed audit')
    with pytest.raises(RuntimeError,match='no lbx_api_key'):audit.generate_audit('0001',source,seg,run,key)


@pytest.mark.parametrize('where',['equal','below_stage','above_stage'])
def test_packaging_rejects_journal_overlap_before_any_write(tmp_path,where):
    stage=tmp_path/'0001-outputs'
    work={'equal':stage,'below_stage':stage/'journal','above_stage':tmp_path}[where]
    content=SimpleNamespace(stage=stage,workbook='0001')
    with pytest.raises(ValueError,match='disjoint'):P.package(content,None,work,None)
    assert not stage.exists()
