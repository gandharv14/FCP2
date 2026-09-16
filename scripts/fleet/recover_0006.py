"""Root-selected source-only 0006 continuation; inspect never claims work.

migrate requires an active central fenced worker. It retains the old tracker,
replaces its expired lease through the owning protocol, writes new descriptors,
and stops before segmentation/model work. Reuse is forbidden; reconcile first.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.parse

HOME=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-queue-extension-20260911T185027Z/authoring/0006-generated-01')
BOUNDS={'config.json':'ec91e5c28114d9905375706a466c226887e6564229804114a0f2dca7bf9db7c0','admission-001/descriptor.json':'aa7017753bfe9ef59f381a2af7ea6fd418df2d0a1cdae0858170a1120d0c6849','dispatcher-v2/first/000/result.json':'71ecc4f3e6e6749737e3072b5365ef95e7dcd5cb2d742a918e65e62b50795215','dispatcher-v2/first/000/resource.json':'44286b3a179760c2cde026bb35dbcdf0fbc696071ee85d15d68443658cfb6130','trackers/workbooks/0006.json':'165368bd028a2ca9c1138dff52af96540648133f5187ec8eeb22663485ea1aeb'}
SOURCE_SHA='81953973e0bbde9508ba0c7ae53b5b725ffb988cf8cc072367e5d5bd0c4ec960'


def custody():
    values={k:os.environ['HARBOR_FLEET_'+k] for k in ('URL','TOKEN_FILE','WORKBOOK','OWNER','CLAIM_TOKEN','FENCE')}
    url=urllib.parse.urlparse(values['URL'])
    if values['WORKBOOK']!='0006' or url.scheme!='http' or url.hostname not in {'127.0.0.1','localhost','::1'}:raise ValueError('0006 fenced loopback custody required')
    args={'workbook':'0006','owner':values['OWNER'],'token':values['CLAIM_TOKEN'],'fence':int(values['FENCE']),'lease_seconds':180}
    req=urllib.request.Request(values['URL'].rstrip('/')+'/v1',data=json.dumps({'action':'heartbeat','arguments':args}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+Path(values['TOKEN_FILE']).read_text().strip()})
    with urllib.request.urlopen(req,timeout=30) as response:state=json.load(response)
    if state['state']!='active' or state['source_sha256']!=SOURCE_SHA:raise ValueError('original source custody differs')
    return values['OWNER']


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['inspect','migrate']);p.add_argument('--code-root',type=Path,required=True);p.add_argument('--code-sha256',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--env-file',type=Path);p.add_argument('--check-runtime',action='store_true');p.add_argument('--continuation-history',type=Path);p.add_argument('--continuation-history-sha256');a=p.parse_args()
    if bool(a.continuation_history)!=bool(a.continuation_history_sha256):raise ValueError('exact history path/hash pair required')
    if a.continuation_history:
        history=a.continuation_history.absolute()
        if hashlib.sha256(history.read_bytes()).hexdigest()!=a.continuation_history_sha256:raise ValueError('continued history changed')
        os.environ.update(HARBOR_FLEET_CONTINUATION_HISTORY_PATH=str(history),HARBOR_FLEET_CONTINUATION_HISTORY_SHA256=a.continuation_history_sha256)
        if a.action=='inspect':os.environ.update(HARBOR_FLEET_WORKBOOK='0006',HARBOR_FLEET_CONTINUATION_READ_ONLY='1')
        elif os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'):raise ValueError('read-only context cannot migrate')
    root=a.code_root.absolute();out=a.output.absolute()
    if out.exists():raise ValueError('operation exists; reconcile without replay')
    if any(q.is_symlink() for q in (root,*root.parents)):raise ValueError('immutable root required')
    inventory=json.loads((root/'.pipeline-code.json').read_text())
    fingerprint=hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    if fingerprint!=a.code_sha256:raise ValueError('explicit reviewed code fingerprint differs')
    sys.path.insert(0,str(root))
    from scripts import corpus,harbor_campaign as C
    from scripts.batch import harbor_source_only_continuation as M,harbor_lease_takeover as T
    if C.ROOT!=root or corpus.code_hash()!=a.code_sha256:raise ValueError('clean immutable code process required')
    def forbidden(*args,**kwargs):raise RuntimeError('model dispatch forbidden during source-only migration')
    C.executor.execute=forbidden
    bound={name:{'path':str(HOME/name),'sha256':sha} for name,sha in BOUNDS.items()}
    for b in bound.values():C.executor.checked_bytes(b)
    config,old,saved,validated=M.inspect(bound['config.json'],bound['admission-001/descriptor.json'],bound['dispatcher-v2/first/000/result.json'],bound['trackers/workbooks/0006.json'])
    owner=custody() if a.action=='migrate' else 'fleet-0006-inspection'
    takeover=T.Selection(HOME,'0006',bound['trackers/workbooks/0006.json'],'environment',bound['dispatcher-v2/first/000/resource.json'],bound['dispatcher-v2/first/000/result.json'],owner,config=bound['config.json'])
    T._recipe(takeover)
    out.mkdir(parents=True)
    inspection=C.keep(out/'inspection.json',dict(status='SOURCE_ONLY_CONTINUATION_VALIDATED',predecessor=bound,code_sha256=a.code_sha256,source=validated['original'],targets=validated['targets'],original_budget=C.encode(old.normalization_budget),next_step='segmentation',model_calls=0,new_admission=False,publication_ready=False))
    if a.action=='inspect':
        if a.check_runtime:C._dependency(old,out)
        print(json.dumps(inspection));return
    if a.env_file is None:raise ValueError('native credential file path required before takeover')
    controls=replace(C.decode(config['controls']),env_file=a.env_file.absolute());C._controls(controls)
    dependency=C._dependency(old,out)
    snapshot=C.keep(out/'code.json',{'root':str(root),'files':[{'relative':str(p.relative_to(root)),'sha256':C.record(p)['sha256']} for p in sorted(root.rglob('*')) if p.is_file() and not {'__pycache__','.pytest_cache'}&set(p.parts)]})
    custody()
    lease,receipt=T.replace_expired(takeover,HOME/'lease-takeovers/fleet-source-only-001')
    custody()
    destination=HOME/'continuations/fleet-source-only-001'
    current,migration,descriptor,result=M.commit(bound['config.json'],bound['admission-001/descriptor.json'],bound['dispatcher-v2/first/000/result.json'],bound['trackers/workbooks/0006.json'],lease,receipt,destination,controls,snapshot)
    old_intake=C.decode(C.read(config['intake']))
    intake=C.keep(destination/'intake.json',C.encode(replace(old_intake,bootstrap=current.bootstrap,environment_run=current.run,owner=lease.owner)))
    updated={**config,**({'continuation_history':C.record(a.continuation_history.absolute())} if a.continuation_history else {}),'code':snapshot,'controls':C.encode(controls),'run_root':str(destination),'intake':intake,'source_continuation':migration}
    new_config=C.keep(destination/'config.json',updated)
    M.layout(updated,destination,'environment')
    C.keep(out/'result.json',dict(status='IMPORTED',descriptor=descriptor,checkpoint=result,config=new_config,receipt=migration,dependency=dependency,model_calls=0,new_admission=False,semantic_budget_reset=False,next_step='segmentation',publication_ready=False))
    print(json.dumps(C.record(out/'result.json')))

if __name__=='__main__':main()
