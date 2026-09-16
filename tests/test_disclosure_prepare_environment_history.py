from pathlib import Path
import json
import pytest
from scripts.fleet import disclosure_review_prepare as P,retained_runtime as RT


def save(path,value):
 path.write_text(json.dumps(value));return P.binding(path)


@pytest.mark.parametrize('fault',[None,'extra_function','foreign_entry','wrong_function','current_only','consumer'])
def test_only_exact_completed_environment_symbol_is_scoped_and_restored(tmp_path,fault):
 old=tmp_path/'environment_checkpoint_continue.py';old.write_text("def takeover_context():return 'old'\ndef untouched():return 'same'\n")
 new=tmp_path/'selected.py';new.write_text("def takeover_context():return 'new'\ndef untouched():return 'changed'\n"if fault=='extra_function'else"def takeover_context():return 'new'\ndef untouched():return 'same'\n")
 entry=tmp_path/'dcc.py';entry.write_text('')
 environment=save(tmp_path/'environment.json',dict(schema_version='fleet-completed-environment-continuation/v1',entry=P.binding(old),takeover_completion={'exact':'completion'}))
 original=save(tmp_path/'dcc.json',dict(context=dict(plan=environment,function='completed_context',kwargs={'probe':True},projection=dict(dispatcher=1,environment=2))))
 loader=dict(helper=P.binding(entry),plan=original,function='context',kwargs={'continued':True})
 symbol=dict(module_name='retained_completed_environment_original',original=P.binding(old),selected=P.binding(new),names=['takeover_context'])
 profile=dict(schema_version=RT.SCHEMA,runtime=P.binding(RT.__file__),entrypoint=loader['helper'],read_call=dict(function='context',args=[original],kwargs={'continued':True}),patches=[],dependencies=[],guard_modules=[],child_plans=[],journal_roots=[],symbols=[symbol])
 if fault=='foreign_entry':symbol['original']=P.binding(entry)
 elif fault=='wrong_function':symbol['names']=['untouched']
 elif fault=='current_only':profile['read_call']['kwargs']={'continued':False}
 p=dict(completed_custody={},completed_environment_runtime=dict(helper=P.binding(RT.__file__),profile=save(tmp_path/'runtime.json',profile)))
 if fault not in (None,'consumer'):
  with pytest.raises(ValueError):P.completed_environment_runtime(p,loader)
  return
 holder=[]
 def run():
  with P.completed_environment_runtime(p,loader):
   module=RT.load_module('test_exact_environment',P.binding(old));holder.append(module)
   assert module.takeover_context()=='new'and module.untouched()=='same'
   if fault=='consumer':raise RuntimeError('consumer failed')
 if fault=='consumer':
  with pytest.raises(RuntimeError,match='consumer'):run()
 else:run()
 assert holder[0].takeover_context()=='old'
