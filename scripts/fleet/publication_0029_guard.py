"""Explicit publication-only authentication for retained0029 namespace readers.

This module never supplies a model journal. The original authoring namespace is
unchanged; a sealed publication wrapper installs this hook on both its namespace
and the separately loaded dispatcher's namespace.
"""
from __future__ import annotations
import hashlib,json,os,time
from pathlib import Path
ROOT=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline')
ADAPTER={'path':str(ROOT/'diagnostics/vm-fleet-20260912/holds-0029/namespace-001/adapter-002/manifest.json'),'sha256':'844653be65e658695762928ae18465bcebbfd0a043bff95c290b85153c7de127'}
SOURCE='55029f5834ed624c303b4d7f00adb9bf0ad88c1ff10856e174e44cc7ae1f9509'
CODE='2d3e15a8e7b500635e6960eac14872beb98adcae75de8f1f53f174eeb42ec98c'
SELECTED=None

def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)),'regular publication dependency required')
    return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'publication dependency changed')
    return json.loads(Path(row['path']).read_bytes())
def adapter():
    m=checked(ADAPTER)
    for key in ('namespace_module','dispatcher','publication_driver','original_dispatcher','original_publication_driver','original_runner'):
        require(binding(m[key]['path'])==m[key],'retained publication adapter changed')
    require(m['schema_version']=='fleet-0029-retained-dispatcher/v1' and m['model_limit']==m['publication_limit']==1,'retained publication profile changed')
    return m

def select(row,plan):
    global SELECTED
    require(checked(row)==plan,'publication selection differs')
    require(plan['schema_version']=='harbor-fleet-remote-publication/v1' and plan['workbook']=='0029' and plan['source_sha256']==SOURCE and Path(plan['code_root']).name==CODE and plan['namespace_adapter']==ADAPTER,'exact0029 publication selection required')
    require(SELECTED is None or SELECTED==row,'publication selection cannot switch')
    SELECTED=dict(row)

def context():
    require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY')=='1' and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL') and not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'live publication-only environment required')
    names=('URL','TOKEN_FILE','WORKBOOK','OWNER','CLAIM_TOKEN','FENCE')
    values={k:os.environ['HARBOR_FLEET_'+k] for k in names}
    require(all(values.values()) and values['WORKBOOK']=='0029' and values['FENCE'].isdigit() and int(values['FENCE'])>0,'publication identity missing')
    from scripts.fleet.coordinator import Client
    return Client(values['URL'],values['TOKEN_FILE']),dict(workbook='0029',owner=values['OWNER'],token=values['CLAIM_TOKEN'],fence=int(values['FENCE']))

def validate_status(status,auth,root,selected,*,now):
    require(status['model_limit']==status['publication_limit']==1 and abs(status['at']-now)<=60,'publication limits or observation freshness differs')
    rows=[w for w in status['workbooks'] if w['workbook']=='0029'];require(len(rows)==1,'unique publication workbook required');w=rows[0]
    require(w['state']=='active' and w['owner']==auth['owner'] and w['fence']==auth['fence'] and w['version']==CODE==Path(root).name and w['source_sha256']==SOURCE and w['expires']>now,'publication source/version/owner/fence/expiry differs')
    active=[]
    for op in status['operations']:
        require(op['state'] in {'active','hold','confirmed','abandoned'} and op['kind'] in {'model','publication'},'unknown global operation')
        require(op['state']!='hold','uncertain global operation')
        if op['state']=='active':active.append(op)
    require(len(active)==1,'one exact serialized publication operation required');op=active[0]
    identity=hashlib.sha256(json.dumps(dict(workbook='0029',request_sha256=selected['sha256']),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    require(op['kind']=='publication' and op['workbook']=='0029' and op['owner']==auth['owner'] and op['fence']==auth['fence'] and op['expires']>now and op['identity']==identity,'publication operation detached from selection')
    return w

def forbidden(*args,**kwargs):raise RuntimeError('model execution and fresh admission forbidden during0029publication')
def install(root):
    require(SELECTED is not None,'publication plan not selected');plan=checked(SELECTED)
    require(str(root)==plan['code_root'],'publication owning root changed')
    from scripts.batch import harbor_admission as A,harbor_model_executor as E
    import xl_transport
    from scripts.fleet import dialogue_0029_proof as P
    require(all(Path(m.__file__).resolve().is_relative_to(Path(root)) for m in (A,E,P,xl_transport)),'foreign publication runtime')
    A.DispatcherAdmission._check=forbidden;E.execute=E._open=forbidden
    xl_transport.read_response=xl_transport._read_response=xl_transport.model_slot=forbidden
    P.verified_code(Path(root))
    client,auth=context();validate_status(client.call('status'),auth,root,SELECTED,now=time.time())
    renewed=client.call('heartbeat',**auth)
    require(all(renewed[k]==auth[k] for k in ('workbook','owner','fence')) and renewed['source_sha256']==SOURCE and renewed['version']==CODE and renewed['state']=='active','publication heartbeat identity differs')
    return dict(workbook='0029',owner=auth['owner'],fence=auth['fence'],version=CODE,models_allowed=False)

def attach(namespace):
    m=adapter();require(binding(namespace.__file__)==m['namespace_module'],'foreign namespace module')
    namespace.install_execution_guards=install

def install_loader(publisher):
    """Explicit adapter: patch only the exact retained dispatcher load result."""
    m=adapter();attach(publisher['namespace']);original=publisher['load']
    def load(name,row):
        result=original(name,row)
        if row==m['dispatcher']:attach(result.namespace)
        elif name=='fleet_publication_dispatcher':raise ValueError('foreign publication dispatcher')
        return result
    publisher['load']=load
