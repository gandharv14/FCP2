"""Real executor/partition protocol on synthetic closed and uncertain transports."""
from dataclasses import replace
import io,json,urllib.error
from pathlib import Path
import pytest
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_partitioned as P
from scripts.batch import harbor_normalization_terminal_repair as T
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read,record
from test_harbor_normalization_producer import setup,mock_review
from test_normalization_partitioned import respond


def closed(setup,monkeypatch,tmp_path,*,status=400,uncertain=False,complete=False):
 s,campaign,_,_=setup();prep=N.prepare(s);pc=N.prepared_call(s,prep)
 monkeypatch.setattr(E,'MAX_RETRIES',0)
 if complete:mock_review(monkeypatch,s)
 else:
  def fail(*a,**k):
   if uncertain:raise urllib.error.URLError('synthetic lost response')
   raise urllib.error.HTTPError(s.policy.endpoint,status,'synthetic terminal rejection',{},io.BytesIO(b'{"error":{"message":"rejected"}}'))
  monkeypatch.setattr(E,'_open',fail)
 N.dispatch(s,prep,campaign,credential='synthetic')
 previous=record(s.run/'production-selection.json');plan=record(E.location(pc,campaign)/'plan.json')
 auth=tmp_path/'authorization.txt';auth.write_text('Synthetic fixture explicitly permits bounded new partition calls.');a=record(auth)
 monkeypatch.setattr(T,'AUTHORIZATION_SHA256',a['sha256'])
 new=replace(s,run=tmp_path/'corrected'/s.run.name)
 return s,new,campaign,previous,plan,a


def propose(args,tmp_path):
 s,new,campaign,previous,plan,a=args
 return T.propose(new,campaign,previous_selection=previous,execution_plan=plan,
   authorization=a,partitions=2,directory=tmp_path/'permit')


def test_terminal_rejection_retained_new_calls_covered_and_no_original_replay(setup,monkeypatch,tmp_path):
 args=closed(setup,monkeypatch,tmp_path);s,new,campaign,previous,plan,a=args
 permit=propose(args,tmp_path);old=E.location(T.prepared(read(plan)),campaign)
 retained={p.name:p.read_bytes()for p in old.iterdir()}
 monkeypatch.setattr(P.K,'MAX_REFERENCES',2)
 prepared=P.prepare(new,campaign,tmp_path/'parts',prior_plan=previous,terminal_rejection=permit)
 calls=[];respond(monkeypatch,new,calls=calls)
 P.dispatch(prepared,0,1,credential='synthetic');P.dispatch(prepared,1,1,credential='synthetic')
 result=P.aggregate(prepared)
 assert result['consumed_physical_attempts']==3 and result['terminal_rejection']==permit
 assert result['semantic_budget_reset'] is False and len(calls)==2
 assert sum(map(len,calls))==len(set().union(*(set(x)for x in calls)))==4
 assert all(p.read_bytes()==retained[p.name]for p in old.iterdir())
 assert all(result['checked_files'][str(old/name)]==E.sha(raw)for name,raw in retained.items())
 with pytest.raises(ValueError,match='delegated'):
  E.execute(T.prepared(read(plan)),campaign,credential='synthetic')
 with pytest.raises(ValueError,match='another partition'):
  P.prepare(new,campaign,tmp_path/'other',prior_plan=previous,terminal_rejection=permit)
 monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('inspection cannot call model'))
 assert P.aggregate(prepared)==result


@pytest.mark.parametrize('options',[{'uncertain':True},{'complete':True},{'status':403}])
def test_uncertain_complete_or_other_http_status_cannot_receive_allowance(setup,monkeypatch,tmp_path,options):
 args=closed(setup,monkeypatch,tmp_path,**options)
 with pytest.raises(ValueError):propose(args,tmp_path)
 assert not (tmp_path/'permit').exists()


def test_partition_count_cannot_silently_expand_allowance(setup,monkeypatch,tmp_path):
 args=closed(setup,monkeypatch,tmp_path);s,new,campaign,previous,plan,a=args;permit=propose(args,tmp_path)
 with pytest.raises(ValueError,match='bounded count'):
  P.prepare(new,campaign,tmp_path/'parts',prior_plan=previous,terminal_rejection=permit)
 assert not (P.old_location(new,campaign)/'scope-delegation.json').exists()


def test_uncertain_new_partition_never_gets_next_round_or_duplicate_call(setup,monkeypatch,tmp_path):
 args=closed(setup,monkeypatch,tmp_path);s,new,campaign,previous,plan,a=args;permit=propose(args,tmp_path)
 monkeypatch.setattr(P.K,'MAX_REFERENCES',2)
 selected=P.prepare(new,campaign,tmp_path/'parts',prior_plan=previous,terminal_rejection=permit)
 dispatched=[]
 def lost(*a,**k):dispatched.append(True);raise urllib.error.URLError('synthetic uncertain partition')
 monkeypatch.setattr(E,'_open',lost)
 assert P.dispatch(selected,0,1,credential='synthetic').status=='incomplete'
 assert P.dispatch(selected,0,1,credential='synthetic').status=='incomplete'
 with pytest.raises(ValueError,match='incomplete'):
  P.dispatch(selected,0,2,credential='synthetic')
 with pytest.raises(ValueError,match='incomplete'):
  P.dispatch(selected,1,1,credential='synthetic')
 assert dispatched==[True]
 assert not E.location(P._prepared_call(selected,0,2),campaign).exists()
