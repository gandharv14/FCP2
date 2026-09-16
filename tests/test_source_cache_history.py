"""Function-local compatibility must retain real fresh-formula/ZIP verification."""
from pathlib import Path
import json,hashlib,importlib.util,copy,zipfile
import pytest
from scripts.batch import harbor_source_cache_history as H
from test_source_cache_equivalence import workbook,selected
ROOT=Path(__file__).resolve().parents[1]

def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,sort_keys=True));return H.binding(p)
def snapshot(root,out):
    files={str(p.relative_to(root)):H.binding(p)['sha256']for p in root.rglob('*')if p.is_file()and p.name!='.pipeline-code.json'}
    save(root/'.pipeline-code.json',files)
    return save(out,dict(root=str(root),files=[dict(relative=str(p.relative_to(root)),sha256=H.binding(p)['sha256'])for p in sorted(root.rglob('*'))if p.is_file()]))

@pytest.fixture
def case(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new'
    for root in [old,new]:
        root.mkdir();(root/'xl_source_cache_equivalence.py').write_bytes((ROOT/'xl_source_cache_equivalence.py').read_bytes())
        (root/'xl_ast_graph.py').write_bytes((ROOT/'xl_ast_graph.py').read_bytes())
        save(root/'scripts/batch/harbor_dialogue_source_warnings.py',{'version':root.name})
    a=snapshot(old,tmp_path/'old.json');b=snapshot(new,tmp_path/'new.json');_,oldfiles,digest=H.snapshot(a);_,newfiles,_=H.snapshot(b)
    source=workbook(tmp_path/'example.xlsx')
    spec=importlib.util.spec_from_file_location('test_selected_cache_verifier',new/'xl_source_cache_equivalence.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
    original=M.implementation;M.implementation=lambda:digest
    candidate,manifest=M.create(source,tmp_path/'created',selected(tmp_path,source));M.implementation=original
    profile=save(tmp_path/'profile.json',dict(schema_version=H.SCHEMA,authorization=H.binding(ROOT/'diagnostics/bulk-production/authorization-20260913.md'),original_snapshot=a,current_snapshot=b,changes={k:dict(before=oldfiles.get(k),after=v)for k,v in newfiles.items()if v!=oldfiles.get(k)},original_sha256=H.binding(source)['sha256']))
    return M,source,candidate,manifest,profile,new,tmp_path

def run(c):
    M,source,candidate,manifest,profile,root,_=c
    return H.verify(M.verify,source,candidate,manifest,profile=profile,root=root)

def test_original_body_runs_and_global_fingerprint_unchanged(case):
    M,source,candidate,manifest,_,_,_=case
    with pytest.raises(ValueError,match='implementation'):M.verify(source,candidate,manifest)
    function=M.implementation
    assert run(case)['recalculation_performed'] is False
    assert M.implementation is function
    with pytest.raises(ValueError,match='implementation'):M.verify(source,candidate,manifest)

@pytest.mark.parametrize('fault',['numerical','authority','changes','source','original_hash','current_root','verifier'])
def test_exact_ancestors_and_unchanged_numerics(case,fault):
    c=list(case);M,source,candidate,manifest,profile,root,tmp=c;p=H.read(profile)
    if fault=='numerical':(root/'xl_ast_graph.py').write_text('changed')
    elif fault=='authority':p['authorization']['sha256']='0'*64
    elif fault=='changes':p['changes']={}
    elif fault=='source':p['original_sha256']='0'*64
    elif fault=='original_hash':c[3]={**manifest,'implementation_sha256':'0'*64}
    elif fault=='current_root':c[5]=tmp/'old'
    elif fault=='verifier':M.verify=lambda *a:True
    c[4]=save(tmp/'changed-profile.json',p)
    with pytest.raises(ValueError):run(c)

def test_rehashed_candidate_formula_edit_still_rejected(case):
    M,_,candidate,manifest,_,_,_=case;infos,parts=M.package(candidate);parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'A1*2',b'A1*3')
    with zipfile.ZipFile(candidate,'w')as z:
        for info in infos:z.writestr(info,parts[info.filename])
    manifest['candidate']=M.binding(candidate)
    with pytest.raises(ValueError,match='package member'):run(case)
