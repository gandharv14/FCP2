"""Continue one intent-only semantic preparation without removing its evidence.

The original authorize function still owns failure validation, bounded prefix
archive, permit, tracker CAS and sealing. Three exact preparation calls change:
validate the retained empty transaction, preserve its directory, append a new
recovery intent. A permit or any partial archive requires separate reconciliation.
"""
from pathlib import Path
import ast,hashlib,inspect,json,textwrap
from types import CodeType,FunctionType
SCHEMA='fleet-semantic-intent-recovery/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical recovery evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'recovery evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def validate(row,S,worker,*,blocked,takeover):
    p=read(row)
    require(set(p)=={'schema_version','helper','original_module','intent','takeover_profile','takeover_completion','workbook','source','authorization'}
        and p['schema_version']==SCHEMA and p['helper']==binding(__file__)
        and p['original_module']==binding(S.__file__)and p['takeover_completion']==takeover,
        'closed exact original semantic preparation required')
    auth=read(p['takeover_profile']);done=read(takeover);stop=read(done['stopped_owner_proof']);old=read(p['intent'])
    # The takeover custody checkpoint is the owning completed review envelope;
    # its exact rejected checkpoint remains the original preparation's blocked.
    expected=dict(auth['selection'])
    # The owning takeover serializer omits this optional field only when null.
    if expected.get('controls_migration')is None:expected.pop('controls_migration',None)
    require(stop['selection']==expected and stop['before']['sha256']==old['tracker']['sha256']
        and stop['selection']['tracker']==old['tracker']and stop['selection']['workbook']==p['workbook'],
        'recovery is detached from the actual interrupted tracker and takeover')
    before=read(stop['before']);require(before['bindings']['original_raw_source']==p['source']
        and p['authorization']['sha256']=='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
        and binding(p['authorization']['path'])==p['authorization'],'source or explicit recovery authorization differs')
    out=worker.run/'dialogue-semantic-retry-02'
    require(out.is_dir()and not out.is_symlink()and set(x.name for x in out.iterdir())=={'intent.json'}
        and p['intent']==binding(out/'intent.json')and set(old)=={'blocked','tracker','code'}
        and old['blocked']==blocked and old['code']==S._worker().code(worker),
        'only the original unchanged intent without permit, archive or CAS may continue')
    require(not any(h.get('schema_version')==S.SCHEMA for h in before.get('handoffs',[])),
        'semantic transaction was already committed')
    return p,out,old

def adapted(original,extra):
    path=Path(original.__code__.co_filename);require(path==Path(original.__globals__['__file__'])and not original.__closure__,'original owning function required')
    tree=ast.parse(path.read_text());nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='authorize']
    require(len(nodes)==1 and ast.dump(nodes[0])==ast.dump(ast.parse(textwrap.dedent(inspect.getsource(original))).body[0]),'owning authorize source differs')
    def sig(c):return(c.co_code,c.co_names,c.co_varnames,c.co_argcount,c.co_kwonlyargcount,c.co_posonlyargcount,c.co_flags,tuple(sig(v)if isinstance(v,CodeType)else v for v in c.co_consts))
    compiled=[c for c in compile(tree,str(path),'exec').co_consts if isinstance(c,CodeType)and c.co_name=='authorize']
    require(len(compiled)==1 and sig(compiled[0])==sig(original.__code__),'loaded owning authorize changed')
    pairs=[("require(not out.exists(), 'prepared semantic transaction requires explicit reconciliation')","_recovery_check(out)"),('out.mkdir()','_recovery_directory(out)'),("producer.put(out/'intent.json', {'blocked': blocked, 'tracker': tracker, 'code': current})","_recovery_intent(out, {'blocked': blocked, 'tracker': tracker, 'code': current}, records)")]
    targets={ast.dump(ast.parse(a,mode='eval').body):ast.parse(b,mode='eval').body for a,b in pairs};counts={k:0 for k in targets}
    class Change(ast.NodeTransformer):
        def visit_Call(self,node):
            key=ast.dump(node)
            if key in targets:counts[key]+=1;return targets[key]
            return self.generic_visit(node)
    Change().visit(nodes[0]);require(all(v==1 for v in counts.values()),'one exact intent preparation boundary required')
    code=next(c for c in compile(ast.fix_missing_locations(tree),str(path),'exec').co_consts if isinstance(c,CodeType)and c.co_name=='authorize')
    fn=FunctionType(code,dict(original.__globals__,**extra),original.__name__,original.__defaults__);fn.__kwdefaults__=original.__kwdefaults__;return fn

def authorize(row,S,worker,selected,lease,*,blocked,takeover):
    def check(out):
        _,expected,_=validate(row,S,worker,blocked=blocked,takeover=takeover);require(out==expected,'foreign preparation directory')
    def append(out,value,records):
        check(out);p,_,old=validate(row,S,worker,blocked=blocked,takeover=takeover)
        require(value['blocked']==old['blocked']and value['code']==old['code'],'recovery changed original review or code')
        receipt=S.producer.put(out/'recovery-intent-001.json',dict(schema_version=SCHEMA,profile=row,original=p['intent'],current=value,models_dispatched=0,semantic_budget_reset=False,automatic_retry=False))
        records.extend([row,p['intent'],receipt])
        return receipt
    fn=adapted(S.authorize,dict(_recovery_check=check,_recovery_directory=check,_recovery_intent=append))
    return fn(worker,selected,lease,blocked=blocked,takeover=takeover)
