import copy,json,time
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import publication_finalization_launch as M


def test_exact_held_promotion_allows_unrelated_live_models():
    p=dict(expected_owner='old',expected_fence=7,prior_reason='uncertain publication; reconcile without replay')
    plan=dict(workbook='0303',code_root='/code/version',source_sha256='source')
    w=dict(workbook='0303',owner='old',fence=7,state='hold',version='version',source_sha256='source',reason=p['prior_reason'])
    model_owner=dict(workbook='0001',owner='other',fence=1,state='active',expires=time.time()+60)
    s=dict(at=time.time(),workbooks=[w,model_owner],operations=[dict(kind='model',state='active',workbook='0001',owner='other',fence=1)])
    cap=NS(validate_occupancy=lambda s:None)
    assert M.central(s,p,plan,cap)==w
    for key,value in [('owner','wrong'),('fence',8),('state','active'),('reason','different'),('version','other'),('source_sha256','other')]:
        changed=copy.deepcopy(s);changed['workbooks'][0][key]=value
        with pytest.raises(ValueError):M.central(changed,p,plan,cap)
    for change in ['uncertain','publication','target-model','stale','unrecognized']:
        changed=copy.deepcopy(s)
        if change=='uncertain':changed['operations'][0]['state']='hold'
        elif change=='publication':changed['operations'][0]['kind']='publication'
        elif change=='target-model':changed['operations'][0]['workbook']='0303'
        elif change=='stale':changed['at']-=61
        else:changed['operations'][0]['owner']='wrong'
        with pytest.raises(ValueError):M.central(changed,p,plan,cap)


@pytest.mark.parametrize('change',['none','guard','not-qualified','changed-code','different-original','different-tracker'])
def test_native_full_qualification_binding_is_required(tmp_path,change):
    def put(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return M.binding(p)
    def envelope(name,value,rc=0):
        return put(name,dict(returncode=0,stdout=json.dumps(dict(returncode=rc,failure=None,seconds=3300,memory_bytes=8589934592,log=json.dumps(value)))))
    a={'path':'/original','sha256':'a'};publisher={'path':'/publisher','sha256':'p'}
    selected=dict(original_plan=a,original_publisher=publisher,digest_cache={'sha256':'d'},pair_lanes={'sha256':'l'},pair_ready={'sha256':'r'})
    plan={'finalization':put('selection.json',selected)}
    p={'plan':{'path':'/new-plan','sha256':'n'},'publisher':{'path':'/new-publisher','sha256':'np'}}
    q=dict(status='CURRENT_QUALIFICATION_VERIFIED_READ_ONLY',models_dispatched=0,publication_mutations=0,qualification=dict(verified_promoted=True,revalidation_required=False),tracker=a,plan=a,publisher=publisher,cache={'sha256':'d'},performance={'harbor_completed_lanes.py':{'sha256':'l'},'harbor_ready_transitions.py':{'sha256':'r'}})
    c=dict(status='RETAINED_FINALIZATION_CONTEXT_VALIDATED',plan=p['plan'],publisher=p['publisher'],tracker=a,models_dispatched=0,publication_mutations=0)
    if change=='not-qualified':q['qualification']['verified_promoted']=False
    elif change=='changed-code':q['performance']['harbor_completed_lanes.py']['sha256']='wrong'
    elif change=='different-original':q['plan']=publisher
    elif change=='different-tracker':c['tracker']=publisher
    p['qualification']=envelope('q.json',q,1 if change=='guard' else 0);p['native_context']=envelope('c.json',c)
    if change=='none':assert M.proofs(p,plan)==(q,c)
    else:
        with pytest.raises(ValueError):M.proofs(p,plan)


def source_case(tmp_path,monkeypatch):
    def put(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value));return M.binding(path)
    auth=put('auth','explicit authorization');monkeypatch.setattr(M,'AUTH',auth['sha256'])
    dep=put('dependency','code');oldpub=put('oldpub','old publisher')
    qpub=put('qpub','qualified publisher');newpub=put('newpub','finalization publisher')
    original=put('original',dict(journal='/native/prior',workbook='0187',source_sha256='source',
        code_root='/code/version',publication_wait={'original':True},source_revision={'completed_reader':dep}))
    intent={'path':'/native/prior/owning-publication/intent.json','sha256':'intent'}
    resume=put('resume',dict(prior_plan=original,intent=intent))
    qp=put('qualified',dict(M.read(original),journal='/native/resume',publication_resume=resume))
    qi=put('qi',dict(publisher=qpub,dependencies={'reader':dep}))
    implementation=put('implementation',dict(publisher=newpub,dependencies={'reader':dep,'source_finalization':dep}))
    policy=put('policy',dict(schema_version='fleet-source-publication-finalization-policy/v1',authorization=auth,
        wall_seconds=3300,memory_bytes=8589934592,disk_reserve_bytes=8589934592,maximum_wait_seconds=120,maximum_finalizations=1))
    selected=dict(schema_version=M.SOURCE_SCHEMA,original_plan=original,original_publisher=oldpub,intent=intent,
        failure={'path':'/native/prior/outcome','sha256':'failure'},finalizer=dep,lifecycle=dep,authorization=auth,
        resource_policy=policy,context_reader=dep,qualification_plan=qp,qualification_implementation=qi)
    sr=put('selection',selected);plan=M.read(original);plan.pop('publication_wait');plan.update(journal='/native/finalization',finalization=sr)
    p=dict(plan=put('plan',plan),publisher=newpub,implementation=implementation)
    tracker={'path':'/native/tracker','sha256':'current'}
    q=dict(status='CURRENT_QUALIFICATION_VERIFIED_READ_ONLY',plan=qp,publisher=qpub,tracker=tracker,
        qualification=dict(verified_promoted=True,revalidation_required=False,state='PROMOTED',prior_promotion=True,
            failure=None,tracker=tracker['path'],tracker_sha256=tracker['sha256']),models_dispatched=0,publication_mutations=0,at=123)
    context=dict(status='RETAINED_FINALIZATION_CONTEXT_VALIDATED',plan=p['plan'],publisher=newpub,
        tracker=tracker,models_dispatched=0,publication_mutations=0)
    def evidence(q,context):
        for name,value in [('qualification',q),('native_context',context)]:
            p[name]=put(name,dict(returncode=0,stdout=json.dumps(dict(returncode=0,failure=None,
                seconds=3300,memory_bytes=8589934592,log=json.dumps(value)))))
    evidence(q,context)
    return put,p,plan,selected,q,context,evidence


