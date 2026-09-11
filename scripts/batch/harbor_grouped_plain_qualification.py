"""Requalify complete grouped audits as provided-input plain tasks.

The original rejected phase1 and all independent reviews remain immutable.
Only the grouped-row heuristic is replaced by authenticated atomic coverage.
"""
from pathlib import Path
import json
import hashlib
from scripts.batch.harbor_admission import record, read, require
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_compact as K
from scripts.batch import harbor_normalization_validator_bridge as B

SCHEMA='harbor-grouped-plain-qualification/v1'
SIDECAR='grouped-plain-qualification.json'


def coverage(rows, fresh, prepared, aggregate, *, minimum=1):
    require(K.equal(K.json_value(rows),K.json_value(fresh)),'complete audit atomic evidence differs')
    atoms=sum(len(row['cells']) for row in rows)
    refs=list(dict.fromkeys(row['reference'] for row in rows if row['clean']))
    parts=[ref for part in prepared['partitions'] for ref in part['references']]
    require(atoms>0 and refs and prepared['full_candidate_references']==refs,'missing complete audit candidates')
    require(len(parts)==len(set(parts)) and set(parts)==set(refs),'partition coverage incomplete')
    require(set(aggregate['review']['cells'])==set(refs),'independent review coverage incomplete')
    require(all(v['eligible'] is False for v in aggregate['review']['cells'].values()),'eligible research input cannot be downgraded')
    reconciliation=aggregate['dependency_reconciliation']
    require(set(reconciliation['candidate_ids'])==set(refs) and len(set(reconciliation['candidate_ids'].values()))==len(refs)
            and reconciliation['review_decisions_changed'] is False,'dependency coverage differs')
    unique=len({row['reference'] for row in rows})
    require(len(refs)>=minimum,'distinct reviewed candidate coverage below floor')
    return dict(audit_atoms=atoms,unique_audited_references=unique,reviewed_candidate_references=len(refs),partitions=len(prepared['partitions']))


