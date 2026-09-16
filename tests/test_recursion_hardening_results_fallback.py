import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from recursion_hardening_results_fallback import MembershipError, matrix_index, collect_exact, compare_membership


def fixture():
    d = {'id':'eval-new','nAttemptsPerProblem':3,'totalSolvers':2,'totalProblems':1,
         'solvers':[{'id':s,'evaluationId':'eval-new','runConfigVersionId':'config-'+s} for s in ['opus','gemini']],
         'problems':[{'evaluationId':'eval-new','problemId':'problem','problemVersionId':'version'}]}
    m = {'evaluationId':'eval-new','cells':[{'solverId':s,'problemVersionId':'version','totalRuns':3,
         'attempts':[{'problemRunId':f'{s}-{n}','attemptNumber':n,'status':'running','score':None,
                      'jobV2Id':f'job-{s}-{n}','infraRetryCount':0} for n in (1,2,3)]} for s in ['opus','gemini']]}
    return d,m


def run(rid):
    solver,n = rid.split('-')
    return {'id':rid,'problemId':'problem','problemVersionId':'version','attemptNumber':int(n),
            'source':'evaluation','solverRunConfigVersionId':'config-'+solver,'status':'completed',
            'finalScore':1,'jobV2Id':'job-'+rid,'transcript':'PRIVATE','gradingConfig':{'command':'PRIVATE'}}


def test_exact_membership_only_and_failures_are_not_model_scores():
    d,m = fixture();calls=[]
    def get(rid):
        calls.append(rid)
        if rid == 'gemini-1': raise RuntimeError('API 500 WITH PRIVATE TEXT')
        return run(rid)
    x=collect_exact(d,m,['opus-1','gemini-1'],get)
    assert set(calls)=={'opus-1','gemini-1'} and x['successfulReads']==1
    good,bad=x['observations']
    assert good['score']==1 and 'transcript' not in good and 'gradingConfig' not in good
    assert bad['readSucceeded'] is False and 'score' not in bad and 'status' not in bad
    assert 'PRIVATE' not in str(x)
    assert x['passAt3'] is None and x['requiresCurrentMatrixReconciliation']


def test_reject_foreign_run_before_any_api_read_or_excessive_budget():
    d,m=fixture()
    def never(rid): raise AssertionError('Should not call API')
    with pytest.raises(MembershipError):collect_exact(d,m,['old-eval-run'],never)
    with pytest.raises(MembershipError):collect_exact(d,m,['opus-1','opus-2'],never,max_reads=1)


@pytest.mark.parametrize('field,value',[('id','old-run'),('source','interactive'),
    ('attemptNumber',2),('problemVersionId','old-version'),('solverRunConfigVersionId','old-config'),
    ('finalScore',.9),('finalScore',True)])
def test_mismatched_live_record_is_rejected(field,value):
    d,m=fixture()
    def get(rid): return {**run(rid),field:value}
    with pytest.raises(MembershipError):collect_exact(d,m,['opus-1'],get)


def test_duplicate_slots_and_missing_matrix_cells_rejected():
    d,m=fixture();m['cells'][0]['attempts'][1]['attemptNumber']=1
    with pytest.raises(MembershipError):matrix_index(d,m)
    d,m=fixture();m['cells'].pop()
    with pytest.raises(MembershipError):matrix_index(d,m)


def test_retry_replacement_detected_by_authoritative_matrix_not_guessed():
    d,m=fixture();new=copy.deepcopy(m)
    new['cells'][0]['attempts'][0].update(problemRunId='replacement',infraRetryCount=1)
    assert compare_membership(d,m,new)==[{'solverId':'opus','problemVersionId':'version','attemptNumber':1,
        'oldProblemRunId':'opus-1','newProblemRunId':'replacement','infraRetryEvidence':True}]
    def get(rid):return {**run(rid),'jobV2Id':'replacement-job'}
    x=collect_exact(d,m,['opus-1'],get)
    assert x['observations'][0]['jobChangedSinceCachedMatrix']


def test_grading_errors_do_not_become_model_failures():
    d,m=fixture()
    def get(rid):return {**run(rid),'finalScore':0,'gradingError':'grader infra fault'}
    x=collect_exact(d,m,['opus-1'],get)
    assert x['observations'][0]['score'] is None and x['observations'][0]['hasGradingError']