def test_source_qualification_preserves_distinct_original_and_qualified_publishers(tmp_path,monkeypatch):
    _,p,plan,selected,q,context,_=source_case(tmp_path,monkeypatch)
    assert len({selected['original_publisher']['sha256'],q['publisher']['sha256'],p['publisher']['sha256']})==3
    assert M.proofs(p,plan)==(q,context)


@pytest.mark.parametrize('change',['authorization','fields','source','journal','qualified-source','resume-intent',
    'dependencies','dependency-bytes','policy-limit','policy-guard','original-publisher','intent-location',
    'qualification-publisher','qualification-plan','performance','promoted','tracker','models','context'])
def test_source_finalization_rejects_detached_or_weakened_custody(tmp_path,monkeypatch,change):
    put,p,plan,s,q,c,evidence=source_case(tmp_path,monkeypatch)
    if change=='authorization':s['authorization']=put('different-auth','not authorized')
    elif change=='fields':s['arbitrary']=True
    elif change=='source':plan['source_sha256']='different'
    elif change=='journal':plan['journal']=M.read(s['original_plan'])['journal']
    elif change=='qualified-source':
        v=M.read(s['qualification_plan']);v['source_sha256']='different';s['qualification_plan']=put('other-qp',v);q['plan']=s['qualification_plan']
    elif change=='resume-intent':
        v=M.read(s['qualification_plan']);v['publication_resume']=put('other-resume',dict(prior_plan=s['original_plan'],intent=s['failure']));s['qualification_plan']=put('other-qp',v);q['plan']=s['qualification_plan']
    elif change=='dependencies':p['implementation']=put('other-impl',dict(publisher=p['publisher'],dependencies={}))
    elif change=='dependency-bytes':Path(s['finalizer']['path']).write_text('changed')
    elif change.startswith('policy-'):
        v=M.read(s['resource_policy']);v['maximum_finalizations' if change=='policy-limit' else 'wall_seconds']=2;s['resource_policy']=put('other-policy',v)
    elif change=='original-publisher':s['original_publisher']['sha256']='wrong'
    elif change=='intent-location':s['intent']['path']='/native/foreign/owning-publication/intent.json'
    elif change=='qualification-publisher':q['publisher']=s['original_publisher']
    elif change=='qualification-plan':q['plan']=s['original_plan']
    elif change=='performance':q['performance']={}
    elif change=='promoted':q['qualification']['verified_promoted']=False
    elif change=='tracker':q['qualification']['tracker_sha256']='different'
    elif change=='models':q['models_dispatched']=1
    elif change=='context':c['tracker']={'path':'/foreign','sha256':'different'}
    plan['finalization']=put('new-selection',s);evidence(q,c)
    with pytest.raises(ValueError):M.proofs(p,plan)
