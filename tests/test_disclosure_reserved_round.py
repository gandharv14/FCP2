from pathlib import Path
import importlib.util,sys
import pytest
from scripts.fleet import disclosure_reserved_round as S
from scripts.batch import harbor_disclosure_review_round as Q
from scripts.batch.harbor_admission import read,record
from test_disclosure_adoption_resume import keep


def original(tmp_path):
    source=Path(Q.__file__).read_text().replace('from scripts.batch import harbor_instruction_worker as W\n        worker, selection, _ = completed_build','from scripts.batch.harbor_disclosure_command_repair import producer_run\n        worker, selection, _ = completed_build').replace("W.task(worker,selection,record(worker.context.tracker_root/(inputs['workbook']+'.json'))).run",'producer_run(worker)')
    path=tmp_path/'original.py';path.write_text(source)
    spec=importlib.util.spec_from_file_location('original_round_test',path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m


def test_exact_two_change_reader_retains_original_implementation_and_restores(tmp_path):
    m=original(tmp_path);old=m.validate;p=keep(tmp_path/'reader.json',dict(original=record(m.__file__),selected=record(Q.__file__)))
    record_before=record(m.__file__)
    with S.reader(m,p):
        assert m.validate is not old and record(m.__file__)==record_before
        # Closed authority validation still runs before the changed producer call.
        with pytest.raises(ValueError):m.validate(keep(tmp_path/'bad.json',{}),{})
    assert m.validate is old


def test_archived_prefix_is_followed_after_reader_globals_were_copied(tmp_path):
    m=original(tmp_path);p=keep(tmp_path/'reader.json',dict(original=record(m.__file__),selected=record(Q.__file__)))
    old=m.prefix_records
    with S.reader(m,p):
        cells=dict(zip(m.validate.__code__.co_freevars,(c.cell_contents for c in m.validate.__closure__)))
        scope=cells['actual'].__globals__
        with S.prefix_callback(m,record(S.__file__)):
            def archived(value):
                assert value=='exact retained prefix'
                return ['verified original archived bytes']
            m.prefix_records=archived
            assert scope['prefix_records']('exact retained prefix')==['verified original archived bytes']
            with pytest.raises(AssertionError):scope['prefix_records']('foreign prefix')
            m.prefix_records=old
            from types import SimpleNamespace
            old_life=m.life;old_read=m.read;old_record=m.record
            m.life=SimpleNamespace(checked_record=lambda row:('archive',row))
            m.read=lambda row:('historical',row)
            m.record=lambda row:('original binding',row)
            assert scope['life'].checked_record('exact')==('archive','exact')
            assert scope['read']('exact')==('historical','exact')
            assert scope['record']('exact')==('original binding','exact')
            m.life=old_life;m.read=old_read;m.record=old_record
        assert scope['prefix_records']is old


def test_prefix_callback_rejects_foreign_reader(tmp_path):
    m=original(tmp_path)
    with pytest.raises(ValueError):
        with S.prefix_callback(m,record(S.__file__)):pytest.fail('unbound callback')


@pytest.mark.parametrize('fault',['implementation','extra_delta','mutated_selected'])
def test_unlisted_or_changed_reader_never_validates(tmp_path,fault):
    m=original(tmp_path);old=m.validate;selected=Path(Q.__file__)
    if fault=='extra_delta':
        path=tmp_path/'broadened.py';path.write_text(selected.read_text().replace('1 <= p[\'round\'] <= p[\'limit\']','0 <= p[\'round\'] <= p[\'limit\']'));selected=path
    if fault=='mutated_selected':
        path=tmp_path/'selected.py';path.write_bytes(selected.read_bytes());selected=path
    value=dict(original=record(m.__file__),selected=record(selected))
    if fault=='implementation':value['original']['sha256']='f'*64
    p=keep(tmp_path/'reader.json',value)
    if fault=='mutated_selected':
        with S.reader(m,p):
            selected.write_text('changed')
            with pytest.raises(ValueError):m.validate({}, {})
    else:
        with pytest.raises(ValueError):
            with S.reader(m,p):pytest.fail('unlisted reader installed')
    assert m.validate is old
