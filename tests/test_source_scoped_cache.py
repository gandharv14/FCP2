from pathlib import Path
import json, re, tomllib, zipfile
import openpyxl
import pytest
from openpyxl.workbook.properties import CalcProperties
import xl_source_cache_equivalence as C
import xl_source_scoped_cache as S


def write(path, value):
    path.write_text(json.dumps(value)); return C.binding(path)


def fixture(tmp_path, *, intermediate='4', cycle=False):
    path=tmp_path/'0001.xlsx'; w=openpyxl.Workbook(); x=w.active; x.title='Summary'
    x['A2']='EBITDA'; x['B2']='=Inputs!A1*2'; x['A3']='NPV'; x['B3']='=B2+1'
    x['E2']='=E2' if cycle else '=1/0'; w.create_sheet('Inputs')['A1']=2
    w.calculation=CalcProperties(calcMode='manual',fullCalcOnLoad=False,forceFullCalc=False,iterate=False);w.save(path)
    infos,parts=C.package(path);raw=parts['xl/worksheets/sheet1.xml'].replace(b'<v />',b'<v></v>')
    for f,v in [('Inputs!A1*2',intermediate),('B2+1','5'),('E2' if cycle else '1/0','0')]:
        raw=raw.replace(b'<f>'+f.encode()+b'</f><v></v>',b'<f>'+f.encode()+b'</f><v>'+v.encode()+b'</v>')
    parts['xl/worksheets/sheet1.xml']=raw
    with zipfile.ZipFile(path,'w')as z:
        for info in infos:z.writestr(info,parts[info.filename])
    from xl_ast_graph import AstGraph,write_csv
    from xl_seg import model,project,bands,condense,frontier,emit
    ast=tmp_path/'ast';ast.mkdir()
    with AstGraph(path,max_range_expand=100,read_values=True,consts=True,verbose=False)as t:t.build();write_csv(ast,t.nodes,t.edges)
    g=model.load(ast,'0001')
    try:
        cg=project.build(g);bg=bands.build(cg);bands.attach_literal_sources(bg,cg,frontier.is_notable_literal);cd=condense.build(bg)
        emit.write_curation(ast,'0001',frontier.score_outputs(bg,cd),6.,40)
        original=ast/'curation.toml';text=original.read_text();blocks=re.split(r'(?m)(?=^\[\[output\]\]\s*$)',text);targets=[]
        for i,block in enumerate(blocks):
            if not block.startswith('[[output]]'):continue
            row=tomllib.loads(block)['output'][0];include='Summary!B3'in bg.bands[row['band']].cells
            if include:targets.extend(bg.bands[row['band']].cells)
            blocks[i]=re.sub(r'(?m)^(include\s*=\s*)(true|false)',lambda m:m[1]+str(include).lower(),block)
        assert targets==['Summary!B3'];curation=tmp_path/'selected.toml';curation.write_text(''.join(blocks))
    finally:g.close()
    return path,dict(projected_sha256=C.digest(path),original_curation=C.binding(original),curation=C.binding(curation),ordered_outputs=targets)


def test_every_selected_formula_matches_while_unrelated_error_stays_unqualified(tmp_path):
    source,s=fixture(tmp_path);before=source.read_bytes();p=S.prove(source,s,30000,1e-6)
    assert p['selected_formulas']==2 and p['whole_workbook_equivalence']is False
    assert p['all_selected_formula_cells_match'] and source.read_bytes()==before
    with pytest.raises(ValueError):C.prove(source,30000,1e-6)


def test_matching_output_does_not_hide_stale_intermediate_cache(tmp_path):
    source,s=fixture(tmp_path,intermediate='99')
    with pytest.raises(ValueError,match='Summary!B2'):S.prove(source,s,30000,1e-6)


def test_unrelated_cycle_is_preserved_but_cannot_be_added_to_scope(tmp_path):
    source,s=fixture(tmp_path,cycle=True);assert S.prove(source,s,30000,1e-6)['active_cycles']==0
    s['ordered_outputs'].append('Summary!E2')
    with pytest.raises(ValueError,match='output cells'):S.prove(source,s,30000,1e-6)


