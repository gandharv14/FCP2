"""Freeze cold-safe fresh publication while preserving original owning functions."""
from pathlib import Path
import ast,hashlib,json
BASE_SHA='d8b9cc78312918e38040442f781dad755db7605292c6d04f0529f580e15c2149'
def build(out, original):
    out=Path(out).absolute();raw=Path(original).read_bytes();assert hashlib.sha256(raw).hexdigest()==BASE_SHA
    out.mkdir(parents=True,exist_ok=False)
    def keep(name,data):
        p=out/name;p.write_bytes(data if isinstance(data,bytes)else data.encode());return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    root=Path(__file__).parent
    rows={name:keep(name+'.py',(root/file).read_bytes())for name,file in [('fresh','fresh_publication.py'),('pinned','pinned_dispatcher.py'),('stdlib','stdlib_imports.py'),('child','undispatched_publication_child.py')]}
    code=raw.decode()
    anchor='    if \'continuation_history\' in plan:\n        return continuation_binding(plan)';assert code.count(anchor)==1
    code=code.replace(anchor,"    if plan.get('source_revision', {}).get('completed_reader') is not None and 'history' not in plan:\n        return fresh_publication.derivative_history_binding(sys.modules[__name__], plan)\n    if plan.get('disclosure_continuation') is not None:\n        return fresh_publication.disclosure_history_binding(sys.modules[__name__], plan)\n    if plan.get('completed_checkpoint') is not None:\n        return fresh_publication.checkpoint_history_binding(sys.modules[__name__], plan)\n    if plan.get('model_tail') is not None:\n        return fresh_publication.model_history_binding(sys.modules[__name__], plan)\n    if plan.get('preserved_gate') is not None:\n        return fresh_publication.preserved_history_binding(sys.modules[__name__], plan)\n    if plan.get('legacy_research_history') is not None:\n        return fresh_publication.legacy_history_binding(sys.modules[__name__], plan)\n    if plan.get('semantic_continuation') is not None and 'history' not in plan:\n        return fresh_publication.semantic_history_binding(sys.modules[__name__], plan)\n"+anchor)
    anchor="prefix = 'HARBOR_FLEET_CONTINUATION_HISTORY_' if 'continuation_history' in plan else 'HARBOR_FLEET_HISTORY_'";assert code.count(anchor)==2
    code=code.replace(anchor,'prefix = fresh_publication.history_prefix(plan)')
    anchor="    if 'continuation_history' in plan:\n        from scripts.fleet.continuation_history import projected_survey\n    else:\n        from scripts.fleet.history import projected_survey";assert code.count(anchor)==1
    code=code.replace(anchor,'    projected_survey = fresh_publication.selected_history_survey(plan)')
    anchor="    certificate = checked(history)\n    projected_survey(tuple(map(Path, certificate['roots'])), set(certificate['known_ids']))";assert code.count(anchor)==1
    code=code.replace(anchor,'    fresh_publication.history_survey(sys.modules[__name__], plan, history, projected_survey)')
    anchor='driver = load("fleet_publication_dispatcher", job["driver"])';assert code.count(anchor)==1
    code=code.replace(anchor,'driver = fresh_publication.dispatcher(sys.modules[__name__], pinned_dispatcher, plan, ready, job)')
    anchor='    publication_only()\n    ready = checked(plan["ready"])';assert code.count(anchor)==1
    code=code.replace(anchor,'    if not any(plan.get(k) is not None for k in ("semantic_continuation","mechanical_tail","preserved_gate","model_tail","completed_checkpoint","disclosure_continuation","source_revision","completed_pre_dispatch")): publication_only()\n    ready = checked(plan["ready"])')
    anchor='    driver = fresh_publication.dispatcher(sys.modules[__name__], pinned_dispatcher, plan, ready, job)';assert code.count(anchor)==1
    code=code.replace(anchor,anchor+'\n    if any(plan.get(k) is not None for k in ("semantic_continuation","mechanical_tail","preserved_gate","model_tail","completed_checkpoint","disclosure_continuation","source_revision","completed_pre_dispatch")): publication_only()')
    code=code.replace('ready = checked(plan["ready"])','ready = fresh_publication.ready_value(sys.modules[__name__], plan)')
    anchor='env[6] == root and job["config"]';assert code.count(anchor)==1
    code=code.replace(anchor,'env[6] == fresh_publication.owning_root(sys.modules[__name__], plan) and job["config"]')
    anchor='job["config"] == ready["config"]';assert code.count(anchor)==1
    code=code.replace(anchor,'job["config"] == fresh_publication.ready_config(sys.modules[__name__], plan, ready, job)')
    anchor='    env = driver.setup(job)';assert code.count(anchor)==1
    code=code.replace(anchor,anchor+'\n    fresh_publication.install_validation(sys.modules[__name__], plan, env)')
    anchor='publication = env[2].run(request, Path(plan["journal"]) / "owning-publication")';assert code.count(anchor)==1
    code=code.replace(anchor,'publication = fresh_publication.publish(sys.modules[__name__], plan_binding, plan, env, request, publication_child_adapter)')
    anchor='driver.verify(job, env, verification_dir)';assert code.count(anchor)==1
    code=code.replace(anchor,'fresh_publication.verify(sys.modules[__name__], driver, job, env, verification_dir, plan)')
    anchor='if __name__ == "__main__": main()';assert code.count(anchor)==1
    tail="stdlib=load('fresh_publication_stdlib',"+repr(rows['stdlib'])+");stdlib.preload_inspect()\n"
    tail+="fresh_publication=load('fresh_publication',"+repr(rows['fresh'])+")\npinned_dispatcher=load('fresh_publication_pinned',"+repr(rows['pinned'])+")\n"+"publication_child_adapter=load('fresh_publication_child',"+repr(rows['child'])+")\nFRESH_DEPENDENCIES="+repr(rows)+"\nif __name__ == '__main__':\n    if len(sys.argv)>1 and sys.argv[1]=='publication-child':fresh_publication.publication_child(sys.modules[__name__],publication_child_adapter)\n    else:main()\n"
    code=code.replace(anchor,tail);publisher=keep('publish.py',code)
    def funcs(text):return {n.name:ast.dump(n,include_attributes=False)for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
    before=funcs(raw.decode());after=funcs(code);assert set(before)==set(after)and [n for n in before if before[n]!=after[n]]==['history_binding','publication_environment','remote_child']
    return keep('manifest.json',json.dumps(dict(schema_version='harbor-fresh-publication-implementation/v1',publisher=publisher,dependencies=rows,original=dict(path=str(Path(original).absolute()),sha256=BASE_SHA),owning_code_changed=False,collection_unchanged=True,model_dispatch_allowed=False),indent=2,sort_keys=True)+'\n')


def repair_history_import(out,parent):
    """Freeze only the reviewed missing optional-history import correction."""
    from scripts.fleet.publication_launch import history_import_repair
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    # The helper transformation is independent of its new binding. Bind the
    # completed new bytes, then derive the exact publisher load replacement.
    _,fresh=history_import_repair(publisher,raw,old,old)
    f=out/'fresh.py';f.write_text(fresh);selected=b(f)
    changed,_=history_import_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def repair_ready_config(out,parent):
    """Freeze the exact missing completed-READY config reader correction."""
    from scripts.fleet.publication_launch import ready_config_repair
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    _,fresh=ready_config_repair(publisher,raw,old,old)
    f=out/'fresh.py';f.write_text(fresh);selected=b(f)
    changed,_=ready_config_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def repair_mechanical_evidence(out,parent):
    """Freeze the exact completed mechanical archive dependency correction."""
    from scripts.fleet.publication_launch import mechanical_evidence_repair
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    _,fresh=mechanical_evidence_repair(publisher,raw,old,old)
    f=out/'fresh.py';f.write_text(fresh);selected=b(f)
    changed,_=mechanical_evidence_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def repair_semantic_evidence(out,parent):
    """Freeze the exact completed physical/semantic dependency correction."""
    from scripts.fleet.publication_launch import semantic_evidence_repair
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    _,fresh=semantic_evidence_repair(publisher,raw,old,old)
    f=out/'fresh.py';f.write_text(fresh);selected=b(f)
    changed,_=semantic_evidence_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def mechanical_guard_repair(publisher,fresh,old_binding,new_binding):
    """The only permitted change is restoring the bound original process guard."""
    import inspect
    from scripts.fleet.fresh_publication import mechanical_publication_guard
    definition=inspect.getsource(mechanical_publication_guard)
    anchor='def mechanical_dispatcher(pub,pinned,plan,ready,job):'
    assert fresh.count(anchor)==1 and 'def mechanical_publication_guard('not in fresh
    changed=fresh.replace(anchor,definition+'\n\n'+anchor)
    anchor="        pub.require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY',\n            'original mechanical enqueue did not qualify')"
    assert changed.count(anchor)==1
    changed=changed.replace(anchor,anchor+"\n        mechanical_publication_guard(pub,old,selected,execution,U,D,env,a['plan'])")
    assert publisher.count(repr(old_binding))==2
    return publisher.replace(repr(old_binding),repr(new_binding)),changed


def repair_mechanical_guard(out,parent):
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    _,fresh=mechanical_guard_repair(publisher,raw,old,old)
    f=out/'fresh.py';f.write_text(fresh);selected=b(f)
    changed,_=mechanical_guard_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def owning_resume_repair(publisher,fresh,old_binding,new_binding):
    """Add the generic exact-intent resume API without changing owning code."""
    import inspect
    from scripts.fleet import fresh_publication as F
    assert 'def resume_selection(' not in fresh and 'def publication_arguments(' not in fresh
    anchor='def publish(pub,plan_binding,plan,env,request,adapter):'
    assert fresh.count(anchor)==1
    changed=fresh.replace(anchor,inspect.getsource(F.resume_selection)+'\n\n'+inspect.getsource(F.publication_arguments)+'\n\n'+inspect.getsource(F.enforce_publication_wait)+'\n\n'+anchor)
    old="    return runner(request, __import__('pathlib').Path(plan['journal'])/'owning-publication')"
    assert changed.count(old)==1
    changed=changed.replace(old,"    journal,arguments=publication_arguments(pub,producer,request,plan)\n    return runner(request,journal,**arguments)")
    anchor="    rows=[*pub.FRESH_DEPENDENCIES.values(),plan['fresh_authoring'],plan['author_helper']]"
    assert changed.count(anchor)==1
    changed=changed.replace(anchor,anchor+"\n    if plan.get('publication_resume') is not None:\n        selected=resume_selection(pub,plan)\n        rows += [plan['publication_resume'],*[selected[k] for k in ('prior_plan','intent','failure_proof','reconciliation','policy')]]")
    anchor="    pub.require(pub.checked(intent)['request']==P.encode(request),'owning queued publication request differs')"
    assert changed.count(anchor)==1
    changed=changed.replace(anchor,anchor+"\n    if plan.get('publication_resume') is not None:\n        _,selected=publication_arguments(pub,P,request,plan)\n        pub.require(selected['resume']==intent,'child does not use the exact retained owning intent')")
    assert publisher.count(repr(old_binding))==2
    wait='publication_wait.enforce_after_slot(wait_policy, checked=checked, require=require)'
    assert publisher.count(wait)==1
    publisher=publisher.replace(wait,'fresh_publication.enforce_publication_wait(sys.modules[__name__],plan,wait_policy)')
    return publisher.replace(repr(old_binding),repr(new_binding)),changed


def add_owning_resume(out,parent):
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    _,fresh=owning_resume_repair(publisher,raw,old,old)
    f=out/'fresh.py';f.write_text(fresh);selected=b(f)
    changed,_=owning_resume_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def preexecute_resume_repair(publisher,fresh,old_binding,new_binding):
    """Repair nested import order and permit a proved stopped exact-intent resume."""
    import inspect
    from scripts.fleet import fresh_publication as F
    tree=ast.parse(fresh);nodes={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    assert 'resume_selection'in nodes and 'publication_child'in nodes
    node=nodes['resume_selection'];lines=fresh.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=[inspect.getsource(F.resume_selection)]
    changed=''.join(lines)
    old="('semantic_continuation','mechanical_tail','preserved_gate','model_tail','completed_checkpoint','disclosure_continuation')):pub.publication_only()"
    assert changed.count(old)==2
    changed=changed.replace(old,"('semantic_continuation','mechanical_tail','preserved_gate','model_tail','completed_checkpoint','disclosure_continuation','source_revision')):pub.publication_only()")
    before={n.name:ast.dump(n,include_attributes=False)for n in tree.body if isinstance(n,ast.FunctionDef)}
    after={n.name:ast.dump(n,include_attributes=False)for n in ast.parse(changed).body if isinstance(n,ast.FunctionDef)}
    assert set(before)==set(after)and {n for n in before if before[n]!=after[n]}=={'resume_selection','publication_child'}
    assert publisher.count(repr(old_binding))==2
    wait='publication_wait.enforce_after_slot(wait_policy, checked=checked, require=require)'
    assert publisher.count(wait)==1
    publisher=publisher.replace(wait,'fresh_publication.enforce_publication_wait(sys.modules[__name__],plan,wait_policy)')
    return publisher.replace(repr(old_binding),repr(new_binding)),changed


def repair_preexecute_resume(out,parent):
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    _,fresh=preexecute_resume_repair(publisher,raw,old,old)
    f=out/'fresh.py';f.write_text(fresh);selected=b(f)
    changed,_=preexecute_resume_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def repair_release_types(out,parent):
    """Freeze only the shared current release-type scope over a proved publisher."""
    from scripts.fleet.migration import binding,checked,retain
    before=checked(parent)
    for row in [before['publisher'],*before['dependencies'].values()]:
        assert binding(row['path'])==row
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text()
    current=(Path(__file__).parent/'fresh_publication.py').read_text()
    def node(text):
        nodes=[n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)and n.name=='checkpoint_publication_scope']
        assert len(nodes)==1
        return nodes[0]
    a=node(raw);b=node(current);lines=raw.splitlines(True)
    lines[a.lineno-1:a.end_lineno]=current.splitlines(True)[b.lineno-1:b.end_lineno]
    fresh=''.join(lines)
    def others(text):
        tree=ast.parse(text);tree.body=[n for n in tree.body if not(isinstance(n,ast.FunctionDef)and n.name=='checkpoint_publication_scope')]
        return ast.dump(tree,include_attributes=False)
    assert others(raw)==others(fresh)
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    f=out/'fresh.py';f.write_text(fresh);selected=binding(f)
    publisher=Path(before['publisher']['path']).read_text();assert publisher.count(repr(old))==2
    pub=out/'publish.py';pub.write_text(publisher.replace(repr(old),repr(selected)))
    result={**before,'publisher':binding(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    retain(out/'derivation.json',dict(parent=parent,original_fresh=old,selected_fresh=selected,changed_functions=['checkpoint_publication_scope'],original_publisher=before['publisher'],publisher=binding(pub),owning_code_changed=False))
    return retain(out/'manifest.json',result)


def qualification_scope_repair(publisher,fresh,old_binding,new_binding):
    """Keep publication/codec/qualification on the same unchanged ready types."""
    import inspect
    from scripts.fleet import fresh_publication as F
    node=next(n for n in ast.parse(fresh).body if isinstance(n,ast.FunctionDef)and n.name=='checkpoint_publication_scope')
    new=inspect.getsource(F.checkpoint_publication_scope)
    start=new.index('        qualification=P.qualification\n')
    end=new.index('    return installed()\n',start)
    original=new[:start]+'''        prior=P.ROOT,P.ready,P.instruction
        P.ROOT,P.ready,P.instruction=new,current,W
        try:yield
        finally:P.ROOT,P.ready,P.instruction=prior
'''+new[end:]
    assert ast.dump(node,include_attributes=False)==ast.dump(ast.parse(original).body[0],include_attributes=False)
    lines=fresh.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[new]
    assert publisher.count(repr(old_binding))==2
    return publisher.replace(repr(old_binding),repr(new_binding)),''.join(lines)


def repair_qualification_scope(out,parent):
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    _,fresh=qualification_scope_repair(publisher,raw,old,old)
    f=out/'fresh.py';f.write_text(fresh);selected=b(f)
    changed,_=qualification_scope_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'fresh':selected}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def build_source_finalization(out,parent):
    """Select the existing finalization primitive; preserve owning and collection code."""
    def b(p):p=Path(p).absolute();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    assert b(parent['path'])==parent
    before=json.loads(Path(parent['path']).read_bytes())
    for row in [before['publisher'],*before['dependencies'].values()]:assert b(row['path'])==row
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    helper=out/'finalization.py';helper.write_bytes((Path(__file__).parent/'publication_finalization.py').read_bytes());bound=b(helper)
    text=Path(before['publisher']['path']).read_text()
    old='if a.action == "remote-child": result = remote_child(selected)'
    assert text.count(old)==1;text=text.replace(old,'if a.action == "remote-child": result = source_finalization.source_remote_child(sys.modules[__name__],selected)')
    old='wait_policy = publication_wait.configuration(plan, checked=checked, binding=binding, require=require)'
    assert text.count(old)==1;text=text.replace(old,'wait_policy = source_finalization.source_wait(sys.modules[__name__],plan) if action == "remote" else publication_wait.configuration(plan, checked=checked, binding=binding, require=require)')
    anchor="if __name__ == '__main__':";assert text.count(anchor)==1
    text=text.replace(anchor,"source_finalization=load('source_publication_finalization',"+repr(bound)+")\n"+anchor)
    def functions(t):return {n.name:ast.dump(n,include_attributes=False)for n in ast.parse(t).body if isinstance(n,ast.FunctionDef)}
    oldf,newf=functions(Path(before['publisher']['path']).read_text()),functions(text)
    assert set(oldf)==set(newf)and {n for n in oldf if oldf[n]!=newf[n]}=={'main','controlled'}
    pub=out/'publish.py';pub.write_text(text)
    result={**before,'publisher':b(pub),'dependencies':{**before['dependencies'],'source_finalization':bound}}
    p=out/'manifest.json';p.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return b(p)


def repair_semantic_root(out,parent):
    """Freeze the exact retained semantic root resolver over an immutable publisher."""
    from scripts.fleet.publication_launch import semantic_root_repair
    from scripts.fleet.migration import binding,checked,retain
    before=checked(parent)
    for row in [before['publisher'],*before['dependencies'].values()]:assert binding(row['path'])==row
    old=before['dependencies']['fresh'];raw=Path(old['path']).read_text();publisher=Path(before['publisher']['path']).read_text()
    _,fresh=semantic_root_repair(publisher,raw,old,old)
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=False)
    f=out/'fresh.py';f.write_text(fresh);selected=binding(f)
    changed,_=semantic_root_repair(publisher,raw,old,selected)
    pub=out/'publish.py';pub.write_text(changed)
    return retain(out/'manifest.json',{**before,'publisher':binding(pub),'dependencies':{**before['dependencies'],'fresh':selected}})
