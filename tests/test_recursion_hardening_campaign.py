import base64,gzip,io,json,re,sys,tarfile,itertools
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from recursion_hardening_campaign import grader_command,summarize,capacity_summary
import recursion_hardening_campaign as campaign
from grader.finance_grader import grade

COUNTER=itertools.count()
def runs(scores,status='completed'):
    return [{'status':status,'finalScore':score,'attemptNumber':i+1,'problemRunId':str(next(COUNTER))} for i,score in enumerate(scores)]

def test_pass3_is_any_complete_solve_and_keeps_three_attempt_denominator():
    rows=[{'taskId':'one','models':{'opus':runs([0,1,0]),'gemini':runs([0]*3)}},
          {'taskId':'two','models':{'opus':runs([0]*3),'gemini':runs([1]*3)}}]
    x=summarize(rows)
    assert x['complete'] and x['allAttemptsTerminal']
    assert x['models']['opus']['passAt3']==.5
    assert x['models']['opus']['perAttemptPassRate']==1/6
    assert x['models']['gemini']['perAttemptPassRate']==.5
    assert x['opusMinusGeminiPercentagePoints']==0

def test_failed_infrastructure_is_not_a_completed_comparison():
    x=summarize([{'taskId':'one','models':{'opus':runs([None]*3,'failed'),'gemini':runs([0]*3)}}])
    assert x['allAttemptsTerminal'] and not x['complete']
    assert x['models']['opus']['infrastructureOrUnscoredAttempts']==3
    assert x['models']['opus']['passAt3'] is None
    assert x['opusMinusGeminiPercentagePoints'] is None
    assert x['models']['opus']['passAt3Bounds']=={'lower':0,'upper':1}
    assert x['opusMinusGeminiPercentagePointBounds']=={'lower':0,'upper':100}

def test_unresolved_attempt_on_already_solved_task_does_not_widen_task_bound():
    o=runs([1,0]);o+=runs([None],'failed');o[-1]['attemptNumber']=3
    result=summarize([{'taskId':'one','models':{'opus':o,'gemini':runs([0]*3)}}])
    assert result['allAttemptsTerminal'] and not result['complete']
    assert result['models']['opus']['passAt3Bounds']=={'lower':1,'upper':1}
    assert result['models']['opus']['uncertainTaskOutcomes']==0
    assert result['opusMinusGeminiPercentagePointBounds']=={'lower':100,'upper':100}

def test_no_continuous_or_extra_attempts_silently_accepted():
    with pytest.raises(AssertionError):
        summarize([{'taskId':'one','models':{'opus':runs([.9]*3),'gemini':runs([0]*3)}}])
    with pytest.raises(AssertionError):
        summarize([{'taskId':'one','models':{'opus':runs([1]*4),'gemini':runs([0]*3)}}])

def test_duplicate_slots_and_runs_rejected_and_partial_metrics_suppressed():
    with pytest.raises(AssertionError):
        summarize([{'taskId':'one','models':{'opus':runs([1])*3,'gemini':runs([0]*3)}}])
    a=runs([1,1,1]);a[2]['problemRunId']=a[0]['problemRunId']
    with pytest.raises(AssertionError):
        summarize([{'taskId':'one','models':{'opus':a,'gemini':runs([0]*3)}}])
    x=summarize([{'taskId':'one','models':{'opus':runs([1]),'gemini':[]}}])
    assert not x['complete'] and x['opusMinusGeminiPercentagePoints'] is None
    assert all(v['passAt3'] is None for v in x['models'].values())

def test_private_archive_and_complete_answer_grading(tmp_path):
    key={'kind':'cell_value','tolerance':{'numeric_abs':1e-6,'numeric_rel':1e-6},
         'targets':{'Model!A1':100,'Model!A2':20},'groups':{'large':['Model!A1'],'small':['Model!A2']}}
    path=tmp_path/'answer_key.json';path.write_text(json.dumps(key))
    cmd=grader_command(path);assert '--mode discrete' in cmd
    encoded=re.search("printf '%s' '([A-Za-z0-9+/=]+)'",cmd)[1]
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)),mode='r:gz') as tf:
        assert json.load(tf.extractfile('answer_key.json'))==key
        assert set(tf.getnames())>={'run_grader.py','finance_grader/core.py'}
    assert grade(key['targets'],key,mode='discrete').score==1
    near={'Model!A1':100,'Model!A2':19}
    assert grade(near,key,mode='continuous').score>.8
    assert grade(near,key,mode='discrete').score==0
    assert grade({'Model!A1':100},key,mode='discrete').score==0

