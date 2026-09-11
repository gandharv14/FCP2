import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest
from openpyxl import Workbook
from scripts.corpus import inventory, digest
from scripts.batch.harbor_tracker import validate_tracker
from scripts.batch.harbor_tracker_lifecycle import validate_lifecycle

from scripts.batch import harbor_admission as a


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value));return a.record(path)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    # Host capacity is an installation probe, like Docker below. Keep generated
    # fixture workflows hermetic; low-disk tests override this observation.
    monkeypatch.setattr(a.shutil,'disk_usage',lambda _:SimpleNamespace(free=8*1024**3))
    source=tmp_path/'raw';source.mkdir();wb=Workbook();wb.active['A1']=3;wb.save(source/'0001.xlsx');wb.close()
    inv=tmp_path/'inventory.json';inventory([source],inv,1)
    history=tmp_path/'runs';history.mkdir();trackers=tmp_path/'fleet/workbooks';trackers.mkdir(parents=True)
    req=tmp_path/'requirements.txt';req.write_text('openpyxl=='+a.importlib.metadata.version('openpyxl')+'\n')
    docker=tmp_path/'docker';docker.write_text('fixture docker executable')
    realwhich=a.shutil.which
    monkeypatch.setattr(a.shutil,'which',lambda n: str(docker) if n=='docker' else realwhich(n))
    def probe(command,**kwargs):
        if command[0]==str(docker):return SimpleNamespace(stdout=json.dumps({'ServerVersion':'29.0','ServerErrors':[]}))
        assert command[:3]==[sys.executable,'-I','-c'];return SimpleNamespace(stdout=json.dumps(['openpyxl','yaml']))
    monkeypatch.setattr(a.subprocess,'run',probe)
    recipe=a.DependencyRecipe((a.record(req),),minimum_disk_bytes=1)
    report=write(tmp_path/'dependency.json',a.dependency_report(recipe,tmp_path))
    policy=a.AdmissionPolicy('fixture',('0001',),trackers,a.record(inv),report,recipe,(history,),expected_original_count=1)
    return a.DispatcherAdmission(policy),policy,history,source/'0001.xlsx',tmp_path


def test_create_has_real_pending_lifecycle_and_exact_intake(setup):
    facade,p,history,source,tmp=setup
    result=facade.create('0001');t=result['tracker'];validate_tracker(t);validate_lifecycle(t)
    assert t['revision']==0 and t['requested_ids']==['0001']
    assert t['bindings']['original_raw_source']==a.record(source)
    assert all(v['state']=='pending' and v['attempt']==0 for v in t['lane_state'].values())
    assert all(v is None for v in t['generation_ids'].values())
    with pytest.raises(a.lifecycle.Conflict):facade.create('0001')


@pytest.mark.parametrize('change',['original','inventory','dependency','requirements','executable','expired','future','oldschema','imports','docker','lowdisk'])
def test_actual_dependency_and_original_evidence_rejected(setup,monkeypatch,change):
    facade,p,history,source,tmp=setup
    if change=='original':source.write_bytes(b'altered')
    elif change=='inventory':Path(p.original_inventory['path']).write_text('{}')
    elif change=='requirements':Path(p.dependencies.requirements[0]['path']).write_text('altered')
    elif change=='dependency':Path(p.dependency['path']).write_text('{}')
    else:
        report=a.read(p.dependency)
        if change=='executable':report['observation']['executable_sha256']='0'*64
        elif change=='expired':report['checked_at_ns']=time.time_ns()-p.dependencies.max_age_ns-1000
        elif change=='future':report['checked_at_ns']=time.time_ns()+p.dependencies.max_age_ns
        elif change=='oldschema':report={'schema_version':1,'passed':True}
        elif change=='imports':monkeypatch.setattr(a.subprocess,'run',lambda *x,**y:SimpleNamespace(stdout='[]'))
        elif change=='docker':monkeypatch.setattr(a.subprocess,'run',lambda cmd,**y:SimpleNamespace(stdout=json.dumps(['openpyxl','yaml']) if cmd[0]==sys.executable else '{"ServerVersion":"29","ServerErrors":["daemon unavailable"]}'))
        elif change=='lowdisk':monkeypatch.setattr(a.shutil,'disk_usage',lambda _:SimpleNamespace(free=0))
        object.__setattr__(p,'dependency',write(tmp/'dependency2.json',report))
    with pytest.raises((ValueError,KeyError)):facade.create('0001')
    assert not (p.tracker_root/'0001.json').exists()


