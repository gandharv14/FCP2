"""Validate an explicitly selected active normalizer in its approved frozen root.

This is process isolation of the active normalization implementation, not
execution of arbitrary historical task code. The current process retains lease
and gate authority. No request, model call, budget, or artifact is rewritten.
"""
from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict

APPROVED_SNAPSHOT_SHA256='9876445cfce18daceb5cfffd0736a4c6a15d5117b5f99a1d844cbcb8e7dea79b'
SNAPSHOT_NAME='normalization-snapshot.json'


def _require(ok,message):
    if not ok:raise ValueError(message)


def _record(path):
    path=Path(path).absolute()
    _require(not any(p.is_symlink() for p in (path,*path.parents)),'normalization validator symlink forbidden')
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def _read(binding):
    _require(isinstance(binding,dict) and set(binding)=={'path','sha256'} and _record(binding['path'])==binding,
             'normalization validator evidence changed')
    return json.loads(Path(binding['path']).read_text())


def _approved(prepared):
    manifest=_read(prepared)
    suffix='/scripts/batch/harbor_normalization_partitioned.py'
    paths=[p for p in manifest['code'] if p.endswith(suffix)]
    _require(len(paths)==1,'ambiguous active normalization root')
    root=Path(paths[0]).parents[2]
    selected=os.environ.get('HARBOR_FLEET_NORMALIZATION_PROFILE')
    extra={}
    if selected:
        profile_binding=json.loads(selected);profile=_read(profile_binding)
        required={'schema_version','prepared','snapshot','authorization'}
        _require(set(profile) in (required,required|{'retained_partition'})
                 and profile['schema_version']=='harbor-normalization-snapshot-selection/v1'
                 and profile['prepared']==prepared,'exact prepared normalization profile required')
        # A new root selection names the existing inventory; it never creates
        # the old hardcoded filename or changes an old approval/hash.
        authority=profile['authorization'];_require(_record(authority['path'])==authority,'normalization selection authority changed')
        binding=profile['snapshot'];snapshot=_read(binding)
        extra={profile_binding['path']:profile_binding['sha256'],authority['path']:authority['sha256']}
        if profile.get('retained_partition'):
            history=profile['retained_partition']
            _require(set(history)=={'loader','plan'},'closed retained partition reader required')
            for row in history.values():_require(_record(row['path'])==row,'retained partition dependency changed')
            selected_plan=_read(history['plan'])
            _require(selected_plan['prepared']==prepared and selected_plan['version']==root.name,'retained partition belongs to another prepared producer')
            extra.update({r['path']:r['sha256'] for r in history.values()})
    else:
        binding=dict(path=str(root.parent/SNAPSHOT_NAME),sha256=APPROVED_SNAPSHOT_SHA256)
        snapshot=_read(binding)['snapshot']
    _require(snapshot['root']==str(root),'untrusted normalization snapshot root')
    rows=snapshot['files'];seen=set();files={}
    actual=set()
    for path in root.rglob('*'):
        _require(not path.is_symlink(),'normalization snapshot contains a symlink')
        relative=path.relative_to(root)
        if any(part in {'__pycache__','.pytest_cache','.git'} for part in relative.parts):continue
        if path.is_file():actual.add(str(relative))
    for item in rows:
        name=item['relative'];relative=Path(name)
        _require(not relative.is_absolute() and '..' not in relative.parts and name not in seen,'invalid snapshot path')
        seen.add(name);entry=dict(path=str(root/relative),sha256=item['sha256'])
        _require(_record(entry['path'])==entry,'active normalization snapshot changed')
        files[entry['path']]=entry['sha256']
    _require(actual==seen,'normalization snapshot file inventory changed')
    _require(all(files.get(path)==sha for path,sha in manifest['code'].items()),'prepared code not completely in approved snapshot')
    files.update(extra)
    return root,binding,files


def _child(request):
    # Imports happen only after selecting the approved implementation root in a
    # fresh interpreter; no modules are replaced in the current gate process.
    value=_read(request);prepared=value['prepared']
    root,snapshot,_=_approved(prepared)
    _require(value['snapshot']==snapshot,'child snapshot selection changed')
    sys.path.insert(0,str(root))
    from scripts.batch import harbor_normalization_partitioned as P
    from scripts.batch import harbor_normalization_producer as N
    _require(Path(P.__file__).resolve()==root/'scripts/batch/harbor_normalization_partitioned.py','wrong active normalizer imported')
    selected_profile=os.environ.get('HARBOR_FLEET_NORMALIZATION_PROFILE')
    if selected_profile:
        profile=_read(json.loads(selected_profile));history=profile.get('retained_partition')
        if history:
            # Reuse the original completed-partition reader selected when the
            # real Calls were produced. Its loader only reconstructs history;
            # no dispatcher, eligibility probe or transport function is run.
            import importlib.util
            _require(_record(history['loader']['path'])==history['loader'],'partition loader changed')
            spec=importlib.util.spec_from_file_location('_normalization_retained_partition',history['loader']['path'])
            loader=importlib.util.module_from_spec(spec);sys.modules[spec.name]=loader;spec.loader.exec_module(loader)
            loaded=loader.load(history['plan'])
            _require(isinstance(loaded,tuple) and len(loaded)==6 and loaded[0]['prepared']==prepared,'retained partition loader contract differs')
            facade=loaded[4]
            _require(facade.__name__==P.__name__ and facade.undispatched_reader_witness is not None,'unselected completed partition facade')
            P=facade
    selected=P.selection(prepared)
    _require(asdict(selected.policy)==value['policy'],'normalization model policy changed')
    _,rows=N.analyse(selected)
    result=P.validate(value['binding'],prepared=prepared,source=value['source'],baseline=value['baseline'],
        draft=value['draft'],rows=rows,policy=selected.policy,bindings=value['bindings'])
    print(json.dumps(result,sort_keys=True,allow_nan=False))


