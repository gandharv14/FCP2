"""Freeze publication-only retained-normalization dependencies explicitly."""
from pathlib import Path
import hashlib
import json
import sys
from scripts.fleet import undispatched_publication as U
from scripts.fleet import undispatched_publication_child as A
from scripts.fleet import source_publication as S

BASE_SHA='d8b9cc78312918e38040442f781dad755db7605292c6d04f0529f580e15c2149'


def build(output,original):
    out=Path(output).absolute();raw=Path(original).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=BASE_SHA:raise ValueError('exact reviewed publisher required')
    out.mkdir(parents=True,exist_ok=False)
    def write(name,value):
        p=out/name
        with p.open('xb')as f:f.write(value if isinstance(value,bytes)else value.encode())
        return A.binding(p)
    rows={name:write(filename,Path(module.__file__).read_bytes()) for name,filename,module in (
        ('normalization_publication','reader.py',U),('publication_child_adapter','child.py',A),
        ('publication_status','status.py',S))}
    prefix='\n# Explicit retained-normalization publication dependencies; original task bytes stay unchanged.\n'
    for name,row in rows.items():
        prefix+=f"{name} = load({name!r}, {row!r})\n"
    code=U.patch(raw.decode());anchor='def history_binding(plan):\n'
    if code.count(anchor)!=1:raise ValueError('publisher dependency anchor changed')
    publisher=write('publish.py',code.replace(anchor,prefix+'\n'+anchor))
    builder=write('build.py',Path(__file__).read_bytes())
    return write('manifest.json',json.dumps(dict(schema_version='fleet-undispatched-publication-successor/v1',
        original=dict(path=str(Path(original).absolute()),sha256=BASE_SHA),publisher=publisher,
        dependencies=rows,builder=builder,task_code_changed=False,owning_publication_bytecode_unchanged=True,
        declared_child_dependency=True,independent_verification_unchanged=True,collection_unchanged=True,
        model_calls_allowed=False,automatic_retry=False),indent=2,sort_keys=True)+'\n')

if __name__=='__main__':print(json.dumps(build(sys.argv[1],sys.argv[2])))