@pytest.mark.parametrize('marker',['task.toml','task.json','task.yaml','task.yml','instruction.md','release-manifest.json','current-release.json'])
def test_fresh_prior_task_marker_blocks_new_identity(setup,marker):
    facade,p,history,source,tmp=setup
    directory=history/'0001-oldtask';directory.mkdir();(directory/marker).write_text('evidence')
    with pytest.raises(a.AdmissionError,match='prior task'):facade.create('0001')


def test_archive_task_identity_excluded_without_extracting(setup):
    facade,p,history,source,tmp=setup
    with zipfile.ZipFile(history/'prior.zip','w') as z:z.writestr('0001-outputs/task.toml','prior task')
    with pytest.raises(a.AdmissionError,match='prior task'):facade.create('0001')


def test_old_tracker_attempts_never_reset(setup):
    facade,p,history,source,tmp=setup
    write(history/'batch/workbooks/0001.json',{'workbook_id':'0001','lane_state':{'harbor-research-auditor':{'attempt':2}},'generation_ids':{},'promotion_ids':{}})
    with pytest.raises(a.AdmissionError,match='lossless resume'):facade.create('0001')


def test_all_prior_audit_artifacts_retained_without_implied_budget_reset(setup):
    facade,p,history,source,tmp=setup
    folder=history/'0001-variable-sources'
    metadata=write(folder/'0001.metadata.json',{'workbook':'0001','api_calls':2,'status':'complete'})
    request=write(folder/'attempts/request.json',{'messages':['opaque retained evidence']})
    t=facade.create('0001')['tracker'];bound=t['handoffs'][0]['intake_evidence']
    assert metadata in bound and request in bound
    assert t['lane_state']['harbor-research-auditor']['state']=='pending'
    assert all(v is None for v in t['generation_ids'].values())


@pytest.mark.parametrize('mutation',['unknown','cohort','candidate','missingnamespace','unmapped','symlink'])
def test_admission_cannot_change_identity_or_forge_lineage(setup,mutation):
    facade,p,history,source,tmp=setup
    if mutation=='unknown':
        with pytest.raises(a.AdmissionError):facade.create('9999')
        return
    if mutation=='cohort':object.__setattr__(p,'requested_ids',('0001','9999'))
    elif mutation=='candidate':
        other=tmp/'candidate.xlsx';other.write_bytes(source.read_bytes()+b'extra')
        with pytest.raises(ValueError):facade.create('0001',raw=a.record(other))
        return
    elif mutation=='missingnamespace':history.rmdir()
    elif mutation=='unmapped':(history/'task.toml').write_text('unknown task')
    elif mutation=='symlink':(history/'shortcut').symlink_to(source)
    with pytest.raises(ValueError):facade.create('0001')


def test_current_native_published_generation_is_revalidated(setup):
    from xl_source_recalc import prepare_source_generation
    facade,p,history,source,tmp=setup
    generation,_=prepare_source_generation(source,'0001',tmp/'source-out/0001')
    t=facade.create('0001',generation=a.record(generation/'generation-manifest.json'))['tracker']
    assert t['raw_source']==a.record(source)
    assert any(x['path']==str(generation/'ast-provenance.json') for x in t['handoffs'][0]['intake_evidence'])


