from pathlib import Path
from types import ModuleType,FunctionType
import json,sys,subprocess
import pytest
from scripts.fleet import retained_runtime as R

def put(path,value):
    path.write_text(json.dumps(value));return R.binding(path)

@pytest.fixture
def fixture(tmp_path):
    owning=tmp_path/'owning.py';owning.write_text("def _boundary(*a,**k):raise ValueError('pending or uncertain next step cannot renew')\n")
    reader=tmp_path/'reader.py';reader.write_text("def completed_history(*a,**k):return ('historical',list(a))\n")
    consumer=tmp_path/'consumer.py';consumer.write_text("import importlib.util\ns=importlib.util.spec_from_file_location('retained_test_owner',"+repr(str(owning))+");R=importlib.util.module_from_spec(s);s.loader.exec_module(R)\ndef context(plan,*,recover=False,full_prefix=True):\n return R._boundary(plan)\n")
    module=R.load_module('retained_test_consumer',R.binding(consumer));plan={'exact':'historical'}
    patch=dict(module=R.binding(consumer),function='context',function_sha256=R.source_hash(module.context),owner_name='R',owning_module=R.binding(owning),reader=R.binding(reader),invocation=dict(args=[plan],kwargs=dict(recover=True,full_prefix=False)))
    profile=dict(schema_version=R.SCHEMA,runtime=R.binding(R.__file__),entrypoint=R.binding(consumer),read_call=dict(function='context',**patch['invocation']),patches=[patch],guard_modules=[],child_plans=[],journal_roots=[str(tmp_path)],dependencies=[],symbols=[])
    return dict(root=tmp_path,consumer=consumer,module=module,owning=owning,reader=reader,patch=patch,profile=profile,row=put(tmp_path/'profile.json',profile))

def test_exact_historical_invocation_only_and_original_restored(fixture):
    v=fixture;old=v['module'].context;raw=v['consumer'].read_bytes()
    with R.installed(v['row']):
        assert v['module'].context({'exact':'historical'},recover=True,full_prefix=False)==('historical',[{'exact':'historical'}])
        with pytest.raises(ValueError,match='pending or uncertain'):v['module'].context({'exact':'historical'})
        with pytest.raises(ValueError,match='pending or uncertain'):v['module'].context({'changed':True},recover=True,full_prefix=False)
    assert v['module'].context is old and v['consumer'].read_bytes()==raw


def test_actual_cold_bootstrap_reads_witnessed_function(fixture):
    v=fixture;row=v['row'];p=subprocess.run([sys.executable,'-I','-B',R.__file__,'check','--profile',row['path'],'--sha256',row['sha256']],capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    value=json.loads(p.stdout);assert value['status']=='RETAINED_CONTEXT_VALIDATED'and len(value['patches'])==1

@pytest.mark.parametrize('fault',['function','producer','reader','multiple','owning-function'])
def test_unbound_or_owning_change_rejected(fixture,fault):
    v=fixture;p=dict(v['patch'])
    if fault=='function':p['function_sha256']='0'*64
    elif fault=='producer':p['module']=dict(p['module'],sha256='0'*64)
    elif fault=='reader':p['reader']=dict(p['reader'],sha256='0'*64)
    elif fault=='owning-function':
        text=v['consumer'].read_text().replace('return R._boundary(plan)','R.authorize(plan)\n return R._boundary(plan)');v['consumer'].write_text(text);v['module']=R.load_module('changed_owning_consumer',R.binding(v['consumer']));p['module']=R.binding(v['consumer']);p['function_sha256']=R.source_hash(v['module'].context)
    else:
        v['consumer'].write_text(v['consumer'].read_text().replace('return R._boundary(plan)','R._boundary(plan)\n return R._boundary(plan)'));v['module']=R.load_module('changed_double_consumer',R.binding(v['consumer']));p['module']=R.binding(v['consumer']);p['function_sha256']=R.source_hash(v['module'].context)
    with pytest.raises(ValueError):R.history_function(v['module'],p)


def test_guard_propagates_profile_without_changing_original_options(fixture):
    v=fixture;guard=v['root']/'guard.py';guard.write_text('def _run_in_process_group(command,**options):return command,options\n');g=R.load_module('retained_test_guard',R.binding(guard));p=v['profile'];p['guard_modules']=[R.binding(guard)];plan=put(v['root']/'child-plan.json',{});p['child_plans']=[plan];row=put(v['root']/'guard-profile.json',p);intent=put(v['root']/'intent.json',{'exact':'owning intent'});cmd=[sys.executable,'-I','-B',p['entrypoint']['path'],'owning-child','--plan',plan['path'],'--plan-sha256',plan['sha256'],'--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']];options=dict(timeout_seconds=3300,memory_limit_bytes=8589934592,grace_seconds=3,guardian_lock_fds=(7,9),env={'fixture':'unchanged'})
    with R.installed(row):
        selected,saved=g._run_in_process_group(cmd,**options)
        assert saved==options and selected[3]==R.__file__
        assert g._run_in_process_group(['docker','unrelated'],**options)==(['docker','unrelated'],options)
        with pytest.raises(FileExistsError):g._run_in_process_group(cmd,**options)
    side=R.checked(R.binding(v['root']/'retained-runtime-bootstrap.json'));assert side['logical_command']==cmd and side['physical_command']==selected and side['profile']==row


