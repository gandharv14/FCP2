from types import SimpleNamespace
from dataclasses import dataclass
import copy
import pytest
from scripts.fleet import semantic_publication_context as R


def require(ok,message):
    if not ok:raise ValueError(message)


def fixture():
    def b(path):return dict(path='/'+path,sha256=path)
    a=dict(reader=b('author'),plan=b('plan'),completed_reader=b('completed'))
    source=b('source');fresh=b('fresh');q=dict(workbook='0079',source=source,fresh_plan=fresh,takeover=b('takeover'))
    p=dict(helper=a['reader'],workbook='0079',source=source,fresh_plan=fresh,physical_context=b('physical'))
    plan=dict(semantic_continuation=a,workbook='0079',source_sha256='source',fresh_authoring=fresh)
    files={'/plan':p,'/physical':q}
    pub=SimpleNamespace(require=require,checked=lambda r:files[r['path']],binding=lambda path:b(path.lstrip('/')))
    return pub,plan,p,q


def test_completed_history_keeps_original_semantic_plan():
    pub,plan,p,q=fixture();assert R.history(pub,plan)==plan['semantic_continuation']['plan']


@pytest.mark.parametrize('case',['source','workbook','fresh','takeover','mixed','helper'])
def test_history_rejects_detached_or_mixed_custody(case):
    pub,plan,p,q=fixture()
    if case=='source':q['source']=dict(path='/source',sha256='other')
    elif case=='workbook':p['workbook']='0007'
    elif case=='fresh':q['fresh_plan']=dict(path='/other',sha256='other')
    elif case=='takeover':q['takeover']=None
    elif case=='mixed':p['completed_takeover']={}
    else:p['helper']=dict(path='/other',sha256='other')
    with pytest.raises(ValueError):R.history(pub,plan)


@dataclass
class Lease:
    owner:str='owner'
    token:str='token'
    revision:int=14
    sha256:str='before'


def retained_fixture():
    previous=Lease();authority=dict(path='/authority',sha256='authority')
    before=dict(revision=14,leases=dict(task_agent=dict(owner='owner',token='token',heartbeat_at=10,expires_at=7200000000100)))
    value=dict(schema_version='fleet-retained-live-continuation-lease/v1',authority=authority,
        lease=vars(previous),tracker=dict(path='/live',sha256='before'),validated_evidence=[dict(path='/live',sha256='before'),dict(path='/evidence',sha256='evidence')],
        minimum_remaining_ns=7200*10**9,observed_at_ns=100,original_expired_replacements=1,lease_operations=0,
        task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False)
    files={'/receipt':value,'/archive':before};pub=SimpleNamespace(require=require,checked=lambda r:files[r['path']],binding=lambda path:dict(path=path,sha256='before'if path=='/archive'else'evidence'))
    return pub,value,previous,authority,dict(before=dict(path='/archive',sha256='before'))


def test_original_retained_live_lease_is_read_without_operation():
    pub,value,lease,authority,semantic=retained_fixture()
    assert R.retained_live(pub,dict(path='/receipt'),authority,lease,semantic)==value


@pytest.mark.parametrize('case',['authority','operations','budget','duration','time','tracker','lease','evidence'])
def test_retained_live_rejects_mutated_evidence(case):
    pub,value,lease,authority,semantic=retained_fixture()
    if case=='authority':value['authority']={}
    elif case=='operations':value['lease_operations']=1
    elif case=='budget':value['semantic_budget_reset']=True
    elif case=='duration':value['minimum_remaining_ns']=1
    elif case=='time':value['observed_at_ns']=101
    elif case=='tracker':value['tracker']['sha256']='wrong'
    elif case=='lease':value['lease']=dict(value['lease'],token='foreign')
    else:value['validated_evidence'][1]['sha256']='wrong'
    with pytest.raises(ValueError):R.retained_live(pub,dict(path='/receipt'),authority,lease,semantic)


def test_reader_evidence_uses_same_physical_history_as_dispatcher():
    from scripts.fleet import fresh_publication as F
    pub,plan,p,q=fixture()
    keys=('authorization','capacity_policy','renewal_module','original_job','outcome','result','descriptor','blocked')
    for key in keys:p[key]=dict(path='/semantic-'+key,sha256=key)
    for key in ('helper','config','snapshot','old_snapshot','original_job','outcome','result','preview','renewal'):
        q[key]=dict(path='/physical-'+key,sha256=key)
    p['tracker']=dict(path='/live-tracker',sha256='old')
    q['tracker']=dict(path='/live-tracker',sha256='older')
    plan['author_helper']=dict(path='/original-author',sha256='code')
    pub.FRESH_DEPENDENCIES={};pub.load=lambda name,row:R
    rows=F.reader_evidence(pub,plan)
    assert q['takeover'] in rows and p['physical_context'] in rows
    assert p['fresh_plan'] in rows and q['config'] in rows
    assert p['tracker'] not in rows and q['tracker'] not in rows
    q['source']=dict(path='/wrong',sha256='wrong')
    with pytest.raises(ValueError):F.reader_evidence(pub,plan)


def test_closed_repair_changes_only_original_dependency_region(tmp_path):
    import ast
    from pathlib import Path
    from scripts.fleet.publication_launch import semantic_evidence_repair
    root=Path(__file__).resolve().parents[1]
    oldfresh=(root/'diagnostics/bulk-production/0079-http-rejection-001/publication-001/implementation-001/fresh.py').read_text()
    old=dict(path='/old',sha256='old');new=dict(path='/new',sha256='new')
    pub='first='+repr(old)+'\nsecond='+repr(old)+'\n'
    changed,fresh=semantic_evidence_repair(pub,oldfresh,old,new)
    assert changed=='first='+repr(new)+'\nsecond='+repr(new)+'\n'
    def functions(text):return {x.name:ast.dump(x,include_attributes=False) for x in ast.parse(text).body if isinstance(x,ast.FunctionDef)}
    a,b=functions(oldfresh),functions(fresh)
    assert {k for k in a if a[k]!=b[k]}=={'reader_evidence'}
    with pytest.raises(ValueError):semantic_evidence_repair(changed,fresh,new,new)
