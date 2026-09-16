"""Isolated, resumable run-config probes for the transcript-audit campaign."""
from __future__ import annotations
import argparse,copy,json
from pathlib import Path
import recursion_campaign as c

DOMAINS=['api.anthropic.com','aiplatform.googleapis.com','us-central1-aiplatform.googleapis.com',
         'us-east5-aiplatform.googleapis.com','oauth2.googleapis.com',
         'pypi.org','files.pythonhosted.org','registry.npmjs.org','models.dev']

def configure(folder,models,stages=('research','plain','named'),trusted_identity=False):
    for model in models:
        parent=c.read(folder/(model+'-parent.json'));image=c.read(folder/(model+'-image.json'))['image']
        work=folder/'configs'/model
        created=c.mutation(work,'create',['run-configs','create'],{
            'scope':{'level':'env','id':c.ENVIRONMENT},'type':'agent-harness',
            'name':f'FCP audited isolation {model} {folder.name}',
            'description':'Private research corpus, restricted solver UID, audited task assets; explicit two-model campaign.',
            'parentRunConfigVersionId':parent['id'],'tags':['fcp-audit-hardening-20260915']})
        vid=created['initialVersionId']
        plan=[(kind,task) for kind,task in [('research','0006'),('plain','0004'),('named','SampleACLtd_model')] if kind in stages]
        if (work/'probe-plan.json').exists():assert c.read(work/'probe-plan.json')==list(stages),'Probe plan changed'
        else:c.save(work/'probe-plan.json',list(stages))
        for kind,task in plan:
            marker=f'FCP_ISOLATION_{model.upper()}_{kind.upper()}_READY'
            prompt=(f'Confirm {task}-inputs.xlsx is readable. Run `python /usr/local/bin/fcp-isolation-probe` '
                    'and inspect its result. Print only the current model CLI process command line and model/effort selection '
                    '(do not print authentication or other environment values). '
                    f'Only if all isolation checks pass, reply exactly {marker}.')
            if trusted_identity:
                prompt=(f'Test the runtime for {task}-inputs.xlsx by executing `/usr/local/bin/fcp-isolation-probe` directly. '
                    'The trusted script checks isolation, tool access, package installation, and the running model identity. '
                    'Read its JSON result. If isolation_passed is true and every check passes, '
                    f'reply exactly {marker}. Otherwise report the failed check names and stop. '
                    'Do not alter the runtime, probe, or model configuration.')
            config=copy.deepcopy(parent['config']);config.update(harnessImageUrl=image,timeoutSeconds=10800,
                privileged=False,allowedDomains=DOMAINS)
            if model=='opus':
                assert config.get('args')=='--model claude-opus-5 --effort max'
                assert config['envVars']['CLAUDE_CODE_EFFORT_LEVEL']=='max'
            else:assert config['envVars']['FCP_OPENCODE_MODEL']=='google-vertex/gemini-3.8-flash'
            payload={'config':config,'probe':{'prompt':prompt,'files':[],
                'qualityCheck':f'PASS only if actual tool output contains isolation_passed true, '
                    f'all checks true, readable {task}-inputs.xlsx, correct '+
                    ('claude-opus-5 --effort max' if model=='opus' else 'google-vertex/gemini-3.8-flash')+
                    f' process invocation, and final {marker}. Fail on authentication, unsupported flags, runtime errors, or false checks.'},
                'notes':f'FCP transcript audit: {model} {kind} filesystem and actual model transport probe.'}
            c.mutation(work,kind+'-update',['run-config-versions','update','--run-config-version-id',vid],payload)
            (work/(kind+'-update.request.json')).chmod(0o600)
            probe=c.mutation(work,kind+'-probe',['run-config-versions','probe','--run-config-version-id',vid])
            status=c.cli('probe-runs','get','--run-config-probe-run-id',probe['probeRunId'])
            c.save(work/(kind+'-status.json'),status)
            print(json.dumps({'model':model,'stage':kind,'probeId':probe['probeRunId'],'status':status['status']}),flush=True)
            if status['status']!='succeeded':break
        else:
            c.mutation(work,'lock',['run-config-versions','lock','--run-config-version-id',vid])
            v=c.cli('run-config-versions','get','--run-config-version-id',vid)
            assert v['lockedAt'] and v['verifiedAt'] and v['config']==config
            c.save(work/'verified.json',v);(work/'verified.json').chmod(0o600)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--models',nargs='+',choices=['opus','gemini'],default=['opus','gemini'])
    p.add_argument('--stages',nargs='+',choices=['research','plain','named'],default=['research','plain','named'])
    p.add_argument('--trusted-identity',action='store_true')
    a=p.parse_args();configure(a.folder,a.models,a.stages,a.trusted_identity)
