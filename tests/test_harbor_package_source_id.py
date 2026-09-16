"""Exact sealed-failure and completed physical-command boundaries."""
from dataclasses import dataclass,replace,asdict
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import json,sys
import pytest
from scripts import harbor_package_source_id as S
from scripts.fleet import package_cli_runtime as CLI


def put(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return CLI.binding(p)

def checked(row):
    assert CLI.binding(row['path'])==row
    return Path(row['path']).read_bytes()

@dataclass
class Content:
    workbook:str
    bindings:dict
    mcp_build_a:Path
    mcp_build_b:Path

def encode(c):
    return dict(workbook=c.workbook,bindings=c.bindings,mcp_build_a=str(c.mcp_build_a),mcp_build_b=str(c.mcp_build_b))
def decode(v):return Content(v['workbook'],v['bindings'],Path(v['mcp_build_a']),Path(v['mcp_build_b']))

@pytest.fixture
def failed(tmp_path,monkeypatch):
    root=tmp_path/'code';root.mkdir();entry=root/'xl_variable_mcp.py';entry.write_text('pass\n');producer=root/'producer.py';producer.write_text('pass\n');campaign=root/'campaign.py';campaign.write_text('pass\n')
    normalized=put(tmp_path/'normalized.json',{});source=put(tmp_path/'0496.xlsx',{})
    cp=dict(workbook='0496',source=source,normalized=normalized,cli=CLI.binding(entry))
    old=tmp_path/'environment/packaging';old.mkdir(parents=True)
    c=Content('0496',dict(source_file=source,normalized_final=normalized),old/'build-a',old/'build-b');current=SimpleNamespace(content=c,public_profiles=['authentic profiles'])
    descriptor=put(tmp_path/'descriptor.json',{'controls':{'exact':'controls'}});checkpoint=put(tmp_path/'checkpoint.json',{})
    commands=lambda c,work,tax: {'build-a':[str(entry),'build',normalized['path'],str(work/'build-a'),'--workbook','0496','--source',str(tmp_path)]}
    put(old/'selection.json',dict(schema_version='harbor-environment-production/v1',content=encode(c),profiles=current.public_profiles,taxonomy='tax',code=CLI.binding(producer),mode='mcp'))
    put(old/'build-a.intent.json',dict(argv=[sys.executable,*commands(c,old,'tax')['build-a']],entry=cp['cli'],model_calls_permitted=False))
    (old/'build-a.log').write_text('Traceback\nValueError: Duplicate explicit source id source-one collides with a different source definition\n')
    put(old/'build-a.result.json',dict(returncode=1,log=CLI.binding(old/'build-a.log')))
    guard=tmp_path/'guard';guard.mkdir();(guard/'step.log').write_text('scripts.batch.harbor_admission.AdmissionError: packaging command failed: build-a\n')
    intent=put(guard/'intent.json',dict(schema_version='harbor-coordinator-step/v1',action='step',arguments=dict(descriptor=descriptor,checkpoint=checkpoint,captures=None),dispatch=True,code=CLI.binding(campaign),controls=CLI.read(descriptor)['controls'],memory_limit_bytes=8*1024**3,timeout_seconds=3300,executable=CLI.binding(Path(sys.executable).resolve())))
    resource=put(guard/'resource.json',dict(intent=intent,returncode=1,metrics={},log=CLI.binding(guard/'step.log'),failure=None))
    outcome=put(guard/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None))
    tp=dict(selection=dict(guard=resource,checkpoint=checkpoint))
    R=SimpleNamespace(read=CLI.read,record=CLI.binding,require=CLI.require,E=SimpleNamespace(checked_bytes=checked),P=SimpleNamespace(__file__=producer),C=SimpleNamespace(__file__=campaign),MEMORY=8*1024**3,decode=decode,encode=lambda x:encode(x)if isinstance(x,Content)else x,commands=commands,checked_tree=lambda d:[CLI.binding(x)for x in sorted(d.rglob('*'))if x.is_file()],notes=SimpleNamespace(evidence_list=lambda v:v))
    monkeypatch.setattr(S,'profile',lambda R,row:({},None,cp,tp))
    return SimpleNamespace(R=R,cp=cp,tp=tp,descriptor=descriptor,checkpoint=checkpoint,outcome=outcome,s=SimpleNamespace(run=old.parent,taxonomy='tax'),current=current,old=old,guard=guard)

