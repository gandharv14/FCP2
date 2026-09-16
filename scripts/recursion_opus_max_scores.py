"""Read-only per-task pass@3 export for the pinned Opus-max rerun."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import csv
import json
import math
import recursion_opus_max as m
c,WORK,policy=m.c,m.WORK,m.policy


def summarize(row, runs):
    ordered=sorted(runs,key=lambda r:r['attemptNumber'])
    if len(ordered)>3 or len({r['attemptNumber'] for r in ordered})!=len(ordered):
        raise ValueError('Ambiguous or duplicated samples: '+row['task'])
    assert {r['attemptNumber'] for r in ordered}<={1,2,3}
    complete=len(ordered)==3 and all(r['status']=='completed' and isinstance(r.get('finalScore'),(int,float))
        and not isinstance(r.get('finalScore'),bool) and math.isfinite(r['finalScore'])
        and not r.get('errorMessage') and not r.get('gradingError') for r in ordered)
    result={**row,'passAt3':int(any(r['finalScore']>=1 for r in ordered)) if complete else None,
            'state':'scored' if complete else 'pending','attempts':ordered}
    if any(r['status'] in ('failed','cancelled') or r.get('gradingError') for r in ordered):result['state']='unscored_error'
    return result


def main():
    selected=c.read(WORK/'selected.json'); config=c.read(WORK/'verified.json');assignments={}
    for i in (1,2):
        detail=c.read(WORK/f'base-{i}.response.json')
        for p in detail['problems']:assignments[p['problemVersionId']]={'evaluationId':detail['id'],'createdAt':detail['createdAt']}
    def fetch(row):
        assignment=assignments.get(row['problemVersionId'])
        if not assignment:return {**row,'state':'approval_required','passAt3':None,'attempts':[]}
        try:
            data=c.cli('problem-runs','list-for-problem','--problem-id',row['problemId'])
            runs=[r for r in data['items'] if r['problemVersionId']==row['problemVersionId']
                  and r.get('solverRunConfigVersionId')==config['id'] and r.get('source')=='evaluation'
                  and r['createdAt']>=assignment['createdAt']]
            assert all(r.get('apiModelName') in (None,policy.MODEL_ID) for r in runs)
            result=summarize({**row,'evaluationId':assignment['evaluationId']},runs)
            c.save(WORK/'runs'/(row['problemId']+'.json'),{'checkedAt':datetime.now(timezone.utc).isoformat(),
                    'truncatedHistory':data.get('truncated'),'selectedRuns':runs})
            return result
        except Exception as exc:return {**row,'evaluationId':assignment['evaluationId'],'state':'read_error','error':str(exc)[:500],'passAt3':None,'attempts':[]}
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(fetch,selected))
    states=Counter(r['state'] for r in results);runstates=Counter(a['status'] for r in results for a in r['attempts'])
    summary={'checkedAt':datetime.now(timezone.utc).isoformat(),'taskCount':len(results),'submittedTasks':len(assignments),
             'states':dict(states),'attemptStatuses':dict(runstates),'passingTriples':sum(r['passAt3']==1 for r in results),
             'completeTriples':sum(r['passAt3'] is not None for r in results),'passAt3':None}
    if states['scored']==len(results):summary['passAt3']=summary['passingTriples']/len(results)
    c.save(WORK/'task-scores.json',results);c.save(WORK/'score-summary.json',summary)
    with (WORK/'task-scores.csv').open('w',newline='') as f:
        fields=['task','problemId','problemVersionId','evaluationId','state','score1','score2','score3','passAt3']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in results:
            data={k:r.get(k) for k in fields}
            for a in r['attempts']:data['score'+str(a['attemptNumber'])]=a.get('finalScore')
            writer.writerow(data)
    print(json.dumps(summary))

if __name__=='__main__':main()
