"""Source-bound capacity keeps real sealed transport and per-partition budgets."""
import json,time
from pathlib import Path
import pytest
from scripts.batch import harbor_normalization_terminal_repair as T
from scripts.batch import harbor_normalization_partitioned as P
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read,record
from test_harbor_normalization_producer import setup
from test_normalization_terminal_repair import closed
from test_normalization_partitioned import respond


def policy(args,tmp_path,n=5,change=None):
 s,new,campaign,previous,plan,auth=args;now=time.time_ns()
 p=dict(schema_version=T.CAPACITY_SCHEMA,authorization=auth,workbook=s.workbook,
   source=s.bindings['original_raw_source'],execution_plan=plan,maximum_partitions=n,
   maximum_additional_physical_calls=2*n,original_consumed_physical_calls=1,
   partition_round_limit=2,transport_retries=0,issued_at_ns=now,expires_at_ns=now+86400*10**9)
 if change:change(p)
 f=tmp_path/'capacity.json';f.write_text(json.dumps(p));return record(f)


def propose(args,tmp_path,capacity,n=5):
 s,new,campaign,previous,plan,auth=args
 return T.propose(new,campaign,previous_selection=previous,execution_plan=plan,
   authorization=auth,partitions=n,directory=tmp_path/'permit',partition_capacity=capacity)


def test_five_requires_explicit_exact_source_request_capacity(setup,monkeypatch,tmp_path):
 args=closed(setup,monkeypatch,tmp_path);s,new,campaign,previous,plan,auth=args
 old=E.location(T.prepared(read(plan)),campaign);before={p.name:p.read_bytes() for p in old.iterdir()}
 with pytest.raises(ValueError,match='explicit capacity'):
  propose(args,tmp_path,None)
 assert not (tmp_path/'permit').exists()
 cap=policy(args,tmp_path);permit=propose(args,tmp_path,cap)
 p,files=T.validate(permit,new,campaign,at=time.time_ns())
 assert p['schema_version']==T.EXTENDED_SCHEMA and p['partitions']==5 and p['maximum_additional_physical_calls']==10
 assert p['original_consumed_physical_calls']==1 and files[cap['path']]==cap['sha256']
 assert {p.name:p.read_bytes()for p in old.iterdir()}==before
 # The actual computed partition count must still agree: a capacity record does
 # not permit manufacturing additional reviews for a smaller workbook.
 with pytest.raises(ValueError,match='bounded count'):
  P.prepare(new,campaign,tmp_path/'parts',prior_plan=previous,terminal_rejection=permit)
 assert not (old.parent/'scope-delegation.json').exists()


@pytest.mark.parametrize('change',[
 lambda p:p.update(workbook='9999'),
 lambda p:p.update(source=p['execution_plan']),
 lambda p:p.update(execution_plan=p['source']),
 lambda p:p.update(authorization=p['source']),
 lambda p:p.update(maximum_partitions=True),
 lambda p:p.update(maximum_partitions=33,maximum_additional_physical_calls=66),
 lambda p:p.update(maximum_additional_physical_calls=11),
 lambda p:p.update(original_consumed_physical_calls=0),
 lambda p:p.update(partition_round_limit=3),
 lambda p:p.update(transport_retries=1),
 lambda p:p.update(expires_at_ns=p['issued_at_ns']-1),
 lambda p:p.update(expires_at_ns=p['issued_at_ns']+86401*10**9),
 lambda p:p.update(issued_at_ns=1,expires_at_ns=2),
 lambda p:p.update(extra='unsupported'),
])
def test_foreign_unbounded_expired_or_retry_capacity_rejected_before_proposal(setup,monkeypatch,tmp_path,change):
 args=closed(setup,monkeypatch,tmp_path);cap=policy(args,tmp_path,change=change)
 with pytest.raises(ValueError):propose(args,tmp_path,cap)
 assert not (tmp_path/'permit').exists()


def test_real_partition_calls_and_aggregate_bind_extended_capacity(setup,monkeypatch,tmp_path):
 args=closed(setup,monkeypatch,tmp_path);s,new,campaign,previous,plan,auth=args
 cap=policy(args,tmp_path,n=4);permit=propose(args,tmp_path,cap,n=4)
 monkeypatch.setattr(P.K,'MAX_REFERENCES',1)
 prepared=P.prepare(new,campaign,tmp_path/'parts',prior_plan=previous,terminal_rejection=permit)
 calls=[];respond(monkeypatch,new,calls=calls)
 for i in range(4):P.dispatch(prepared,i,1,credential='synthetic')
 result=P.aggregate(prepared)
 assert len(calls)==4 and result['consumed_physical_attempts']==5
 assert result['checked_files'][cap['path']]==cap['sha256']
 with pytest.raises(ValueError,match='valid partition'):
  P.dispatch(prepared,0,2,credential='synthetic')
 Path(cap['path']).write_bytes(Path(cap['path']).read_bytes()+b' ')
 with pytest.raises(ValueError):P.aggregate(prepared)
