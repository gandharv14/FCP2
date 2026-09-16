import ast
import importlib.util
import json
import hashlib
import copy
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
from scripts.fleet import publish as P, source_publication as S
from scripts.fleet.build_source_publication import build


@pytest.fixture
def case(tmp_path):
    tmp_path = tmp_path.resolve()
    files = {'code.py': hashlib.sha256(b'immutable = True\n').hexdigest()}
    fingerprint = hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    root = tmp_path/fingerprint; root.mkdir()
    (root/'code.py').write_text('immutable = True\n')
    P.retain(root/'.pipeline-code.json',files)
    snap = P.retain(tmp_path/'snapshot.json', dict(root=str(root), fingerprint=root.name, files=files))
    history = P.retain(tmp_path/'history.json', dict(schema_version='fleet-source-to-first-authoring-history/v1',workbook='0036',history_roots=['/retained/full/root'],known_ids=['0036']))
    dispatcher = P.binding(root/'code.py')
    selected = P.retain(tmp_path/'selected.json', dict(schema_version='fleet-source-to-first-authoring-plan/v1',workbook='0036',snapshot=snap,history=history,dispatcher=dispatcher))
    frozen = P.retain(tmp_path/'owning-code.json',dict(root=str(root),files=[dict(relative=n,sha256=P.sha(root/n)) for n in sorted(['.pipeline-code.json','code.py'])]))
    config = P.retain(tmp_path/'config.json', dict(code=frozen))
    job = P.retain(tmp_path/'job.json', dict(config=config,driver=dispatcher))
    value = dict(status='PUBLICATION_READY',publication_performed=False,automatic_retry=False,workbook='0036',plan=selected,source_history=history,config=config,job=job)
    ready = P.retain(tmp_path/'ready.json',value)
    return dict(workbook='0036',code_root=str(root),source_authoring=selected,ready=ready), tmp_path, value


def validate(plan):
    return S.history_binding(plan, checked=P.checked, binding=P.binding, require=P.require)


def test_selected_completed_source_ready(case):
    plan, _, ready = case
    assert validate(plan) == ready['source_history']


@pytest.mark.parametrize('key,value', [('status','RETRY_READY'),('publication_performed',True),('automatic_retry',True),('workbook','0200'),('plan',{}),('source_history',{})])
def test_unqualified_or_detached_ready_fails(case,key,value):
    plan,tmp,ready = case
    ready[key]=value;plan['ready']=P.retain(tmp/'bad-ready.json',ready)
    with pytest.raises(ValueError):validate(plan)


@pytest.mark.parametrize('change', ['extra','tamper','missing','symlink'])
def test_every_snapshot_byte_and_path_is_bound(case,change):
    plan,_,_=case;root=Path(plan['code_root'])
    if change=='extra':(root/'unselected.py').write_text('extra')
    elif change=='tamper':(root/'code.py').write_text('changed')
    elif change=='missing':(root/'code.py').unlink()
    else:(root/'alias.py').symlink_to(root/'code.py')
    with pytest.raises(ValueError):validate(plan)


@pytest.mark.parametrize('key',['history','continuation_history','source_continuation'])
def test_mixed_authority_rejected(case,key):
    plan,_,_=case;plan[key]={}
    with pytest.raises(ValueError,match='mixed'):validate(plan)


def test_owning_config_requires_ordered_whole_snapshot(case):
    plan,tmp,ready=case
    bad=P.retain(tmp/'bad-code.json',dict(root=plan['code_root'],files=[]))
    config=P.retain(tmp/'bad-config.json',dict(code=bad))
    ready['config']=config;ready['job']=P.retain(tmp/'bad-job.json',dict(config=config,driver=P.binding(Path(plan['code_root'])/'code.py')))
    plan['ready']=P.retain(tmp/'bad-ready.json',ready)
    with pytest.raises(ValueError,match='configuration'):validate(plan)


