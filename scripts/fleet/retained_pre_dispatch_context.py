"""Read a completed cold-setup continuation for its first semantic rejection.

Original source, failed setup, completed renewal and every successor guard stay
bound. This reader does not renew custody, prepare a permit or dispatch a model.
"""
from pathlib import Path
from contextlib import ExitStack,contextmanager
import hashlib,importlib.util,json,sys

SCHEMA='fleet-retained-pre-dispatch-review/v1'
FIELDS={'schema_version','helper','utilities','workbook','source','pre_dispatch','fresh_plan',
        'original_job','config','snapshot','outcome','result','descriptor','blocked'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path)
    require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical retained pre-dispatch evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'retained pre-dispatch evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'retained pre-dispatch reader changed')
    s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def selection(p):
    require(set(p)in(FIELDS,FIELDS|{'terminal_http'}) and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed completed pre-dispatch review required')
    old=read(p['pre_dispatch']);worker=read(old['original_worker_job']);fresh=read(p['fresh_plan']);original=read(old['original_job']);job=read(p['original_job'])
    require(old['schema_version']=='fleet-pre-dispatch-continuation/v1'and set(old.get('renewal_setup',{}))=={'dispatcher','completed'}
        and worker['inputs'][0]==p['fresh_plan']and worker['source']==p['source']and worker['version']==Path(fresh['code_root']).name
        and old['workbook']==fresh['workbook']==p['workbook']and old['source']==p['source']
        and original['driver']==fresh['dispatcher']and original['config']==p['config']
        and read(p['config'])['code']==p['snapshot']and read(p['snapshot'])['root']==fresh['code_root']
        and p['original_job']==binding(Path(old['home'])/'authoring-job.json')
        and job==dict(original,driver=old['renewal_setup']['dispatcher'],outcome=old['renewal_setup']['completed'],operations=old['operations']),
        'completed pre-dispatch source or authoring lineage changed')
    outer=read(old['outer']);intent=read(outer['intent'])
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['automatic_retry']is False and outer['publication_performed']is False
        and intent['job']==old['original_worker_job'],'original pre-renewal failure changed')
    return old,fresh,original,job

def completed_prefix(U,p,old,D,env,job,*,terminal=None):
    root=Path(old['operations']);dirs=sorted(x for x in root.iterdir()if x.is_dir())
    require(1<len(dirs)<=job['maximum_steps']and [x.name for x in dirs]==['%03d'%i for i in range(len(dirs))]
        and {x.name for x in root.iterdir()}=={'job.json','hold.json',*[x.name for x in dirs]}
        and read(binding(root/'job.json'))==job,'completed successor directory is not contiguous')
    cp,value=D.successful(job['outcome'],env)
    for i,d in enumerate(dirs):
        row=binding(d/'outcome.json');out=read(row);intent=read(read(out['resource'])['intent'])
        if i==0:
            require(set(intent)=={'job','checkpoint','seconds','driver'}and intent['job']==p['original_job']
                and intent['checkpoint']==cp and intent['driver']==job['driver']
                and type(intent['seconds'])is int and 60<=intent['seconds']<=900,'first successor is not its original renewal')
        else:
            require(intent.get('action')=='step'and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=cp),
                    'successor skipped the completed checkpoint')
        if i==len(dirs)-1:
            require(row==p['outcome']and out['result']==p['result'],'final rejection moved from its completed guard')
            value,_,_=(U.completed_failure(p,env[1],env[7])if terminal is None else terminal());cp=p['result']
        else:cp,value=D.successful(row,env)
    status,step=('DIALOGUE_REPAIR_REQUIRED','dialogue-review')if terminal is None else('MODEL_RECONCILIATION_REQUIRED','disclosure-review')
    require(value['status']==status and value['next_step']==step
        and value['descriptor']==p['descriptor']and value['checkpoint']==p['blocked']
        and read(binding(root/'hold.json'))['outcome']==p['outcome'],'completed successor is not the first review rejection')
    return cp,value

