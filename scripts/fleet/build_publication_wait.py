"""Decorate a frozen original-controlled publisher with explicit admission policy."""
from pathlib import Path
import ast,hashlib,json
BASE_SHA='d8b9cc78312918e38040442f781dad755db7605292c6d04f0529f580e15c2149'
def binding(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def build(out,parent,original):
 out=Path(out).absolute();m=json.loads(Path(parent['path']).read_bytes());assert binding(parent['path'])==parent
 old=Path(m['publisher']['path']).read_text();assert binding(m['publisher']['path'])==m['publisher'];base=Path(original).read_text();assert hashlib.sha256(base.encode()).hexdigest()==BASE_SHA
 def fun(s):return next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef)and n.name=='controlled')
 assert ast.dump(fun(old),include_attributes=False)==ast.dump(fun(base),include_attributes=False),'only original controlled publication supported'
 assert 'publication_wait.configuration(' not in old
 out.mkdir(parents=True,exist_ok=False)
 def keep(name,data):
  p=out/name;p.write_bytes(data if isinstance(data,bytes)else data.encode());return binding(p)
 wait=keep('publication_wait.py',Path(__file__).with_name('publication_wait.py').read_bytes())
 f=fun(old);body=ast.get_source_segment(old,f)
 anchor='plan = checked(plan_binding); journal = safe(plan["journal"])';assert body.count(anchor)==1
 body=body.replace(anchor,anchor+'\n    wait_policy = publication_wait.configuration(plan, checked=checked, binding=binding, require=require)')
 anchor='time.monotonic()-start < 3300';assert body.count(anchor)==1;body=body.replace(anchor,"time.monotonic()-start < wait_policy['maximum_wait_seconds']")
 anchor='env = publication_environment(plan, auth, action=action, url=url, token_file=token_file)';assert body.count(anchor)==1
 body=body.replace(anchor,"try:\n            publication_wait.enforce_after_slot(wait_policy, checked=checked, require=require)\n        except Exception as exc:\n            retain(journal / 'admission-rejected.json', dict(policy=wait_policy['policy'], owning_child_started=False, failure_type=type(exc).__name__, automatic_retry=False))\n            raise\n        "+anchor)
 lines=old.splitlines(keepends=True);code=''.join(lines[:f.lineno-1])+body+'\n'+''.join(lines[f.end_lineno:])
 tree=ast.parse(code);last=tree.body[-1];assert isinstance(last,ast.If)and '__name__'in ast.unparse(last.test),'explicit main entrypoint required'
 lines=code.splitlines(keepends=True);code=''.join(lines[:last.lineno-1])+"publication_wait=load('publication_wait',"+repr(wait)+")\n"+''.join(lines[last.lineno-1:])
 publisher=keep('publish.py',code)
 deps={**m['dependencies'],'publication_wait':wait,'original_publisher':m['publisher'],'original_implementation':parent}
 result=dict(schema_version='harbor-publication-wait-implementation/v1',publisher=publisher,dependencies=deps,original=parent,default_wait_seconds=3300,selected_wait_seconds=120,post_slot_runway_seconds=3700,owning_guard_changed=False,model_dispatch_allowed=False)
 return keep('manifest.json',json.dumps(result,sort_keys=True,indent=2)+'\n')