def invoke(f):return S.failure(f.R,{},f.descriptor,f.checkpoint,f.outcome,f.s,f.current)

def test_exact_failed_build_preserves_every_byte(failed):
    f=failed;before={p:p.read_bytes()for p in f.old.rglob('*')if p.is_file()};e=invoke(f)
    assert {str(p)for p in before} <= {x['path']for x in e}
    assert all(p.read_bytes()==v for p,v in before.items())

@pytest.mark.parametrize('extra',['build-b.intent.json','mask.intent.json','package.intent.json','complete.json','build-a.runtime.json'])
def test_later_or_already_corrected_command_is_not_replayed(failed,extra):
    put(failed.old/extra,{})
    with pytest.raises(ValueError,match='later package'):invoke(failed)

@pytest.mark.parametrize('change',['different_error','successful_build','wrong_source','model_permission','different_argv','unsealed_guard','other_takeover'])
def test_rehashed_substantive_drift_is_rejected(failed,change):
    f=failed
    if change=='different_error':
        (f.old/'build-a.log').write_text('ValueError: different error\n');put(f.old/'build-a.result.json',dict(returncode=1,log=CLI.binding(f.old/'build-a.log')))
    elif change=='successful_build':put(f.old/'build-a.result.json',dict(returncode=0,log=CLI.binding(f.old/'build-a.log')))
    elif change=='wrong_source':f.cp['source']=f.cp['normalized']
    elif change in ('model_permission','different_argv'):
        v=CLI.read(CLI.binding(f.old/'build-a.intent.json'))
        if change=='model_permission':v['model_calls_permitted']=True
        else:v['argv'].append('--other')
        put(f.old/'build-a.intent.json',v)
    elif change=='other_takeover':f.tp['selection']['checkpoint']=f.descriptor
    else:
        v=CLI.read(CLI.read(f.outcome)['resource']);v['failure']={'type':'TimeoutError'}
        res=put(f.guard/'resource.json',v);o=CLI.read(f.outcome);o['resource']=res;f.outcome=put(f.guard/'outcome.json',o)
    with pytest.raises(ValueError):invoke(f)

def test_fresh_namespace_and_inputs_are_both_required(failed,tmp_path):
    f=failed;f.cp['work']=str(tmp_path/'fresh/package')
    S.destination(f.R,{},tmp_path/'fresh',f.current)
    with pytest.raises(ValueError):S.destination(f.R,{},tmp_path/'other',f.current)


def test_historical_takeover_requires_exact_archived_after_not_live_alias(failed,monkeypatch):
    f=failed
    @dataclass
    class Lease:
        owner:str;token:str;revision:int;sha256:str
    old=Lease('old','token1',4,'a'*64);new=Lease('new','token2',5,'b'*64)
    raw_source={'path':'/retained-original/0496.xlsx','sha256':'c'*64}
    before={'revision':4,'leases':{'task_agent':{'owner':'old','token':'token1'}},'bindings':{'original_raw_source':raw_source,'source_file':f.cp['source']}}
    after={**before,'revision':5};selection=dict(checkpoint=f.checkpoint,worker_kind='environment',workbook='0496',tracker={'path':str(f.old.parent/'0496.json')})
    stop=put(f.old.parent/'stop.json',dict(selection=selection,before={'sha256':old.sha256}));done=put(f.old.parent/'takeover.json',dict(stopped_owner_proof=stop))
    monkeypatch.setattr(S,'profile',lambda R,row:({'takeover_completion':done},None,f.cp,{'selection':selection}))
    @contextmanager
    def takeover(*a):yield SimpleNamespace(validate_completion=lambda b:(before,after,new),_selection=lambda v:v)
    monkeypatch.setattr(S,'takeover',takeover)
    f.R.read=lambda row: {'selection':None} if row==f.checkpoint else CLI.read(row)
    f.R.decode=lambda v:SimpleNamespace(content=SimpleNamespace(bindings={'original_raw_source':raw_source,'source_file':f.cp['source']}))
    f.R.C.selected=lambda d:(SimpleNamespace(bootstrap=SimpleNamespace(policy=SimpleNamespace(tracker_root=f.old.parent),workbook='0496')),None,None)
    S.lease(f.R,{},f.descriptor,f.checkpoint,old,after,new)
    with pytest.raises(ValueError,match='detached'):S.lease(f.R,{},f.descriptor,f.checkpoint,old,{**after,'revision':6},new)
    with pytest.raises(ValueError,match='detached'):S.lease(f.R,{},f.descriptor,f.checkpoint,replace(old,token='foreign'),after,new)
    changed={**after,'bindings':{**after['bindings'],'source_file':raw_source}}
    with pytest.raises(ValueError):S.lease(f.R,{},f.descriptor,f.checkpoint,old,changed,new)

