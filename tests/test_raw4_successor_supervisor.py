import copy,importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'diagnostics/raw-workbooks4-deployment-20260916/successor-supervisor-001'
spec=importlib.util.spec_from_file_location('raw4_successor',BASE/'pool_v5_raw4_successor.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)


def inputs():
    manifest=M.read(M.SUCCESSOR_MANIFEST)
    for row in manifest['entries']:
        selected=row['job'];job=M.read(selected)
        existing=dict(state='ready',fence=1,owner=None,claim_id=None,expires=None,version=row['old_version'],source_sha256=row['source']['sha256'],reason=json.dumps(row['reason']))
        yield row,selected,job,existing


def test_actual_exact_sixteen_accepted():
    rows=list(inputs());assert len(rows)==16 and len({r[0]['workbook'] for r in rows})==16
    for row,selected,job,existing in rows:assert M.authorized_pre_admission_successor(existing,selected,job,row['host'])


@pytest.mark.parametrize('field,value',[('state','active'),('state','hold'),('fence',2),('owner','other'),('claim_id','other'),('expires',123),('version','other'),('source_sha256','0'*64),('reason','{}')])
def test_claim_change_rejected(field,value):
    row,selected,job,existing=next(inputs());existing[field]=value
    assert not M.authorized_pre_admission_successor(existing,selected,job,row['host'])


def test_wrong_host_and_job_rejected():
    row,selected,job,existing=next(inputs())
    assert not M.authorized_pre_admission_successor(existing,selected,job,'wrong')
    wrong=copy.deepcopy(job);wrong['version']='wrong'
    assert not M.authorized_pre_admission_successor(existing,selected,wrong,row['host'])


def test_derivative_only_changes_exception_and_helper():
    receipt=json.loads((BASE/'prepared.json').read_text());old=Path(receipt['base_supervisor']['path']).read_text();new=Path(receipt['supervisor']['path']).read_text()
    start=new.index('\n# This external supervisor');end=new.index('\ndef reserve(',start)
    stripped=new[:start]+new[end:]
    stripped=stripped.replace(" and not authorized_pre_admission_successor(existing,selected,job,host)","")
    assert stripped==old
