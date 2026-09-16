"""Evidence-pinned Recursion audit hardening campaign, separate from global policies."""
from __future__ import annotations
import argparse,base64,hashlib,io,json,re,tarfile,gzip,concurrent.futures,time
from datetime import datetime,timezone
from pathlib import Path
import recursion_campaign as c
ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'reports/recursion/gemini-high-score-audit-20260915'
WORK=AUDIT/'refactor'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def read_evaluation(*args):
    """Retry only explicitly read-only API operations; never retry mutations."""
    assert args[:2] in (('evaluations','get'),('evaluations','get-results'),('evaluations','list'))
    for attempt in range(3):
        try:return c.cli(*args, timeout_seconds=90)
        except RuntimeError as error:
            transient=any(s in str(error) for s in ('Internal server error','Service Unavailable','Gateway','timed out','ECONNRESET','ETIMEDOUT','fetch failed'))
            if not transient or attempt==2:raise
            print(f'Transient {args[0]} {args[1]} read failure; retrying read only.',flush=True)
            time.sleep((5,15)[attempt])

def select_assets():
    assert c.read(AUDIT/'audit-gate.json')['status']=='complete'
    campaigns={}
    for p in (c.REPORTS/'campaigns').glob('*/problems/*/verified.json'):
        v=c.read(p);campaigns[v['id']]=p.parents[2]/'tasks'/(p.parent.name+'-outputs')
    rows=[]
    for r in c.read(AUDIT/'selected-latest-high-tasks.json'):
        tid=r['task'].split('-inputs.')[0]
        vs=c.read(AUDIT/'versions'/(r['problemId']+'.json'))
        v=next(v for v in vs if v['id']==r['problemVersionId'])
        private=WORK/'source-definitions'/tid;private.mkdir(parents=True,exist_ok=True)
        (private/'instruction.md').write_text(v['prompt'])
        command=v['gradingConfig']['command'];match=re.search("printf '%s' '([A-Za-z0-9+/=]+)'",command)
        assert match,(tid,'no grader archive')
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(match[1])),mode='r:*') as tf:
            answer=json.load(tf.extractfile('answer_key.json'))
        c.save(private/'answer_key.json',answer)
        if r['generation']:
            source=campaigns[v['id']];env=source/'environment';asset=env/r['task'];research=env/'mcp-server';assumptions=env/'additional-assumptions.md'
            assert asset.exists(),asset
            manifest=c.read(source/'generation-manifest.json')
            for rel,item in manifest['artifacts'].items():
                p=source/rel
                assert p.exists() and sha(p)==item['sha256'],p
            assert (source/'instruction.md').read_text()==v['prompt'],tid
            assert c.read(source/'tests/answer_key.json')==answer,tid
            provenance={'kind':'generation_manifest','generationId':manifest['generation_id'],'source':str(source)}
        else:
            source=AUDIT/'image-assets/tasks'/tid;asset=source/r['task'];research=source/'mcp-server';assumptions=source/'additional-assumptions.md'
            assert asset.exists(),asset
            provenance={'kind':'original_solver_image','image':'us-central1-docker.pkg.dev/lb-ml-dev/fde-runners/runner-fcp-claude-code-opus-5@sha256:dd7cb52a1ad7e29c29717c2ca45b60f4a1e9eb1ce283c50045e82a7eaf280316','source':str(source)}
        row={**r,'taskId':tid,'workbookPath':str(asset),'workbookSha256':sha(asset),'researchRoot':str(research) if research.exists() else None,'additionalAssumptionsPath':str(assumptions) if assumptions.exists() else None,'promptPath':str(private/'instruction.md'),'answerKeyPath':str(private/'answer_key.json'),'answerKeySha256':sha(private/'answer_key.json'),'sourceProvenance':provenance}
        rows.append(row)
    c.save(WORK/'selected-assets.json',rows)
    print(json.dumps({'tasks':len(rows),'generation':sum(r['generation'] for r in rows),'research':sum(bool(r['researchRoot']) for r in rows)}))

