"""Resumable Opus-max rollout and full-project evaluation receipts."""
import argparse
import json
from pathlib import Path
import recursion_campaign as c
import recursion_solver_policy as policy

WORK = c.REPORTS/'opus-max-20260915'


def inventory():
    first = c.read(WORK/'problems-page1.json')
    items = list(first['items']); page = first; n = 1
    while page.get('nextCursor'):
        n += 1
        path = WORK/f'problems-page{n}.json'
        if path.exists():
            page = c.read(path)
        else:
            page = c.cli('problems','list-for-environment','--environment-id',c.ENVIRONMENT,
                         '--limit','100','--enrichment-filters','{}','--cursor',page['nextCursor'])
            c.save(path,page)
        items.extend(page['items'])
    assert len(items) == first['total'] == len({r['id'] for r in items})
    c.save(WORK/'inventory.json',items)
    print(json.dumps({'total':len(items),'drafts':sum(r['isDraft'] for r in items),
        'generationRecords':sum((r.get('externalId') or '').startswith('fcp-generation-') for r in items)}))


def smoke():
    parent = c.read(WORK/'parent.json')
    draft = c.mutation(WORK,'create',['run-configs','create-version','--run-config-id',parent['runConfigId'],
                       '--parent-run-config-version-id',parent['id']])
    payload = {'config':policy.enforce(parent['config']), 'probe':dict(parent['probe']),
               'notes':'Permanent FCP policy: only claude-opus-5, max effort, all auxiliary model aliases pinned to Opus 5.'}
    payload['probe']['prompt'] = ('First use Bash to print only CLAUDE_CODE_EFFORT_LEVEL, ANTHROPIC_MODEL, '
       'ANTHROPIC_DEFAULT_HAIKU_MODEL, CLAUDE_CODE_SUBAGENT_MODEL and the current Claude process command line '
       '(do not print other environment values). Confirm effort is max and model is claude-opus-5. '
       'Use research list_sources once and confirm 0370-inputs.xlsx exists. Then reply FCP_OPUS_MAX_READY.')
    payload['probe']['qualityCheck'] = ('PASS only if actual Bash output shows effort max and model claude-opus-5, '
       'the running Claude command includes --effort max, 0370-inputs.xlsx exists, research list_sources succeeds, '
       'and final answer is FCP_OPUS_MAX_READY. Fail for auth, unsupported flags, wrong models, or runtime errors.')
    c.mutation(WORK,'update',['run-config-versions','update','--run-config-version-id',draft['id']],payload)
    probe = c.mutation(WORK,'probe',['run-config-versions','probe','--run-config-version-id',draft['id']])
    status = c.cli('probe-runs','get','--run-config-probe-run-id',probe['probeRunId'])
    c.save(WORK/'probe-status.json',status)
    print(json.dumps({'versionId':draft['id'],'probeId':probe['probeRunId'],'status':status['status']}))

def versions():
    import concurrent.futures
    rows=c.read(WORK/'inventory.json')
    cache=c.REPORTS/'evaluation-coverage-20260914/versions'
    def get(row):
        out=WORK/'versions'/(row['id']+'.json')
        if out.exists(): return
        old=cache/out.name
        values=c.read(old) if old.exists() else []
        latest=max(values,key=lambda v:int(v['version'][1:])) if values else None
        if latest is None or latest['createdAt'] != row['latestVersionCreatedAt']:
            values=c.cli('problem-versions','list-for-problem','--problem-id',row['id'])
        c.save(out,values)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(get,rows))
    import re
    selected={}
    for row in rows:
        v=max(c.read(WORK/'versions'/(row['id']+'.json')),key=lambda v:int(v['version'][1:]))
        assert v['lockedAt'] and v['environmentId']==c.ENVIRONMENT
        names=set(re.findall(r'`([^`\n]+\.(?:xlsx|xlsm|xls))`',v['prompt'],re.I))
        assert len(names)==1,(row['title'],names)
        name=next(iter(names))
        entry={'task':name,'title':row['title'],'problemId':row['id'],'problemVersionId':v['id'],
               'environmentId':c.ENVIRONMENT,'createdAt':v['createdAt'],'generation':(row.get('externalId') or '').startswith('fcp-generation-')}
        if name not in selected or entry['createdAt']>selected[name]['createdAt']:selected[name]=entry
    c.save(WORK/'selected.json',sorted(selected.values(),key=lambda r:r['task']))
    print(json.dumps({'latestTasks':len(selected),'generationTasks':sum(r['generation'] for r in selected.values())}))


if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['inventory','smoke','versions']); args=p.parse_args()
    globals()[args.action]()