from test_package_cli_runtime import setup as cli_setup

@pytest.fixture
def selected_profile(cli_setup,monkeypatch,tmp_path):
    cp,clirow,_,_,producer,keep=cli_setup;root=producer.ROOT
    modules={}
    for name,relative in [('P','scripts/batch/harbor_environment_producer.py'),('C','scripts/harbor_campaign.py'),('F','scripts/batch/harbor_environment_refresh.py'),('W','scripts/batch/harbor_environment_worker.py'),('notes','scripts/batch/harbor_lane_notes.py')]:
        q=root/relative;q.parent.mkdir(parents=True,exist_ok=True);q.write_text('pass\n');modules[name]=SimpleNamespace(__file__=q)
    snapshot=keep(tmp_path/'snapshot.json',dict(root=str(root),files=[dict(relative=str(p.relative_to(root)),sha256=CLI.binding(p)['sha256'])for p in root.rglob('*')if p.is_file()]))
    shared={k:put(tmp_path/(k+'.json'),{})for k in ('lifecycle','allowance','takeover','stdlib')}
    takeover=put(tmp_path/'takeover-profile.json',dict(schema_version='harbor-allowance-takeover-direct/v1',original_snapshot=snapshot,shared=shared,allowance=put(tmp_path/'authorization.json',{'limits':{'stage_seconds':7200}})))
    recovery=put(tmp_path/'recovery.py',{})
    p=dict(schema_version=S.SCHEMA,guard_seconds=3300,helper=CLI.binding(S.__file__),snapshot=snapshot,cli_runtime=CLI.binding(CLI.__file__),cli_profile=clirow,takeover_profile=takeover,takeover_completion=put(tmp_path/'done.json',{}),recovery=recovery,retained_helper=put(tmp_path/'context.py',{}),retained_plan=put(tmp_path/'context.json',{}))
    R=SimpleNamespace(read=CLI.read,record=CLI.binding,require=CLI.require,E=SimpleNamespace(checked_bytes=checked),ROOT=root,AFTER_PRODUCER=CLI.binding(modules['P'].__file__)['sha256'],**modules)
    return R,put(tmp_path/'integration.json',p),root,cli_setup


def test_complete_original_snapshot_is_authenticated(selected_profile):
    R,row,root,_=selected_profile;S.profile(R,row)
    (root/'unselected.py').write_text('pass\n')
    with pytest.raises(ValueError,match='snapshot changed'):S.profile(R,row)


def test_completed_physical_receipts_bind_both_exact_invocations(selected_profile):
    R,row,root,values=selected_profile;cp,clirow,intent,argv,producer,keep=values;work=Path(cp['work'])
    for label in ('build-a','build-b'):
        command=list(argv);command[4]=str(work/label);ib=put(work/(label+'.intent.json'),dict(argv=command,entry=cp['cli'],model_calls_permitted=False))
        put(work/(label+'.runtime.json'),dict(schema_version=CLI.SCHEMA,profile=clirow,intent=ib,logical_command=command,physical_command=CLI.physical(clirow,ib),automatic_retry=False))
    S.completed_commands(R,row,work)
    p=work/'build-b.runtime.json';v=CLI.read(CLI.binding(p));v['physical_command'].append('--other');put(p,v)
    with pytest.raises(ValueError,match='physical source-ID'):S.completed_commands(R,row,work)


@pytest.mark.parametrize('seconds',[900,3301,7201,True])
def test_only_explicit_bounded_source_id_guard_is_selected(selected_profile,seconds):
    R,row,_,_=selected_profile;p=CLI.read(row);p['guard_seconds']=seconds;row=put(Path(row['path']).with_name('changed-guard.json'),p)
    with pytest.raises(ValueError,match='guard exceeds'):S.profile(R,row)