def grader_command(answer_path):
    stream=io.BytesIO()
    sources={'answer_key.json':Path(answer_path),'run_grader.py':ROOT/'grader/run_grader.py'}
    sources.update({'finance_grader/'+p.name:p for p in sorted((ROOT/'grader/finance_grader').glob('*.py'))})
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for name,path in sources.items():
            data=path.read_bytes();info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600
            archive.addfile(info,io.BytesIO(data))
    encoded=base64.b64encode(gzip.compress(stream.getvalue(),mtime=0)).decode()
    return (': FCP_AUDIT_DISCRETE_20260915; set -e; mkdir -p /tmp/fcp-grader /tmp/fcp-verifier; '
        f"printf '%s' '{encoded}' | base64 -d | tar -xz -C /tmp/fcp-grader; "
        'python3 /tmp/fcp-grader/run_grader.py --workspace /workspace '
        '--answer-key /tmp/fcp-grader/answer_key.json --output-dir /tmp/fcp-verifier --mode discrete; '
        'cp /tmp/fcp-verifier/reward.json "$GRADE_OUTPUT"')

def prepare():
    import sys
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from recursion_hardening_workbooks import mask_workbook
    from recursion_hardening_probe import DOMAINS
    from grader.finance_grader import grade
    audit=c.read(AUDIT/'audit-gate.json');assert audit['status']=='complete' and audit['auditedTranscripts']==1804
    repairs={r['task']:r for r in c.read(WORK/'workbook-fixes/manifest.json')}
    depth_rows=c.read(WORK/'depth-fixes/manifest.json')
    depth={r['taskId']:r for r in depth_rows};assert len(depth)==17,'Every depth candidate needs a documented disposition'
    assert not any(r.get('blocker') for r in depth_rows),'Unresolved task-depth defects require repair before publication'
    output=[];validation=[]
    for original in c.read(WORK/'selected-assets.json'):
        r=dict(original);tid=r['taskId'];r['repairs']=[]
        if tid in repairs:
            item=repairs[tid];assert item['before_sha256']==r['workbookSha256']
            r.update(workbookPath=item['output'],workbookSha256=item['after_sha256'])
            r['repairs'].append('documented_calculated_values_removed')
        d=depth.get(tid)
        if d and d.get('status')=='fixed':
            r.update(promptPath=d['promptPath'],answerKeyPath=d['answerKeyPath'])
            r['answerKeySha256']=sha(Path(r['answerKeyPath']))
            r['repairs'].append('documented_shallow_targets_expanded')
            if d.get('workbookPath'):
                # Any depth workbook must be composed from the exact selected
                # source or already repaired workbook, never an older version.
                assert d['workbookSourceSha256']==r['workbookSha256'],tid
                r.update(workbookPath=d['workbookPath'],workbookSha256=d['workbookSha256'])
            if d.get('additionalMasks'):
                target=WORK/'composed-workbooks'/tid/Path(r['workbookPath']).name
                item=mask_workbook(Path(r['workbookPath']),target,d['additionalMasks'],r['workbookSha256'])
                c.save(target.parent/'mask-receipt.json',item)
                r.update(workbookPath=str(target),workbookSha256=item['after_sha256'])
        assert sha(Path(r['workbookPath']))==r['workbookSha256']
        assert sha(Path(r['answerKeyPath']))==r['answerKeySha256']
        prompt=Path(r['promptPath']).read_text()
        normalized=prompt.replace('/app/answers.json','/workspace/answers.json').replace('http://mcp-server:8000/mcp','http://127.0.0.1:8000/mcp')
        if normalized!=prompt:
            staged=WORK/'final-prompts'/tid/'instruction.md';staged.parent.mkdir(parents=True,exist_ok=True)
            staged.write_text(normalized)
            r['promptPath']=str(staged)
            if '/app/answers.json' in prompt:r['repairs'].append('output_path_aligned_with_workspace')
            if 'http://mcp-server:8000/mcp' in prompt:r['repairs'].append('research_url_aligned_with_local_service')
        r['promptSha256']=sha(Path(r['promptPath']))
        key=c.read(Path(r['answerKeyPath']));assert key['targets']
        assert grade(key['targets'],key,mode='discrete').score==1
        assert grade({a:0 for a in key['targets']},key,mode='discrete').score==0,(tid,'zero baseline still passes')
        names=set(re.findall(r'([A-Za-z0-9_.-]+-inputs\.(?:xlsx|xlsm))',Path(r['promptPath']).read_text()))
        assert names=={r['task']},(tid,names)
        r['researchFiles']={str(p):sha(p) for p in sorted(Path(r['researchRoot']).rglob('*')) if p.is_file() and '__pycache__' not in p.parts} if r['researchRoot'] else {}
        r['additionalAssumptionsSha256']=sha(Path(r['additionalAssumptionsPath'])) if r['additionalAssumptionsPath'] else None
        r['repairs']+=['research_corpus_isolated'] if r['researchRoot'] else []
        r['repairs'].append('strict_complete_answer_pass')
        output.append(r);validation.append({'taskId':tid,'oraclePass':True,'zeroBaselineFails':True,'targetCount':len(key['targets'])})
    assert len(output)==574
    c.save(WORK/'final-assets.json',output);c.save(WORK/'final-grader-validation.json',validation)
    c.save(WORK/'campaign.json',{'id':'fcp-audit-hardening-20260915','status':'validated','createdAt':datetime.now(timezone.utc).isoformat(),
        'tasks':output,'assetsSha256':sha(WORK/'final-assets.json'),'models':['claude-opus-5:max','google-vertex/gemini-3.8-flash'],
        'runtimeManifestSha256':sha(WORK/'runtime/overlay-manifest.json'),
        'allowedDomains':list(DOMAINS),
        'graderSources':{str(p):sha(p) for p in [ROOT/'grader/run_grader.py',*sorted((ROOT/'grader/finance_grader').glob('*.py'))]},
        'attemptsPerTaskPerModel':3,'expectedAttempts':3444,'gradingMode':'discrete'})
    print(json.dumps({'validatedTasks':574,'workbookRepairs':len(repairs),'depthRepairs':sum(x['status']=='fixed' for x in depth_rows)}))

