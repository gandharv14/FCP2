"""Freeze a0029-only v7 history reader/publication wrapper; old code untouched."""
import argparse,ast,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
from scripts.fleet.legacy_0029_namespace import binding,checked,retain,require
BASE=ROOT/'diagnostics/authoring-resume-20260910'
def apply(raw,old,new):
    require(raw.count(old)==1,'exact0029 dispatcher patch context differs');return raw.replace(old,new,1)
def function(raw,name,new):
    nodes=[n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name==name];require(len(nodes)==1,'exact0029 function required');n=nodes[0];lines=raw.splitlines(keepends=True);return ''.join(lines[:n.lineno-1]+[new.rstrip()+'\n']+lines[n.end_lineno:])
def build(certificate,output):
    h=checked(certificate);require(h['schema_version']=='fleet-0029-retained-namespace-history/v1' and h['workbook']=='0029' and len(h['retained_legacy_calls'])==8,'exact0029 namespace certificate required')
    out=Path(output).absolute();require(not out.exists(),'0029 adapter exists; choose a new immutable version');out.mkdir(parents=True)
    def write(name,text):
        p=out/name;p.write_text(text);return binding(p)
    source=(ROOT/'scripts/fleet/legacy_0029_namespace.py').read_text();namespace=write('namespace.py',apply(source,'\nPROFILE=None\n','\nPROFILE='+repr(certificate)+'\n'))
    loader='''import importlib.util as _namespace_import
_namespace_binding = '''+repr(namespace)+'''
assert hashlib.sha256(Path(_namespace_binding['path']).read_bytes()).hexdigest()==_namespace_binding['sha256'], '0029 namespace reader changed'
_namespace_spec = _namespace_import.spec_from_file_location('retained_0029_namespace', _namespace_binding['path'])
namespace = _namespace_import.module_from_spec(_namespace_spec)
_namespace_spec.loader.exec_module(namespace)
'''
    old=BASE/'runner/advance_authoring_v7.py';raw=old.read_text();raw=apply(raw,'from author_workbook import binding,checked,retain,imports,code_snapshot',"sys.path.insert(0,"+repr(str(BASE/'runner'))+")\nfrom author_workbook import binding,checked,retain,imports,code_snapshot\n"+loader)
    raw=apply(raw,'BASE=Path(__file__).resolve().parents[1]','BASE=Path('+repr(str(BASE))+')')
    raw=function(raw,'history_authority','def history_authority(item,intake,inventory,root):\n    return namespace.history_authority(item,intake,inventory,root)')
    raw=apply(raw,"    outcome=job['outcome'];ops=operation_namespace(config,job['operations'])","    namespace.require_instruction_outcome(job,env)\n    outcome=job['outcome'];ops=operation_namespace(config,job['operations'])")
    dispatcher=write('dispatcher.py',raw)
    pub=ROOT/'scripts/fleet/publish.py';raw=pub.read_text();original_publication=write('original-publish.py',raw);needle="def history_binding(plan):\n";raw=apply(raw,needle,loader+'\n'+needle)
    raw=apply(raw,"    if 'continuation_history' in plan:\n        return continuation_binding(plan)","    if plan.get('workbook')=='0029':\n        return namespace.publication_history(plan)\n    if 'continuation_history' in plan:\n        return continuation_binding(plan)")
    oldprefix="prefix = 'HARBOR_FLEET_CONTINUATION_HISTORY_' if 'continuation_history' in plan else 'HARBOR_FLEET_HISTORY_'"
    require(raw.count(oldprefix)==2,'0029 publication environment patch differs');raw=raw.replace(oldprefix,"prefix = 'HARBOR_FLEET_0029_NAMESPACE_' if plan.get('workbook')=='0029' else ('HARBOR_FLEET_CONTINUATION_HISTORY_' if 'continuation_history' in plan else 'HARBOR_FLEET_HISTORY_')")
    oldblock="""    if 'continuation_history' in plan:
        from scripts.fleet.continuation_history import projected_survey
    else:
        from scripts.fleet.history import projected_survey
    certificate = checked(history)
    projected_survey(tuple(map(Path, certificate['roots'])), set(certificate['known_ids']))
"""
    newblock="""    if plan.get('workbook')=='0029':
        namespace.publication_live(plan)
    else:
        if 'continuation_history' in plan:
            from scripts.fleet.continuation_history import projected_survey
        else:
            from scripts.fleet.history import projected_survey
        certificate = checked(history)
        projected_survey(tuple(map(Path, certificate['roots'])), set(certificate['known_ids']))
"""
    raw=apply(raw,oldblock,newblock);publication=write('publish.py',raw)
    builder=write('build-source.py',Path(__file__).read_text());namespace_source=write('namespace-source.py',source)
    return retain(out/'manifest.json',dict(schema_version='fleet-0029-retained-dispatcher/v1',namespace_certificate=certificate,namespace_module=namespace,dispatcher=dispatcher,publication_driver=publication,original_dispatcher=binding(old),original_publication_driver=original_publication,original_runner=binding(BASE/'runner/author_workbook.py'),builder=builder,namespace_source=namespace_source,fresh_admission_allowed=False,historical_replay_allowed=False,model_limit=1,publication_limit=1,selected_task_code_unchanged=True))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--certificate',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(dict(path=str(Path(a.certificate).absolute()),sha256=a.sha256),a.output)))