def test_environment_and_unchanged_publication_collection(case,monkeypatch):
    plan,tmp,_=case
    manifest=P.checked(build(tmp/'publisher',P.__file__))
    spec=importlib.util.spec_from_file_location('test_source_publisher',manifest['publisher']['path']);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setenv('HARBOR_FLEET_MODEL_JOURNAL','old')
    monkeypatch.setenv('HARBOR_FLEET_HISTORY_PATH','old')
    monkeypatch.setenv('HARBOR_FLEET_SOURCE_HISTORY_PATH','old')
    monkeypatch.setenv('LITELLM_API_KEY','synthetic')
    auth=dict(workbook='0036',owner='new-publication',token='synthetic',fence=7)
    env=module.publication_environment(plan,auth,action='remote',url='http://127.0.0.1:1',token_file=tmp/'unused')
    assert env['HARBOR_FLEET_SOURCE_HISTORY_PATH']==validate(plan)['path']
    assert env['HARBOR_FLEET_OWNER']=='new-publication' and env['HARBOR_FLEET_FENCE']=='7'
    assert not any(k in env for k in ['HARBOR_FLEET_MODEL_JOURNAL','HARBOR_FLEET_HISTORY_PATH','LITELLM_API_KEY'])
    def funcs(code):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(code).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    old=funcs(Path(P.__file__).read_text());new=funcs(Path(manifest['publisher']['path']).read_text())
    assert {k for k in old if old[k]!=new[k]}=={'history_binding','publication_environment','remote_child'}
    assert "from scripts.fleet.source_authoring_history import survey as projected_survey" in Path(manifest['publisher']['path']).read_text()
    # The exact live owning certificate reader is invoked; the adapter never
    # replaces it with a preflight-only or synthetic claim authority.
    assert "projected_survey(tuple(map(Path, certificate['history_roots']" in Path(manifest['publisher']['path']).read_text()


def test_patch_rejects_unreviewed_publisher():
    with pytest.raises(ValueError):S.patch('def history_binding(plan): pass')


