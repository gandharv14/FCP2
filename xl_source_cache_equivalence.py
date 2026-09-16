"""Explicit source cache verification policies; never Excel recalculation.

Only an exact manual-calculation flag can change after every formula in the
authorized scope recomputes from primitive inputs and matches its saved value. Original formula/cache
parts remain byte-identical to the separately verified formula projection.
The original policy is acyclic; v2 additionally admits exact certified cycles.
V4 makes a separate selected-task claim: every formula in its pinned dependency
cones must match, and downstream generations must retain that exact scope.
"""
from pathlib import Path
import hashlib,json,math,os,re,tempfile,time,zipfile

POLICY='source-cache-equivalence/v1'
CERTIFIED_POLICY='source-cache-equivalence-certified-cycles/v2'
LARGE_ACYCLIC_POLICY='source-cache-equivalence-large-acyclic/v3'
SELECTED_SCOPE_POLICY='source-cache-equivalence-selected-scope/v4'
POLICIES={POLICY,CERTIFIED_POLICY,LARGE_ACYCLIC_POLICY,SELECTED_SCOPE_POLICY}
SCHEMA='source-cache-equivalence-manifest/v1'
AUTHORIZATION_SHA256='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'


def require(ok,message):
    if not ok:raise ValueError(message)


def digest(path):
    p=Path(path);require(p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'regular cache evidence required')
    return hashlib.sha256(p.read_bytes()).hexdigest()


def binding(path):return dict(path=str(Path(path).absolute()),sha256=digest(path))