@pytest.mark.parametrize('alter',['manifest','ast','health','builder'])
def test_changed_published_generation_cannot_admit(setup,monkeypatch,alter):
    import xl_source_publication as publication
    from xl_source_recalc import prepare_source_generation
    facade,p,history,source,tmp=setup
    generation,_=prepare_source_generation(source,'0001',tmp/'source-out/0001')
    bound=a.record(generation/'generation-manifest.json')
    if alter=='builder':
        fake=tmp/'xl_ast_graph.py';fake.write_text('changed builder')
        monkeypatch.setattr(publication,'_default_builder_paths',lambda:(fake,))
    else:
        path=generation/({'manifest':'generation-manifest.json','ast':'ast/0001/nodes.csv','health':'health.json'}[alter]);path.chmod(0o600);path.write_text('{}')
    with pytest.raises(ValueError):facade.create('0001',generation=bound)


def transformed_setup(setup):
    from xl_volatile_formula_remediation import build_plan,apply_plan
    from xl_source_recalc import prepare_source_generation
    facade,p,history,source,tmp=setup
    wb=Workbook();wb.calculation.fullCalcOnLoad=False;wb.calculation.calcMode='auto';wb.active['A1']=3;wb.active['B1']='=OFFSET(A1,0,)';wb.save(source);wb.close()
    with zipfile.ZipFile(source) as z:parts={n:z.read(n) for n in z.namelist()}
    parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'<v></v>',b'<v>3</v>').replace(b'<v />',b'<v>3</v>')
    with zipfile.ZipFile(source,'w') as z:
        for n,v in parts.items():z.writestr(n,v)
    inv=tmp/'newinventory.json';inventory([source.parent],inv,1);object.__setattr__(p,'original_inventory',a.record(inv));facade=a.DispatcherAdmission(p)
    plan=build_plan(source);candidate=tmp/'0001.xlsx';receipt=apply_plan(source,plan,candidate)
    planref=write(tmp/'plan.json',plan);manifestref=write(tmp/'normalization.json',receipt)
    normalization=write(tmp/'normalization-record.json',dict(original_sha256=digest(source),candidate=str(candidate),candidate_sha256=digest(candidate),plan=planref['path'],plan_sha256=planref['sha256'],manifest=manifestref['path'],manifest_sha256=manifestref['sha256']))
    generation,_=prepare_source_generation(candidate,'0001',tmp/'candidate-out/0001')
    return facade,a.record(candidate),a.record(generation/'generation-manifest.json'),normalization


def test_actual_deterministic_candidate_uses_independent_lineage_validator(setup):
    facade,candidate,generation,normalization=transformed_setup(setup)
    t=facade.create('0001',raw=candidate,generation=generation,normalization=(normalization,))['tracker']
    assert t['raw_source']==candidate and t['bindings']['original_raw_source']!=candidate


@pytest.mark.parametrize('change',['omitted','changed_manifest','wrongoriginal'])
def test_false_candidate_lineage_cannot_admit(setup,change):
    facade,candidate,generation,normalization=transformed_setup(setup)
    items=(normalization,)
    if change=='omitted':items=()
    elif change=='changed_manifest':Path(a.read(normalization)['manifest']).write_text('{}')
    else:
        value=a.read(normalization);value['original_sha256']='0'*64;normalization=write(Path(normalization['path']),value);items=(normalization,)
    with pytest.raises(ValueError):facade.create('0001',raw=candidate,generation=generation,normalization=items)


def test_metadata_without_actual_requests_blocks_budget_reset(setup):
    facade,p,history,source,tmp=setup
    meta=write(history/'0001-variable-sources/0001.metadata.json',{'workbook':'0001','api_calls':1})
    with pytest.raises(a.AdmissionError,match='actual budget inheritance'):
        a.validate_prior_audit_budget([meta],None,'0001')


