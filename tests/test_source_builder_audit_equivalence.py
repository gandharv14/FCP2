"""Real immutable source manifests; no corpus rebuild, model or live mutation."""
import copy
import json
import importlib.util
import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook

import xl_audit_equivalence as eq
from xl_source_health import inspect_workbook
from xl_source_recalc import create_identity_documents


@pytest.fixture
def pair(tmp_path):
    source = tmp_path/'fixture.xlsx'
    wb=Workbook();wb.active.title='Sheet';wb.active['A1']=3;wb.save(source)
    ast=tmp_path/'ast';ast.mkdir()
    nodes='id,value,array_formula,op,expr,parse_status\nSheet!A1,3,False,,,not_applicable\n'
    edges='source,target,role,arg_index\n'
    (ast/'nodes.csv').write_text(nodes);(ast/'edges.csv').write_text(edges)
    def publish(name, *, args=None, node_text=None, edge_text=None, change_value=False,
                modules=('xl_ast_graph.py',), storage_suffix=b''):
        code=tmp_path/name/'xl_ast_graph.py';code.parent.mkdir();code.write_text('# '+name+'\n')
        codes=[]
        for module in modules:
            path=code.with_name(module)
            if module=='xl_graph_store.py':
                path.write_bytes(Path(eq.__file__).with_name(module).read_bytes()+storage_suffix)
            elif module!='xl_ast_graph.py':path.write_text('# unapproved module\n')
            codes.append(path)
        if node_text is not None:(ast/'nodes.csv').write_text(node_text)
        if edge_text is not None:(ast/'edges.csv').write_text(edge_text)
        if change_value:
            wb.active['A1']=4;wb.save(source)
        request,result=create_identity_documents(source)
        directory,manifest=eq.source_pub.publish_source_generation(source,ast,tmp_path/'published',
            request=request,result=result,health=inspect_workbook(source),builder_code_paths=codes,
            builder_args=args or {'production':True,'include_cached_values':True,'max_range_expand':100})
        return {'path':str(directory),'id':manifest['generation_id'],
                'manifest_sha256':eq.digest(directory/'generation-manifest.json')}
    old=publish('old')
    return old,publish,nodes,edges


def test_builder_code_only_refresh_accepts_real_source_manifests(pair):
    old,publish,_,_=pair;new=publish('new')
    result=eq.source_builder_equivalence(old,new)
    assert result['semantic_ast_bytes_unchanged'] and result['builder_arguments_unchanged']
    assert old['id']!=new['id']
    assert len(result['unchanged_artifacts'])==6


def test_legacy_builder_accepts_only_installed_storage_addition(pair):
    old,publish,_,_=pair
    new=publish('with-storage',modules=('xl_ast_graph.py','xl_graph_store.py'))
    result=eq.source_builder_equivalence(old,new)
    migration=result['builder_inventory_migration']
    assert migration=={'policy_version':'ast-disk-storage-builder-addition/v1',
        'before':['xl_ast_graph.py'],'after':['xl_ast_graph.py','xl_graph_store.py'],
        'added_module_sha256':eq.digest(Path(eq.__file__).with_name('xl_graph_store.py')),
        'added_module_size_bytes':Path(eq.__file__).with_name('xl_graph_store.py').stat().st_size}
    assert result['semantic_ast_bytes_unchanged'] and len(result['unchanged_artifacts'])==6


@pytest.mark.parametrize('modules',[
    ('xl_ast_graph.py','other.py'),
    ('xl_ast_graph.py','xl_graph_store.py','other.py'),
    ('other.py','xl_graph_store.py'),
    ('xl_graph_store.py',),
])
def test_builder_inventory_addition_policy_rejects_other_modules(pair,modules):
    old,publish,_,_=pair;new=publish('unapproved',modules=modules)
    with pytest.raises(ValueError,match='builder module inventory'):
        eq.source_builder_equivalence(old,new)


def test_storage_module_cannot_be_removed_from_builder_provenance(pair):
    _,publish,_,_=pair
    old=publish('with-storage',modules=('xl_ast_graph.py','xl_graph_store.py'))
    new=publish('removed')
    with pytest.raises(ValueError,match='builder module inventory'):
        eq.source_builder_equivalence(old,new)