def read(row):
    require(set(row)=={'path','sha256'} and digest(row['path'])==row['sha256'],'cache evidence bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def object_hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def keep(path,value):
    p=Path(path)
    with p.open('x')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    return binding(p)


def policy(row,source,*,at=None):
    p=read(row)
    fields={'schema_version','workbook','source_sha256','authorization','created_at_ns','expires_at_ns','max_formula_cells','relative_tolerance'}
    certified=p.get('schema_version')==CERTIFIED_POLICY
    scoped=p.get('schema_version')==SELECTED_SCOPE_POLICY
    require(set(p)==fields|({'max_cycle_cells'} if certified else set())|({'scope'} if scoped else set()) and p['schema_version']in POLICIES,'closed cache-equivalence policy required')
    if certified:require(type(p['max_cycle_cells'])is int and 0<p['max_cycle_cells']<=128,'bounded certified cycle size required')
    require(p['workbook']==Path(source).stem and p['source_sha256']==digest(source),'cache policy source differs')
    require(binding(p['authorization']['path'])==p['authorization'] and p['authorization']['sha256']==AUTHORIZATION_SHA256,'cache authorization changed or is not selected production authority')
    require(type(p['created_at_ns'])is int and type(p['expires_at_ns'])is int and p['created_at_ns']<p['expires_at_ns']<=p['created_at_ns']+24*3600*10**9,'bounded cache policy duration required')
    require(p['created_at_ns']<= (time.time_ns()if at is None else at) <p['expires_at_ns'],'cache policy expired or not yet active')
    require(type(p['max_formula_cells'])is int and 0<p['max_formula_cells']<=(30000 if p['schema_version']in {LARGE_ACYCLIC_POLICY,SELECTED_SCOPE_POLICY} else 10000) and type(p['relative_tolerance'])is float and p['relative_tolerance']==1e-6,'cache proof scope changed')
    if scoped:
        from xl_source_scoped_cache import selection
        selection(p['scope'],source)
    return p


def policy_proof(source,p):
    if p['schema_version']==SELECTED_SCOPE_POLICY:
        from xl_source_scoped_cache import prove as selected_proof
        return selected_proof(source,read(p['scope']),p['max_formula_cells'],p['relative_tolerance'])
    return prove(source,p['max_formula_cells'],p['relative_tolerance'],max_cycle_cells=p.get('max_cycle_cells'))


def implementation():
    from scripts.corpus import code_hash
    return code_hash()


def certified_cycles(result,maximum):
    """Accept exact finite endpoints only with a unique contractive certificate."""
    groups=result.iterated
    require(type(maximum)is int and 0<maximum<=128,'bounded certified cycle size required')
    require(len(groups)==result.coverage.get('active_cycles')==len(result.cycle_groups),'complete cycle evidence required')
    matched=set()
    for group in groups:
        c=group.get('certification',{})
        identity=result.cycle_membership.get(group.get('seed'))
        members=result.cycle_groups.get(identity,())
        require(identity is not None and identity not in matched and members
            and len(set(members))==len(members)
            and all(result.cycle_membership.get(cell)==identity for cell in members)
            and group.get('members')==list(members[:20]),'complete cycle membership differs from its diagnostic sample')
        matched.add(identity)
        require(group.get('certified')is True and group.get('converged')is True
            and group.get('budget_exhausted')is False and group.get('targets_stable')is True
            and group.get('topology_stable')is True and group.get('residual')==0
            and group.get('max_change')==0 and group.get('errors')==0 and group.get('unresolved')==0,
            'native finite cycle endpoint is not exact and stable')
        dimension=1 if c.get('kind')=='scalar_affine' else c.get('dimension')
        require(c.get('kind')in{'affine_system','scalar_affine'} and c.get('certified')is True
            and c.get('contractive')is True and c.get('endpoint_error_bound')==0
            and type(dimension)is int and 0<dimension<=maximum
            and dimension==group.get('member_count')==len(members),
            'complete bounded unique affine cycle certificate required')
        bound=c.get('contraction_bound')
        require(type(bound)in(int,float) and math.isfinite(bound) and 0<=bound<1,
            'strict finite contraction bound required')
    return dict(count=len(groups),certificate_sha256=object_hash(groups),membership_sha256=object_hash(sorted(sorted(x)for x in result.cycle_groups.values())),maximum_cycle_cells=maximum,
        native_finite_endpoints_preserved=True)


def prove(source,maximum,tolerance,*,max_cycle_cells=None):
    """Recompute the complete formula set before opening expected cached values."""
    from xl_source_health import inspect_workbook
    from xl_ast_graph import AstGraph,write_csv
    from xl_seg import model,project,evaluate
    from xl_segment import stabilize_runtime_proof
    health=inspect_workbook(source)
    require(health['route']=='recalc_candidate' and set(health['reason_codes'])=={'manual_calculation_mode'},'only manual mode without other source barriers is supported')
    require(0<health['formula_count']<=maximum,'full formula scope exceeds policy')
    before=digest(source)
    with tempfile.TemporaryDirectory(prefix='source-cache-proof-')as tmp:
        ast=Path(tmp)
        with AstGraph(source,max_range_expand=100,read_values=True,consts=True,verbose=False)as tree:
            tree.build();write_csv(ast,tree.nodes,tree.edges)
        graph=model.load(ast,Path(source).stem)
        try:
            cg=project.build(graph);formulas={c for c,i in cg.info.items()if i.node.kind=='formula'}
            require(len(formulas)==health['formula_count'],'formula coverage differs from source XML')
            result,inputs,_,proof=stabilize_runtime_proof(graph,cg,set(),formulas,calculation=evaluate.workbook_calculation_metadata(source))
            require(result.strict_proof and not(inputs&formulas) and not(result.seeded_cells&formulas),'calculated formula seed forbidden')
            require(not result.oracle_reads and not result.oracle_accesses and result.coverage.get('expected_cache_reads')==0,'saved formula cache was used to compute a result')
            require(formulas<=result.evaluated_cells and proof['closure']['stabilized'] and proof['closure']['targets_stable'],'full fresh proof did not stabilize')
            if max_cycle_cells is None:
                require(not result.iterated and not result.cycle_groups and result.coverage.get('active_cycles')==0,'cyclic cache equivalence requires a separate certified policy')
                cycles=None
            else:cycles=certified_cycles(result,max_cycle_cells)
            require(result.coverage.get('errors')==result.coverage.get('unresolved')==0 and not result.coverage.get('unknown_ops'),'formula computation incomplete')
            cache=evaluate.workbook_expected_cache(source);require(cache is not None,'source expected cache unavailable');rows=[]
            try:
                for cell in sorted(formulas):
                    sheet,row,col=model.split_ref(cell);expected=cache(sheet,row,col);actual=result.values.get(cell,evaluate.Unresolved('missing_formula_result'))
                    verdict,_=evaluate.compare(expected,actual,tolerance=tolerance)
                    require(not isinstance(actual,(evaluate.ExcelError,evaluate.Unresolved)) and verdict=='match','cached formula mismatch: '+cell)
                    require(isinstance(expected,bool)==isinstance(actual,bool),'cached formula Boolean type mismatch: '+cell)
                    if isinstance(expected,str) or isinstance(actual,str):require(expected==actual,'cached formula string mismatch: '+cell)
                    if isinstance(actual,(int,float)) and not isinstance(actual,bool):require(math.isfinite(actual),'nonfinite formula result: '+cell)
                    rows.append(dict(cell=cell,expected=repr(expected),actual=repr(actual)))
            finally:cache.close()
            require(digest(source)==before,'source changed during cache proof')
            return dict(formulas=len(formulas),formula_set_sha256=object_hash(sorted(formulas)),comparisons_sha256=object_hash(rows),primitive_inputs_sha256=object_hash(sorted(inputs)),formula_seeds=0,oracle_reads=0,active_cycles=result.coverage['active_cycles'],closure_stabilized=True,all_formula_cells_match=True,tolerance=tolerance,**({'certified_cycles':cycles}if cycles is not None else{}))
        finally:graph.close()


def package(path):
    with zipfile.ZipFile(path)as z:
        infos=z.infolist();require(len(infos)==len({x.filename for x in infos}),'duplicate package member')
        return infos,{x.filename:z.read(x)for x in infos}


def automatic_members(members):
    require('xl/workbook.xml'in members,'workbook metadata absent')
    raw=members['xl/workbook.xml'];pattern=rb'(<(?:[A-Za-z_][\w.-]*:)?calcPr\b[^>]*\bcalcMode\s*=\s*)([\"\'])manual\2'
    matches=list(re.finditer(pattern,raw));require(len(matches)==1,'exact manual calculation attribute required')
    result=dict(members);result['xl/workbook.xml']=re.sub(pattern,lambda m:m[1]+m[2]+b'auto'+m[2],raw,count=1)
    return result


def create(source,directory,policy_binding):
    source=Path(source).absolute();p=policy(policy_binding,source);directory=Path(directory).absolute();require(not directory.exists(),'cache normalization namespace consumed');directory.mkdir(parents=True)
    from xl_volatile_formula_remediation import build_plan,apply_plan,verify_manifest
    from xl_source_health import inspect_workbook
    initial=inspect_workbook(source);identity=initial['route']=='recalc_candidate' and set(initial['reason_codes'])=={'manual_calculation_mode'}
    plan=None if identity else build_plan(source)
    require(identity or (plan['status']=='eligible' and not plan['unresolved']),'original formula projection unresolved')
    projection_plan=None if identity else keep(directory/'projection-plan.json',plan)
    projection=directory/'projection';projection.mkdir();projected=projection/source.name
    if not identity:
        receipt=apply_plan(source,plan,projected);verify_manifest(source,projected,plan,receipt)
        projection_manifest=keep(directory/'projection-manifest.json',receipt)
    else:
        projected.write_bytes(source.read_bytes());projection_manifest=None
    proof=policy_proof(projected,p);proof_binding=keep(directory/'formula-proof.json',proof)
    infos,before=package(projected);after=automatic_members(before);candidate=directory/source.name
    with zipfile.ZipFile(candidate,'x')as z:
        for info in infos:z.writestr(info,after[info.filename])
    from xl_source_health import inspect_workbook
    require(inspect_workbook(candidate)['route']=='pass','candidate still has a source barrier')
    manifest=dict(schema_version=SCHEMA,policy_version=p['schema_version'],policy=policy_binding,authorized_at_ns=time.time_ns(),implementation_sha256=implementation(),source=binding(source),projected=binding(projected),projection_plan=projection_plan,projection_manifest=projection_manifest,proof=proof_binding,candidate=binding(candidate),changed_member='xl/workbook.xml',calculation_change=dict(before='manual',after='auto'),recalculation_performed=False,models_dispatched=0)
    policy(policy_binding,source,at=manifest['authorized_at_ns']);keep(directory/'manifest.json',manifest);verify(source,candidate,manifest)
    return candidate,manifest


def verify(source,candidate,manifest):
    require(set(manifest)=={'schema_version','policy_version','policy','authorized_at_ns','implementation_sha256','source','projected','projection_plan','projection_manifest','proof','candidate','changed_member','calculation_change','recalculation_performed','models_dispatched'} and manifest['schema_version']==SCHEMA and manifest['policy_version']in POLICIES,'closed cache lineage required')
    require(manifest['source']==binding(source) and manifest['candidate']['sha256']==digest(candidate) and digest(manifest['candidate']['path'])==digest(candidate),'cache lineage source/candidate changed')
    require(manifest['implementation_sha256']==implementation(),'cache verifier implementation changed')
    require(manifest['recalculation_performed']is False and manifest['models_dispatched']==0 and manifest['changed_member']=='xl/workbook.xml' and manifest['calculation_change']==dict(before='manual',after='auto'),'cache evidence claims changed')
    p=policy(manifest['policy'],source,at=manifest['authorized_at_ns']);require(p['schema_version']==manifest['policy_version'],'cache policy version differs');plan=read(manifest['projection_plan'])if manifest['projection_plan']is not None else None;projected=Path(manifest['projected']['path']);require(binding(projected)==manifest['projected'],'projected source changed')
    from xl_volatile_formula_remediation import verify_manifest,validate_plan
    if plan is not None:
        validate_plan(plan,source)
        require(manifest['projection_manifest']is not None,'projection receipt missing');verify_manifest(source,projected,plan,read(manifest['projection_manifest']))
    else:require(manifest['projection_manifest']is None and digest(projected)==digest(source),'identity projection changed')
    require(policy_proof(projected,p)==read(manifest['proof']),'fresh cache proof differs')
    before=package(projected)[1];after=package(candidate)[1];require(after==automatic_members(before),'normalization changed a formula, cache, or other package member')
    from xl_source_health import inspect_workbook
    require(inspect_workbook(candidate)['route']=='pass','cache candidate no longer passes source health')
    return dict(original_sha256=digest(source),candidate_sha256=digest(candidate),formula_proof=manifest['proof'],recalculation_performed=False)


def preview(source,policy_binding):
    """Private full proof and metadata candidate, without a task attempt."""
    with tempfile.TemporaryDirectory(prefix='cache-equivalence-preview-')as tmp:
        candidate,manifest=create(source,Path(tmp)/'candidate',policy_binding)
        result=verify(source,candidate,manifest)
        return dict(status='CACHE_EQUIVALENCE_PREFLIGHT_PASS',original_sha256=result['original_sha256'],candidate_sha256=result['candidate_sha256'],source_qualified=False,models_dispatched=0,task_attempts=0)