def _invoke(root,payload):
    from scripts.batch.hardened_runner import _run_in_process_group
    with tempfile.TemporaryDirectory(prefix='normalization-validation-') as temp:
        directory=Path(temp).resolve();request=directory/'request.json';response=directory/'response.json'
        request.write_text(json.dumps(payload,sort_keys=True,allow_nan=False));request.chmod(0o600)
        request_binding=_record(request);metrics={}
        with response.open('xb') as output:
            rc=_run_in_process_group([sys.executable,'-I','-B',str(Path(__file__).resolve()),'_child',
                request_binding['path'],request_binding['sha256']],stdout=output,timeout_seconds=900,
                grace_seconds=3,memory_limit_bytes=8*1024**3,metrics=metrics,cwd=root,
                env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'})
        if rc!=0:
            failed=Path(tempfile.mkdtemp(prefix='normalization-validation-failed-')).resolve()
            for name,raw in [('request.json',request.read_bytes()),('response.log',response.read_bytes())]:
                target=failed/name
                with target.open('xb') as handle:handle.write(raw)
                target.chmod(0o600)
            failure=dict(returncode=rc,request=_record(failed/'request.json'),response=_record(failed/'response.log'),
                         metrics=metrics,models_dispatched=0,automatic_retry=False)
            saved=failed/'failure.json';saved.write_text(json.dumps(failure,sort_keys=True));saved.chmod(0o600)
            raise ValueError('active normalization validator rejected selected evidence; retained '+str(saved)+
                             '\n'+response.read_text(errors='replace')[-4000:])
        return json.loads(response.read_text())


def validate(binding,*,prepared,source,baseline,draft,rows,policy,bindings):
    from scripts.batch import harbor_normalization_partitioned as P
    from scripts.batch import harbor_normalization_producer as N
    from scripts.batch import harbor_normalization_compact as K
    manifest=_read(prepared)
    if str(Path(P.__file__).resolve()) in manifest['code']:
        return P.validate(binding,prepared=prepared,source=source,baseline=baseline,draft=draft,
                          rows=rows,policy=policy,bindings=bindings)
    root,snapshot,files=_approved(prepared)
    # The current parent's independently reconstructed candidate rows must still
    # equal the approved producer's complete retained analysis, including drops.
    analysis=N.expand_artifact(_read(manifest['legacy']['analysis.json']))
    _require(K.equal(K.json_value(rows),analysis),'current normalization rows differ from approved analysis')
    payload=dict(binding=binding,prepared=prepared,source=source,baseline=baseline,draft=draft,
                 policy=asdict(policy),bindings=bindings,snapshot=snapshot)
    result=_invoke(root,payload)
    _require(set(result)=={'review','semantic_attempt','checked_files'},'invalid owning normalization result')
    # Recheck immutable input/code bytes after the subprocess, before returning
    # evidence to the current completed gate.
    _,after,after_files=_approved(prepared)
    _require(after==snapshot and after_files==files,'normalization snapshot changed during validation')
    for path,sha in result['checked_files'].items():
        _require(_record(path)['sha256']==sha,'normalization evidence changed during validation')
    result['checked_files'].update(files)
    result['checked_files'][snapshot['path']]=snapshot['sha256']
    own=_record(__file__);result['checked_files'][own['path']]=own['sha256']
    return result


def validate_content(selection):
    from scripts.batch import harbor_normalization_producer as N
    _require(selection.normalization_call is None,'partition review cannot nominate a single model call')
    b=selection.bindings;run=selection.normalization_run
    manifest=_read(selection.normalization_prepared);selected=_read(manifest['selection'])
    _require(str(run)==selected['run'],'partition normalization run differs')
    _,rows=N.gen.analyse(selection.workbook,b['source_file']['path'],b['source_file']['sha256'],
                        runs_root=run.parent,inputs_root=Path(b['baseline_inputs']['path']).parent)
    return validate(b['normalization_partitioned_review'],prepared=selection.normalization_prepared,
        source=b['source_file'],baseline=b['baseline_inputs'],draft=b['normalization_draft'],rows=rows,
        policy=selection.model_policy,bindings=b)


if __name__=='__main__':
    _require(len(sys.argv)==4 and sys.argv[1]=='_child','read-only validator invocation required')
    _child(dict(path=sys.argv[2],sha256=sys.argv[3]))