def validate_inventory(selected,env,p):
    q=read(selected['plan']);C,I,P,W,R,config,root,controls=env
    require(set(selected)=={'helper','plan','projection','config','snapshot','kind'}and selected['kind']=='completed_pre_dispatch'
        and selected['helper']==q['helper']==binding(__file__)
        and selected['projection']==dict(environment=3,dispatcher=2,job=1)
        and selected['config']==q['config']and selected['snapshot']==q['snapshot']
        and all(q[k]==p[k]for k in('workbook','source','original_job','outcome','result','descriptor','blocked'))
        and config==read(q['config'])and config['code']==q['snapshot']and config['workbook']==q['workbook']
        and root==Path(read(q['snapshot'])['root'])and all(Path(m.__file__).is_relative_to(root)for m in(C,I,P,W,R))
        and C.SECONDS==3300 and C.MEMORY==8589934592,'retained original source inventory or guard changed')
    return q

def source_context(row):
    p=read(row);old,fresh,original,job=selection(p);U=module('pre_dispatch_semantic_utilities',p['utilities']);M=module('pre_dispatch_completed_original',old['helper'])
    root=Path(fresh['code_root'])
    for name,loaded in tuple(sys.modules.items()):
        if name=='scripts'or name.startswith('scripts.'):
            require(getattr(loaded,'__file__',None)and Path(loaded.__file__).is_relative_to(root),
                    'foreign code already loaded before original source setup')
    sys.path.insert(0,str(root))
    # Original source/configuration verification runs before selecting the sole
    # already-proved cold setup delta. The historical dispatcher stays intact.
    _,_,_,oldenv=U.validated_authoring(dict(workbook=p['workbook'],fresh_plan=p['fresh_plan'],original_job=old['original_job']))
    M.renewal_dispatcher(original['driver'],job['driver']);D=module('pre_dispatch_selected_dispatcher',job['driver']);env=D.setup(job)
    require(env[5:]==oldenv[5:]and env[6]==Path(fresh['code_root']),'selected setup changed original task or controls')
    M.renewal_setup_guard(old,original,D,env)
    return p,old,job,U,D,env


def context(row,*,initial=False):
    require(initial is False,'completed pre-dispatch context is history only')
    p,old,job,U,D,env=source_context(row)
    require('terminal_http'not in p,'semantic continuation cannot adopt a terminal transport failure')
    completed_prefix(U,p,old,D,env,job)
    value,chosen,lease=U.completed_failure(p,env[1],env[7]);w,controls,_=env[1].selected(p['descriptor'])
    require(w.root==env[6]and w.run==Path(env[5]['run_root'])/'instruction'and controls==env[7]
        and chosen.content.workbook==p['workbook']and chosen.content.bindings['original_raw_source']==p['source']
        and w.dialogue_semantic_retry is None,'completed rejection is not original unused semantic round two')
    # Shared semantic machinery closes this manager at the same boundary as
    # other retained readers. There are no module projections to restore here.
    D._retained_disclosure_manager=ExitStack();D._retained_disclosure_original_env=env
    return p,job,D,env


def terminal_http_failure(p,D,env):
    """A sealed 400 is transport evidence, never a semantic rejection or pass."""
    from scripts.fleet import disclosure_payload_continue as P
    from scripts.batch import harbor_disclosure_review_round as Q
    selected=dict(p,old_snapshot=p['snapshot'])
    value,descriptor=P.failure(selected,D,env[5]);worker,controls,_=env[1].selected(p['descriptor'])
    chosen=env[1].codec.decode(value['selection']);lease=env[0].decode(value['lease'])
    require(value['descriptor']==p['descriptor']and value['checkpoint']==p['blocked']
        and worker.root==env[6]and worker.run==Path(env[5]['run_root'])/'instruction'and controls==env[7]
        and chosen.content.workbook==p['workbook']and chosen.content.bindings['original_raw_source']==p['source'],
        'HTTP continuation changed original guarded source')
    request=env[3].request_saved(worker.run/'steps/disclosure-review/request.json')
    from scripts.batch import harbor_disclosure_payload_repair as payload
    rejected,_,_=payload._rejected(request.prepared,worker.campaign)
    require(p['terminal_http']==rejected,'HTTP continuation selected another sealed failure')
    Q.closed_rejection(rejected,Q.semantic_scope(dict(workbook=p['workbook'],original_source=p['source'])),1)
    return value,chosen,lease


@contextmanager
def cold_context(row,*,continued=True):
    require(continued is True,'HTTP context only reads the completed stopped prefix')
    p,old,job,U,D,env=source_context(row)
    require('terminal_http'in p,'explicit sealed HTTP rejection required')
    terminal=lambda:terminal_http_failure(p,D,env)
    completed_prefix(U,p,old,D,env,job,terminal=terminal)
    value,selection,lease=terminal()
    yield p,job,D,env,lease