def test_finished_partition_releases_capacity_without_cancelling_old_jobs():
    def job(identity,model,status='running'):
        return {'id':identity,'runConfigVersionId':model,'jobStatus':status,'metadata':{'concurrency':50}}
    prior=[{'id':'old1','solvers':[job('a','o')]},{'id':'old2','solvers':[job('b','o')]}]
    current={'id':'new1','solvers':[job('c','o'),job('d','g','completed')]}
    mapping={'o':'opus','g':'gemini'}
    x=capacity_summary(prior+[current],mapping,150,1)
    assert x['availableConcurrencyByModel']=={'opus':0,'gemini':150}
    assert x['launchThroughPartition']==1
    current['solvers'][0]['jobStatus']='completed'
    x=capacity_summary(prior+[current],mapping,150,1)
    assert x['availableConcurrencyByModel']=={'opus':50,'gemini':150}
    assert x['launchThroughPartition']==2
    with pytest.raises(KeyError):capacity_summary(prior,{},150,1)

def test_transient_read_retry_cannot_resubmit_a_mutation(monkeypatch):
    calls=[]
    def cli(*args, **kwargs):
        assert kwargs == {'timeout_seconds':90}
        calls.append(args)
        if len(calls)==1:raise RuntimeError('Internal server error')
        return {'cells':[]}
    monkeypatch.setattr(campaign.c,'cli',cli)
    monkeypatch.setattr(campaign.time,'sleep',lambda _:None)
    assert campaign.read_evaluation('evaluations','get-results','--evaluation-id','one')=={'cells':[]}
    assert len(calls)==2
    with pytest.raises(AssertionError):campaign.read_evaluation('evaluations','bulk-create')
    assert len(calls)==2


def test_task_revision_supersedes_all_attempts_for_both_models_even_before_rerun():
    base=[{'taskId':'one'}]
    solvers={'o':'opus','g':'gemini'}
    def entry(eid,version,score):
        cells=[]
        for solver in solvers:
            attempts=runs([score]*3)
            for a in attempts:a['score']=a.pop('finalScore')
            cells.append({'solverId':solver,'problemVersionId':version,'attempts':attempts})
        return eid,solvers,{'problems':[{'problemVersionId':version}]},{'evaluationId':eid,'cells':cells}
    old=entry('old-eval','v1',1)
    rows,old_rows=campaign.assemble_evaluation_rows(base,[old],{'one':'v2'},{'v1':'one','v2':'one'})
    assert rows[0]['models']=={'opus':[],'gemini':[]}
    assert len(old_rows)==2 and all(len(r['attempts'])==3 for r in old_rows)
    revised=entry('new-eval','v2',0)
    rows,old_rows=campaign.assemble_evaluation_rows(base,[old,revised],{'one':'v2'},{'v1':'one','v2':'one'})
    summary=campaign.summarize(rows)
    assert summary['complete'] and all(m['passAt3']==0 for m in summary['models'].values())
    assert all(a['evaluationId']=='new-eval' for g in rows[0]['models'].values() for a in g)
    revised[3]['evaluationId']='foreign-eval'
    with pytest.raises(AssertionError):
        campaign.assemble_evaluation_rows(base,[old,revised],{'one':'v2'},{'v1':'one','v2':'one'})
    revised[3]['evaluationId']='new-eval'
    with pytest.raises(AssertionError):
        campaign.assemble_evaluation_rows(base,[old,revised,revised],{'one':'v2'},{'v1':'one','v2':'one'})
    revised[3]['cells'][0]['attempts'].pop()
    with pytest.raises(AssertionError):
        campaign.assemble_evaluation_rows(base,[old,revised],{'one':'v2'},{'v1':'one','v2':'one'})


def test_single_model_evaluations_merge_on_fixed_tasks_without_extra_attempts():
    rows=[{'taskId':'one'}];entries=[]
    for model in ('opus','gemini'):
        attempts=runs([0,1,0]);attempts=[{**a,'score':a['finalScore']} for a in attempts]
        entries.append((model,{model:model},{'problems':[{'problemVersionId':'v1'}]},
            {'evaluationId':model,'cells':[{'solverId':model,'problemVersionId':'v1','attempts':attempts}]}))
    merged,old=campaign.assemble_evaluation_rows(rows,entries,{'one':'v1'},{'v1':'one'})
    assert not old and campaign.summarize(merged)['complete']
    assert all(len(g)==3 for g in merged[0]['models'].values())


