"""Read actual completed pre-dispatch continuations for original publication.

The original fresh source authority, stopped failure, selected dispatcher and
every completed successor remain evidence. No initial live context is replayed.
"""
from pathlib import Path


def selection(pub,plan,ready,job):
    a=plan['completed_pre_dispatch']
    pub.require(set(a)=={'reader','plan'} and pub.binding(__file__)==a['reader'],
                'closed completed pre-dispatch reader required')
    p=pub.checked(a['plan']);original_worker=pub.checked(p['original_worker_job'])
    fresh=pub.checked(plan['fresh_authoring']);original=pub.checked(p['original_job'])
    pub.require(p['schema_version']=='fleet-pre-dispatch-continuation/v1'
        and 'renewal_setup'in p and set(p['renewal_setup'])=={'dispatcher','completed'}
        and original_worker['inputs'][0]==plan['fresh_authoring']
        and original_worker['source']==p['source'] and original_worker['version']==Path(plan['code_root']).name
        and p['workbook']==fresh['workbook']==ready['workbook']==plan['workbook']
        and p['source']['sha256']==plan['source_sha256'] and fresh['code_root']==plan['code_root']
        and original['driver']==fresh['dispatcher'], 'completed continuation changed original fresh authority')
    expected=dict(original,driver=p['renewal_setup']['dispatcher'],outcome=p['renewal_setup']['completed'],operations=p['operations'])
    pub.require(job==expected and ready['plan']==a['plan'] and ready['status']=='PUBLICATION_READY'
        and ready['publication_performed']is False and ready['automatic_retry']is False
        and ready['config']==original['config'] and ready['job']==pub.binding(Path(p['home'])/'authoring-job.json')
        and plan['ready']==pub.binding(Path(p['home'])/'result.json'),
        'publication does not select the actual completed continuation')
    outer=pub.checked(p['outer']);wi=pub.checked(outer['intent'])
    pub.require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==1
        and outer['failure_type']is None and outer['automatic_retry']is False
        and outer['publication_performed']is False and wi['job']==p['original_worker_job'],
        'original stopped author failure changed')
    M=pub.load('completed_pre_dispatch_original',p['helper'])
    M.renewal_dispatcher(original['driver'],job['driver'])
    return p,original,M


def completed_tail(pub,p,D,env,job,ready):
    ops=Path(p['operations']);directories=sorted(x for x in ops.iterdir()if x.is_dir())
    pub.require(0<len(directories)<=job['maximum_steps']
        and [x.name for x in directories]==['%03d'%i for i in range(len(directories))]
        and {x.name for x in ops.iterdir()}=={'job.json','hold.json',*[x.name for x in directories]}
        and pub.checked(pub.binding(ops/'job.json'))==job,
        'completed successor namespace is not a contiguous original dispatcher run')
    cp,value=D.successful(job['outcome'],env)
    for i,directory in enumerate(directories):
        row=pub.binding(directory/'outcome.json');out=pub.checked(row)
        intent=pub.checked(pub.checked(out['resource'])['intent'])
        if i==0:
            pub.require(set(intent)=={'job','checkpoint','seconds','driver'}
                and intent['job']==ready['job'] and intent['checkpoint']==cp
                and intent['driver']==job['driver'] and type(intent['seconds'])is int and 60<=intent['seconds']<=900,
                'first successor is not the original explicit renewal')
        else:
            pub.require(intent.get('action')=='step' and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=cp),
                        'completed successor skipped its original checkpoint')
        cp,value=D.successful(row,env)
    pub.require(row==ready['outcome'] and cp==ready['checkpoint'] and value['status']=='PUBLICATION_READY'
        and pub.checked(pub.binding(ops/'hold.json'))['outcome']==row,
        'last completed successor is not the actual READY guard')


def dispatcher(pub,pinned,plan,ready,job):
    p,original,M=selection(pub,plan,ready,job)
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
                'original author helper changed')
    D=pinned.load(pub,job['driver'],plan['author_helper'],namespace='completed_pre_dispatch_original_dispatcher')
    original_setup=D.setup
    def setup(observed):
        pub.require(observed==job,'publication changed the completed dispatcher job')
        env=original_setup(observed)
        M.renewal_setup_guard(p,original,D,env)
        completed_tail(pub,p,D,env,job,ready)
        return env
    D.setup=setup
    return D


def dependencies(pub,plan):
    a=plan['completed_pre_dispatch'];p=pub.checked(a['plan'])
    # The pre-renewal tracker is historical data inside the bound plan, not a
    # live file input after the original renewal and enqueue changed custody.
    return [a['reader'],a['plan'],*[p[k]for k in ('helper','original_worker_job','original_job','outer','failed','custody')],
            *p['renewal_setup'].values()]
