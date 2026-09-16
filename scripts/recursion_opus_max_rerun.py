"""Prepare and submit the authorized latest-task rerun with durable receipts."""
import argparse
import json
import recursion_opus_max as m
c,WORK,policy=m.c,m.WORK,m.policy


def prepare():
    selected=c.read(WORK/'selected.json');parent=c.read(WORK/'verified.json')
    policy.validate(parent['config'])
    groups={'base':[r for r in selected if not r['generation']]}
    for f in (c.REPORTS/'campaigns').glob('*/campaign.json'):
        ids={c.read(f.parent/'problems'/r['task_id']/'verified.json')['id'] for r in c.read(f)['tasks']}
        rows=[r for r in selected if r['problemVersionId'] in ids]
        if rows:groups[f.parent.name]=rows
    assert sum(map(len,groups.values()))==len(selected)
    for name,rows in groups.items():
        if name=='base':
            for i in range(2):
                payload={'name':f'FCP Opus 5 MAX — latest tasks — base {i+1}/2 — pass@3 — 20260915',
                  'description':'Fresh independent rerun; latest version per task; full-answer success threshold 1; historical samples excluded.',
                  'metadata':{'schemaVersion':1,'attemptsPerProblem':3,'successThreshold':1},
                  'tags':['fcp','opus-5-max','full-latest-rerun','pass-at-3','20260915'],
                  'solvers':[{'displayName':'Claude Opus 5 MAX','runConfigVersionId':parent['id'],'concurrency':50}],
                  'problems':[{k:r[k] for k in ['problemId','problemVersionId','environmentId']} for r in rows[i::2]]}
                c.save(WORK/f'base-{i+1}.request.json',payload)
        else:
            folder=c.REPORTS/'campaigns'/name
            original=c.read(folder/'configs/opus/verified.json') if (folder/'configs/opus/verified.json').exists() else None
            config=policy.enforce(parent['config']);config['harnessImageUrl']=c.read(folder/'opus-image.json')['image']
            c.save(WORK/'proposals'/(name+'.json'),{'campaign':name,'tasks':rows,'config':config,'credentialParent':parent['id'],
                'probe':original['probe'] if original else None,'attempts':3*len(rows),'concurrency':10})
    c.save(WORK/'rerun-plan.json',{'tasks':len(selected),'attempts':3*len(selected),'readyTasks':len(groups['base']),
      'blockedTasks':len(selected)-len(groups['base']),'requiredAdditionalConfigs':len(groups)-1,
      'concurrency':150,'groupCounts':{k:len(v) for k,v in groups.items()}})
    print(json.dumps(c.read(WORK/'rerun-plan.json')))


def launch_base():
    parent=c.read(WORK/'verified.json')
    live=c.cli('run-config-versions','get','--run-config-version-id',parent['id']);policy.validate(live['config'])
    assert live['lockedAt'] and live['verifiedAt']
    for i in (1,2):
        payload=c.read(WORK/f'base-{i}.request.json')
        response=c.mutation(WORK,f'base-{i}',['evaluations','bulk-create'],payload)
        assert response['nAttemptsPerProblem']==3 and response['totalProblems']==len(payload['problems'])
        assert response['totalSolvers']==1 and response['solvers'][0]['runConfigVersionId']==parent['id']
        print(json.dumps({'evaluationId':response['id'],'tasks':response['totalProblems'],'status':response['status']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','launch_base']);args=p.parse_args();globals()[args.action]()
