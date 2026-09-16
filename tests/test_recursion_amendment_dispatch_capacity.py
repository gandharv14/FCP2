"""Fixed amendment scheduling parity without changing logical evaluation slots."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_hardening_revisions as r


def save(p, d):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(d))


def fixture(work, kind='content_successor', amendment='0002'):
    revisions=[];prior=[]
    for n in range(18):
        tid=str(n);path=work/'versions'/(tid+'.json')
        save(path, {'id':'new-'+tid,'problemId':'problem-'+tid,'environmentId':'environment'})
        revisions.append({'taskId':tid,'newProblemVersionId':'new-'+tid,'baseProblemVersionId':'old-'+tid,
                          'oldVersionIds':['old-'+tid],'verifiedPath':str(path)})
        prior.append({'problemId':'problem-'+tid,'problemVersionId':'old-'+tid})
    save(work/'evaluation-1.request.json',{'problems':prior});save(work/'evaluation-1.response.json',{'id':'prior'})
    save(work/'task-validity-review.json',{'status':'complete'})
    active={'kind':kind,'revisions':revisions}
    if amendment:active.update(amendmentId=amendment,amendmentDelta=revisions,dispatchPrefix='correction-content-2')
    configs={m:{'id':'config-'+m,'lockedAt':'locked','verifiedAt':'verified'} for m in r.MODELS}
    return active,configs


@pytest.mark.parametrize('kind,amendment,expected',[
    ('content_successor','0002',30),('content_successor',None,50),('prompt_revision',None,10)])
def test_fixed_limit_preserves_18_tasks_three_attempts_and_prior_generations(tmp_path,kind,amendment,expected):
    active,configs=fixture(tmp_path,kind,amendment)
    with patch.object(r,'activation',return_value=active),patch.object(r,'active_configs',return_value=configs):
        payload=r.correction_payload(tmp_path)
    assert len(payload['problems'])==18
    assert payload['metadata']['attemptsPerProblem']==3
    assert [s['concurrency'] for s in payload['solvers']]==[expected,expected]
    assert {s['runConfigVersionId'] for s in payload['solvers']}=={'config-opus','config-gemini'}


def test_thirty_free_slots_launches_gemini_then_exact_opus_counterpart(tmp_path):
    active,configs=fixture(tmp_path)
    cap={'checkedAt':datetime.now(timezone.utc).isoformat(),'availableConcurrencyByModel':{'opus':0,'gemini':30}}
    def mutation(folder,name,args,payload):
        save(folder/(name+'.request.json'),payload);save(folder/(name+'.response.json'),{'id':name});return {'id':name}
    with patch.object(r,'activation',return_value=active),patch.object(r,'active_configs',return_value=configs),patch.object(r,'known_configs',return_value={'config-opus':'opus','config-gemini':'gemini'}),patch.object(r,'fresh_capacity',return_value=cap),patch.object(r.c,'mutation',side_effect=mutation):
        r.launch_corrections(tmp_path,confirmed=True,models=['gemini'])
        cap['availableConcurrencyByModel']={'opus':30,'gemini':0}
        r.launch_corrections(tmp_path,confirmed=True,models=['opus'])
    gem=r.read(tmp_path/'correction-content-2-gemini.request.json');opus=r.read(tmp_path/'correction-content-2-opus.request.json')
    assert gem['problems']==opus['problems'] and len(gem['problems'])==18
    for payload in [gem,opus]:
        assert payload['solvers'][0]['concurrency']==30 and payload['metadata']['attemptsPerProblem']==3


def test_existing_counterpart_concurrency_drift_rejected_before_mutation(tmp_path):
    active,configs=fixture(tmp_path)
    with patch.object(r,'activation',return_value=active),patch.object(r,'active_configs',return_value=configs):
        payload=r.correction_payload(tmp_path)
    payload['solvers']=[payload['solvers'][1]];payload['solvers'][0]['concurrency']=50
    save(tmp_path/'correction-content-2-gemini.request.json',payload);save(tmp_path/'correction-content-2-gemini.response.json',{'id':'already'})
    with patch.object(r,'activation',return_value=active),patch.object(r,'active_configs',return_value=configs),patch.object(r,'known_configs',return_value={'config-opus':'opus','config-gemini':'gemini'}),patch.object(r,'fresh_capacity') as cap,patch.object(r.c,'mutation') as mutate:
        with pytest.raises(ValueError,match='counterpart concurrency changed'):
            r.launch_corrections(tmp_path,confirmed=True,models=['opus'])
        cap.assert_not_called();mutate.assert_not_called()