@pytest.mark.parametrize('fault',['source','target','curation','bound'])
def test_scope_and_original_projection_are_exact(tmp_path,fault):
    source,s=fixture(tmp_path)
    if fault=='source':s['projected_sha256']='0'*64
    elif fault=='target':s['ordered_outputs']=['Summary!B2']
    elif fault=='curation':s['curation']['sha256']='0'*64
    with pytest.raises(ValueError):S.prove(source,s,1 if fault=='bound' else 30000,1e-6)


@pytest.mark.parametrize('fault',['target','order','curation'])
def test_final_generation_cannot_expand_reorder_or_change_curation(tmp_path,fault):
    c=tmp_path/'curation.toml';c.write_text('bound selection')
    sr=write(tmp_path/'scope.json',dict(curation=C.binding(c),ordered_outputs=['A!A1','A!B1']))
    policy=write(tmp_path/'policy.json',dict(scope=sr));manifest=dict(policy=policy)
    verification=dict(ordered_output_cells=['A!A1','A!B1'])
    S.check_generation(manifest,c,verification)
    if fault=='target':verification['ordered_output_cells'].append('A!C1')
    elif fault=='order':verification['ordered_output_cells'].reverse()
    else:c.write_text('changed selection')
    with pytest.raises(ValueError,match='authorized selected scope'):S.check_generation(manifest,c,verification)


def selected_record(tmp_path):
    source=tmp_path/'source.xlsx';source.write_bytes(b'original retained source')
    original=tmp_path/'original.toml';original.write_text('[[output]]\nband="A!A1"\ninclude=true\nlabel="Keep"\n[[output]]\nband="A!B1"\ninclude=true\nlabel="Excluded"\n')
    curation=tmp_path/'scoped.toml';curation.write_text(original.read_text().replace('band="A!B1"\ninclude=true','band="A!B1"\ninclude=false'))
    failure=write(tmp_path/'failure.json',dict(models=0,claims=0,result=dict(state='STOPPED',stage='source_cache_equivalence',bindings=dict(source_sha256=C.digest(source)))))
    diagnostic=write(tmp_path/'diagnostic.json',dict(verification=dict(passed=True,status='pass',blocking_reasons=[],ordered_output_cells=['A!A1'],outputs=dict(match=1,mismatch=0,unresolved=0,unverifiable=0),fingerprints=dict(curation=dict(sha256=C.digest(curation))))))
    s=dict(schema_version=S.SCHEMA,source=C.binding(source),projected_sha256='a'*64,original_curation=C.binding(original),curation=C.binding(curation),ordered_outputs=['A!A1'],excluded_bands=['A!B1'],prior_failure=failure,diagnostic=diagnostic)
    return source,s


@pytest.mark.parametrize('fault',[None,'label','excluded','outputs','source','failed-diagnostic','extra-field','prior-model'])
def test_explicit_selection_preserves_original_failure_and_whole_band_delta(tmp_path,fault):
    source,s=selected_record(tmp_path)
    if fault=='label':
        p=Path(s['curation']['path']);p.write_text(p.read_text().replace('Keep','Reworded'));s['curation']=C.binding(p)
    elif fault=='excluded':s['excluded_bands']=[]
    elif fault=='outputs':s['ordered_outputs']=['A!B1']
    elif fault=='source':source.write_bytes(b'changed')
    elif fault=='failed-diagnostic':
        d=C.read(s['diagnostic']);d['verification']['passed']=False;s['diagnostic']=write(tmp_path/'bad-diagnostic.json',d)
    elif fault=='extra-field':s['allow_all']=True
    elif fault=='prior-model':
        f=C.read(s['prior_failure']);f['models']=1;s['prior_failure']=write(tmp_path/'bad-failure.json',f)
    row=write(tmp_path/'selected.json',s)
    if fault is None:assert S.selection(row,source)==s
    else:
        with pytest.raises(ValueError):S.selection(row,source)


