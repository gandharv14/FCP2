"""Exact historical code closure; source validation is an explicit test double.

These fixtures prove code custody/profile checks only, not financial approval.
"""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

import xl_audit_historical_context as H
from scripts.batch.harbor_admission import record
from xl_seg.diagnostics import fingerprint_values


@pytest.fixture
def closure(tmp_path,monkeypatch):
    current=[p.name for p in H.Q.seg_pub.verifier_paths()]
    assert {'xl_graph_store.py','disk_model.py'} <= set(current)
    source={'ast_provenance':{'builder':{'code':[]}}}
    monkeypatch.setattr(H.Q.source_pub,'validate_source_generation',lambda *a,**k:source)
    def make(names):
        bindings=[]
        for name in names:
            p=tmp_path/name;p.write_text('# retained historical '+name+'\n')
            bindings.append(record(p))
        logical=[{'path':Path(b['path']).name,'sha256':b['sha256'],
                  'size_bytes':Path(b['path']).stat().st_size} for b in bindings]
        selected=SimpleNamespace(historical_verifier_code=tuple(bindings),
            old_bindings={'source_generation':{'path':str(tmp_path/'old'),'id':'old'}})
        manifest={'evidence':{'verifier_code':fingerprint_values(logical)}}
        source['ast_provenance']['builder']['code']=[{'name':r['path'],'sha256':r['sha256'],
            'size_bytes':r['size_bytes']} for r in logical if r['path'] in ('xl_ast_graph.py','xl_graph_store.py')]
        return selected,manifest,source
    return current,make


@pytest.mark.parametrize('profile',['current','pre-storage/v1'])
def test_complete_current_and_pre_storage_profiles_match_original_fingerprint(closure,profile):
    names,make=closure
    if profile!='current':names=[n for n in names if n not in {'xl_graph_store.py','disk_model.py'}]
    selection,manifest,_=make(names)
    files=H._code_closure(selection,manifest)
    assert files=={b['path']:b['sha256'] for b in selection.historical_verifier_code}
    assert manifest['evidence']['verifier_code']['count']==len(names)


@pytest.mark.parametrize('change',['only_ast_store','only_seg_store','other_missing',
    'two_other_missing','replacement','order','extra','duplicate'])
def test_other_rehashed_profiles_cannot_omit_or_reorder_modules(closure,change):
    names,make=closure;names=list(names)
    if change=='only_ast_store':names.remove('xl_graph_store.py')
    elif change=='only_seg_store':names.remove('disk_model.py')
    elif change=='other_missing':names.remove('evaluate.py')
    elif change=='two_other_missing':names.remove('evaluate.py');names.remove('project.py')
    elif change=='replacement':names[names.index('evaluate.py')]='unapproved.py'
    elif change=='order':names[0],names[1]=names[1],names[0]
    elif change=='extra':names.append('unapproved.py')
    else:names[names.index('evaluate.py')]='project.py'
    selection,manifest,_=make(names)
    with pytest.raises(ValueError,match='verifier'):H._code_closure(selection,manifest)


@pytest.mark.parametrize('field',['count','sha256','available','encoding','size_bytes'])
def test_pre_storage_profile_requires_entire_original_fingerprint(closure,field):
    names,make=closure;selection,manifest,_=make([n for n in names if n not in {'xl_graph_store.py','disk_model.py'}])
    fingerprint=manifest['evidence']['verifier_code']
    fingerprint[field]={'count':0,'sha256':'0'*64,'available':False,'encoding':'different','size_bytes':0}[field]
    with pytest.raises(ValueError,match='fingerprint differs'):H._code_closure(selection,manifest)


@pytest.mark.parametrize('profile',['current','pre-storage/v1'])
@pytest.mark.parametrize('field',['name','sha256','size_bytes','duplicate','empty'])
def test_every_historical_builder_binding_is_required(closure,profile,field):
    names,make=closure
    if profile!='current':names=[n for n in names if n not in {'xl_graph_store.py','disk_model.py'}]
    selection,manifest,source=make(names);builder=source['ast_provenance']['builder']['code']
    if field=='duplicate':builder.append(copy.deepcopy(builder[0]))
    elif field=='empty':builder.clear()
    else:builder[0][field]={'name':'unapproved.py','sha256':'0'*64,'size_bytes':0}[field]
    with pytest.raises(ValueError,match='historical builder code'):H._code_closure(selection,manifest)


@pytest.mark.parametrize('what',['missing','changed','rehashed'])
def test_retained_verifier_files_cannot_change(closure,what):
    names,make=closure;selection,manifest,_=make(names)
    path=Path(selection.historical_verifier_code[3]['path'])
    if what=='missing':path.unlink()
    else:
        path.write_bytes(path.read_bytes()+b'# changed\n')
        if what=='rehashed':
            rows=list(selection.historical_verifier_code);rows[3]=record(path)
            selection.historical_verifier_code=tuple(rows)
    with pytest.raises((ValueError,OSError)):H._code_closure(selection,manifest)


def test_old_profile_cannot_omit_storage_code_claimed_by_old_builder(closure):
    names,make=closure;selection,manifest,source=make([n for n in names if n not in {'xl_graph_store.py','disk_model.py'}])
    source['ast_provenance']['builder']['code'].append({'name':'xl_graph_store.py','sha256':'0'*64,'size_bytes':1})
    with pytest.raises(ValueError,match='historical builder code differs'):H._code_closure(selection,manifest)
