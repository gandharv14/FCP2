"""Seal only the publication authentication correction; old adapters unchanged."""
import hashlib,json,sys
from pathlib import Path
from scripts.fleet import publication_0029_guard as G

def build(output):
    out=Path(output).absolute();G.require(not out.exists(),'publication successor exists');out.mkdir(parents=True)
    def write(name,raw):
        p=out/name
        with p.open('xb') as f:f.write(raw if isinstance(raw,bytes) else raw.encode())
        return G.binding(p)
    old=G.adapter();guard=write('guard.py',Path(G.__file__).read_bytes());source=Path(old['publication_driver']['path']).read_text()
    prefix="""\n# Explicit publication-only successor; retained authoring namespace stays intact.
_guard_binding = %r
assert hashlib.sha256(Path(_guard_binding['path']).read_bytes()).hexdigest()==_guard_binding['sha256'], 'publication guard changed'
_guard_spec = _namespace_import.spec_from_file_location('publication_0029_guard',_guard_binding['path'])
publication_guard = _namespace_import.module_from_spec(_guard_spec)
_guard_spec.loader.exec_module(publication_guard)
publication_guard.install_loader(globals())
\n"""%guard
    needle='def history_binding(plan):\n';G.require(source.count(needle)==1,'publisher patch anchor differs');source=source.replace(needle,prefix+needle,1)
    needle='    plan = checked(plan_binding)\n    require(plan["schema_version"] == "harbor-fleet-remote-publication/v1", "remote publication plan required")'
    G.require(source.count(needle)==1,'remote child anchor differs');source=source.replace(needle,needle+'\n    publication_guard.select(plan_binding,plan)',1)
    publisher=write('publish.py',source);builder=write('build-source.py',Path(__file__).read_bytes())
    manifest=dict(schema_version='fleet-0029-publication-auth-successor/v1',original_adapter=G.ADAPTER,original_publication_driver=old['publication_driver'],publication_driver=publisher,guard=guard,builder=builder,authoring_adapter_unchanged=True,task_code_unchanged=True,model_journal_required=False,model_execution_allowed=False,global_model_limit=1,global_publication_limit=1)
    return write('manifest.json',json.dumps(manifest,indent=2,sort_keys=True)+'\n')
if __name__=='__main__':print(json.dumps(build(sys.argv[1])))