def project(original_guard, witnesses=()):
    import plain_eligibility as policy
    guard=read(original_guard);directory=Path(original_guard['path']).parent
    require(original_guard==record(directory/'result.json'),'foreign phase1 guard')
    resource=record(directory/'resource.json');r=read(resource);intent=record(directory/'intent.json');i=read(intent)
    require(r['returncode']==0 and r['failure'] is None and r['intent']==intent and i['action']=='emit','phase1 guard did not complete')
    require(guard['status']=='phase1','original guard is not phase1')
    phase_binding=guard['phase1'];phase=read(phase_binding)
    require(phase['status']=='normalization_rejected' and phase['semantic_budget_reset'] is False,'only retained rejected phase1 may qualify')
    prepared=phase['prepared'];m=read(prepared);selection=read(m['selection']);run=Path(selection['run'])
    require(phase_binding==record(run/'phase1-result.json') and i['workbook']==phase['workbook']==selection['workbook'],'foreign normalization identity')
    plan=read(i['plan']);require(plan['workbooks'][phase['workbook']]['prepared']==prepared,'guard reviewed another preparation')
    mutable=list(phase['outputs'].values())
    require(len(witnesses) in (0,len(mutable)), 'incomplete immutable output witnesses')
    def retained(binding):
        matches=[w for w in witnesses if w['original']==binding]
        if matches:
            require(binding in mutable and len(matches)==1 and matches[0]['copy']['sha256']==binding['sha256'],'invalid output witness')
            return read(matches[0]['copy'])
        return read(binding)
    require(not witnesses or {json.dumps(w['original'],sort_keys=True) for w in witnesses}=={json.dumps(x,sort_keys=True) for x in mutable},'foreign mutable witness')
    bindings=selection['bindings'];draft,rows=N.gen.analyse(phase['workbook'],bindings['source_file']['path'],bindings['source_file']['sha256'],runs_root=run.parent,inputs_root=Path(bindings['baseline_inputs']['path']).parent)
    require(K.equal(draft,read(m['legacy']['draft.json'])),'original audit draft changed')
    retained_rows=N.expand_artifact(read(m['legacy']['analysis.json']))
    aggregate=read(phase['normalization_partitioned_review'])
    owned=B.validate(phase['normalization_partitioned_review'],prepared=prepared,source=bindings['source_file'],baseline=bindings['baseline_inputs'],draft=m['legacy']['draft.json'],rows=rows,policy=N.Policy(**selection['policy']),bindings=bindings)
    require(owned['review']==aggregate['review'],'owning review changed')
    counts=coverage(retained_rows,rows,m,aggregate)
    # This resolver requires live source/segmentation PASS evidence and exact
    # curation/formula/target bindings; zero research variables are not a source waiver.
    pair=N.resolve_build_context(phase['workbook'],source_root=Path(bindings['source_generation']['path']).parent.parent,source_generation_id=bindings['source_generation']['id'],segmentation_root=Path(bindings['segmentation_generation']['path']).parent.parent,segmentation_generation_id=bindings['segmentation_generation']['id'])
    require(pair['bindings']==selection['pipeline_bindings'],'verified source generation changed')
    _,projection=N.expected_outputs(phase['workbook'],draft,rows,run,selection['pipeline_bindings'])
    old_exact=all(record(run/name)['sha256']==binding['sha256'] for name,binding in phase['outputs'].items())
    new_exact=all(path.read_bytes()==(json.dumps(value,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode() for path,value in projection.items())
    require(old_exact or new_exact,'current outputs are neither complete original phase1 nor complete generated phase2')
    original_mode=policy.evaluate(run,allow_grouped=False)
    require(original_mode['mode']=='fail' and original_mode['reason'].startswith('draft_rows '),'failure is not solely grouped row floor')
    require(original_mode['first_atomic_variables']==0 and not original_mode['atomic_variables'] and not original_mode['forced_exclusions'] and not original_mode['extraction_codes'] and not original_mode['sentinel_row'],'plain prerequisites fail')
    coverage(retained_rows,rows,m,aggregate,minimum=original_mode['draft_row_floor'])
    report=retained(phase['outputs']['normalization_report.json']);dispositions=report.get('dispositions') or []
    require(dispositions and all(d.get('status')=='excluded' and str(d.get('reason') or '').strip() for d in dispositions),'complete exclusion reasons required')
    require(read(phase['first_normalization'])['atomic_variables']==0,'original positive normalization cannot be downgraded')
    require(read(phase['phase1'])['variables']==[] and retained(phase['outputs']['normalized.json'])['variables']==[],'plain spec is not empty')
    checked={**owned['checked_files']}
    for binding in [original_guard,resource,intent,i['plan'],phase_binding,prepared,phase['first_normalization'],phase['phase1'],*phase['outputs'].values(),phase['plain_eligibility']]:
        retained(binding)
        matches=[w for w in witnesses if w['original']==binding]
        selected=matches[0]['copy'] if matches else binding
        checked[selected['path']]=selected['sha256']
    return dict(schema_version=SCHEMA,original_guard=original_guard,witnesses=list(witnesses),original_phase1=phase_binding,prepared=prepared,run=str(run),workbook=phase['workbook'],coverage=counts,original_mode=original_mode,source_verified=True,original_inputs_retained=True,review_decisions_changed=False,semantic_budget_reset=False,checked_files=dict(sorted(checked.items())),code={record(__file__)['path']:record(__file__)['sha256'],record(policy.__file__)['path']:record(policy.__file__)['sha256']})


def prepare(original_guard,directory):
    directory=Path(directory);require(not directory.exists(),'fresh qualification namespace required')
    project(original_guard);directory.mkdir(parents=True)
    phase=read(read(original_guard)['phase1']);witnesses=[]
    for name,binding in phase['outputs'].items():
        read(binding);copy=N.keep(directory/('original-'+name),Path(binding['path']).read_bytes())
        witnesses.append(dict(original=binding,copy=copy))
    value=project(original_guard,witnesses)
    return N.keep(directory/'qualification.json',value)


PRIOR_SNAPSHOT_SHA256='5508a04dbb2aa5258ab9fe7ece236270d6e6fa5f454d842fab2a768626dedaed'
SELF='scripts/batch/harbor_grouped_plain_qualification.py'
REFRESH='scripts/batch/harbor_environment_refresh.py'
REFRESH_SUCCESSOR_SHA256='69c515ca4c8a7aa283e51717f3816852bcee9043d9a0bd42d93c1a3d9ce118f2'
BOUNDARY_SNAPSHOT_SHA256='089d7e8c1822d58f34e33f6355f6a4be688b463ec16078b774b927ce4bc79c56'


def _inventory(root):
    files={}
    for path in Path(root).rglob('*'):
        require(not path.is_symlink(),'qualification snapshot contains symlink')
        rel=path.relative_to(root)
        if any(x in {'__pycache__','.pytest_cache','.git'} for x in rel.parts):continue
        if path.is_file():files[str(rel)]=record(path)['sha256']
    return files


PERFORMANCE_SNAPSHOT_SHA256='56f2065fa2ee08d8057b1f2c6de2a8010d141e21ea03a89424116cba69f17f52'
LOCAL_RESTART='scripts/batch/harbor_instruction_local_restart.py'
LOCAL_RESTART_SHA256='d49d0a56a9101c0049162c7205a37956fa5c5d39a373034d42ed24c4cf3ab9e3'
TAKEOVER='scripts/batch/harbor_lease_takeover.py'
TAKEOVER_SHA256='076a453c1f9e9b5eb7ec5f9e90ca989899468c985acfd99221b6291f606e1506'
COORDINATOR='scripts/harbor_instruction_campaign.py'
RESTART_HOOK="    from scripts.batch.harbor_instruction_local_restart import validate_selected\n    validate_selected(w,d)\n"
RESTART_ANCHOR="    for row in d['origin']:base.executor.checked_bytes(row)"
LOCAL_COMMAND='scripts/batch/harbor_instruction_local_command.py'
LOCAL_COMMAND_OVERLAY_SHA256='3dc2ab187e124ebe6ac614ed5e1b5f697e17d0f385473e9ca076c6fa41565be0'
RESTART_SNAPSHOT_SHA256='ca1de5448b6c05707d69c0ca0c329c5542cc9470aad87b4291089de796d62277'


def _local_command_code_profile(prior,current,now):
    """Retain qualification across the exact metadata-only local write repair."""
    overlay=read(dict(path=str(prior.parent/'0184-local-command-reviewed-overlay-v3.json'),sha256=LOCAL_COMMAND_OVERLAY_SHA256))
    predecessor=dict(path=str(prior.parent/'0184-local-restart-snapshot.json'),sha256=RESTART_SNAPSHOT_SHA256)
    require(overlay['predecessor']==predecessor,'local write predecessor changed')
    baseline=read(predecessor)['snapshot'];root=Path(baseline['root'])
    files={row['relative']:row['sha256'] for row in baseline['files']}
    require(len(files)==len(baseline['files']) and _inventory(root)==files,'local write baseline changed')
    expected_names={LOCAL_COMMAND,LOCAL_RESTART,'scripts/batch/harbor_instruction_worker.py',
                    '.cursor/skills/task-disclosure/scripts/disclose.py'}
    rows=overlay['files'];names=[row['relative'] for row in rows]
    require(len(names)==len(set(names)) and set(names)==expected_names,'local write overlay scope changed')
    expected=dict(files)
    for row in rows:
        path=Path(overlay['root'])/row['relative']
        require(row==dict(relative=row['relative'],path=str(path),sha256=record(path)['sha256']),
                'local write reviewed overlay bytes changed')
        expected[row['relative']]=row['sha256']
    require(set(now)==set(expected) and all(now[k]==v for k,v in expected.items() if k!=SELF),
            'local write successor changed unrelated or reviewed code')


def _instruction_code_profile(prior,current,now):
    """Exact reviewed disclosure performance/restart code; financial checks stay identical."""
    if LOCAL_COMMAND in now:
        return _local_command_code_profile(prior,current,now)
    selected=read(dict(path=str(prior.parent/'0184-indexed-methods-snapshot.json'),sha256=PERFORMANCE_SNAPSHOT_SHA256))
    baseline=selected['snapshot'];root=Path(baseline['root']);files={r['relative']:r['sha256'] for r in baseline['files']}
    require(len(files)==len(baseline['files']) and _inventory(root)==files,'performance baseline inventory changed')
    require(set(now)==set(files)|{LOCAL_RESTART},'instruction successor inventory differs')
    allowed={SELF,LOCAL_RESTART,COORDINATOR,TAKEOVER}
    require(all(now[k]==v for k,v in files.items() if k not in allowed),'instruction repair changed unrelated baseline code')
    require(now[LOCAL_RESTART]==LOCAL_RESTART_SHA256 and now[TAKEOVER]==TAKEOVER_SHA256,'unreviewed local restart or lease authority')
    text=(root/COORDINATOR).read_text()
    require(text.count(RESTART_ANCHOR)==1 and (current/COORDINATOR).read_text()==text.replace(RESTART_ANCHOR,RESTART_HOOK+RESTART_ANCHOR),'unreviewed coordinator restart hook')
    require(now['plain_eligibility.py']==files['plain_eligibility.py'],'instruction restart changed financial eligibility')


def validate_code_relocation(value,expected):
    """One approved phase-boundary repair; old bytes are verified, never run."""
    paths=[p for p in value['code'] if p.endswith('/'+SELF)]
    require(len(paths)==1,'ambiguous qualification validator predecessor')
    prior=Path(paths[0]).parents[2];current=Path(__file__).resolve().parents[2]
    snapshot=read(dict(path=str(prior.parent/'downstream-snapshot-v3.json'),sha256=PRIOR_SNAPSHOT_SHA256))['snapshot']
    require(snapshot['root']==str(prior),'unapproved qualification predecessor root')
    files={r['relative']:r['sha256'] for r in snapshot['files']}
    require(_inventory(prior)==files,'qualification predecessor snapshot changed')
    now=_inventory(current)
    allowed={SELF}
    if now.get(REFRESH)!=files.get(REFRESH):
        boundary=read(dict(path=str(prior.parent/'downstream-snapshot-v4.json'),sha256=BOUNDARY_SNAPSHOT_SHA256))['snapshot']
        boundary_root=Path(boundary['root']);boundary_files={r['relative']:r['sha256'] for r in boundary['files']}
        require(_inventory(boundary_root)==boundary_files and set(boundary_files)==set(files)
                and all(boundary_files[k]==v for k,v in files.items() if k!=SELF),'qualification boundary snapshot changed')
        require(now.get(REFRESH)==REFRESH_SUCCESSOR_SHA256,'unreviewed refresh-chain successor')
        allowed.add(REFRESH)
    if not (set(now)==set(files) and all(now[k]==v for k,v in files.items() if k not in allowed)):
        _instruction_code_profile(prior,current,now)
    require(value['code']=={str(prior/name):files[name] for name in (SELF,'plain_eligibility.py')},'qualification predecessor code differs')
    require(expected['code']=={str(current/name):now[name] for name in (SELF,'plain_eligibility.py')},'current qualification code differs')
    rebased={}
    for path,sha in expected['checked_files'].items():
        p=Path(path)
        if p.is_relative_to(current):
            rel=str(p.relative_to(current))
            require(rel!=SELF and files.get(rel)==sha,'only same-byte validator evidence may relocate')
            path=str(prior/rel)
        rebased[path]=sha
    require(value['checked_files']==rebased,'qualification financial or evidence bindings changed')
    require({k:v for k,v in value.items() if k not in {'code','checked_files'}}=={k:v for k,v in expected.items() if k not in {'code','checked_files'}},'qualification data changed during code transition')


def validate(binding,*,run):
    value=read(binding);expected=project(value['original_guard'],value['witnesses'])
    require({k:v for k,v in value.items() if k not in {'code','checked_files'}}=={k:v for k,v in expected.items() if k not in {'code','checked_files'}},'qualification data changed')
    if value!=expected:validate_code_relocation(value,expected)
    require(Path(value['run'])==Path(run),'grouped plain qualification changed')
    return expected


def install(binding):
    value=read(binding);run=Path(value['run']);validate(binding,run=run)
    # New pointer only; no previous normalization receipt/report is replaced.
    sidecar=N.keep(run/SIDECAR,dict(schema_version=SCHEMA,qualification=binding))
    import plain_eligibility as policy
    mode=policy.evaluate(run)
    require(mode['mode']=='plain' and mode['eligible'] is True,'plain successor failed')
    old=read(value['original_phase1'])
    successor={**old,'status':'phase1_evidence_ready','original_phase1':value['original_phase1'],'grouped_plain_qualification':binding,'grouped_plain_sidecar':sidecar,'plain_eligibility':N.keep(Path(binding['path']).parent/'plain-eligibility.json',mode)}
    return N.keep(Path(binding['path']).parent/'phase1-result.json',successor)


def validate_sidecar(run):
    selected=read(record(Path(run)/SIDECAR));require(set(selected)=={'schema_version','qualification'} and selected['schema_version']==SCHEMA,'invalid grouped plain sidecar')
    return validate(selected['qualification'],run=run)


def validate_successor(binding, *, original_guard):
    value=read(binding);qualification=value['grouped_plain_qualification'];proof=read(qualification)
    require(proof['original_guard']==original_guard,'successor belongs to another original guard')
    run=Path(proof['run']);validate(qualification,run=run)
    sidecar=record(run/SIDECAR)
    require(read(sidecar)==dict(schema_version=SCHEMA,qualification=qualification),'successor sidecar is not selected')
    import plain_eligibility as policy
    mode=policy.evaluate(run)
    require(mode['mode']=='plain' and mode['eligible'] is True and read(value['plain_eligibility'])==mode,'plain successor mode changed')
    expected={**read(proof['original_phase1']),'status':'phase1_evidence_ready','original_phase1':proof['original_phase1'],
              'grouped_plain_qualification':qualification,'grouped_plain_sidecar':sidecar,'plain_eligibility':value['plain_eligibility']}
    require(value==expected,'plain successor changed original normalization bindings')
    return value