def test_dependency_rebinding_updates_canonical_package_and_warm_globals(fixture):
    v=fixture;name='scripts.retained_test_dependency';old=v['root']/'old_dependency.py';new=v['root']/'new_dependency.py';old.write_text('version=1\n');new.write_text('version=2\n');module=R.load_module(name,R.binding(old));import scripts;scripts.retained_test_dependency=module
    consumer=ModuleType('retained_warm_consumer');consumer.life=module;namespace={'life':module};exec('def version():return life.version',namespace);consumer.version=namespace['version'];sys.modules[consumer.__name__]=consumer
    p=v['profile'];p['dependencies']=[dict(module_name=name,original=R.binding(old),selected=R.binding(new))];row=put(v['root']/'dependencies.json',p)
    try:
        with R.installed(row):
            assert sys.modules[name]is scripts.retained_test_dependency is consumer.life
            assert consumer.version()==2 and consumer.life.__file__==str(new)
        assert sys.modules[name]is scripts.retained_test_dependency is consumer.life is module
        assert consumer.version()==1
    finally:sys.modules.pop(name,None);sys.modules.pop(consumer.__name__,None);del scripts.retained_test_dependency

def test_cold_physical_child_runs_original_entry_with_declared_logical_argv(fixture):
    v=fixture;v['consumer'].write_text(v['consumer'].read_text()+"\nif __name__=='__main__':\n import sys,json\n print(json.dumps({'argv':sys.argv,'value':context({'exact':'historical'},recover=True,full_prefix=False)}))\n")
    p=v['profile'];p['entrypoint']=p['patches'][0]['module']=R.binding(v['consumer']);guard=v['root']/'childguard.py';guard.write_text('def _run_in_process_group(command,**options):return command\n');g=R.load_module('retained_cold_guard',R.binding(guard));p['guard_modules']=[R.binding(guard)];plan=put(v['root']/'selected-plan.json',{});p['child_plans']=[plan];row=put(v['root']/'cold-profile.json',p);intent=put(v['root']/'intent.json',{});logical=[sys.executable,'-I','-B',p['entrypoint']['path'],'owning-child','--plan',plan['path'],'--plan-sha256',plan['sha256'],'--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']]
    with R.installed(row):physical=g._run_in_process_group(logical,timeout_seconds=3300,memory_limit_bytes=8589934592)
    result=subprocess.run(physical,capture_output=True,text=True,timeout=20);assert result.returncode==0,result.stderr
    value=json.loads(result.stdout);assert value['argv']==logical[3:]and value['value']==['historical',[{'exact':'historical'}]]
    bad=list(physical);bad[-1]='0'*64;result=subprocess.run(bad,capture_output=True,text=True,timeout=20);assert result.returncode!=0