def test_partition_dispatch_tracks_models_separately_and_rejects_partial_cohorts():
    rows=[{'problemId':str(i),'problemVersionId':'v'+str(i)} for i in range(6)]
    def request(part,models):
        return {'metadata':{'attemptsPerProblem':3,'successThreshold':1},
            'problems':rows[part::3],'solvers':[{'runConfigVersionId':m} for m in models]}
    cfg={'opus':'o','gemini':'g'}
    requests=[request(0,['o','g']),request(1,['g'])]
    got=campaign.dispatched_model_partitions(rows,requests,cfg)
    assert got=={(0,'opus'),(0,'gemini'),(1,'gemini')}
    successor={'opus':'o2','gemini':'g2'}
    known={'o':'opus','g':'gemini','o2':'opus','g2':'gemini'}
    assert campaign.dispatched_model_partitions(rows,requests,successor,known_configs=known)==got
    duplicate=request(0,['o2'])
    with pytest.raises(AssertionError):
        campaign.dispatched_model_partitions(rows,requests+[duplicate],successor,known_configs=known)
    current={r['problemId']:r['problemVersionId'] for r in rows};current['1']='changed-v1'
    with pytest.raises(AssertionError):campaign.dispatched_model_partitions(rows,requests,cfg,current)
    mismatched=request(1,['o']);mismatched['problems']=[dict(p) for p in mismatched['problems']]
    mismatched['problems'][0]['problemVersionId']='other-version'
    with pytest.raises(AssertionError):campaign.dispatched_model_partitions(rows,requests+[mismatched],cfg)
    requests.append(request(1,['o']))
    assert (1,'opus') in campaign.dispatched_model_partitions(rows,requests,cfg)
    with pytest.raises(AssertionError):campaign.dispatched_model_partitions(rows,requests+[request(1,['g'])],cfg)
    bad=request(2,['o']);bad['problems']=bad['problems'][:1]
    with pytest.raises(AssertionError):campaign.dispatched_model_partitions(rows,[bad],cfg)


def test_evidence_adjudication_keeps_raw_null_and_requires_exact_run_binding(tmp_path):
    evidence=tmp_path/'evidence.json';evidence.write_text('{"confirmed":true}')
    failures=runs([None]*3,'failed')
    for a in failures:a.update(evaluationId='e1',errorMessage='exit137')
    rows=[{'taskId':'one','models':{'opus':failures,'gemini':runs([0]*3)}}]
    ledger={'schemaVersion':1,'policy':{'appliesEquallyTo':['opus','gemini']},'adjudications':{}}
    for a in failures:
        ledger['adjudications'][a['problemRunId']]={'taskId':'one','model':'opus','attemptNumber':a['attemptNumber'],
            'evaluationId':'e1','classification':'probable_solver_resource_exhaustion','derivedOutcome':0,
            'rawScore':None,'rawStatus':'failed','rawErrorMessage':'exit137','newSolverAttemptsLaunched':0,
            'canonicalGraderScoreProduced':False,'reason':'Bounded supporting evidence.',
            'evidence':[{'path':'evidence.json','sha256':campaign.sha(evidence)}]}
    campaign.apply_failure_adjudications(rows,ledger,tmp_path)
    result=campaign.summarize(rows)
    assert result['complete'] and result['models']['opus']['scoredAttempts']==0
    assert result['models']['opus']['adjudicatedSolverFailures']==3
    assert result['models']['opus']['perAttemptPassRate']==0
    assert all(a['finalScore'] is None for a in failures)
    post=tmp_path/'regrade'/'post-regrade-result.json';post.parent.mkdir()
    first=failures[0];item=ledger['adjudications'][first['problemRunId']]
    post.write_text(json.dumps({'id':first['problemRunId'],'status':'failed','finalScore':None,
        'errorMessage':'regrade rejected','attemptNumber':1}))
    item['sameRunRegrade']={'status':'failed','score':None,'attemptNumber':1,'regradeCount':0,'error':'regrade rejected'}
    item['evidence'].append({'path':'regrade/post-regrade-result.json','sha256':campaign.sha(post)})
    first['errorMessage']='regrade rejected'
    campaign.apply_failure_adjudications(rows,ledger,tmp_path)
    assert first['finalScore'] is None and item['rawErrorMessage']=='exit137'
    first['errorMessage']='unexpected error'
    with pytest.raises(AssertionError):campaign.apply_failure_adjudications(rows,ledger,tmp_path)
    first['errorMessage']='regrade rejected'
    evidence.write_text('{"changed":true}')
    with pytest.raises(AssertionError):campaign.apply_failure_adjudications(rows,ledger,tmp_path)
    ledger['adjudications'][failures[0]['problemRunId']]['model']='gemini'
    with pytest.raises(AssertionError):campaign.apply_failure_adjudications(rows,ledger,tmp_path)