def validate_campaign():
    campaign=c.read(WORK/'campaign.json');assert campaign['status']=='validated'
    assert sha(WORK/'final-assets.json')==campaign['assetsSha256']
    assert c.read(WORK/'final-assets.json')==campaign['tasks']
    assert sha(WORK/'runtime/overlay-manifest.json')==campaign['runtimeManifestSha256']
    for path,digest in campaign['graderSources'].items():assert sha(Path(path))==digest,path
    for model in ('opus','gemini'):
        image=c.read(WORK/'final-images'/(model+'-image.json'))
        assert image['assetsManifestSha256']==campaign['assetsSha256']
        assert image['runtimeManifestSha256']==campaign['runtimeManifestSha256']
        config=c.read(WORK/'final-images/configs'/model/'verified.json')
        assert config['lockedAt'] and config['verifiedAt']
        assert config['config']['harnessImageUrl']==image['image']
        assert config['config']['allowedDomains']==campaign['allowedDomains']
        for stage in ('research','plain','named'):
            folder=WORK/'final-images/configs'/model
            probe=c.read(folder/(stage+'-status.json'))
            receipt=c.read(folder/(stage+'-probe.response.json'))
            request=c.read(folder/(stage+'-update.request.json'))
            assert probe['status']=='succeeded' and probe['id']==receipt['probeRunId']
            assert probe['runConfigVersionId']==config['id']
            assert request['config']['harnessImageUrl']==image['image']
            assert request['config']['allowedDomains']==campaign['allowedDomains']
    return campaign

def provision():
    campaign=validate_campaign()
    binding=c.read(WORK/'final-images/configs/opus/verified.json')['id']
    def one(r):
        for pathkey,hashkey in [('promptPath','promptSha256'),('answerKeyPath','answerKeySha256'),('workbookPath','workbookSha256')]:
            assert sha(Path(r[pathkey]))==r[hashkey],r['taskId']
        work=WORK/'problems'/r['taskId']
        created=c.mutation(work,'create',['problem-versions','create','--problem-id',r['problemId'],
            '--copy-from-version-id',r['problemVersionId']]);vid=created['id']
        payload={'prompt':Path(r['promptPath']).read_text(),'timeoutSeconds':10800,'maxTurns':None,
            'containerImage':None,'containerSize':'medium','privileged':False,
            'allowedDomains':campaign['allowedDomains'],'solverRunConfigVersionId':binding,'graderRunConfigVersionId':None,
            'gradingConfig':{'type':'programmatic','command':grader_command(r['answerKeyPath']),'outputFormat':'json'}}
        c.mutation(work,'update',['problem-versions','update','--problem-version-id',vid],payload)
        c.mutation(work,'lock',['problem-versions','lock','--problem-version-id',vid])
        v=c.cli('problem-versions','get','--problem-version-id',vid)
        assert v['lockedAt'] and v['gradingConfig']==payload['gradingConfig'] and v['prompt']==payload['prompt']
        c.save(work/'verified.json',v)
        print('Problem ready',r['taskId'],flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(one,campaign['tasks']))

