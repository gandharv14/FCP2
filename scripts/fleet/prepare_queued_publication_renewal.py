"""Prepare the existing queued-lease primitive from authenticated completed custody.

No task, lease, model or publication is executed by this preparation. The
original native runner and root admission gate revalidate all evidence at CAS.
"""
from pathlib import Path
import json
from scripts.fleet.migration import binding,checked,retain


def preparation_context(prepared,home,custody):
    """Resolve either original completed custody or a stopped direct warning."""
    if 'continuation' not in prepared:
        selection=checked(binding(home/'selection.json'))
        launched=checked(selection['launch'])
        return checked(checked(launched['intent'])['plan']),custody
    prior=checked(prepared['continuation']);launched=checked(prepared['launched'])
    launch=checked(checked(launched['intent'])['plan']);job=checked(launch['job'])
    plan=checked(prepared['plan'])
    if (prior['schema_version']!='harbor-direct-warning-continuation/v1'
        or custody['status']!='STOPPED_DIRECT_WARNING_CUSTODY' or custody['processes']!=[]
        or launched['remote']['root_intent']!=launched['intent']
        or launch['root_plan']!=prepared['continuation'] or job['driver']!=prior['helper']
        or job['source']['sha256']!=prior['source_sha256'] or job['workbook']!=prior['workbook']
        or plan['ready']!=custody['ready']
        or plan['retained_warning']!=dict(continuation_helper=prior['helper'],continuation_plan=prepared['continuation'],job=launch['job'],root_launch=prepared['launched'])):
        raise ValueError('original stopped direct warning publication required')
    mapping=[]
    for row in custody['copies']:
        if set(row)!={'native','copy'} or binding(row['copy']['path'])!=row['copy'] or row['copy']['sha256']!=row['native']['sha256']:
            raise ValueError('direct warning custody copy changed')
        mapping.append(dict(original=row['native'],local=row['copy']))
    if len({row['original']['path']for row in mapping})!=len(mapping):
        raise ValueError('duplicate direct warning custody')
    process=[row for row in mapping if row['original']==custody['process']]
    if len(process)!=1 or custody['process']['path']!=launch['remote_receipt']:
        raise ValueError('original direct warning process custody required')
    return launch,dict(custody,mapping=mapping)


def guarded_proof(prepared,outer):
    guard=json.loads(outer['stdout']);proof=json.loads(guard['log'])
    if 'continuation'in prepared:
        if (outer['returncode']!=0 or guard['returncode']!=0 or guard['failure']is not None
            or proof['status']!='PASS' or proof['models_dispatched']!=0 or proof['publication_mutations']!=0):
            raise ValueError('actual direct warning publication guard required')
        proof=checked(proof['proof'])
    return guard,proof


def prepare(repository,prepared_row,guard_row,allowance,output,*,runtime):
    root=Path(repository).absolute();home=Path(prepared_row['path']).parent
    prepared=checked(prepared_row);custody=checked(prepared['custody'])
    launch,custody=preparation_context(prepared,home,custody);job=checked(launch['job'])
    outer=checked(guard_row);guard,proof=guarded_proof(prepared,outer)
    if (outer['returncode']!=0 or guard['returncode']!=0 or guard['failure']is not None
        or proof['status']!='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED'
        or proof['models_dispatched']!=0 or proof['publication_mutations']!=0
        or proof['plan']!=prepared['plan']or proof['ready']!=custody['ready']
        or proof['tracker']!=custody['tracker']):
        raise ValueError('actual completed original publication proof required')
    policy=checked(allowance);plan=checked(prepared['plan']);manifest=checked(prepared['implementation'])
    if policy['scope']!=dict(workbook=job['workbook'],original_sha256=job['source']['sha256']):
        raise ValueError('queued allowance belongs to another original source')
    if manifest['publisher']!=proof['publisher']or plan['ready']!=custody['ready']:
        raise ValueError('publication implementation or actual READY changed')
    out=Path(output).absolute();out.mkdir();implementation=out/'implementation';implementation.mkdir()
    rows={}
    for name,source in [('runner','scripts/fleet/queued_publication_renewal.py'),('root','scripts/fleet/queued_renewal_launch.py'),('queued','scripts/batch/harbor_queued_publication_lease.py')]:
        path=implementation/(name+'.py');path.write_bytes((root/source).read_bytes());rows[name]=binding(path)
    proof_row=retain(out/'ready-proof.json',proof)
    lease=retain(out/'lease-profile.json',dict(schema_version='harbor-queued-publication-lease/v1',workbook=job['workbook'],source_sha256=job['source']['sha256'],tracker=proof['tracker'],ready_proof=proof_row,allowance=allowance,home='/home/gmahajan_labelbox_com/synthetic-data-fleet/'+job['workbook']+'-'+out.name))
    process=next(x['original']for x in custody['mapping']if x['original']['path']==launch['remote_receipt'])
    native=dict(schema_version='fleet-queued-publication-renewal/v1',implementation=rows['runner'],publisher=proof['publisher'],publication_plan=prepared['plan'],queued_reader=rows['queued'],lease_profile=lease,process=process,job=launch['job'],outcome=custody['outcome'])
    if custody.get('completed_ready')is not None:
        certificate=checked(custody['completed_ready'])
        if certificate['worker_outcome']!=custody['outcome']or certificate['ready']!=custody['ready']:raise ValueError('stopped READY certificate differs')
        path=implementation/'completed_ready.py';path.write_bytes((root/'scripts/fleet/publication_launch.py').read_bytes())
        native.update(completed_ready=custody['completed_ready'],completed_ready_reader=binding(path))
    selected=retain(out/'plan.json',native);control=root/'diagnostics/vm-fleet-20260912/control'
    frozen=checked(runtime);ref=checked(binding(control/'source-pool-next-001/publication-001/0282/queued-lease-001/root-profile.json'))
    version_custody=retain(out/'version-custody.json',dict(schema_version='fleet-publication-version-custody/v1',files=custody['mapping']))
    root_profile=retain(out/'root-profile.json',dict(ref,version_custody=version_custody,helper=rows['root'],runtime=runtime,coordinator=launch['coordinator_receipt'],capacity=binding(Path(frozen['root'])/'scripts/fleet/capacity_policy.py'),plan=selected,runner=rows['runner'],host=launch['host'],python=job['command'][0],expected_fence=launch['expected_fence']+1,expected_owner=launch['new_owner'],source_sha256=job['source']['sha256'],version=job['version'],outcome=custody['outcome']))
    cmd=[str(root/'.venv/bin/python'),'-I','-B',rows['root']['path'],'--profile',root_profile['path'],'--sha256',root_profile['sha256']]
    retain(out/'commands.json',dict(preflight=[*cmd,'--journal',str(out/'root-preflight-001')],execute=[*cmd,'--journal',str(out/'root-renew-001'),'--execute']))
    probe=out/'probe.py';probe.write_text('import inspect,importlib.util,sys,json,os\nos.environ["HARBOR_FLEET_URL"]="http://127.0.0.1:8765"\nrow='+repr(rows['runner'])+'\ns=importlib.util.spec_from_file_location("queued",row["path"]);M=importlib.util.module_from_spec(s);sys.modules[s.name]=M;s.loader.exec_module(M)\nassert M.binding(row["path"])==row\nprint(json.dumps(M.run('+repr(selected)+',renew=False)))\n')
    files=[str(x)for x in implementation.iterdir()]+[str(out/'ready-proof.json'),str(out/'lease-profile.json'),str(out/'plan.json'),str(probe)]
    retain(out/'stage-files.json',files)
    return root_profile