def test_scoped_policy_is_explicit_and_original_limits_are_unchanged(tmp_path):
    import time
    source,s=selected_record(tmp_path);scope=write(tmp_path/'scope.json',s)
    auth=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
    now=time.time_ns();p=dict(schema_version=C.SELECTED_SCOPE_POLICY,workbook=source.stem,source_sha256=C.digest(source),authorization=C.binding(auth),created_at_ns=now-10**9,expires_at_ns=now+10**12,max_formula_cells=30000,relative_tolerance=1e-6,scope=scope)
    assert C.policy(write(tmp_path/'policy.json',p),source)==p
    old=dict(p,schema_version=C.POLICY);old.pop('scope')
    with pytest.raises(ValueError,match='scope'):C.policy(write(tmp_path/'old-policy.json',old),source)
    p.pop('scope')
    with pytest.raises(ValueError,match='closed'):C.policy(write(tmp_path/'missing-scope.json',p),source)

from test_corpus_source_checkpoint import completed


@pytest.mark.parametrize('fault',['targets','curation'])
def test_actual_verified_fastpath_rejects_scope_drift(completed,tmp_path,monkeypatch,fault):
    from unittest.mock import Mock
    from scripts import corpus_build as B
    from test_corpus_native_source_reuse import _verified_build
    args,source,_,_=completed;ledger_path=_verified_build(completed,tmp_path)
    ledger=json.loads(ledger_path.read_bytes());curation=Path(args.curation_root)/'0001/curation.toml'
    scope=write(tmp_path/'scope-fastpath.json',dict(curation=C.binding(curation),ordered_outputs=['Sheet!B1']))
    policy=write(tmp_path/'policy-fastpath.json',dict(schema_version=C.SELECTED_SCOPE_POLICY,scope=scope))
    args.cache_equivalence_policy=policy['path'];args.cache_equivalence_policy_sha256=policy['sha256']
    # This test isolates the already-verified reuse branch; actual numerical and
    # policy validation are exercised above and by the native affected proof.
    monkeypatch.setattr(C,'policy',lambda *a,**k:C.read(policy));monkeypatch.setattr(C,'verify',lambda *a,**k:None)
    manifest=write(tmp_path/'manifest-fastpath.json',dict(policy_version=C.SELECTED_SCOPE_POLICY,policy=policy))
    ledger['build']['normalization']=dict(policy_version=C.SELECTED_SCOPE_POLICY,original_sha256=source['sha256'],candidate=source['path'],candidate_sha256=source['sha256'],manifest=manifest['path'],manifest_sha256=manifest['sha256'],curation=str(curation))
    ledger['build']['bindings']['cache_equivalence_policy_sha256']=policy['sha256'];ledger_path.write_text(json.dumps(ledger))
    if fault=='targets':
        v=C.read(scope);v['ordered_outputs']=['Sheet!B1','Sheet!C1'];scope=write(tmp_path/'scope-expanded.json',v)
        policy=write(tmp_path/'policy-expanded.json',dict(schema_version=C.SELECTED_SCOPE_POLICY,scope=scope))
        m=C.read(manifest);m['policy']=policy;manifest=write(tmp_path/'manifest-expanded.json',m)
        args.cache_equivalence_policy=policy['path'];args.cache_equivalence_policy_sha256=policy['sha256']
        ledger['build']['normalization'].update(manifest=manifest['path'],manifest_sha256=manifest['sha256']);ledger['build']['bindings']['cache_equivalence_policy_sha256']=policy['sha256'];ledger_path.write_text(json.dumps(ledger))
        fresh=Mock(return_value=1);monkeypatch.setattr(B,'_run_in_process_group',fresh)
        result=B.execute_one(args,source,'fixture-current')
        assert not result.get('reused') and fresh.called
        after=json.loads(ledger_path.read_bytes());assert 'authorized selected scope'in after['verified_reuse_rejections'][-1]['reason']
    else:
        curation.write_text(curation.read_text()+'# selection changed\n')
        with pytest.raises(ValueError,match='authorized scoped curation'):B.execute_one(args,source,'fixture-current')
