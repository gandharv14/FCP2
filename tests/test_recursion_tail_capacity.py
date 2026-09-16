import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_tail_capacity as t
import recursion_hardening_revisions as r


def fixture():
    request={'tags':['fcp-audit-hardening-20260915'],'metadata':{'attemptsPerProblem':3},
        'problems':[{'problemId':'p'+str(n),'problemVersionId':'v'+str(n),'environmentId':'env'} for n in range(4)],
        'solvers':[{'runConfigVersionId':m,'concurrency':50} for m in ['opus','gemini']]}
    detail={'id':'e','nAttemptsPerProblem':3,'problems':copy.deepcopy(request['problems']),
        'solvers':[{'id':'s'+m,'evaluationId':'e','runConfigVersionId':m,'metadata':{'concurrency':50},'jobStatus':'running'} for m in ['opus','gemini']]}
    cells=[]
    for m in ['opus','gemini']:
        for n in range(4):
            cells.append({'solverId':'s'+m,'problemVersionId':'v'+str(n),'attempts':[
                {'problemRunId':f'{m}-{n}-{a}','attemptNumber':a,'status':'completed','score':None} for a in [1,2,3]]})
    matrix={'evaluationId':'e','cells':cells}
    return detail,matrix,{'request':request,'response':copy.deepcopy(detail)}


def test_pending_running_grading_and_unrecognized_states_stay_reserved():
    d,m,review=fixture()
    for cell,status in zip(m['cells'][:4],['pending','running','grading','future-state']):cell['attempts'][0]['status']=status
    assert t.remaining_slots(d,m,review,r.TERMINAL)=={'sopus':4,'sgemini':0}
    for cell in m['cells']:
        for a in cell['attempts']:a['status']='failed'
    assert t.remaining_slots(d,m,review,r.TERMINAL)=={'sopus':0,'sgemini':0}
    m['cells'][0]['attempts'][0]['status']='cancelled'
    assert t.remaining_slots(d,m,review,r.TERMINAL)['sopus']==0


@pytest.mark.parametrize('bad',[
    lambda d,m: m.update(evaluationId='foreign'),
    lambda d,m: m['cells'].pop(),
    lambda d,m: m['cells'].append(copy.deepcopy(m['cells'][0])),
    lambda d,m: m['cells'][0].update(problemVersionId='unexpected'),
    lambda d,m: m['cells'][0].update(solverId='unexpected'),
    lambda d,m: m['cells'][0]['attempts'].pop(),
    lambda d,m: m['cells'][0]['attempts'][0].update(attemptNumber=2),
    lambda d,m: m['cells'][0]['attempts'][0].update(problemRunId=m['cells'][1]['attempts'][0]['problemRunId']),
    lambda d,m: m['cells'][0]['attempts'][0].update(problemRunId=None),
    lambda d,m: m['cells'][0]['attempts'][0].update(status=None),
    lambda d,m: d.update(nAttemptsPerProblem=4),
    lambda d,m: d['problems'][0].update(environmentId='foreign'),
    lambda d,m: d['solvers'][0].update(id='wrong'),
    lambda d,m: d['solvers'][0]['metadata'].update(concurrency=30),
])
def test_invalid_proof_fails_closed(bad):
    d,m,review=fixture();bad(d,m)
    with pytest.raises(ValueError):t.remaining_slots(d,m,review,r.TERMINAL)


def test_missing_matrix_fails_closed():
    d,m,review=fixture()
    with pytest.raises(ValueError):t.remaining_slots(d,None,review,r.TERMINAL)