def test_added_storage_module_must_match_installed_bytes(pair):
    old,publish,_,_=pair
    new=publish('changed-store',modules=('xl_ast_graph.py','xl_graph_store.py'),storage_suffix=b'\n# changed\n')
    with pytest.raises(ValueError,match='differs from installed'):
        eq.source_builder_equivalence(old,new)


@pytest.mark.parametrize('what',['source','nodes','edges','builder_arguments'])
def test_storage_addition_does_not_relax_financial_comparisons(pair,what):
    old,publish,nodes,edges=pair
    change={'source':{'change_value':True},'nodes':{'node_text':nodes.replace('Sheet!A1,3','Sheet!A1,4')},
        'edges':{'edge_text':edges+'Sheet!A1,Sheet!A2,arg,0\n'},
        'builder_arguments':{'args':{'production':True,'include_cached_values':True,'max_range_expand':101}}}[what]
    new=publish('changed-financial',modules=('xl_ast_graph.py','xl_graph_store.py'),**change)
    with pytest.raises(ValueError,match='bytes changed|outside builder provenance'):
        eq.source_builder_equivalence(old,new)


def test_same_builder_inventory_uses_existing_contract(pair):
    old,publish,_,_=pair;new=publish('same-inventory')
    assert 'builder_inventory_migration' not in eq.source_builder_equivalence(old,new)


@pytest.mark.parametrize('change', ['value','array_formula','op','expr','parse_status','column','row_order'])
def test_any_ast_node_semantics_or_schema_change_rejected(pair,change):
    old,publish,nodes,_=pair
    if change=='column':text=nodes.replace('parse_status','ast_schema_version')
    elif change=='row_order':text=nodes+'Sheet!A2,4,False,,,not_applicable\n'
    elif change=='value':text=nodes.replace('Sheet!A1,3','Sheet!A1,4')
    elif change=='array_formula':text=nodes.replace('False','True')
    elif change=='op':text=nodes.replace('False,,,','False,SUM,,')
    elif change=='expr':text=nodes.replace('False,,,','False,,A2,')
    else:text=nodes.replace('not_applicable','ok')
    new=publish('new',node_text=text)
    with pytest.raises(ValueError,match='bytes changed'):eq.source_builder_equivalence(old,new)


def test_changed_edges_rejected(pair):
    old,publish,_,edges=pair;new=publish('new',edge_text=edges+'Sheet!A1,Sheet!A2,arg,0\n')
    with pytest.raises(ValueError,match='bytes changed'):eq.source_builder_equivalence(old,new)


def test_changed_source_rejected_even_with_same_ast(pair):
    old,publish,_,_=pair;new=publish('new',change_value=True)
    with pytest.raises(ValueError,match='bytes changed'):eq.source_builder_equivalence(old,new)


def test_changed_builder_arguments_rejected_even_with_same_ast(pair):
    old,publish,_,_=pair;new=publish('new',args={'production':True,'include_cached_values':True,'max_range_expand':101})
    with pytest.raises(ValueError,match='outside builder provenance'):eq.source_builder_equivalence(old,new)


@pytest.mark.parametrize('artifact',['nodes.csv','edges.csv','health.json','request.json','result.json','ast-provenance.json'])
def test_changed_published_artifact_cannot_be_rehashed_into_receipt(pair,artifact):
    old,publish,_,_=pair;new=publish('new');directory=Path(new['path'])
    relative='ast/fixture/'+artifact if artifact in ('nodes.csv','edges.csv') else artifact
    p=directory/relative;p.chmod(0o644);p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError):eq.source_builder_equivalence(old,new)


@pytest.mark.parametrize('field',['policy_version','engine','policy_decision'])
def test_changed_source_policy_or_engine_cannot_hide_in_builder_metadata(pair,field):
    old,publish,_,_=pair;new=publish('new');p=Path(new['path'])/'generation-manifest.json';m=eq.read(p)
    m['identity'][field]='changed';p.chmod(0o644);p.write_text(json.dumps(m));new['manifest_sha256']=eq.digest(p)
    with pytest.raises(ValueError):eq.source_builder_equivalence(old,new)


