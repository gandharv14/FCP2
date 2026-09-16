import copy,hashlib,importlib.util,io,json,shutil,tarfile
from pathlib import Path
import pytest
from scripts.fleet import publish as P
ROOT=Path(__file__).resolve().parents[1];F=ROOT/'diagnostics/vm-fleet-20260912/control/0029-publication-002/stage_collection.py'
s=importlib.util.spec_from_file_location('stage0029',F);S=importlib.util.module_from_spec(s);s.loader.exec_module(S)
def fixture(root):
    immutable=root/'immutable';immutable.mkdir();text=immutable/'instruction.md';text.write_text('source-backed fixture')
    artifacts={'instruction.md':dict(path='instruction.md',algorithm='sha256',sha256=P.sha(text),size_bytes=text.stat().st_size)}
    identity=dict(workbook_id='0029',schema_version='task-generation-identity/v2',artifacts=artifacts,bindings={})
    generation=hashlib.sha256((json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode()).hexdigest()
    task=P.retain(immutable/'generation-manifest.json',dict(schema_version='task-generation/v2',identity=identity,generation_id=generation,artifacts=artifacts,bindings={}))
    output=root/'remote';shutil.copytree(immutable,output)
    tracker=P.retain(root/'tracker.json',dict(workbook_id='0029',execution_state='PROMOTED',promotion_failure=None,promotion_ids={'task_generation_id':generation}))
    release=P.retain(root/'release.json',dict(task_generation_id=generation));pub=P.retain(root/'pub.json',dict(status='PROMOTED'))
    report=P.retain(root/'report.json',dict(schema_version='independent-published-task-copy-check/v1',workbook='0029',passed=True,byte_identical_to_immutable_generation=True,current_qualification_verified=True,model_calls=0,runtime_operations=0,output=str(output),delivered_files=len(P.files(output)),delivered_bytes=sum(x['size_bytes'] for x in P.files(output).values()),tracker=tracker,release=release,task_manifest=task))
    package=P.pack(output,report,pub,root/'export');return Path(package['archive']['path']),P.checked(package['manifest'])

def test_full_verified_archive_is_staged_without_output(tmp_path):
    a,m=fixture(tmp_path);assert S.verify_archive(a,m)['workbook']=='0029';assert not (tmp_path/'final').exists()
    assert S.COLLECTOR['sha256']=='9305a332cb17407f5b247034388a8e0f56614ac68cb3eac5d674b529b3672295'

def test_corrupt_transfer_rejected(tmp_path):
    a,m=fixture(tmp_path);a.write_bytes(a.read_bytes()[:-8])
    with pytest.raises(ValueError):S.verify_archive(a,m)

def test_detached_verification_hash_rejected(tmp_path):
    a,m=fixture(tmp_path);m['verification']['sha256']='0'*64
    with pytest.raises(ValueError):S.verify_archive(a,m)

def test_rehashed_task_cannot_reuse_generation(tmp_path):
    a,m=fixture(tmp_path);bad=tmp_path/'bad.tar.gz'
    with tarfile.open(a,'r:gz') as old,tarfile.open(bad,'w:gz') as new:
        for member in old.getmembers():
            raw=old.extractfile(member).read()
            if member.name=='task/instruction.md':raw=b'X'*len(raw);m['files'][member.name]['sha256']=hashlib.sha256(raw).hexdigest()
            new.addfile(member,io.BytesIO(raw))
    m['archive_sha256']=P.sha(bad)
    with pytest.raises(ValueError):S.verify_archive(bad,m)

def test_report_delivered_counts_must_match(tmp_path):
    a,m=fixture(tmp_path);m['remote_output']='foreign'
    with pytest.raises(ValueError):S.verify_archive(a,m)
