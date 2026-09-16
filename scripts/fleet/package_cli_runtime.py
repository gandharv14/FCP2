"""Declare one narrow source-ID correction for original deterministic MCP CLIs."""
from pathlib import Path
from contextlib import contextmanager
import argparse,ast,hashlib,importlib.util,json,os,runpy,sys
from types import FunctionType,ModuleType

SCHEMA='fleet-package-cli-source-id/v1'


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)), 'canonical package code/evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'package runtime evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def profile(row):
    p=read(row)
    require(set(p)=={'schema_version','helper','original','selected','cli','workbook','normalized','source','work'}
        and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed source-ID CLI runtime required')
    for name in ('original','selected','cli','normalized','source'):
        require(binding(p[name]['path'])==p[name],'package runtime input changed')
    root=Path(p['cli']['path']).parent
    require(p['original']==binding(root/'mcp_env/build.py') and Path(p['cli']['path']).name=='xl_variable_mcp.py'
        and Path(p['source']['path']).name==p['workbook']+'.xlsx' and Path(p['work']).is_absolute(),
        'source-ID CLI runtime detached from original source/entry')
    trees=[]
    for name in ('original','selected'):
        tree=ast.parse(Path(p[name]['path']).read_text());found=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='source_id']
        require(len(found)==1,'unique source_id function required');tree.body.remove(found[0]);trees.append(ast.dump(tree))
    require(trees[0]==trees[1],'package runtime changed code outside source_id')
    return p


def command(p,intent):
    v=read(intent);argv=v['argv'];work=Path(p['work']);label=Path(intent['path']).name.removesuffix('.intent.json')
    require(label in {'build-a','build-b'} and Path(intent['path'])==work/(label+'.intent.json')
        and v==dict(argv=argv,entry=p['cli'],model_calls_permitted=False)
        and argv==[sys.executable,p['cli']['path'],'build',p['normalized']['path'],str(work/label),'--workbook',p['workbook'],'--source',str(Path(p['source']['path']).parent)],
        'original package build command changed')
    return argv,label


def physical(row,intent):
    p=profile(row);command(p,intent)
    return [sys.executable,'-I','-B',__file__,'run','--profile',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]


@contextmanager
def installed(producer,row):
    p=profile(row);require(Path(producer.ROOT)==Path(p['cli']['path']).parent,'original producer root differs')
    import mcp_env.build as builder
    require(binding(builder.__file__)==p['original'],'loaded original MCP builder differs')
    spec=importlib.util.spec_from_file_location('selected_package_source_id',p['selected']['path']);selected=importlib.util.module_from_spec(spec);spec.loader.exec_module(selected)
    prior=builder.source_id;restores=[];seen=set()
    for module in tuple(sys.modules.values()):
        if not isinstance(module,ModuleType):continue
        dictionaries=[module.__dict__,*[v.__globals__ for v in tuple(module.__dict__.values())if isinstance(v,FunctionType)]]
        for namespace in dictionaries:
            if id(namespace)in seen:continue
            seen.add(id(namespace))
            for key,value in tuple(namespace.items()):
                if value is prior:restores.append((namespace,key,value));namespace[key]=selected.source_id
    original=producer.subprocess.run
    def run(argv,**kwargs):
        if len(argv)>2 and argv[1]==p['cli']['path'] and argv[2]=='build':
            work=Path(p['work']);label=Path(argv[4]).name;intent=binding(work/(label+'.intent.json'));logical,selected_label=command(p,intent)
            require(list(argv)==logical and label==selected_label,'build argv differs from original intent')
            selected=physical(row,intent);side=work/(label+'.runtime.json');require(not side.exists(),'package CLI already dispatched; preserve uncertain attempt')
            producer.journal.save(side,dict(schema_version=SCHEMA,profile=row,intent=intent,logical_command=logical,physical_command=selected,automatic_retry=False))
            return original(selected,**kwargs)
        return original(argv,**kwargs)
    producer.subprocess.run=run
    try:yield p
    finally:
        producer.subprocess.run=original
        for namespace,key,value in reversed(restores):namespace[key]=value


def execute(row,intent):
    p=profile(row);argv,label=command(p,intent);side=read(binding(Path(p['work'])/(label+'.runtime.json')))
    require(side==dict(schema_version=SCHEMA,profile=row,intent=intent,logical_command=argv,physical_command=physical(row,intent),automatic_retry=False),'actual package CLI declaration changed')
    require(sys.argv==side['physical_command'][3:],'undeclared physical package invocation')
    root=Path(p['cli']['path']).parent;sys.path.insert(0,str(root));import mcp_env.build as original
    require(binding(original.__file__)==p['original'],'loaded original MCP builder differs')
    spec=importlib.util.spec_from_file_location('selected_package_builder',p['selected']['path']);selected=importlib.util.module_from_spec(spec);spec.loader.exec_module(selected)
    original.source_id=selected.source_id
    def network(event,args):
        if event=='socket.connect':raise RuntimeError('deterministic MCP build cannot use network')
    sys.addaudithook(network);sys.argv=argv[1:]
    try:runpy.run_path(p['cli']['path'],run_name='__main__')
    except SystemExit as e:require(e.code in (None,0),'original MCP CLI failed')
    for name in ('normalized','source'):require(binding(p[name]['path'])==p[name],'MCP CLI changed source inputs')


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['run']);a.add_argument('--profile',required=True);a.add_argument('--sha256',required=True);a.add_argument('--intent',required=True);a.add_argument('--intent-sha256',required=True);v=a.parse_args();execute(dict(path=v.profile,sha256=v.sha256),dict(path=v.intent,sha256=v.intent_sha256))