def test_existing_budget_contract_counts_exact_retained_requests(setup):
    facade,p,history,source,tmp=setup
    folder=history/'0001-variable-sources'
    meta=write(folder/'0001.metadata.json',{'workbook':'0001','api_calls':1})
    req=write(folder/'request.json',{'model':'fixture','endpoint':'https://example.invalid','messages':[{'role':'system','content':'audit'},{'role':'user','content':'Workbook metadata:\n{"workbook":"0001"}\n\nInventory batch 1 of 1 (NDJSON):\n{"row_id":"row1","band":"Sheet!A1"}'}]})
    resp=write(folder/'response.json',{'content':'fixture','finish_reason':'stop'})
    inherited=write(tmp/'budget.json',{'schema_version':'audit-inherited-budget/v1','workbook':'0001','attempts':[{'request':req,'response':resp}]})
    batches=a.validate_prior_audit_budget([meta,req,resp],inherited,'0001')
    assert len(batches)==1 and batches[0][0]['row_id']=='row1'
    with pytest.raises(ValueError):a.validate_prior_audit_budget([meta],inherited,'0001')
    more=write(folder/'request2.json',a.read(req))
    with pytest.raises(ValueError):a.validate_prior_audit_budget([meta,req,resp,more],inherited,'0001')
    Path(resp['path']).write_text('{}')
    with pytest.raises(ValueError):a.validate_prior_audit_budget([meta,req,resp],inherited,'0001')


def test_exact_new50_selection_uses_inventory_and_full_history_scope(setup):
    facade,p,history,source,tmp=setup
    rows=[]
    for n in range(1,51):
        rows.append(dict(workbook_id=f'{n:04}',path=str(tmp/f'{n:04}.xlsx'),sha256=f'{n:064x}',size_bytes=n,format='.xlsx',corpus='fixtures'))
    unsigned=dict(schema_version='raw-corpus/v1',sources=rows)
    inv=write(tmp/'all-originals.json',{**unsigned,'inventory_sha256':a.object_hash(unsigned)})
    survey=write(tmp/'survey.json',dict(inventory=inv,scanned_roots=[str(history)],unmapped_prior_artifacts=[]))
    manifest=dict(schema_version='new50-qualified-task-candidate-proposal/v1',target_new_qualified_tasks=50,denominator_for_original_rebuild=50,survey=survey,selected_candidates=[dict(original=r,exclusion_evidence=[],prior_audit_metadata=[]) for r in rows])
    binding=write(tmp/'candidates.json',manifest)
    args=dict(batch='new50',tracker_root=p.tracker_root,original_inventory=inv,dependency=p.dependency,dependencies=p.dependencies,history_roots=(history,),expected_original_count=50)
    policy=a.policy_from_candidate_manifest(binding,**args);assert policy.requested_ids==tuple(r['workbook_id'] for r in rows)
    for mutation in ('duplicate','excluded','changedoriginal','short'):
        altered=json.loads(json.dumps(manifest))
        if mutation=='duplicate':altered['selected_candidates'][1]=altered['selected_candidates'][0]
        elif mutation=='excluded':altered['selected_candidates'][0]['exclusion_evidence']=['old task']
        elif mutation=='changedoriginal':altered['selected_candidates'][0]['original']['sha256']='0'*64
        else:altered['selected_candidates'].pop()
        bound=write(tmp/f'{mutation}.json',altered)
        with pytest.raises(ValueError):a.policy_from_candidate_manifest(bound,**args)
    with pytest.raises(ValueError):a.policy_from_candidate_manifest(binding,**{**args,'history_roots':()})


def test_prior_task_compatibility_link_excludes_identity_without_descent(setup):
    facade,p,history,source,tmp=setup
    (history/'0001-outputs').symlink_to(tmp/'missing-private-target',target_is_directory=True)
    with pytest.raises(a.AdmissionError,match='prior task'):
        facade.create('0001')


@pytest.mark.parametrize('technical', ['code', 'runtime', 'snapshots'])
def test_explicit_technical_namespace_links_are_pruned_without_descent(setup,technical):
    facade,p,history,source,tmp=setup
    (history/technical).symlink_to(tmp/'unavailable-technical-target',target_is_directory=True)
    excluded,_=a.survey_namespaces((history,),{'0001'})
    assert excluded == {}