def test_inventory_allows_only_three_explicit_locators():
    old={'metadata':{'workbook':'fixture','artifact':'fixture-inputs.xlsx','sheet_names':['Sheet'],
        'document_properties':{},'segmentation':{'generation_id':'a'*64,'manifest_sha256':'b'*64,
        'source_generation_id':'c'*64,'inventory_approval_sha256':None,'recalc_signals_sha256':None}},'variables':[]}
    fresh=copy.deepcopy(old);fresh['metadata']['segmentation'].update(generation_id='d'*64,manifest_sha256='e'*64,source_generation_id='f'*64)
    changes=eq.inventory_equivalence(old,fresh,source_builder_changed=True)
    assert set(changes)=={'/metadata/segmentation/'+k for k in eq.SOURCE_LOCATORS}
    for key in ('inventory_approval_sha256','recalc_signals_sha256'):
        rejected=copy.deepcopy(fresh);rejected['metadata']['segmentation'][key]='1'*64
        with pytest.raises(ValueError,match='beyond locator'):eq.inventory_equivalence(old,rejected,source_builder_changed=True)


def test_incomplete_source_relabel_is_not_three_locator_equivalence():
    old={'metadata':{'segmentation':{'generation_id':'a'*64,'manifest_sha256':'b'*64,'source_generation_id':'c'*64}},'variables':[]}
    new=copy.deepcopy(old);new['metadata']['segmentation'].update(generation_id='d'*64,manifest_sha256='e'*64)
    with pytest.raises(ValueError,match='three-locator'):eq.inventory_equivalence(old,new,source_builder_changed=True)


@pytest.fixture
def audit_case(tmp_path, monkeypatch):
    # Reuse the genuine saved audit/journal fixture. Only generation context is
    # doubled here; source manifest validation has separate real-manifest tests.
    import test_audit_equivalence as module
    old,new,output,calls=module.case.__wrapped__(tmp_path,monkeypatch)
    old_context=module.eq._context
    old_dir=Path(old['source_generation']['path']);new_dir=old_dir.with_name('d'*64)
    shutil.copytree(old_dir,new_dir)
    new['source_generation']={'path':str(new_dir),'id':new_dir.name,
        'manifest_sha256':eq.digest(new_dir/'generation-manifest.json')}
    def context(bindings, *, historical):
        c,m,i=old_context(bindings,historical=historical)
        if not historical:i['metadata']['segmentation']['source_generation_id']=new_dir.name
        return c,m,i
    monkeypatch.setattr(eq,'_context',context)
    seen=[]
    def source_compare(a,b):
        assert a==old['source_generation'] and b==new['source_generation']
        seen.append((a,b));return {'semantic_ast_bytes_unchanged':True,'builder_arguments_unchanged':True}
    monkeypatch.setattr(eq,'source_builder_equivalence',source_compare)
    return old,new,output,seen


def test_new_source_receipt_requires_both_source_comparison_and_original_completed_audit(audit_case,tmp_path):
    old,new,output,seen=audit_case
    before={p:p.read_bytes() for p in output.rglob('*') if p.is_file()}
    receipt=eq.propose_audit_equivalence(old,new)
    assert len(seen)==1 and len(receipt['differences'])==3
    assert receipt['retained_consumption']['submitted_counts']=={'1':1}
    assert receipt['semantic_attempts_added']==0 and receipt['model_reexecuted'] is False
    p=tmp_path/'receipt.json';p.write_text(json.dumps(receipt))
    eq.validate_audit_equivalence(p,current_bindings=new)
    assert all(p.read_bytes()==data for p,data in before.items())


def test_new_source_cannot_cover_missing_response(audit_case):
    old,new,output,_=audit_case
    next(output.rglob('*.response.json')).unlink()
    with pytest.raises(ValueError):eq.propose_audit_equivalence(old,new)


@pytest.mark.parametrize('field',['model','endpoint','project_id'])
def test_new_source_cannot_refresh_model_or_configuration(audit_case,field):
    old,new,_,_=audit_case
    with pytest.raises(eq.audit.AuditRevalidationRequired):
        eq.propose_audit_equivalence(old,new,**{field:'changed'})


def test_new_source_current_live_proof_is_still_required(audit_case,monkeypatch):
    old,new,_,_=audit_case;original=eq._context
    def reject(bindings, *, historical):
        if not historical:raise ValueError('fresh current proof absent')
        return original(bindings,historical=historical)
    monkeypatch.setattr(eq,'_context',reject)
    with pytest.raises(ValueError,match='fresh current proof'):eq.propose_audit_equivalence(old,new)