@pytest.fixture
def operation():
    plan=dict(workbook='0036',code_root='/code/'+'a'*64);bound=dict(path='/selected-plan',sha256='b'*64)
    auth=dict(workbook='0036',owner='publication',fence=2,token='synthetic')
    identity=hashlib.sha256(json.dumps(dict(workbook='0036',request_sha256=bound['sha256']),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    row=dict(workbook='0036',state='active',owner=auth['owner'],fence=2,expires=200,version='a'*64,source_sha256='source')
    op=dict(kind='publication',state='active',workbook='0036',owner=auth['owner'],fence=2,expires=200,identity=identity)
    return dict(at=100,model_limit=1,publication_limit=1,workbooks=[row],operations=[op]),auth,bound,plan


def status_validate(v):return S.validate_status(*v,'source',now=100,require=P.require)


def test_live_selected_publication_operation(operation):assert status_validate(operation)['fence']==2


@pytest.mark.parametrize('kind,field,value', [('workbooks','version','old'),('workbooks','source_sha256','other'),('workbooks','fence',1),('workbooks','owner','old'),('workbooks','expires',100),('operations','identity','other'),('operations','state','hold'),('operations','kind','model'),('operations','expires',100),('operations','fence',1)])
def test_foreign_stale_uncertain_operation_fails(operation,kind,field,value):
    operation[0][kind][0][field]=value
    with pytest.raises(ValueError):status_validate(operation)


def test_known_other_model_can_coexist_with_serialized_publication(operation):
    status=operation[0];row=dict(workbook='0013',state='active',owner='other',fence=3,expires=200)
    status['workbooks'].append(row);status['operations'].append(dict(row,kind='model'))
    status_validate(operation)
    status['workbooks'][1]['fence']=4
    with pytest.raises(ValueError,match='concurrent'):status_validate(operation)


@pytest.mark.parametrize('failure',[None,'tamper','foreign-cache'])
def test_actual_preimport_helper_boundary(tmp_path,monkeypatch,failure):
    root=tmp_path.resolve();helper=root/'author_workbook.py';helper.write_text('value = 123\n');bound=P.binding(helper)
    preparer=root/'preparer.py';preparer.write_text('prepared = False\n');selected=P.retain(root/'selected.json',dict(preparer=P.binding(preparer)))
    monkeypatch.setattr(S,'AUTHOR_HELPER',bound);monkeypatch.delitem(sys.modules,'author_workbook',raising=False)
    if failure=='tamper':helper.write_text('value = 999\n')
    if failure=='foreign-cache':monkeypatch.setitem(sys.modules,'author_workbook',SimpleNamespace(__file__=str(preparer)))
    seen=[]
    def load(name,row):
        seen.append(row);return P.load(name,row)
    if failure:
        with pytest.raises(ValueError):S.preload(dict(source_authoring=selected),checked=P.checked,binding=P.binding,load=load,require=P.require)
        assert not seen
    else:
        result=S.preload(dict(source_authoring=selected),checked=P.checked,binding=P.binding,load=load,require=P.require)
        assert result.prepared is False and sys.modules['author_workbook'].value==123
        assert seen==[bound,P.binding(preparer)]
    sys.modules.pop('author_workbook',None)

@pytest.mark.parametrize('fault', ['missing_manifest','changed_manifest','self_entry','wrong_fingerprint'])
def test_canonical_manifest_identity_is_validated(case,fault):
    plan,tmp,_=case;root=Path(plan['code_root']);selected=P.checked(plan['source_authoring']);snap=P.checked(selected['snapshot'])
    if fault=='missing_manifest':
        (root/'.pipeline-code.json').unlink()
    elif fault=='changed_manifest':
        (root/'.pipeline-code.json').write_text('{}')
    else:
        if fault=='self_entry':snap['files']['.pipeline-code.json']=P.sha(root/'.pipeline-code.json')
        else:snap['files']['code.py']='0'*64
        selected['snapshot']=P.retain(tmp/'wrong-snapshot.json',snap)
        plan['source_authoring']=P.retain(tmp/'wrong-selection.json',selected)
    with pytest.raises((ValueError,FileNotFoundError)):validate(plan)


def authorized_unbounded_policy(status):
    status['model_limit']=None
    status['model_policy']=dict(schema_version='fleet-concurrency-policy/v1',revision=1,model_limit=None,
        authorization=dict(path='/root/retained-authorization.json',sha256='4cf7d589371b8b7805b34658f801884ac769ea8bc69009631610975e32c94263'),
        change_id='remove-global-model-limit-20260913-001',per_workbook_model_limit=1,
        unknown_model_hold_barrier='global',publication_limit=1)


def test_authorized_many_other_books_can_run_models_during_one_publication(operation):
    status=operation[0];authorized_unbounded_policy(status)
    for wb in ['0013','0200','0064']:
        owner=dict(workbook=wb,state='active',owner='owner-'+wb,fence=4,expires=200)
        status['workbooks'].append(owner);status['operations'].append(dict(owner,kind='model'))
    assert status_validate(operation)['workbook']=='0036'
    status['operations'].append(dict(status['operations'][-1]))
    with pytest.raises(ValueError,match='occupancy'):status_validate(operation)


@pytest.mark.parametrize('fault',['no_policy','wrong_authorization','wrong_revision','publication2','same_book','held','owner_mismatch'])
def test_new_policy_cannot_weaken_publication_or_unknown_operation_guards(operation,fault):
    status=operation[0];authorized_unbounded_policy(status)
    if fault=='no_policy':status.pop('model_policy')
    elif fault=='wrong_authorization':status['model_policy']['authorization']['sha256']='0'*64
    elif fault=='wrong_revision':status['model_policy']['revision']=2
    elif fault=='publication2':status['publication_limit']=2
    else:
        wb='0036' if fault=='same_book' else '0013'
        row=dict(workbook=wb,state='active',owner='other',fence=3,expires=200)
        if wb!='0036':status['workbooks'].append(row)
        op=dict(row,kind='model')
        if fault=='held':op['state']='hold'
        if fault=='owner_mismatch':op['owner']='unmatched'
        status['operations'].append(op)
    with pytest.raises(ValueError):status_validate(operation)


def test_policy_dependency_is_hash_checked_before_import(tmp_path,monkeypatch):
    q=tmp_path/'policy.py';q.write_text("raise AssertionError('must never execute tampered dependency')")
    monkeypatch.setattr(S,'CAPACITY_POLICY',dict(path=str(q),sha256=S.CAPACITY_POLICY_SHA))
    with pytest.raises(ValueError,match='policy changed'):S.capacity_policy()

@pytest.mark.parametrize('bad',[None,'unknown-owner','target','publication'])
def test_unrelated_model_uncertainty_remains_visible_during_publication(operation,bad):
    status=operation[0];row=dict(workbook='0413',state='hold',owner='other',fence=3,expires=90)
    status['workbooks'].append(row);op=dict(row,kind='model');status['operations'].append(op)
    if bad=='unknown-owner':op['owner']='lost'
    elif bad=='target':op['workbook']='0036'
    elif bad=='publication':op['kind']='publication'
    before=copy.deepcopy(status)
    if bad:
        with pytest.raises(ValueError):status_validate(operation)
    else:status_validate(operation)
    assert status==before