def test_fresh_capacity_reduces_only_reviewed_campaign_tail(tmp_path):
    d,m,review=fixture()
    for cell in m['cells'][:4]:cell['attempts'][0]['status']='grading'
    calls=[]
    ext={'id':'other','solvers':[{'id':'other-solver','runConfigVersionId':'opus','jobStatus':'running','metadata':{'concurrency':75}}]}
    def cli(*args):
        calls.append(args)
        if args[1]=='list':return {'items':[{'id':'e','status':'running'},{'id':'other','status':'running'}]}
        if args[1]=='get':return d if args[-1]=='e' else ext
        assert args[1]=='get-results' and args[-1]=='e';return m
    with patch.object(r,'known_configs',return_value={'opus':'opus','gemini':'gemini'}),patch.object(t,'reviewed_evaluations',return_value=({'e':review},{'sha256':'pin'})),patch.object(r.c,'cli',side_effect=cli),patch.object(r.c,'CONFIG',{'concurrencyPerModel':150}):
        cap=r.fresh_capacity(tmp_path)
    assert cap['reservedConcurrencyByModel']=={'opus':79,'gemini':0}
    assert cap['availableConcurrencyByModel']['opus']==71
    assert len(cap['tailReservationBounds'])==2
    assert all(x['configuredConcurrency']==50 and x['matrixSha256'] for x in cap['tailReservationBounds'])
    assert ('evaluations','get-results','--evaluation-id','e') in calls
    assert not any(x[1]=='get-results' and x[-1]=='other' for x in calls)


def test_configured_ceiling_still_caps_remaining_slots():
    d,m,review=fixture()
    for record in [d,review['response']]:
        for s in record['solvers']:s['metadata']['concurrency']=2
    for s in review['request']['solvers']:s['concurrency']=2
    for c in m['cells']:
        for a in c['attempts']:a['status']='pending'
    assert t.remaining_slots(d,m,review,r.TERMINAL)=={'sopus':12,'sgemini':12}
    # The caller uses min(configured, remaining), not remaining alone.
    assert min(2,t.remaining_slots(d,m,review,r.TERMINAL)['sopus'])==2


def write_review(work, review):
    folder=work/'content-amendments/0002';folder.mkdir(parents=True)
    req=work/'evaluation-2.request.json';resp=work/'evaluation-2.response.json'
    req.write_text(json.dumps(review['request']));resp.write_text(json.dumps(review['response']))
    document={'schemaVersion':1,'status':'reviewed','scope':'campaign_tail_capacity_only',
        'evidenceHashes':{str(p):t.sha(p) for p in [req,resp]},
        'evaluations':[{'evaluationId':'e','requestPath':str(req),'requestSha256':t.sha(req),
                       'responsePath':str(resp),'responseSha256':t.sha(resp)}]}
    path=folder/'reviewed-tail-capacity-evaluations.json';path.write_text(json.dumps(document));return path,req


def test_review_loader_pins_exact_paired_request_and_response(tmp_path):
    _,_,review=fixture();path,req=write_review(tmp_path,review)
    mapping,pin=t.reviewed_evaluations(tmp_path,{'opus':'opus','gemini':'gemini'})
    assert mapping=={'e':review} and pin['sha256']==t.sha(path)
    req.write_text(req.read_text()+'\n')
    with pytest.raises(ValueError,match='evidence changed'):t.reviewed_evaluations(tmp_path,{'opus':'opus','gemini':'gemini'})


def test_tail_review_cannot_import_unknown_external_config(tmp_path):
    _,_,review=fixture();write_review(tmp_path,review)
    with pytest.raises(ValueError,match='known paired campaign'):
        t.reviewed_evaluations(tmp_path,{'opus':'opus'})


def test_capacity_invalid_reviewed_matrix_does_not_release_slots(tmp_path):
    d,m,review=fixture();m['cells'].pop()
    def cli(*args):
        if args[1]=='list':return {'items':[{'id':'e','status':'running'}]}
        return d if args[1]=='get' else m
    with patch.object(r,'known_configs',return_value={'opus':'opus','gemini':'gemini'}),patch.object(t,'reviewed_evaluations',return_value=({'e':review},{})),patch.object(r.c,'cli',side_effect=cli):
        with pytest.raises(ValueError,match='Incomplete tail matrix'):r.fresh_capacity(tmp_path)