def capacity_summary(details, model_by_config, model_limit, dispatched):
    from recursion_hardening_revisions import solver_is_terminal
    reserved={m:0 for m in ('opus','gemini')};jobs=[]
    assert len({v['id'] for v in details})==len(details)
    for evaluation in details:
        for solver in evaluation['solvers']:
            if solver_is_terminal(evaluation,solver,evaluation.get('_terminalEvidenceMatrix')):
                continue
            model=model_by_config[solver['runConfigVersionId']]
            amount=solver['metadata']['concurrency'];assert 0<amount<=75
            reserved[model]+=amount
            jobs.append({'evaluationId':evaluation['id'],'solverId':solver['id'],
                'model':model,'jobStatus':solver['jobStatus'],'concurrency':amount})
    available={m:model_limit-reserved[m] for m in reserved}
    assert min(available.values())>=0,'Existing model reservations exceed the configured cap'
    return {'modelCapacity':model_limit,'reservedConcurrencyByModel':reserved,
        'availableConcurrencyByModel':available,'activeJobs':jobs,
        'launchThroughPartition':min(3,dispatched+min(available.values())//50)}


def refresh_capacity():
    import recursion_hardening_revisions as revisions
    listing=read_evaluation('evaluations','list','--limit','100','--sort-by','createdAt','--sort-direction','desc')
    pages=[listing];items=list(listing['items']);cursor=listing.get('nextCursor');seen_cursors=set()
    while cursor:
        assert cursor not in seen_cursors,'Evaluation pagination repeated a cursor'
        seen_cursors.add(cursor)
        page=read_evaluation('evaluations','list','--limit','100','--sort-by','createdAt','--sort-direction','desc','--cursor',cursor)
        pages.append(page);items.extend(page['items']);cursor=page.get('nextCursor')
    assert len({r['id'] for r in items})==len(items),'Duplicate evaluation in capacity snapshot'
    listing={'items':items,'pagesRead':len(pages),'nextCursor':None}
    c.save(WORK/'capacity/evaluations.json',listing)
    active=[r for r in listing['items'] if r['status'] not in ('completed','failed','cancelled','canceled')]
    def one(r):
        v=read_evaluation('evaluations','get','--evaluation-id',r['id'])
        if any(s['jobStatus']=='partial' for s in v['solvers']) and not v.get('completedAt'):
            v['_terminalEvidenceMatrix']=read_evaluation('evaluations','get-results','--evaluation-id',r['id'])
        c.save(WORK/'capacity'/(r['id']+'.json'),v);return v
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:details=list(pool.map(one,active))
    mapping=revisions.known_configs()
    result=capacity_summary(details,mapping,c.CONFIG['concurrencyPerModel'],len(list(WORK.glob('evaluation-*.response.json'))))
    result['checkedAt']=datetime.now(timezone.utc).isoformat()
    c.save(WORK/'launch-capacity.json',result);print(json.dumps(result))


def launch():
    campaign=validate_campaign()
    import recursion_hardening_revisions as revisions
    if (WORK/'task-validity-review.json').exists():
        assert c.read(WORK/'task-validity-review.json')['status']=='complete','Task validity review must finish before additional dispatch'
    capacity=c.read(WORK/'launch-capacity.json')
    remaining=dict(capacity['availableConcurrencyByModel'])
    assert (datetime.now(timezone.utc)-datetime.fromisoformat(capacity['checkedAt'])).total_seconds()<300,'Refresh stale capacity'
    configs=revisions.active_configs()
    for m,v in configs.items():assert v['lockedAt'] and v['verifiedAt'],m
    versions=revisions.versions_for(campaign['tasks'])
    assets=revisions.active_assets(campaign['tasks'])
    binding_context=revisions.run_binding_context()
    for intent in WORK.glob('evaluation-*.intent.json'):
        assert intent.with_name(intent.name.replace('.intent.json','.response.json')).exists(), 'Reconcile uncertain evaluation submission before any new dispatch'
    # One solver record accepts at most75 concurrent runs. Partitioning keeps
    # the total cap explicit, with no hidden extra model attempts.
    partitions=3;per=50
    # Freeze the partition map before scores exist; independently dispatch later
    # partitions only as the old evaluations release capacity.
    requests=[c.read(p.with_name(p.name.replace('.response.json','.request.json')))
        for p in WORK.glob('evaluation-*.response.json')]
    dispatched=dispatched_model_partitions(campaign['tasks'],requests,{m:v['id'] for m,v in configs.items()},
        current_versions={r['problemId']:versions[r['taskId']]['id'] for r in campaign['tasks']},
        known_configs=revisions.known_configs())
    for part in range(partitions):
        selected=[m for m in configs if (part,m) not in dispatched and remaining[m]>=per]
        if not selected:continue
        rows=assets[part::partitions]
        problems=[]
        for r in rows:
            v=versions[r['taskId']]
            assert v['lockedAt'] and v['prompt']==Path(r['promptPath']).read_text()
            assert v['gradingConfig']=={'type':'programmatic','command':grader_command(r['answerKeyPath']),'outputFormat':'json'}
            assert binding_context['knownConfigs'][v['solverRunConfigVersionId']]=='opus'
            for model in selected:
                revisions.validate_run_binding(r['taskId'],v['id'],configs[model]['id'],context=binding_context)
            problems.append({'problemId':r['problemId'],'problemVersionId':v['id'],'environmentId':c.ENVIRONMENT})
        payload={'name':f'FCP audited hardening — pass@3 — {part+1}/{partitions} — '+', '.join(selected),
            'description':'574 distinct tasks selected from1804 historical Gemini3.8 Flash attempts scoring>80%; audited repairs; identical inputs and strict complete-answer pass for both models.',
            'metadata':{'schemaVersion':1,'attemptsPerProblem':3,'successThreshold':1,'retryPolicy':{'maxInfraRetries':2}},
            'tags':['fcp-audit-hardening-20260915'],
            'solvers':[{'displayName':'Opus 5 thinking max' if m=='opus' else 'Gemini 3.8 Flash',
                'runConfigVersionId':configs[m]['id'],'concurrency':per} for m in selected],
            'problems':problems}
        prefix='evaluation-'+str(part+1)+('' if len(selected)==2 else '-'+selected[0])
        result=c.mutation(WORK,prefix,['evaluations','bulk-create'],payload)
        for m in selected:remaining[m]-=per
        print(json.dumps({'partition':part+1,'models':selected,'id':result['id'],'problems':len(rows),'attemptsPerModel':len(rows)*3}),flush=True)


def dispatched_model_partitions(rows, requests, configs, current_versions=None, known_configs=None):
    """Independent model scheduling preserves the predetermined task partitions."""
    expected=[{r['problemId'] for r in rows[part::3]} for part in range(3)]
    seen=set();pins={}
    mapping=known_configs if known_configs is not None else {v:m for m,v in configs.items()}
    for request in requests:
        assert request['metadata']['attemptsPerProblem']==3 and request['metadata']['successThreshold']==1
        actual={p['problemId'] for p in request['problems']}
        assert len(actual)==len(request['problems']) and actual in expected,'Unexpected partition'
        part=expected.index(actual)
        versions={p['problemId']:p['problemVersionId'] for p in request['problems']}
        assert all(versions.values()),'Unpinned task version'
        if part in pins:assert versions==pins[part],'Models received different task versions'
        pins[part]=versions
        models=[mapping[s['runConfigVersionId']] for s in request['solvers']]
        assert set(models)<=set(configs),'Unknown model in prior dispatch'
        assert models and len(set(models))==len(models),'Duplicate or empty solver set'
        for model in models:
            assert (part,model) not in seen,'Duplicate model partition'
            seen.add((part,model))
    if current_versions is not None:
        for part,versions in pins.items():
            if sum((part,m) in seen for m in configs)<2:
                assert versions=={p:current_versions[p] for p in versions},'Pending counterpart version changed; explicit both-model correction required'
    return seen

def assemble_evaluation_rows(base_rows, entries, active_versions, byversion):
    """Choose complete task versions, never favorable attempts from old versions."""
    tasks={r['taskId']:{'taskId':r['taskId'],'problemVersionId':active_versions[r['taskId']],
        'models':{'opus':[],'gemini':[]}} for r in base_rows}
    seen=set();run_ids=set();superseded=[]
    for eid,solvermap,planned,result in entries:
        assert result['evaluationId']==eid,'Foreign evaluation matrix'
        assert 1<=len(solvermap)<=2 and len(set(solvermap.values()))==len(solvermap)
        assert set(solvermap.values())<={'opus','gemini'}
        expected={(s,p['problemVersionId']) for s in solvermap for p in planned['problems']}
        actual={(x['solverId'],x['problemVersionId']) for x in result['cells']}
        assert actual==expected and len(actual)==len(result['cells']),'Evaluation matrix mismatch'
        for cell in result['cells']:
            model=solvermap[cell['solverId']];version=cell['problemVersionId'];tid=byversion[version]
            assert (model,version) not in seen,'Same task version appears in multiple evaluations'
            seen.add((model,version))
            attempts=cell['attempts'];assert {a['attemptNumber'] for a in attempts}=={1,2,3} and len(attempts)==3
            for a in attempts:
                assert a['problemRunId'] not in run_ids,'Duplicate run ID across active or superseded versions'
                run_ids.add(a['problemRunId'])
            enriched=[{**a,'finalScore':a['score'],'evaluationId':eid,'problemVersionId':version} for a in attempts]
            if version==active_versions[tid]:
                assert not tasks[tid]['models'][model]
                tasks[tid]['models'][model]=enriched
            else:
                superseded.append({'taskId':tid,'model':model,'problemVersionId':version,
                    'supersededByVersionId':active_versions[tid],'attempts':enriched})
    return list(tasks.values()),superseded


def apply_failure_adjudications(rows, ledger, root=ROOT):
    assert ledger['schemaVersion']==1
    assert set(ledger['policy']['appliesEquallyTo'])=={'opus','gemini'}
    checked={}
    for row in rows:
        for model,attempts in row['models'].items():
            for attempt in attempts:
                item=ledger['adjudications'].get(attempt['problemRunId'])
                if not item:continue
                assert item['taskId']==row['taskId'] and item['model']==model
                assert item['attemptNumber']==attempt['attemptNumber'] and item['evaluationId']==attempt['evaluationId']
                assert item['classification']=='probable_solver_resource_exhaustion' and item['derivedOutcome']==0
                assert item['rawScore'] is None and attempt['finalScore'] is None
                assert attempt['status']==item['rawStatus']=='failed'
                allowed_errors={item['rawErrorMessage']}
                regrade=item.get('sameRunRegrade')
                if regrade:
                    assert regrade['status']=='failed' and regrade['score'] is None
                    assert regrade['attemptNumber']==attempt['attemptNumber'] and regrade['regradeCount']==0
                    assert any(e['path'].endswith('/post-regrade-result.json') for e in item['evidence'])
                    allowed_errors.add(regrade['error'])
                assert attempt['errorMessage'] in allowed_errors,'Unexpected failure transition'
                assert item['newSolverAttemptsLaunched']==0 and not item['canonicalGraderScoreProduced']
                assert item['evidence']
                for evidence in item['evidence']:
                    path=(root/evidence['path']).resolve()
                    assert root.resolve() in path.parents,'Evidence outside audit workspace'
                    if path not in checked:checked[path]=sha(path)
                    assert checked[path]==evidence['sha256'],'Failure evidence changed'
                if regrade:
                    record=c.read(root/next(e['path'] for e in item['evidence'] if e['path'].endswith('/post-regrade-result.json')))
                    assert record['id']==attempt['problemRunId'] and record['status']=='failed' and record['finalScore'] is None
                    assert record['errorMessage']==regrade['error'] and record['attemptNumber']==attempt['attemptNumber']
                attempt['failureAdjudication']={'outcome':0,'classification':item['classification'],
                    'reason':item['reason'],'evidenceLedger':'failure-adjudications.json'}
    return rows


def attempt_outcome(attempt):
    if (attempt['status']=='completed' and attempt.get('finalScore') is not None
            and not attempt.get('errorMessage') and not attempt.get('gradingError')):
        assert attempt['finalScore'] in (0,1),'Campaign must use discrete grader'
        return attempt['finalScore']
    adjudication=attempt.get('failureAdjudication')
    if adjudication:
        assert attempt['status']=='failed' and attempt.get('finalScore') is None
        assert adjudication['outcome']==0 and adjudication['classification']=='probable_solver_resource_exhaustion'
        return 0
    return None


def collect():
    import recursion_terminal_matrix_cache as terminal_cache
    campaign=c.read(WORK/'campaign.json')
    import recursion_hardening_revisions as revisions
    configs=revisions.known_configs()
    binding_context=revisions.run_binding_context()
    byversion=revisions.historical_versions(context=binding_context)
    active_versions={t:v['id'] for t,v in revisions.versions_for(campaign['tasks']).items()}
    byversion.update({v:t for t,v in active_versions.items()})
    entries=[];collection_sources=[]
    receipts=sorted([*WORK.glob('evaluation-*.response.json'),*WORK.glob('correction-*.response.json')])
    for receipt in receipts:
        evaluation=c.read(receipt);eid=evaluation['id'];prefix=receipt.name.split('.')[0]
        detail=read_evaluation('evaluations','get','--evaluation-id',eid)
        detail_read_at=datetime.now(timezone.utc).isoformat()
        cached=terminal_cache.load(WORK,prefix,detail)
        if cached is None:
            result=read_evaluation('evaluations','get-results','--evaluation-id',eid)
            source={'kind':'remote_get_results','matrixRetrievedAt':datetime.now(timezone.utc).isoformat()}
        else:
            result,source=cached
        assert detail['id']==eid and result['evaluationId']==eid,'Evaluation provenance mismatch'
        c.save(WORK/'results'/(prefix+'-detail.json'),detail);c.save(WORK/'results'/(prefix+'-matrix.json'),result)
        solver_configs={s['id']:s['runConfigVersionId'] for s in detail['solvers']}
        solvermap={s:configs[cfg] for s,cfg in solver_configs.items()}
        planned=c.read(WORK/(prefix+'.request.json'))
        assert {s['runConfigVersionId'] for s in detail['solvers']}=={s['runConfigVersionId'] for s in planned['solvers']}
        assert planned['metadata']['attemptsPerProblem']==3 and planned['metadata']['successThreshold']==1
        for cell in result['cells']:
            revisions.validate_run_binding(byversion[cell['problemVersionId']],cell['problemVersionId'],
                solver_configs[cell['solverId']],context=binding_context)
        collection_sources.append({**source,'evaluationId':eid,'prefix':prefix,'detailRetrievedAt':detail_read_at})
        entries.append((eid,solvermap,planned,result))
    rows,superseded=assemble_evaluation_rows(campaign['tasks'],entries,active_versions,byversion)
    c.save(WORK/'results/superseded-task-runs.json',superseded)
    ledger=WORK/'results/failure-adjudications.json'
    if ledger.exists():rows=apply_failure_adjudications(rows,c.read(ledger))
    collected=datetime.now(timezone.utc).isoformat()
    previous=c.read(WORK/'results/task-runs.json') if (WORK/'results/task-runs.json').exists() else []
    before={(r['taskId'],m,a['attemptNumber']):a for r in previous for m,attempts in r['models'].items() for a in attempts}
    events=[]
    for r in rows:
        for m,attempts in r['models'].items():
            for a in attempts:
                old=before.get((r['taskId'],m,a['attemptNumber']))
                fields=('problemRunId','problemVersionId','status','finalScore','errorMessage','gradingError','failureAdjudication')
                current={k:a.get(k) for k in fields};prior={k:old.get(k) for k in fields} if old else None
                if current!=prior:events.append({'collectedAt':collected,'taskId':r['taskId'],'model':m,
                    'attemptNumber':a['attemptNumber'],'previous':prior,'current':current})
    (WORK/'results').mkdir(exist_ok=True)
    with (WORK/'results/attempt-events.jsonl').open('a') as stream:
        for event in events:stream.write(json.dumps(event)+'\n')
    c.save(WORK/'results/task-runs.json',rows)
    c.save(WORK/'results/collection-sources.json',{'collectedAt':collected,'sources':collection_sources})
    summary=summarize(rows);summary['collectedAt']=collected
    c.save(WORK/'results/summary.json',summary);print(json.dumps(summary))

def summarize(rows):
    result={'tasks':len(rows),'models':{}}
    assert len({r['taskId'] for r in rows})==len(rows),'Duplicate task'
    all_ids=[]
    for r in rows:
        for attempts in r['models'].values():
            assert len({a['attemptNumber'] for a in attempts})==len(attempts),'Duplicate logical attempt'
            assert all(a['attemptNumber'] in (1,2,3) for a in attempts)
            all_ids.extend(a['problemRunId'] for a in attempts)
    assert len(set(all_ids))==len(all_ids),'Duplicate run ID'
    for m in ('opus','gemini'):
        groups=[r['models'][m] for r in rows];assert all(len(x)<=3 for x in groups),'Extra attempts require reconciliation'
        runs=[r for group in groups for r in group]
        completed=[r for r in runs if r['status']=='completed' and r.get('finalScore') is not None and not r.get('errorMessage') and not r.get('gradingError')]
        assert all(r['finalScore'] in (0,1) for r in completed),'Campaign must use discrete grader'
        valid=[r for r in runs if attempt_outcome(r) is not None]
        finished=sum(len(g)==3 and all(r['status'] in ('completed','failed','cancelled') for r in g) for g in groups)
        solved=sum(any(r['status']=='completed' and r.get('finalScore')==1 and not r.get('errorMessage') and not r.get('gradingError') for r in g) for g in groups)
        uncertain=sum(not any(attempt_outcome(r)==1 for r in g) and
            (len(g)<3 or any(attempt_outcome(r) is None for r in g)) for g in groups)
        result['models'][m]={'expectedAttempts':len(rows)*3,'observedAttempts':len(runs),'scoredAttempts':len(completed),
            'validAttempts':len(valid),'adjudicatedSolverFailures':len(valid)-len(completed),
            'resolvedTasks':finished,'solvedTasks':solved,'passAt3':None,
            'uncertainTaskOutcomes':uncertain,
            'passAt3Bounds':{'lower':solved/len(rows),'upper':(solved+uncertain)/len(rows)} if rows else None,
            'perAttemptPassRate':None,
            'infrastructureOrUnscoredAttempts':sum(r['status'] in ('completed','failed','cancelled') and attempt_outcome(r) is None for r in runs)}
    result['allAttemptsTerminal']=all(x['resolvedTasks']==len(rows) for x in result['models'].values())
    result['complete']=all(x['validAttempts']==len(rows)*3 for x in result['models'].values())
    if result['complete'] and rows:
        for m in ('opus','gemini'):
            result['models'][m]['passAt3']=result['models'][m]['solvedTasks']/len(rows)
            result['models'][m]['perAttemptPassRate']=sum(attempt_outcome(r) for task in rows for r in task['models'][m])/(len(rows)*3)
    result['opusMinusGeminiPercentagePoints']=100*(result['models']['opus']['passAt3']-result['models']['gemini']['passAt3']) if result['complete'] and rows else None
    ob=result['models']['opus']['passAt3Bounds'];gb=result['models']['gemini']['passAt3Bounds']
    result['opusMinusGeminiPercentagePointBounds']={'lower':100*(ob['lower']-gb['upper']),
        'upper':100*(ob['upper']-gb['lower'])} if rows else None
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['select-assets','prepare','provision','refresh-capacity','launch','collect']);args=p.parse_args()
    globals()[args.action.replace('-','_')]()
