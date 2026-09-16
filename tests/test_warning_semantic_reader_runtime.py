"""A retained terminal reader is selected only over the exact original module."""
from pathlib import Path
import pytest
from scripts.fleet import retained_warning_continue as M
from scripts.fleet import retained_runtime as RT

@pytest.fixture
def selected(tmp_path):
    old=tmp_path/'old.py';new=tmp_path/'new.py'
    old.write_text("def semantic_current():return 'old'\ndef unchanged():return 'same'\n")
    new.write_text("def semantic_current():return semantic_terminal()\ndef unchanged():return 'same'\ndef semantic_terminal():return 'terminal'\n")
    helper={'path':'/original/U.py','sha256':'a'*64}
    fresh=M.keep(tmp_path/'fresh.json',dict(schema_version='fleet-retained-completed-disclosure/v1',helper=M.binding(old),semantic_successor=dict(helper=helper,output='/original/output')))
    oldplan=M.keep(tmp_path/'oldplan.json',dict(helper=helper,output='/original/output',fresh_plan=fresh,retained_context=dict(kind='completed_disclosure',plan=fresh)))
    profile=dict(schema_version=RT.SCHEMA,runtime=M.binding(RT.__file__),entrypoint=M.binding(M.__file__),
        read_call=dict(function='context',args=[],kwargs={}),patches=[],guard_modules=[],child_plans=[],journal_roots=[],dependencies=[],
        symbols=[dict(module_name='original_completed_reader',original=M.binding(old),selected=M.binding(new),names=['semantic_current'])])
    row=M.keep(tmp_path/'runtime.json',profile)
    p=dict(helper=M.binding(M.__file__),live_renewal=dict(plan=oldplan,completed_semantic=dict(runtime_profile=row)))
    return p,profile,old,new,tmp_path

@pytest.mark.parametrize('failure',[False,True])
def test_original_import_is_scoped_and_restored(selected,failure):
    p,profile,old,new,tmp=selected
    try:
        with M.semantic_context_runtime(p):
            loaded=M.module('original_completed_reader',M.binding(old))
            assert loaded.semantic_current()=='terminal'and loaded.unchanged()=='same'
            if failure:raise ValueError('original context failed')
    except ValueError:
        assert failure
    assert loaded.semantic_current()=='old'

@pytest.mark.parametrize('mutation',['unrelated-function','extra-symbol','wrong-original','wrong-entrypoint','guard','foreign-round'])
def test_undeclared_reader_change_rejected(selected,mutation):
    p,profile,old,new,tmp=selected
    if mutation=='unrelated-function':
        new.write_text(new.read_text().replace("'same'","'changed'"));profile['symbols'][0]['selected']=M.binding(new)
    elif mutation=='extra-symbol':profile['symbols'][0]['names'].append('unchanged')
    elif mutation=='wrong-original':profile['symbols'][0]['original']=M.binding(new)
    elif mutation=='wrong-entrypoint':profile['entrypoint']=M.binding(old)
    elif mutation=='guard':profile['guard_modules']=[M.binding(old)]
    else:
        oldplan=M.read(p['live_renewal']['plan']);oldplan['output']='/other'
        p['live_renewal']['plan']=M.keep(tmp/'other-plan.json',oldplan)
    p['live_renewal']['completed_semantic']['runtime_profile']=M.keep(tmp/'changed-runtime.json',profile)
    with pytest.raises(ValueError):M.semantic_context_runtime(p)
