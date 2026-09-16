"""Freeze a narrow original-v7 history reader successor, preserving task code."""
from pathlib import Path
import ast,hashlib,json
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
OLD=ROOT/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v7.py'
OLD_SHA='7c60e87d211e97f7145b6f05ee9cbd9a6a76a18f26c48d8172f2f57bf308156f'
def binding(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def build(destination):
 assert binding(OLD)['sha256']==OLD_SHA
 destination=Path(destination).absolute();destination.mkdir(parents=True,exist_ok=False)
 namespace=destination/'namespace.py';namespace.write_bytes((ROOT/'scripts/fleet/retained_environment_namespace.py').read_bytes())
 helper=destination/'continue.py';helper.write_bytes((ROOT/'scripts/fleet/retained_environment_continue.py').read_bytes())
 code=OLD.read_text();old_import='from author_workbook import binding,checked,retain,imports,code_snapshot'
 source=binding(OLD.parent/'author_workbook.py')
 prefix="sys.path.insert(0,"+repr(str(OLD.parent))+")\nassert hashlib.sha256(Path("+repr(source['path'])+").read_bytes()).hexdigest()=="+repr(source['sha256'])+", 'original author helper changed'\n"
 code=code.replace(old_import,prefix+old_import,1)
 old_base="BASE=Path(__file__).resolve().parents[1]";ns=binding(namespace)
 new_base="_namespace_binding = "+repr(ns)+"\nassert hashlib.sha256(Path(_namespace_binding['path']).read_bytes()).hexdigest()==_namespace_binding['sha256'], 'continued namespace changed'\n_namespace_spec=importlib.util.spec_from_file_location('retained_environment_namespace',_namespace_binding['path'])\nnamespace=importlib.util.module_from_spec(_namespace_spec);_namespace_spec.loader.exec_module(namespace)\nBASE=Path("+repr(str(OLD.parents[1]))+")"
 assert code.count(old_base)==1;code=code.replace(old_base,new_base)
 node=next(n for n in ast.parse(code).body if isinstance(n,ast.FunctionDef) and n.name=='history_authority');lines=code.splitlines(keepends=True)
 code=''.join(lines[:node.lineno-1])+"def history_authority(item,intake,inventory,root):\n    return namespace.history_authority(item,intake,inventory,root)\n"+''.join(lines[node.end_lineno:])
 driver=destination/'dispatcher.py';driver.write_text(code);ast.parse(code)
 manifest=dict(schema_version='retained-environment-v7-adapter/v1',original_dispatcher=binding(OLD),original_author_helper=source,dispatcher=binding(driver),namespace=ns,helper=binding(helper),files=[binding(driver),ns,binding(helper)],task_code_changed=False,owning_stages_changed=False,stage_seconds=3300,memory_limit_bytes=8*1024**3,model_limit=1,history_kinds=['0006-source-continuation','0190-existing-review'])
 p=destination/'manifest.json';p.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n');print(json.dumps(binding(p)))
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();build(a.output)