def test_exact_function_symbol_rebind_covers_cached_imports(fixture):
    v=fixture;old=v['root']/'old_words.py';new=v['root']/'new_words.py';old.write_text('def clause_covered():return False\ndef untouched():return 1\n');new.write_text('def clause_covered():return True\ndef untouched():return 2\n');module=R.load_module('retained_aa_lib',R.binding(old));consumer=ModuleType('retained_dialogue_validator');consumer.clause_covered=module.clause_covered;namespace={'cached':module.clause_covered};exec('def validate():return cached()',namespace);consumer.validate=namespace['validate'];sys.modules[consumer.__name__]=consumer
    p=v['profile'];p['symbols']=[dict(module_name='retained_aa_lib',original=R.binding(old),selected=R.binding(new),names=['clause_covered'])];row=put(v['root']/'symbols.json',p);before=module.clause_covered
    try:
        with R.installed(row):
            assert consumer.validate()is True and consumer.clause_covered is module.clause_covered
            assert module.untouched()==1 and Path(module.__file__).read_bytes()==old.read_bytes()
        assert consumer.clause_covered is module.clause_covered is before and consumer.validate()is False
    finally:sys.modules.pop('retained_aa_lib',None);sys.modules.pop(consumer.__name__,None)

def test_factory_projection_covers_actual_returned_namespace(fixture):
    v=fixture;factory=v['root']/'factory.py';factory.write_text('from types import SimpleNamespace\ndef install():\n return SimpleNamespace(value=1)\n')
    module=R.load_module('retained_test_factory',R.binding(factory));reader=v['root']/'projection.py';reader.write_text('def validate(c):return c\ndef install_projection(m,c):\n m.value=c["sha256"]\n return m\n')
    certificate=put(v['root']/'certificate.json',{'bound':True})
    p=v['profile'];p['schema_version']=R.SCHEMA2;p['factories']=[dict(module=R.binding(factory),function='install',function_sha256=R.source_hash(module.install),reader=R.binding(reader),certificate=certificate)]
    row=put(v['root']/'factory-profile.json',p);original=module.install
    with R.installed(row):assert module.install().value==certificate['sha256']
    assert module.install is original and module.install().value==1
    p['factories'][0]['function_sha256']='0'*64;row=put(v['root']/'wrong-factory.json',p)
    with pytest.raises(ValueError,match='factory function'):
        with R.installed(row):pass

def test_multiple_declared_projections_compose_in_profile_order(fixture):
    v=fixture;factory=v['root']/'stack_factory.py';factory.write_text('from types import SimpleNamespace\ndef install():return SimpleNamespace(value=[])\n')
    module=R.load_module('retained_stack_factory',R.binding(factory));reader=v['root']/'stack_projection.py';reader.write_text('def validate(c):return c\ndef install_projection(m,c):\n m.value.append(c["sha256"])\n return m\n')
    rows=[]
    for n in (1,2):
        cert=put(v['root']/('certificate%d.json'%n),{'ordinal':n})
        rows.append(dict(module=R.binding(factory),function='install',function_sha256=R.source_hash(module.install),reader=R.binding(reader),certificate=cert))
    p=v['profile'];p.update(schema_version=R.SCHEMA2,factories=rows,patches=[]);row=put(v['root']/'stack.json',p)
    with R.installed(row):
        assert module.install().value==[r['certificate']['sha256']for r in rows]
        with R.installed(row):assert module.install().value==[r['certificate']['sha256']for r in rows]
        assert module.install().value==[r['certificate']['sha256']for r in rows]
    assert module.install().value==[]


def test_completed_history_import_keeps_current_custody_canonical(fixture):
    v=fixture
    old=v['root']/'old_takeover.py';new=v['root']/'new_takeover.py'
    old.write_text('version=2\ndef validate_completion(value):\n if value!="completed-2":raise ValueError("foreign completion")\n return version\n')
    new.write_text('version=3\ndef validate_completion(value):return version\n')
    consumer=v['root']/'completed_history.py'
    consumer.write_text('def _takeover_chain(value):\n from scripts.batch import harbor_lease_takeover as T\n return T.validate_completion(value)\n')
    m=R.load_module('retained_import_consumer',R.binding(consumer));original=m._takeover_chain
    import scripts.batch as package
    name='scripts.batch.harbor_lease_takeover';before=sys.modules.get(name);attr=getattr(package,'harbor_lease_takeover',None)
    current=R.load_module(name,R.binding(new));package.harbor_lease_takeover=current
    p=v['profile'];p.update(schema_version=R.SCHEMA3,patches=[],factories=[],historical_imports=[dict(module=R.binding(consumer),function='_takeover_chain',function_sha256=R.source_hash(original),dependency=dict(module_name=name,selected=R.binding(old)))])
    row=put(v['root']/'historical-imports.json',p)
    try:
        with R.installed(row):
            assert m._takeover_chain('completed-2')==2
            assert sys.modules[name]is package.harbor_lease_takeover is current
            assert current.validate_completion('current')==3
            with pytest.raises(ValueError,match='foreign completion'):m._takeover_chain('foreign')
            assert sys.modules[name]is package.harbor_lease_takeover is current
            with R.installed(row):assert m._takeover_chain('completed-2')==2
            assert sys.modules[name]is current
        assert m._takeover_chain is original and m._takeover_chain('completed-2')==3
    finally:
        if before is None:sys.modules.pop(name,None)
        else:sys.modules[name]=before
        if attr is None:del package.harbor_lease_takeover
        else:package.harbor_lease_takeover=attr


