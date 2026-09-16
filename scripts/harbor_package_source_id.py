"""Closed source-ID CLI integration for the original package recovery protocol.

The retained failed command is evidence, never an input namespace for replay.
A completed original takeover and original package/gate implementation retain
ownership of leases and lane approval.
"""
from contextlib import contextmanager
from dataclasses import asdict,replace
from pathlib import Path
import hashlib,importlib.util,re,sys

SCHEMA='harbor-source-id-package-recovery/v1'
FIELDS={'schema_version','helper','snapshot','cli_runtime','cli_profile','takeover_profile','takeover_completion','recovery','retained_helper','retained_plan','guard_seconds'}


def module(R,name,row):
    R.E.checked_bytes(row)
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
    return m


def profile(R,row):
    p=R.read(row)
    R.require(set(p)in(FIELDS,FIELDS|{'analysis_cache'}) and p['schema_version']==SCHEMA and p['helper']==R.record(__file__),'closed source-ID recovery integration required')
    for key in set(p)-{'schema_version','guard_seconds'}:R.E.checked_bytes(p[key])
    cli=module(R,'_package_recovery_cli',p['cli_runtime']);cp=cli.profile(p['cli_profile'])
    snap=R.read(p['snapshot']);root=Path(snap['root'])
    R.require(root==R.ROOT and cp['cli']==R.record(root/'xl_variable_mcp.py'),'package integration original root changed')
    expected={r['relative']:r['sha256'] for r in snap['files']}
    R.require(len(expected)==len(snap['files']),'duplicate snapshot inventory')
    actual={}
    for path in root.rglob('*'):
        R.require(not path.is_symlink(),'symlink in original package snapshot')
        if path.is_file() and '__pycache__' not in path.relative_to(root).parts:actual[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    R.require(actual==expected,'complete original package snapshot changed')
    for m,relative in ((R.P,'scripts/batch/harbor_environment_producer.py'),(R.C,'scripts/harbor_campaign.py'),(R.F,'scripts/batch/harbor_environment_refresh.py'),(R.W,'scripts/batch/harbor_environment_worker.py'),(R.notes,'scripts/batch/harbor_lane_notes.py')):
        R.require(R.record(m.__file__)==R.record(root/relative),'loaded package dependency differs from original snapshot')
    R.require(R.record(R.P.__file__)['sha256']==R.AFTER_PRODUCER,'original producer is not the reviewed deterministic package implementation')
    tp=R.read(p['takeover_profile'])
    R.require(tp['schema_version']=='harbor-allowance-takeover-direct/v1' and tp['original_snapshot']==p['snapshot'] and set(tp['shared'])=={'lifecycle','allowance','takeover','stdlib'},'original declared takeover required')
    for item in tp['shared'].values():R.E.checked_bytes(item)
    allowance=R.read(tp['allowance'])
    R.require(type(p['guard_seconds'])is int and p['guard_seconds']==3300 and p['guard_seconds']<=allowance['limits']['stage_seconds'],'source-ID guard exceeds explicit operational allowance')
    return p,cli,cp,tp


def code(R,row):
    p,cli,cp,tp=profile(R,row)
    R.require(p['recovery']==R.record(R.__file__),'different selected package recovery implementation')
    values=[R.record(R.__file__),row,*[p[k]for k in sorted(set(p)-{'schema_version','guard_seconds'})],*tp['shared'].values(),*[cp[k]for k in ('original','selected','cli')]]
    return R.notes.evidence_list(values)


@contextmanager
def takeover(R,tp):
    import scripts.batch as package
    saved=[]
    try:
        for key,name in [('lifecycle','harbor_tracker_lifecycle'),('allowance','harbor_continuation_allowance')]:
            full='scripts.batch.'+name;saved.append((full,sys.modules.get(full),name,getattr(package,name,None)))
            m=module(R,full,tp['shared'][key]);setattr(package,name,m)
        yield module(R,'_source_id_package_takeover',tp['shared']['takeover'])
    finally:
        for full,old,name,attr in reversed(saved):
            if old is None:sys.modules.pop(full,None)
            else:sys.modules[full]=old
            if attr is None:delattr(package,name)
            else:setattr(package,name,attr)


def lease(R,row,descriptor,checkpoint,original,before,current):
    p,cli,cp,tp=profile(R,row)
    with takeover(R,tp) as T:
        prior,after,issued=T.validate_completion(p['takeover_completion'])
        done=R.read(p['takeover_completion']);stop=R.read(done['stopped_owner_proof'])
        R.require(T._selection(stop['selection'])==T._selection(tp['selection']),'completed takeover differs from selected original plan')
    selected=tp['selection']
    R.require(selected['checkpoint']==checkpoint and selected['worker_kind']=='environment' and selected['workbook']==cp['workbook']
        and stop['before']['sha256']==original.sha256 and prior['revision']==original.revision
        and (prior['leases']['task_agent']['owner'],prior['leases']['task_agent']['token'])==(original.owner,original.token)
        and after==before and asdict(issued)==asdict(current),'package lease detached from completed phase2 and original takeover')
    s,_,_=R.C.selected(descriptor)
    content=R.decode(R.read(checkpoint)['selection']).content
    original_source=content.bindings['original_raw_source']
    R.require(selected['tracker']['path']==str(s.bootstrap.policy.tracker_root/(s.bootstrap.workbook+'.json'))
        and prior['bindings']['original_raw_source']==after['bindings']['original_raw_source']==original_source
        and content.bindings['source_file']==prior['bindings']['source_file']==after['bindings']['source_file']==cp['source'],'takeover source/tracker scope changed')


def destination(R,row,directory,current):
    _,_,cp,_=profile(R,row)
    R.require(Path(cp['work'])==directory/'package' and cp['workbook']==current.content.workbook
        and cp['normalized']==current.content.bindings['normalized_final'] and cp['source']==current.content.bindings['source_file'],
        'fresh package CLI inputs or namespace differ from owning selection')


def failure(R,row,descriptor,checkpoint,outcome,s,current):
    _,_,cp,tp=profile(R,row)
    v=R.read(outcome)
    R.require(v==dict(status='RECONCILIATION_REQUIRED',resource=v['resource'],automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None),'sealed original package guard required')
    resource=R.read(v['resource']);intent=R.read(resource['intent'])
    R.require(set(resource)=={'intent','returncode','metrics','log','failure'} and resource['returncode']==1 and resource['failure']is None,'unresolved package command cannot be recovered')
    R.require(intent['schema_version']=='harbor-coordinator-step/v1' and intent['action']=='step' and intent['arguments']==dict(descriptor=descriptor,checkpoint=checkpoint,captures=None)
        and intent['dispatch']is True and intent['code']==R.record(R.C.__file__) and intent['controls']==R.read(descriptor)['controls']
        and intent['memory_limit_bytes']==R.MEMORY and intent['timeout_seconds']==3300,'original package guard identity changed')
    R.require(tp['selection']['guard']==v['resource'] and tp['selection']['checkpoint']==checkpoint,'takeover does not retain this failed package guard')
    for item in (resource['log'],intent['executable']):R.E.checked_bytes(item)
    R.require(R.E.checked_bytes(resource['log']).rstrip().endswith(b'AdmissionError: packaging command failed: build-a'),'outer failure was not first build-a')
    old=s.run/'packaging';saved=R.read(R.record(old/'selection.json'));prior=R.decode(saved['content'])
    expected=replace(current.content,mcp_build_a=old/'build-a',mcp_build_b=old/'build-b')
    R.require(prior==expected and saved==dict(schema_version='harbor-environment-production/v1',content=R.encode(expected),profiles=R.encode(current.public_profiles),taxonomy=s.taxonomy,code=R.record(R.P.__file__),mode='mcp'),'failed package selection changed')
    R.require(cp['workbook']==expected.workbook and cp['source']==expected.bindings['source_file'] and cp['normalized']==expected.bindings['normalized_final'],'source-ID fix belongs to different package inputs')
    result=R.read(R.record(old/'build-a.result.json'));command=R.read(R.record(old/'build-a.intent.json'))
    R.require(result==dict(returncode=1,log=R.record(old/'build-a.log')),'first build did not seal failure')
    log=R.E.checked_bytes(result['log']).decode()
    R.require(re.search(r'(?:^|\n)ValueError: Duplicate explicit source id [^\n]+ collides with a different source definition\s*$',log)is not None,'different source-ID failure')
    argv=[intent['executable']['path'],*R.commands(expected,old,s.taxonomy)['build-a']]
    # The original interpreter may be spelled through its resolved symlink.
    R.require(Path(command['argv'][0]).resolve()==Path(argv[0]).resolve() and command==dict(argv=[command['argv'][0],*argv[1:]],entry=cp['cli'],model_calls_permitted=False),'failed original build command changed')
    tree=R.checked_tree(old);relative={str(Path(x['path']).relative_to(old))for x in tree}
    allowed={'selection.json','build-a.intent.json','build-a.result.json','build-a.log'}
    R.require(all(x in allowed or x.startswith('build-a/')for x in relative) and allowed<=relative,'later package command or completion already exists')
    return R.notes.evidence_list([descriptor,checkpoint,outcome,v['resource'],resource['intent'],resource['log'],intent['executable'],*tree])


def completed_commands(R,row,root):
    p,cli,cp,_=profile(R,row)
    R.require(Path(cp['work'])==root,'completed physical command namespace differs')
    for label in ('build-a','build-b'):
        intent=R.record(root/(label+'.intent.json'));logical,_=cli.command(cp,intent)
        value=R.read(R.record(root/(label+'.runtime.json')))
        R.require(value==dict(schema_version=cli.SCHEMA,profile=p['cli_profile'],intent=intent,logical_command=logical,physical_command=cli.physical(p['cli_profile'],intent),automatic_retry=False),'completed physical source-ID command changed')


@contextmanager
def installed(R,row):
    p,cli,_,_=profile(R,row)
    with cli.installed(R.P,p['cli_profile']):yield



@contextmanager
def analysis_scope(R,row,checkpoint):
    p,_,cp,_=profile(R,row)
    if 'analysis_cache'not in p:
        yield None;return
    cache=module(R,'_package_normalization_analysis_cache',p['analysis_cache'])
    retained=R.read(p['retained_plan']);runtime=R.read(retained['runtime_profile'])
    content=R.decode(R.read(checkpoint)['selection']).content
    R.require(content.bindings['source_file']==cp['source'],'analysis source differs from package source')
    dependencies=[p['retained_plan'],p['retained_helper'],retained['runtime'],retained['runtime_profile']]
    for symbol in runtime['symbols']:dependencies.extend((symbol['original'],symbol['selected']))
    with cache.installed(R.W.normalization.gen,workbook=content.workbook,source=content.bindings['source_file'],
        baseline=content.bindings['baseline_inputs'],draft=content.bindings['normalization_draft'],
        normalizer=R.record(R.ROOT/'gen_normalizer.py'),dependencies=dependencies)as metrics:
        yield metrics

def child_command(R,row,intent):
    p,_,_,_=profile(R,row)
    v=R.read(intent)
    R.require(v.get('integration')==row and v['code']==code(R,row),'package child declaration changed')
    return [sys.executable,'-I','-B',__file__,'child','--intent',intent['path'],'--sha256',intent['sha256']]


def child(intent):
    # Bootstrap only the bound retained loader. It authenticates the original
    # complete source/campaign context and retains the three declared symbols.
    import json
    raw=Path(intent['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=intent['sha256']:raise ValueError('package child intent changed')
    v=json.loads(raw);row=v['integration'];raw=Path(row['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('package integration changed')
    p=json.loads(raw)
    class Bootstrap:
        class E:
            @staticmethod
            def checked_bytes(r):
                path=Path(r['path'])
                if not path.is_absolute() or any(q.is_symlink()for q in(path,*path.parents)):raise ValueError('unsafe package dependency')
                value=path.read_bytes()
                if hashlib.sha256(value).hexdigest()!=r['sha256']:raise ValueError('package dependency changed')
                return value
    M=module(Bootstrap,'_package_original_context',p['retained_helper'])
    with M.context(p['retained_plan'],initial=False):
        R=module(Bootstrap,'_selected_package_recovery',p['recovery'])
        code(R,row)
        checkpoint=v['arguments']['checkpoint']if v['action']=='prepare'else R.read(v['arguments']['prepared'])['checkpoint']
        with analysis_scope(R,row,checkpoint)as metrics:
            result=R.child(intent)
        if metrics is not None:R.keep(Path(intent['path']).parent/'analysis-cache.json',dict(integration=row,intent=intent,metrics=metrics,process_scoped=True))
        return result


if __name__=='__main__':
    import argparse,json
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['child']);parser.add_argument('--intent',required=True);parser.add_argument('--sha256',required=True)
    args=parser.parse_args();print(json.dumps(child(dict(path=args.intent,sha256=args.sha256))))
