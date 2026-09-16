import json
from pathlib import Path
import pytest
from scripts.fleet import normalization_remaining as N


def save(p,value):
    p.write_text(json.dumps(value));return N.binding(p)


def fixture(tmp_path):
    old=tmp_path/'original';old.mkdir();new=tmp_path/'selected.py'
    text='CONST=3\ndef coalesce_same_atom_aliases(rows,included):\n return rows,included,{}\ndef untouched():\n return CONST\n'
    (old/'gen_normalizer.py').write_text(text);new.write_text(text.replace('return rows,included,{}','return rows,included,{"retained": True}'))
    rt=tmp_path/'runtime.py';rt.write_text('declared=1')
    p=dict(runtime=N.binding(rt),patches=[],dependencies=[],guard_modules=[],child_plans=[],journal_roots=[],
        symbols=[dict(module_name='gen_normalizer',names=['coalesce_same_atom_aliases'],original=N.binding(old/'gen_normalizer.py'),selected=N.binding(new))])
    return old,new,p,N.binding(rt)


def test_exact_function_change_preserves_all_other_generator_ast(tmp_path):
    old,new,p,helper=fixture(tmp_path);row=save(tmp_path/'profile.json',p)
    assert N.generator_delta(row,old,helper)==p['symbols'][0]
    assert 'return rows,included,{}'in(old/'gen_normalizer.py').read_text()


@pytest.mark.parametrize('change', ['constant','other-function','import','extra-function','second-symbol','dependency','wrong-function'])
def test_rejects_unrelated_generator_or_runtime_changes(tmp_path,change):
    old,new,p,helper=fixture(tmp_path)
    if change=='constant':new.write_text(new.read_text().replace('CONST=3','CONST=4'))
    elif change=='other-function':new.write_text(new.read_text().replace('return CONST','return 7'))
    elif change=='import':new.write_text('import os\n'+new.read_text())
    elif change=='extra-function':new.write_text(new.read_text()+'\ndef extra():\n return 1\n')
    elif change=='second-symbol':p['symbols'].append(dict(p['symbols'][0]))
    elif change=='dependency':p['dependencies']=[dict(module_name='any')]
    else:p['symbols'][0]['names']=['untouched']
    p['symbols'][0]['selected']=N.binding(new)
    with pytest.raises(ValueError):N.generator_delta(save(tmp_path/'profile.json',p),old,helper)


def test_changed_runtime_profile_bytes_and_parent_links_rejected(tmp_path):
    old,new,p,helper=fixture(tmp_path);row=save(tmp_path/'profile.json',p)
    new.write_text(new.read_text()+'\n')
    with pytest.raises(ValueError,match='bytes changed'):N.generator_delta(row,old,helper)
    alias=tmp_path/'alias';alias.symlink_to(old,target_is_directory=True)
    with pytest.raises(ValueError,match='canonical'):N.binding(alias/'gen_normalizer.py')


@pytest.fixture
def stopped(tmp_path):
    from types import SimpleNamespace
    home=tmp_path/'old';d=home/'stages/emit';d.mkdir(parents=True)
    run=home/'normalization';run.mkdir();prep=home/'stages/prepare';prep.mkdir()
    helper=tmp_path/'original.py';helper.write_text('original=True')
    previous=save(tmp_path/'previous.json',{'home':str(home)})
    prepared=save(tmp_path/'prepared.json',{'original':'partition'})
    source={'path':'/original/source','sha256':'a'*64}
    intent=save(d/'intent.json',dict(action='emit',arguments=dict(prepared=prepared),plan=previous,
        driver=N.binding(helper),seconds=N.SECONDS,memory=N.MEMORY))
    (d/'step.log').write_text('retained alias conflict')
    guard=save(d/'resource.json',dict(returncode=1,failure_type=None,intent=intent,log=N.binding(d/'step.log')))
    result=save(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook='0001',plan=previous,
        failure_type='ValueError',publication_performed=False,automatic_retry=False))
    job=save(tmp_path/'job.json',dict(driver=N.binding(helper),source=source,workbook='0001',result=str(home/'result.json')))
    wi=save(tmp_path/'worker-intent.json',dict(job=job))
    outer=save(tmp_path/'outcome.json',dict(returncode=1,failure_type=None,result=result,intent=wi,automatic_retry=False))
    survey=dict(at=1,claims=0,models=0,processes=[],files=[dict(native=x,copy=x)for x in(guard,outer)])
    stop=save(tmp_path/'stopped.json',survey)
    save(prep/'result.json',dict(value=prepared))
    save(run/'normalization-review.json',{'valid':True,'actual_fixture':True})
    p=dict(failed_guard=guard,prior_outcome=outer,stopped=stop,prepared=prepared,previous=previous,
        previous_helper=N.binding(helper),source=source,workbook='0001')
    calls=[]
    def aggregate(bound):
        calls.append(bound);return {'review':{'valid':True,'actual_fixture':True}}
    P=SimpleNamespace(selection=lambda b:SimpleNamespace(run=run),aggregate=aggregate)
    return p,dict(home=str(home)),P,calls,run,survey


def test_completed_reviews_revalidated_without_dispatch(stopped):
    p,old,P,calls,run,survey=stopped
    assert N.stopped_emission(p,old,None,P)['review']['valid']
    assert calls==[p['prepared']]and not (run/'normalized.json').exists()


@pytest.mark.parametrize('state',['uncertain-worker','live-process','emitted-output','started-refresh','changed-review','changed-evidence'])
def test_stopped_boundary_never_replays_uncertain_or_advanced_work(stopped,state,tmp_path):
    p,old,P,calls,run,survey=stopped
    if state=='uncertain-worker':
        v=N.read(p['prior_outcome']);v['returncode']=None
        p['prior_outcome']=save(tmp_path/'uncertain.json',v)
    elif state=='live-process':
        survey['processes']=[{'pid':123}];p['stopped']=save(tmp_path/'live.json',survey)
    elif state=='emitted-output':(run/'normalized.json').write_text('{}')
    elif state=='started-refresh':(Path(old['home'])/'refresh').mkdir()
    elif state=='changed-review':(run/'normalization-review.json').write_text('{"valid":false}')
    else:Path(p['failed_guard']['path']).write_text('{}')
    with pytest.raises(ValueError):N.stopped_emission(p,old,None,P)
    assert len(calls)==(1 if state=='changed-review'else 0)


def test_optional_source_id_delta_is_closed_to_one_original_function(tmp_path):
    old,new,p,helper=fixture(tmp_path);(old/'mcp_env').mkdir()
    original=old/'mcp_env/build.py';original.write_text('CONST=1\ndef source_id(source):\n return source["id"][:72]\ndef untouched():\n return CONST\n')
    selected=tmp_path/'build.py';selected.write_text(original.read_text().replace('source["id"][:72]','source["id"][-72:]'))
    p['symbols'].append(dict(module_name='mcp_env.build',names=['source_id'],original=N.binding(original),selected=N.binding(selected)))
    assert N.generator_delta(save(tmp_path/'optional.json',p),old,helper)==p['symbols'][0]
    selected.write_text(selected.read_text().replace('CONST=1','CONST=2'));p['symbols'][1]['selected']=N.binding(selected)
    with pytest.raises(ValueError,match='beyond'):N.generator_delta(save(tmp_path/'bad-optional.json',p),old,helper)