@pytest.mark.parametrize('fault',['function-hash','dependency-bytes','owning-call'])
def test_historical_import_rejects_changed_or_owning_function(tmp_path,fault):
    dep=tmp_path/'old.py';dep.write_text('version=2\n');pinned=R.load_module('old_for_import_test',R.binding(dep))
    source=tmp_path/'history.py';source.write_text('def _takeover_chain(value):\n from scripts.batch import harbor_lease_takeover as T\n return T.version\n')
    if fault=='owning-call':source.write_text(source.read_text().replace('return T.version','return T.replace_expired(value)'))
    module=R.load_module('history_for_import_test',R.binding(source));row=dict(module=R.binding(source),function='_takeover_chain',function_sha256=R.source_hash(module._takeover_chain),dependency=dict(module_name='scripts.batch.harbor_lease_takeover',selected=R.binding(dep)))
    if fault=='function-hash':row['function_sha256']='0'*64
    if fault=='dependency-bytes':
        wrapped=R.historical_import_function(module,row,pinned);dep.write_text('version=7\n')
        with pytest.raises(ValueError,match='bytes changed'):wrapped('completed')
    else:
        with pytest.raises(ValueError):R.historical_import_function(module,row,pinned)


def test_cold_historical_read_entry_is_explicit_and_owning_names_remain_rejected(fixture):
    v=fixture;p=dict(v['profile']);p['read_call']=dict(function='cold_context',args=[],kwargs={})
    row=put(v['root']/'cold-context.json',p)
    assert R.profile(row)['read_call']['function']=='cold_context'
    for name in ['run','authorize','replace_expired','child']:
        p['read_call']={**p['read_call'],'function':name}
        with pytest.raises(ValueError):R.profile(put(v['root']/(name+'.json'),p))


def test_refresh_dependency_keeps_prepare_validate_commit_history_module_identity(fixture):
    v=fixture;name='scripts.retained_test_refresh';old=v['root']/'old_refresh.py';new=v['root']/'new_refresh.py'
    source='def prepare():return {"code":__file__}\ndef validate(receipt):\n assert receipt["code"]==__file__\n return __file__\ndef commit(receipt):return validate(receipt)\ndef history(receipt):return validate(receipt)\n'
    old.write_text(source);new.write_text(source+'\n# prospective selected module\n')
    original=R.load_module(name,R.binding(old));import scripts;scripts.retained_test_refresh=original
    consumer=ModuleType('retained_refresh_consumer');consumer.refresh=original;sys.modules[consumer.__name__]=consumer
    p=v['profile'];p['dependencies']=[dict(module_name=name,original=R.binding(old),selected=R.binding(new))];row=put(v['root']/'coherent-refresh.json',p)
    try:
        with R.installed(row):
            selected=consumer.refresh;receipt=selected.prepare()
            assert selected is scripts.retained_test_refresh is sys.modules[name]
            assert receipt['code']==selected.validate(receipt)==selected.commit(receipt)==selected.history(receipt)==str(new)
            assert all(getattr(selected,n).__globals__ is selected.__dict__ for n in ('prepare','validate','commit','history'))
            with pytest.raises(AssertionError):selected.validate({'code':str(old)})
        assert consumer.refresh is original
    finally:
        sys.modules.pop(name,None);sys.modules.pop(consumer.__name__,None);del scripts.retained_test_refresh
