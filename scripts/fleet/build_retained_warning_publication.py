"""Freeze a declared warning publication reader without altering owning code."""
from pathlib import Path
import ast,hashlib,json,sys
BASE_SHA='d8b9cc78312918e38040442f781dad755db7605292c6d04f0529f580e15c2149'
def reader_successor(out,parent,reader_source):
 """Select one explicit reader; retain every original publication function."""
 def binding(path):
  path=Path(path).absolute();return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
 assert binding(parent['path'])==parent
 old=json.loads(Path(parent['path']).read_bytes())
 for row in (old['publisher'],*old['dependencies'].values()):assert binding(row['path'])==row
 original=old['dependencies']['reader'];source=Path(old['publisher']['path']).read_text()
 assert source.count(repr(original))==1
 out=Path(out).absolute();out.mkdir(parents=True)
 def write(name,data):
  path=out/name
  with path.open('xb')as stream:stream.write(data)
  return binding(path)
 reader=write('reader.py',Path(reader_source).read_bytes())
 selected=source.replace(repr(original),repr(reader))
 def functions(text):return [ast.dump(n,include_attributes=False)for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
 assert functions(source)==functions(selected)
 publisher=write('publish.py',selected.encode())
 result=dict(old,publisher=publisher,dependencies=dict(old['dependencies'],reader=reader,previous_warning_reader=original))
 return write('manifest.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode())

def bind_capacity(source,capacity):
 """Pin the declared dependency independently of the reader's directory."""
 assert set(capacity)=={'path','sha256'} and Path(capacity['path']).is_absolute()
 assert hashlib.sha256(Path(capacity['path']).read_bytes()).hexdigest()==capacity['sha256']
 tree=ast.parse(source)
 assignments=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name)and t.id=='CAPACITY_POLICY' for t in n.targets)]
 hashes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name)and t.id=='CAPACITY_POLICY_SHA' for t in n.targets)]
 assert len(assignments)==len(hashes)==1 and ast.literal_eval(hashes[0].value)==capacity['sha256']
 node=assignments[0];lines=source.splitlines(keepends=True)
 return ''.join(lines[:node.lineno-1])+'CAPACITY_POLICY = '+repr(capacity)+'\n'+''.join(lines[node.end_lineno:])

def closure_successor(out,manifest_binding):
 """Relocate only a status dependency declaration; preserve owning functions."""
 old_path=Path(manifest_binding['path']);assert hashlib.sha256(old_path.read_bytes()).hexdigest()==manifest_binding['sha256']
 old=json.loads(old_path.read_text())
 for row in [old['publisher'],*old['dependencies'].values()]:
  assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']
 out=Path(out).absolute();assert not out.exists()
 status=old['dependencies']['status'];capacity=old['dependencies']['capacity']
 selected=bind_capacity(Path(status['path']).read_text(),capacity)
 publisher=Path(old['publisher']['path']).read_text();assert publisher.count(repr(status))==1
 out.mkdir(parents=True)
 def write(name,text):
  p=out/name
  with p.open('x')as f:f.write(text)
  return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 status_new=write('status.py',selected)
 pub_new=write('publish.py',publisher.replace(repr(status),repr(status_new)))
 result=dict(old,publisher=pub_new,dependencies=dict(old['dependencies'],status=status_new))
 row=write('manifest.json',json.dumps(result,indent=2,sort_keys=True)+'\n')
 write('closure-change.json',json.dumps(dict(original=manifest_binding,selected=row,original_status=status,selected_status=status_new,capacity=capacity,changed_assignment='CAPACITY_POLICY',owning_functions_changed=False),indent=2,sort_keys=True)+'\n')
 return row

def build(out,original,reader_source=None):
 out=Path(out).absolute();raw=Path(original).read_bytes();assert hashlib.sha256(raw).hexdigest()==BASE_SHA
 out.mkdir(parents=True,exist_ok=False)
 def write(name,data):
  p=out/name;p.write_bytes(data if isinstance(data,bytes)else data.encode());return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 root=Path(__file__).parent
 rows={name:write(name+'.py',(Path(reader_source).absolute() if name=='reader' and reader_source is not None else root/file).read_bytes()) for name,file in [('reader','retained_warning_publication.py'),('child','undispatched_publication_child.py'),('status','source_publication.py'),('stdlib','stdlib_imports.py')]}
 from scripts.fleet.source_publication import CAPACITY_POLICY_SHA
 capacity=Path('diagnostics/vm-fleet-20260912/control/0190-warning-continuation-001/preparation-002/capacity_policy.py').absolute();assert hashlib.sha256(capacity.read_bytes()).hexdigest()==CAPACITY_POLICY_SHA
 rows['capacity']=write('capacity_policy.py',capacity.read_bytes())
 rows['status']=write('status.py',bind_capacity(Path(rows['status']['path']).read_text(),rows['capacity']))
 code=raw.decode()
 def replace(name,new):
  nonlocal code
  f=next(n for n in ast.parse(code).body if isinstance(n,ast.FunctionDef)and n.name==name);lines=code.splitlines(keepends=True);code=''.join(lines[:f.lineno-1])+new+'\n\n'+''.join(lines[f.end_lineno:])
 replace('history_binding',"def history_binding(plan):\n    return warning_publication.history_binding(plan,checked=checked,binding=binding,require=require)")
 replace('remote_child',"def remote_child(plan_binding):\n    return warning_publication.remote_child(sys.modules[__name__],plan_binding,publication_child_adapter,publication_status.validate_status)")
 # Retained live warnings carry their original legacy certificate into the
 # cold child. Select the reviewed environment builder along with the reader.
 environment=(root/'publish.py').read_text()
 node=next(n for n in ast.parse(environment).body if isinstance(n,ast.FunctionDef) and n.name=='publication_environment')
 replace('publication_environment',''.join(environment.splitlines(keepends=True)[node.lineno-1:node.end_lineno]))
 anchor='if __name__ == "__main__": main()';assert code.count(anchor)==1
 suffix="""stdlib_bootstrap=load('publication_stdlib',STDLIB)
stdlib_bootstrap.preload_inspect()
warning_publication=load('warning_publication',READER)
publication_child_adapter=load('publication_child_adapter',CHILD)
publication_status=load('publication_status',STATUS)
if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='publication-child':
        warning_publication.publication_child(sys.modules[__name__],publication_child_adapter,publication_status.validate_status)
    else:main()
"""
 for constant,key in [('STDLIB','stdlib'),('READER','reader'),('CHILD','child'),('STATUS','status')]:suffix=suffix.replace(constant,repr(rows[key]))
 code=code.replace(anchor,suffix)
 publisher=write('publish.py',code)
 return write('manifest.json',json.dumps(dict(schema_version='harbor-retained-warning-publication-implementation/v1',publisher=publisher,dependencies=rows,original=dict(path=str(Path(original).absolute()),sha256=BASE_SHA),owning_code_changed=False,collection_unchanged=True,model_dispatch_allowed=False),indent=2,sort_keys=True)+'\n')
if __name__=='__main__':print(json.dumps(build(sys.argv[1],sys.argv[2])))
