"""Cold publication imports for a sealed fresh-authoring result.

The selected original dispatcher, publisher, history survey and verifier remain
responsible for qualification. This reader grants no authoring authority.
"""
def derivative_history_binding(pub,plan):
    """The completed derivative retains its full original author/review ancestry."""
    pub.require(not any(k in plan for k in ('history','continuation_history','source_continuation','semantic_continuation','fresh_authoring')),
        'completed derivative cannot mix fresh authoring authority')
    a=plan['source_revision'];selected=pub.checked(a['plan'])
    pub.require(set(a)=={'reader','plan','outcome','completed_reader'}
        and selected['schema_version']=='fleet-dialogue-source-revision-adoption/v1'
        and selected['helper']==a['reader']and selected['workbook']==plan['workbook']
        and selected['source']['sha256']==plan['source_sha256'],
        'completed derivative history source differs')
    return a['plan']


def ready_value(pub,plan):
    """Supply omitted locators by authenticating the real completed derivative."""
    ready=pub.checked(plan['ready'])
    if plan.get('source_revision',{}).get('completed_reader')is None:return ready
    from pathlib import Path
    a=plan['source_revision'];selected=pub.checked(a['plan']);actual=pub.checked(a['outcome'])
    pub.require(ready['schema_version']==selected['schema_version']=='fleet-dialogue-source-revision-adoption/v1'
        and ready['status']=='PUBLICATION_READY'and ready['plan']==a['plan']
        and ready['operations']==([a['outcome']]if selected.get('continuation',{}).get('completed_outcomes')else[pub.binding(Path(selected['operations'])/('%03d'%i)/'outcome.json')for i in range(3)])
        and ready['operations'][-1]==a['outcome']and ready['models_dispatched']==0
        and ready['publication_performed']is False and ready['automatic_retry']is False
        and ((ready.get('retained_adoption')==selected['continuation']['plan']
            and plan['ready']['path']==str(Path(selected['continuation']['output'])/'result.json'))if 'continuation'in selected else
            ('retained_adoption'not in ready and plan['ready']['path']==str(Path(selected['output'])/'result.json')))
        and actual['status']=='COMPLETED_OPERATION'and actual['result']==ready['checkpoint'],
        'actual stopped derivative READY differs')
    author=pub.checked(selected['authoring_job'])
    return dict(ready,workbook=selected['workbook'],job=selected['authoring_job'],config=author['config'],outcome=a['outcome'],source_revision_outcome=a['outcome'])


def checkpoint_history_binding(pub,plan):
    pub.require(not any(k in plan for k in ('history','continuation_history','source_continuation',
        'semantic_continuation','legacy_research_history','fresh_authoring','preserved_gate','mechanical_tail','model_tail')),
        'completed checkpoint cannot mix authoring authorities')
    a=plan['completed_checkpoint'];pub.require(set(a)=={'reader','plan'},'closed completed checkpoint history required')
    selected=pub.checked(a['plan'])
    pub.require(selected['schema_version']=='fleet-completed-instruction-continuation/v1'
        and selected['helper']==a['reader']and pub.binding(a['reader']['path'])==a['reader']
        and selected['workbook']==plan['workbook']and selected['source']['sha256']==plan['source_sha256'],
        'completed checkpoint source or reader differs')
    return a['plan']


def checkpoint_original_job(pub,plan):
    checkpoint_history_binding(pub,plan);selected=pub.checked(plan['completed_checkpoint']['plan'])
    tail=pub.checked(selected['previous']);source=pub.checked(tail['previous'])
    return selected,pub.checked(source['original_job'])


def checkpoint_publication_scope(P,W,facade):
    """Bind the unchanged publisher to the already authenticated current code."""
    from contextlib import contextmanager
    from pathlib import Path
    import hashlib,sys
    @contextmanager
    def installed():
        old,new=Path(facade['original']),Path(facade['selected'])
        paths=[r/'scripts/harbor_publish.py'for r in(old,new)]
        if (Path(P.__file__)!=paths[0]or P.ROOT!=old or W.ROOT!=new
                or Path(W.__file__)!=new/'scripts/batch/harbor_instruction_worker.py'
                or any(not p.is_file()or any(x.is_symlink()for x in(p,*p.parents))for p in paths)
                or hashlib.sha256(paths[0].read_bytes()).digest()!=hashlib.sha256(paths[1].read_bytes()).digest()):
            raise ValueError('publication implementation differs from authenticated code successor')
        current=sys.modules['scripts.batch.harbor_ready_transitions']
        if (Path(current.__file__)!=new/'scripts/batch/harbor_ready_transitions.py'or current.ROOT!=new
                or Path(P.ready.__file__)not in(old/'scripts/batch/harbor_ready_transitions.py',Path(current.__file__))
                or Path(P.instruction.__file__)not in(old/'scripts/batch/harbor_instruction_worker.py',Path(W.__file__))):
            raise ValueError('publication dependency differs from selected worker')
        qualification=P.qualification
        qualification_paths=[r/'scripts/batch/harbor_promotion_evidence.py'for r in(old,new)]
        if (Path(qualification.__file__)not in qualification_paths
                or any(not path.is_file()or any(x.is_symlink()for x in(path,*path.parents))for path in qualification_paths)
                or qualification_paths[0].read_bytes()!=qualification_paths[1].read_bytes()
                or Path(qualification.transitions.__file__)not in (old/'scripts/batch/harbor_ready_transitions.py',Path(current.__file__))):
            raise ValueError('promotion qualification differs from authenticated code successor')
        prior=P.ROOT,P.ready,P.instruction,qualification.transitions
        P.ROOT,P.ready,P.instruction,qualification.transitions=new,current,W,current
        try:yield
        finally:P.ROOT,P.ready,P.instruction,qualification.transitions=prior
    return installed()


def checkpoint_publication_context(original):
    """Insert current publication dependencies before historical READY reads."""
    import ast,inspect,textwrap
    tree=ast.parse(textwrap.dedent(inspect.getsource(original)));count=0
    anchor=ast.parse("stack.enter_context(L.installed(W,original=facade['original'],selected=facade['selected']))").body[0]
    class Add(ast.NodeTransformer):
        def visit_Expr(self,node):
            nonlocal count
            if ast.dump(node)==ast.dump(anchor):
                count+=1
                return [node,ast.copy_location(ast.parse('stack.enter_context(_publication_scope(env[2],W,facade))').body[0],node)]
            return self.generic_visit(node)
    changed=Add().visit(tree)
    if count!=1:raise ValueError('completed checkpoint publication insertion site changed')
    scope=dict(inspect.unwrap(original).__globals__);scope['_publication_scope']=checkpoint_publication_scope
    exec(compile(ast.fix_missing_locations(changed),inspect.unwrap(original).__code__.co_filename,'exec'),scope)
    return scope[original.__name__]


def checkpoint_dispatcher(pub,pinned,plan,ready,job):
    from pathlib import Path
    a=plan['completed_checkpoint'];selected,original=checkpoint_original_job(pub,plan)
    pub.require(ready['status']=='PUBLICATION_READY'and ready['workbook']==plan['workbook']
        and ready['continuation']==a['plan']and ready['publication_performed']is False
        and ready['automatic_retry']is False
        and ready['job']==pub.binding(Path(selected['output'])/'authoring-job.json')
        and job['driver']==original['driver']and job['config']==original['config'],
        'actual completed checkpoint READY required')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_checkpoint_original_dispatcher')
    M=pub.load('publication_completed_checkpoint',a['reader']);manager=checkpoint_publication_context(M.context)(a['plan'])
    try:
        p,derived,S,U,D,env,w,s,l,K,raw,initial_job,B,T,successful,done=manager.__enter__()
        pub.require(p==selected and initial_job==original and str(env[6])==plan['code_root'],
            'completed checkpoint original context differs')
        ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(selected['output']).name
        pub.require(job==dict(original,outcome=done,operations=str(ops))
            and [x.name for x in sorted(ops.iterdir())if x.is_dir()]==['000']
            and ready['outcome']==pub.binding(ops/'000/outcome.json'),
            'completed checkpoint did not preserve the single remaining enqueue')
        out=pub.checked(ready['outcome']);guard=pub.checked(out['resource']);intent=pub.checked(guard['intent'])
        side=pub.checked(pub.binding(ops/'000/disclosure-bootstrap.json'))
        kind,bound=U.retained_command(side['original_command'],derived,D,env)
        pub.require(kind=='instruction'and bound==guard['intent']and side==dict(schema_version=S.SCHEMA,
            plan=a['plan'],helper=a['reader'],kind=kind,intent=bound,original_command=side['original_command'],
            seconds=U.retained_seconds(kind,bound,env),memory_bytes=8589934592),
            'completed checkpoint original cold child differs')
        B.completed(done,env[1])
        checkpoint,value=D.successful(ready['outcome'],env)
        pub.require(checkpoint==ready['checkpoint']and value['status']=='PUBLICATION_READY',
            'original completed enqueue is not ready')
        request=D.publication_request(env,value)
        env[2]._paths(request,Path(plan['journal'])/'owning-publication');env[2]._checkpoint(request)
        pub.require(request.selection.content.bindings['original_raw_source']==selected['source'],
            'completed checkpoint queued source changed')
        env[0]._run_in_process_group=raw
        def setup(current):pub.require(current==job,'completed checkpoint publication job changed');return env
        D.setup=setup;D._checkpoint_publication_manager=manager
        return D
    except BaseException:
        manager.__exit__(*__import__('sys').exc_info());raise


def model_history_binding(pub,plan):
    pub.require(not any(k in plan for k in ('history','continuation_history','source_continuation','semantic_continuation','legacy_research_history','fresh_authoring','preserved_gate','mechanical_tail')),
        'completed model history cannot select another author authority')
    a=plan['model_tail'];pub.require(set(a)=={'reader','plan'},'closed completed model context required')
    selected=pub.checked(a['plan'])
    pub.require(selected['schema_version']=='fleet-instruction-unacquired-model-continuation/v1'
        and 'completed_tail'in selected and selected['helper']==a['reader']
        and pub.binding(a['reader']['path'])==a['reader']and selected['workbook']==plan['workbook']
        and selected['source']['sha256']==plan['source_sha256'],'completed model source or reader differs')
    return a['plan']


def completed_model_projection(original,witness,checkpoint,runtime,custody):
    """Exact original retained-context hooks, with P checking queued custody."""
    import ast,inspect,textwrap,hashlib
    source=textwrap.dedent(inspect.getsource(original));tree=ast.parse(source)
    if hashlib.sha256(ast.dump(tree.body[0],include_attributes=False).encode()).hexdigest()!=witness:
        raise ValueError('original retained model context changed')
    count=[0,0,0,0];branch=ast.parse('invocation is None',mode='eval').body;active=ast.parse('W.active(w,s,l)').body[0]
    class Change(ast.NodeTransformer):
        def visit_If(self,node):
            if ast.dump(node.test,include_attributes=False)==ast.dump(branch,include_attributes=False):
                count[0]+=1;return ast.copy_location(ast.parse('w,controls,s,l=_current_checkpoint(env,worker)').body[0],node)
            return self.generic_visit(node)
        def visit_Expr(self,node):
            if ast.dump(node,include_attributes=False)==ast.dump(active,include_attributes=False):
                count[3]+=1;return ast.copy_location(ast.parse('_publication_custody(w,s,l)').body[0],node)
            return self.generic_visit(node)
        def visit_With(self,node):
            node=self.generic_visit(node)
            for i,child in enumerate(node.body):
                if isinstance(child,ast.Assign)and isinstance(child.value,ast.Call)and isinstance(child.value.func,ast.Name)and child.value.func.id=='_current_checkpoint':
                    count[1]+=1;node.body.insert(i,ast.parse('stack.enter_context(_current_runtime(W,I,A))').body[0]);break
            for i,child in enumerate(node.body):
                if isinstance(child,ast.Expr)and isinstance(child.value,ast.Yield)and isinstance(child.value.value,ast.Tuple):
                    count[2]+=1;node.body.insert(i,ast.parse('stack.enter_context(_current_runtime(env[3],env[1],A))').body[0]);break
            return node
    changed=Change().visit(tree)
    if count!=[1,1,1,1]:raise ValueError('completed model context hooks differ')
    scope=dict(inspect.unwrap(original).__globals__);scope.update(_current_checkpoint=checkpoint,_current_runtime=runtime,_publication_custody=custody)
    exec(compile(ast.fix_missing_locations(changed),inspect.unwrap(original).__code__.co_filename,'exec'),scope)
    return scope[original.__name__]


def model_dispatcher(pub,pinned,plan,ready,job):
    from pathlib import Path
    from contextlib import contextmanager
    from types import SimpleNamespace
    a=plan['model_tail'];model_history_binding(pub,plan);selected=pub.checked(a['plan'])
    pub.require(ready['status']=='PUBLICATION_READY'and ready['workbook']==plan['workbook']
        and ready['continuation']==a['plan']and ready['publication_performed']is False and ready['automatic_retry']is False
        and ready['config']==job['config']and ready['job']==pub.binding(Path(selected['output'])/'authoring-job.json'),
        'actual completed model READY required')
    M=pub.load('publication_completed_model',a['reader']);old=pub.checked(selected['previous']);source=pub.checked(old['previous']);original=pub.checked(source['original_job'])
    pub.require(job['driver']==original['driver']and job['config']==original['config'],'completed model original dispatcher differs')
    D=pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_model_original_dispatcher');retained={}
    def checkpoint(env,worker):
        actual,value=D.successful(ready['outcome'],env)
        pub.require(actual==ready['checkpoint']and value['status']=='PUBLICATION_READY','original model enqueue did not qualify')
        I=env[1];w,controls,_=I.selected(value['descriptor']);request=D.publication_request(env,value)
        env[2]._paths(request,Path(plan['journal'])/'owning-publication');env[2]._checkpoint(request)
        pub.require(w.run==worker.run and w.root==worker.root and w.context==worker.context
            and request.selection.content.bindings['original_raw_source']==selected['source'],'model queued source or worker differs')
        retained.update(request=request,producer=env[2],worker=w);return w,controls,request.selection,request.lease
    def custody(w,s,l):
        request=retained['request'];pub.require(w==retained['worker']and s==request.selection and l==request.lease,'model queued custody changed')
        retained['producer']._checkpoint(request)
    original_module=M.module
    def module(name,row):
        result=original_module(name,row)
        if row!=selected['context_adapter']:return result
        @contextmanager
        def installed(parent,bound,*,witness,checkpoint:object,runtime):
            pub.require(bound==selected['previous']and witness==selected['context_witness'],'model retained parent differs')
            projected=completed_model_projection(parent.context,witness,publication_checkpoint,runtime,custody)
            with projected(bound,continued=True)as value:yield value
        return SimpleNamespace(function_sha=result.function_sha,installed=installed)
    publication_checkpoint=checkpoint;M.module=module
    @contextmanager
    def retained_model():
        try:
            with M.context(a['plan'],continued=True)as value:yield value
        finally:M.module=original_module
    manager=retained_model()
    try:
        p,parent,U,actual_D,env,w,s,l,raw,initial_job,recovery,args=manager.__enter__()
        pub.require(p==selected and initial_job==original and str(env[6])==plan['code_root'],'model publication source context differs')
        home=Path(selected['output']);first=pub.binding(home/'first-step/outcome.json');ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name
        pub.require(job==dict(original,outcome=first,operations=str(ops)),'model publication remaining job differs')
        directories=[x for x in sorted(ops.iterdir())if x.is_dir()and x.name.isdigit()]
        pub.require([x.name for x in directories]==['%03d'%i for i in range(len(directories))],'model publication tail is not contiguous')
        outcomes=[first,*[pub.binding(x/'outcome.json')for x in directories]];pub.require(outcomes[-1]==ready['outcome'],'model tail does not end at actual READY')
        preceding=M.completed_tail(selected)[0]['checkpoint']
        for outcome in outcomes:
            value=pub.checked(outcome);resource=pub.checked(value['resource']);intent=pub.checked(resource['intent'])
            side=pub.checked(pub.binding(Path(resource['intent']['path']).parent/'model-continuation-bootstrap.json'))
            kind,bound=U.retained_command(side['original_command'],selected,actual_D,env)
            pub.require(bound==resource['intent']and side==dict(schema_version=M.SCHEMA,plan=a['plan'],helper=a['reader'],kind=kind,intent=resource['intent'],original_command=side['original_command'],automatic_retry=False),
                'completed model cold child differs')
            pub.require((intent['arguments']['checkpoint']if kind=='instruction'else intent['checkpoint'])==preceding,'completed model predecessor differs')
            actual_D.successful(outcome,env);preceding=value['result']
        env[0]._run_in_process_group=raw
        def setup(current):pub.require(current==job,'publication changed completed model job');return env
        actual_D.setup=setup;actual_D._model_publication_manager=manager
        return actual_D
    except BaseException:
        manager.__exit__(*__import__('sys').exc_info());M.module=original_module;raise


def preserved_history_binding(pub,plan):
    pub.require(not any(k in plan for k in ('history','continuation_history','source_continuation','semantic_continuation','legacy_research_history','fresh_authoring')),
        'preserved aggregate history cannot become a fresh history survey')
    a=plan['preserved_gate'];pub.require(set(a)=={'reader','plan'},'closed completed preserved-gate context required')
    selected=pub.checked(a['plan'])
    pub.require(selected['schema_version']=='fleet-preserved-owning-gate-continuation/v1'
        and selected['helper']==a['reader']and pub.binding(a['reader']['path'])==a['reader']
        and selected['workbook']==plan['workbook']and selected['source']['sha256']==plan['source_sha256'],
        'preserved publication history differs from original source')
    return a['plan']


def preserved_context(original,checkpoint,custody):
    """Read completed author history while the original P owns current custody.

    This exact two-site projection never enters an authoring command. The
    supplied checkpoint is the actual guarded READY result, and P independently
    validates enqueue, live leases and every publication transition.
    """
    import ast,inspect,textwrap
    tree=ast.parse(textwrap.dedent(inspect.getsource(original)));counts=[0,0]
    branch=ast.parse('invocation is None',mode='eval').body
    active=ast.parse('W.active(w,s,l)').body[0]
    class Project(ast.NodeTransformer):
        def visit_If(self,node):
            if ast.dump(node.test,include_attributes=False)==ast.dump(branch,include_attributes=False):
                counts[0]+=1;return ast.copy_location(ast.parse('w,controls,s,l=_completed_publication_checkpoint(env,worker)').body[0],node)
            return self.generic_visit(node)
        def visit_Expr(self,node):
            if ast.dump(node,include_attributes=False)==ast.dump(active,include_attributes=False):
                counts[1]+=1;return ast.copy_location(ast.parse('_completed_publication_custody(w,s,l)').body[0],node)
            return self.generic_visit(node)
    changed=Project().visit(tree)
    if counts!=[1,1]:raise ValueError('preserved completed context shape changed')
    scope=dict(inspect.unwrap(original).__globals__)
    scope.update(_completed_publication_checkpoint=checkpoint,_completed_publication_custody=custody)
    exec(compile(ast.fix_missing_locations(changed),inspect.unwrap(original).__code__.co_filename,'exec'),scope)
    return scope[original.__name__]


def preserved_dispatcher(pub,pinned,plan,ready,job):
    from pathlib import Path
    a=plan['preserved_gate'];preserved_history_binding(pub,plan);selected=pub.checked(a['plan'])
    pub.require(ready['status']=='PUBLICATION_READY'and ready['workbook']==plan['workbook']
        and ready['continuation']==a['plan']and ready['publication_performed']is False
        and ready['automatic_retry']is False and ready['config']==job['config']
        and ready['job']==pub.binding(Path(selected['output'])/'authoring-job.json'),
        'actual preserved-gate READY required')
    M=pub.load('publication_preserved_context',a['reader'])
    old=pub.checked(selected['previous']);original=pub.checked(old['original_job'])
    pub.require(job['driver']==original['driver']and job['config']==original['config'],
        'preserved publication changed original dispatcher/config')
    D=pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_preserved_original_dispatcher')
    retained={}
    def checkpoint(env,worker):
        actual,value=D.successful(ready['outcome'],env)
        pub.require(actual==ready['checkpoint']and value['status']=='PUBLICATION_READY',
            'preserved terminal result is not original guarded enqueue')
        I=env[1];w,controls,_=I.selected(value['descriptor']);request=D.publication_request(env,value)
        env[2]._paths(request,Path(plan['journal'])/'owning-publication');env[2]._checkpoint(request)
        pub.require(w.run==worker.run and w.root==worker.root and w.context==worker.context
            and request.selection.content.bindings['original_raw_source']==selected['source'],
            'completed publication checkpoint changed source or worker')
        retained.update(request=request,producer=env[2],worker=w)
        return w,controls,request.selection,request.lease
    def custody(w,s,l):
        request=retained['request'];pub.require(w==retained['worker']and s==request.selection and l==request.lease,
            'completed publication context changed queued authority')
        retained['producer']._checkpoint(request)
    context=preserved_context(M.context,checkpoint,custody);manager=context(a['plan'],continued=True)
    p,S,K,profile,U,actual_D,env,w,s,l,A,raw,initial_job=manager.__enter__()
    try:
        pub.require(p==selected and initial_job==original and str(env[6])==plan['code_root'],
            'preserved completed source environment differs')
        first=pub.binding(Path(selected['output'])/'first-step/outcome.json')
        ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(selected['output']).name
        pub.require(job==dict(original,outcome=first,operations=str(ops)),
            'preserved completed remaining job differs')
        directories=[path for path in sorted(ops.iterdir())if path.is_dir()and path.name.isdigit()]
        pub.require([path.name for path in directories]==['%03d'%i for i in range(len(directories))],
            'preserved publication tail is not contiguous')
        outcomes=[first,*[pub.binding(path/'outcome.json')for path in directories]]
        pub.require(outcomes[-1]==ready['outcome'],'preserved completed tail does not end at actual READY')
        preceding=pub.binding(M.repair_home(selected)/'repair/result.json')
        for outcome in outcomes:
            value=pub.checked(outcome);resource=pub.checked(value['resource']);intent=pub.checked(resource['intent'])
            side=pub.checked(pub.binding(Path(resource['intent']['path']).parent/'preserved-gate-bootstrap.json'))
            kind,bound=U.retained_command(side['original_command'],selected,actual_D,env)
            seconds=env[1].guard_seconds('step',intent['arguments'])if kind=='instruction'else intent['seconds']
            pub.require(bound==resource['intent']and side==dict(schema_version=M.SCHEMA,plan=a['plan'],helper=a['reader'],kind=kind,
                intent=resource['intent'],original_command=side['original_command'],seconds=seconds,memory_bytes=8589934592,automatic_retry=False),
                'completed preserved cold child differs')
            pub.require((intent['arguments']['checkpoint']if kind=='instruction'else intent['checkpoint'])==preceding,
                'preserved completed tail lost its original predecessor')
            actual_D.successful(outcome,env)
            preceding=value['result']
        env[0]._run_in_process_group=raw
        def setup(current):
            pub.require(current==job,'publication cannot change completed authoring job');return env
        actual_D.setup=setup;actual_D._preserved_publication_manager=manager
        return actual_D
    except BaseException:
        manager.__exit__(*__import__('sys').exc_info());raise


def legacy_history_binding(pub,plan):
    """Retain the completed research authority selected before fresh authoring."""
    pub.require(not any(k in plan for k in ('history','continuation_history','source_continuation','semantic_continuation')),
        'legacy research cannot replace a fresh or semantic history certificate')
    row=plan['legacy_research_history'];certificate=pub.checked(row)
    selected=pub.checked(plan['fresh_authoring'])
    pub.require(certificate.get('schema_version')in {'harbor-legacy-completed-research/v1','harbor-legacy-package-authoring/v1','harbor-legacy-staged-authoring/v1','harbor-legacy-opaque-authoring/v1'}
        and certificate['workbook']==plan['workbook']==selected['workbook']
        and certificate['original']['sha256']==plan['source_sha256']
        and selected['schema_version']=='harbor-fleet-fresh-plan/v1'
        and selected['code_root']==plan['code_root']
        and selected.get('legacy_research_history')==row
        and selected['home']==certificate['home']+'/authoring'
        and selected['reconstruction']['mode']=='existing'
        and selected['reconstruction']['generated_curation']is None,
        'legacy research publication source or original authoring authority differs')
    if certificate['schema_version']in {'harbor-legacy-package-authoring/v1','harbor-legacy-staged-authoring/v1','harbor-legacy-opaque-authoring/v1'}:
        pub.require(pub.checked(certificate['authoring']['snapshot'])['root']==plan['code_root'],
            'publication author snapshot differs')
    return row


def semantic_history_binding(pub,plan):
    """Keep retained semantic authority separate from a fresh-history survey."""
    pub.require(not any(k in plan for k in ('history','continuation_history','source_continuation')),
        'retained semantic history cannot replace a fresh-history certificate')
    a=plan['semantic_continuation']
    if 'completed_reader'in a:
        pub.require(not any(k in plan for k in ('legacy_research_history','preserved_gate','model_tail')),
            'completed semantic history cannot mix authorities')
        return pub.load('completed_semantic_publication_history',a['completed_reader']).history(pub,plan)
    pub.require(set(a)=={'reader','plan'},'closed retained semantic authority required')
    selected=pub.checked(a['plan'])
    pub.require(selected['helper']==a['reader'] and pub.binding(a['reader']['path'])==a['reader']
        and selected['workbook']==plan['workbook'] and selected['source']['sha256']==plan['source_sha256']
        and selected['fresh_plan']==plan['fresh_authoring']
        and 'retained_context' in selected and 'completed_takeover' in selected,
        'retained semantic publication authority differs')
    return a['plan']


def disclosure_history_binding(pub,plan):
    a=plan['disclosure_continuation']
    M=pub.load('completed_disclosure_history',a['completed_reader'])
    return M.history(pub,plan)


def history_prefix(plan):
    if plan.get('source_revision',{}).get('completed_reader')is not None and 'history'not in plan:return 'HARBOR_FLEET_RETAINED_HISTORY_'
    if plan.get('disclosure_continuation')is not None:return 'HARBOR_FLEET_RETAINED_HISTORY_'
    if plan.get('completed_checkpoint')is not None:return 'HARBOR_FLEET_RETAINED_HISTORY_'
    if plan.get('model_tail')is not None:return 'HARBOR_FLEET_RETAINED_HISTORY_'
    if plan.get('preserved_gate')is not None:return 'HARBOR_FLEET_RETAINED_HISTORY_'
    if plan.get('legacy_research_history')is not None:return 'HARBOR_FLEET_LEGACY_RESEARCH_'
    if plan.get('semantic_continuation')is not None and 'history'not in plan:return 'HARBOR_FLEET_RETAINED_HISTORY_'
    return 'HARBOR_FLEET_CONTINUATION_HISTORY_'if 'continuation_history'in plan else 'HARBOR_FLEET_HISTORY_'


def history_survey(pub,plan,history,projected_survey):
    if plan.get('source_revision',{}).get('completed_reader')is not None and 'history'not in plan:
        pub.require(history==derivative_history_binding(pub,plan),'completed derivative history differs');return
    if plan.get('disclosure_continuation')is not None:
        pub.require(history==disclosure_history_binding(pub,plan),'completed disclosure authority changed');return
    if plan.get('completed_checkpoint')is not None:
        pub.require(history==checkpoint_history_binding(pub,plan),'completed checkpoint authority changed');return
    if plan.get('model_tail')is not None:
        pub.require(history==model_history_binding(pub,plan),'completed model authority changed');return
    if plan.get('preserved_gate')is not None:
        pub.require(history==preserved_history_binding(pub,plan),'preserved history authority changed');return
    if plan.get('semantic_continuation')is not None and 'history'not in plan:
        pub.require(history==semantic_history_binding(pub,plan),'retained semantic history binding changed')
        # semantic_dispatcher performs the full original retained-context proof
        # before any P request. This authority is never a fresh-ID survey.
        return
    from pathlib import Path
    if plan.get('legacy_research_history')is not None:
        pub.require(history==legacy_history_binding(pub,plan),'legacy research history binding changed')
        from scripts.batch import harbor_legacy_research_history as L
        certificate=L.validate(history,workbook=plan['workbook'])
        selected=pub.checked(plan['fresh_authoring'])
        L.source_handoff(history,pub.checked(selected['reconstruction']['handoff']))
        projected_survey(tuple(map(Path,certificate['history_roots'])),set(certificate['known_ids']))
        return
    certificate=pub.checked(history)
    projected_survey(tuple(map(Path,certificate['roots'])),set(certificate['known_ids']))


def selected_history_survey(plan):
    if plan.get('source_revision',{}).get('completed_reader')is not None and 'history'not in plan:return None
    if plan.get('disclosure_continuation')is not None:return None
    if plan.get('completed_checkpoint')is not None:return None
    if plan.get('model_tail')is not None:return None
    if plan.get('preserved_gate')is not None:return None
    if plan.get('legacy_research_history')is not None:
        from scripts.batch.harbor_legacy_research_history import projected_survey
        return projected_survey
    if plan.get('semantic_continuation')is not None and 'history'not in plan:return None
    if 'continuation_history'in plan:
        from scripts.fleet.continuation_history import projected_survey
    else:
        from scripts.fleet.history import projected_survey
    return projected_survey


def dispatcher(pub, pinned, plan, ready, job):
    if plan.get('completed_pre_dispatch')is not None:
        pub.require(not any(plan.get(k)is not None for k in ('disclosure_continuation','completed_checkpoint','model_tail','preserved_gate','mechanical_tail','naturalization_continuation','semantic_continuation','source_revision','completed_normalization')),
            'completed pre-dispatch cannot mix continuation authorities')
        M=pub.load('completed_pre_dispatch_publication',plan['completed_pre_dispatch']['reader'])
        return M.dispatcher(pub,pinned,plan,ready,job)
    if plan.get('disclosure_continuation')is not None:
        M=pub.load('completed_disclosure_publication',plan['disclosure_continuation']['completed_reader'])
        return M.dispatcher(pub,pinned,plan,ready,job)
    if plan.get('completed_checkpoint')is not None:return checkpoint_dispatcher(pub,pinned,plan,ready,job)
    if plan.get('model_tail')is not None:return model_dispatcher(pub,pinned,plan,ready,job)
    if plan.get('preserved_gate')is not None:return preserved_dispatcher(pub,pinned,plan,ready,job)
    if plan.get('mechanical_tail') is not None:
        return mechanical_dispatcher(pub,pinned,plan,ready,job)
    if plan.get('naturalization_continuation') is not None:
        return naturalization_dispatcher(pub,pinned,plan,ready,job)
    if plan.get('semantic_continuation') is not None:
        return semantic_dispatcher(pub,pinned,plan,ready,job)
    if plan.get('source_revision') is not None:
        return source_revision_dispatcher(pub,pinned,plan,ready,job)
    if plan.get('completed_normalization') is not None:
        return normalization_dispatcher(pub,pinned,plan,ready,job)
    selected = pub.checked(plan['fresh_authoring'])
    pub.require(selected['schema_version'] == 'harbor-fleet-fresh-plan/v1'
        and selected['workbook'] == plan['workbook'] == ready['workbook']
        and selected['code_root'] == plan['code_root']
        and ready['plan'] == plan['fresh_authoring']
        and job['driver'] == selected['dispatcher']
        and ready['config'] == job['config'], 'fresh publication authority differs')
    pub.require(pub.binding(selected['preparer']['path']) == selected['preparer'],
                'selected fresh preparer changed')
    pub.require(plan['author_helper']['sha256'] == '414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
                'unknown original fresh author helper')
    return pinned.load(pub, job['driver'], plan['author_helper'],
                       namespace='fleet_publication_dispatcher')



def owning_root(pub,plan):
    """Authenticate the selected semantic root while retaining original config custody."""
    from pathlib import Path
    a=plan.get('semantic_continuation')
    if a is None or a.get('completed_reader')is None:return Path(plan['code_root'])
    M=pub.load('completed_semantic_owning_root',a['completed_reader'])
    M.history(pub,plan)
    selected=pub.checked(a['plan'])
    if selected['schema_version']=='fleet-completed-instruction-continuation/v1':
        chosen=selected['semantic_context'];selected=pub.checked(chosen['plan'])
        pub.require(set(chosen)=={'helper','plan'} and selected['helper']==chosen['helper'],
            'semantic owning root lost retained context')
    context=selected['retained_context'];q=pub.checked(context['plan'])
    pub.require(selected['schema_version']=='fleet-instruction-semantic-continuation/v1'
        and context.get('kind')in {'completed_disclosure','completed_pre_dispatch'} and selected['fresh_plan']==context['plan']==plan['fresh_authoring']
        and q['schema_version']in {'fleet-retained-completed-disclosure/v1','fleet-retained-completed-aggregate/v1','fleet-retained-pre-dispatch-review/v1'}
        and q['snapshot']==context['snapshot'] and q['config']==context['config']
        and q['helper']==context['helper'] and q['workbook']==selected['workbook']==plan['workbook']
        and q['source']==selected['source'] and q['source']['sha256']==plan['source_sha256'],
        'semantic owning root detached from selected source')
    root=Path(pub.checked(q['snapshot'])['root'])
    pub.require(root.is_absolute() and root.name==M.version(plan,pub.checked),'semantic owning root version changed')
    return root


def ready_config(pub,plan,ready,job):
    """Read the original job's config when a completed tail omits duplication."""
    if 'config'in ready:return ready['config']
    if plan.get('disclosure_continuation')is not None:
        a=plan['disclosure_continuation'];M=pub.load('completed_disclosure_ready_config',a['completed_reader'])
        return M.ready_config(pub,plan,ready,job)
    if plan.get('completed_checkpoint')is not None:
        from pathlib import Path
        selected,original=checkpoint_original_job(pub,plan)
        pub.require(ready['continuation']==plan['completed_checkpoint']['plan']and ready['status']=='PUBLICATION_READY'
            and ready['workbook']==plan['workbook']and ready['job']==pub.binding(Path(selected['output'])/'authoring-job.json')
            and pub.checked(ready['job'])==job and job['config']==original['config'],
            'completed checkpoint READY config changed')
        return job['config']
    a=plan['mechanical_tail'];selected=pub.checked(a['plan']);old=pub.checked(selected['original'])
    from pathlib import Path
    pub.require(selected['helper']==a['reader'] and selected['workbook']==ready['workbook']==plan['workbook']
        and selected['source']['sha256']==plan['source_sha256']
        and ready['continuation']==a['plan'] and ready['status']=='PUBLICATION_READY'
        and ready['job']==pub.binding(Path(selected['output'])/'authoring-job.json')
        and pub.checked(ready['job'])==job and pub.checked(old['original_job'])['config']==job['config'],
        'completed mechanical READY config differs from original job')
    return job['config']


def mechanical_publication_guard(pub,old,selected,execution,U,D,env,row):
    """Restore the exact original guard after a completed author context opens."""
    from pathlib import Path
    guard=env[0]._run_in_process_group
    pub.require(pub.binding(U.__file__)==old['utilities']
        and guard.__globals__ is U.__dict__
        and guard.__code__.co_name=='guarded'
        and Path(guard.__code__.co_filename)==Path(U.__file__),
        'publication requires the declared completed author guard')
    closed=dict(zip(guard.__code__.co_freevars,(c.cell_contents for c in guard.__closure__ or ())))
    pub.require(set(closed)=={'D','env','original','p','row'}
        and closed['D']is D and closed['env']is env and closed['p']==execution
        and closed['row']==row and execution=={**old,**selected,'original_job':old['original_job']},
        'completed author guard detached from its exact context')
    original=closed['original'];path=Path(env[6])/'scripts/batch/hardened_runner.py'
    pub.require(original.__name__=='_run_in_process_group'
        and Path(original.__code__.co_filename)==path
        and original.__globals__.get('__file__')==str(path)
        and pub.binding(path)==pub.binding(original.__code__.co_filename),
        'completed author guard does not delegate to the original bounded runner')
    # Only the instruction-specific command router is removed. The original
    # hardened runner, P resource limits, central custody and P adapter remain.
    env[0]._run_in_process_group=original


def mechanical_dispatcher(pub,pinned,plan,ready,job):
    """Retain the accepted review and original mechanical archive through P."""
    from pathlib import Path
    a=plan['mechanical_tail']
    pub.require(set(a)=={'reader','plan'},'closed completed mechanical reader required')
    selected=pub.checked(a['plan']);old=pub.checked(selected['original'])
    fresh=pub.checked(plan['fresh_authoring'])
    pub.require(selected['schema_version']=='fleet-completed-mechanical-dialogue-tail/v1'
        and selected['helper']==a['reader'] and selected['workbook']==plan['workbook']
        and selected['source']['sha256']==plan['source_sha256']
        and old['fresh_plan']==plan['fresh_authoring'] and fresh['code_root']==plan['code_root']
        and fresh['workbook']==plan['workbook'] and fresh['schema_version']=='harbor-fleet-fresh-plan/v1',
        'mechanical publication source or fresh authority differs')
    pub.require(ready['status']=='PUBLICATION_READY' and ready['workbook']==plan['workbook']
        and ready['continuation']==a['plan'] and ready['models_dispatched']==0
        and ready['publication_performed']is False and ready['automatic_retry']is False
        and ready['job']==pub.binding(Path(selected['output'])/'authoring-job.json'),
        'actual completed mechanical READY required')
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
        'unknown original mechanical author helper')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_mechanical_original_dispatcher')
    M=pub.load('publication_completed_mechanical',a['reader']);manager=M.context(a['plan'],child=True)
    p,original,U,D,env,worker,selection,lease,K,V,execution=manager.__enter__()
    try:
        base=pub.checked(old['original_job']);home=Path(selected['output'])
        pub.require(p==selected and str(env[6])==plan['code_root']
            and base['driver']==fresh['dispatcher'] and base['config']==job['config']
            and job==dict(base,outcome=selected['review_outcome'],operations=str(Path(env[5]['run_root'])/'dispatcher-v2'/home.name)),
            'mechanical publication changed original completed-review job')
        checkpoint,value=D.successful(ready['outcome'],env)
        pub.require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY',
            'original mechanical enqueue did not qualify')
        mechanical_publication_guard(pub,old,selected,execution,U,D,env,a['plan'])
    except BaseException:
        manager.__exit__(*__import__('sys').exc_info());raise
    D._mechanical_publication_manager=manager
    return D


def naturalization_dispatcher(pub,pinned,plan,ready,job):
    """Read a completed original NAT revision without replaying its permit.

    The original fresh-history certificate remains required. Only the actual
    remaining-job/result shape differs from an uninterrupted fresh author.
    """
    from pathlib import Path
    a=plan['naturalization_continuation']
    pub.require(set(a)=={'reader','plan'},'closed completed NAT reader required')
    selected=pub.checked(a['plan']);fresh=pub.checked(plan['fresh_authoring'])
    pub.require(selected['schema_version']=='fleet-naturalization-continuation/v1'
        and selected['entry']==a['reader'] and selected['workbook']==plan['workbook']
        and selected['source']['sha256']==plan['source_sha256']
        and fresh['schema_version']=='harbor-fleet-fresh-plan/v1'
        and fresh['workbook']==plan['workbook'] and fresh['code_root']==plan['code_root'],
        'NAT publication source or fresh identity differs')
    pub.require(ready['status']=='PUBLICATION_READY' and ready['workbook']==plan['workbook']
        and ready['continuation']==a['plan'] and ready['publication_performed']is False
        and ready['automatic_retry']is False and ready['config']==job['config'],
        'actual completed NAT READY required')
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
        'unknown original NAT author helper')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='publication_nat_original_dispatcher')
    M=pub.load('publication_completed_nat',a['reader'])
    p,original,D,env,A,L,N=M.load(a['plan'])
    pub.require(p==selected and str(env[6])==plan['code_root'] and original['driver']==fresh['dispatcher']
        and original==pub.checked(pub.binding(Path(fresh['home'])/'authoring-job.json')),
        'NAT publication lost original fresh job')
    authority=a['plan'];home=Path(selected['output'])
    if 'resume'in selected:
        continuation=pub.checked(selected['resume']);authority=continuation['prior_plan']
        prior=pub.checked(authority);start=continuation['import_outcome']
        pub.require(prior['original_job']==selected['original_job'] and prior['source']==selected['source']
            and prior['workbook']==plan['workbook'] and 'resume'not in prior,
            'completed NAT import changed prior authority')
    else:
        prior=selected;start=pub.binding(home/'import/outcome.json')
    oldhome=Path(prior['output']);guard=pub.checked(ready['permit_guard'])
    pub.require(ready['permit_guard']==pub.binding(oldhome/'permit-resource.json')
        and guard['returncode']==0 and guard['failure']is None and guard['models']==0
        and guard['seconds']==3300 and guard['memory_bytes']==8589934592
        and pub.checked(guard['intent'])==dict(plan=authority,entry=prior['entry'],automatic_retry=False),
        'actual original NAT permit guard required')
    permit=pub.checked(pub.binding(oldhome/'permit-complete.json'))
    pub.require(permit['plan']==authority and permit['models']==0
        and permit['semantic_budget_reset']is False and permit['automatic_retry']is False,
        'NAT review allowance history changed')
    for item in (guard['log'],permit['completion']):pub.require(pub.binding(item['path'])==item,'NAT permit evidence changed')
    pub.require(job=={**original,'outcome':start,'operations':str(Path(env[5]['run_root'])/'dispatcher-v2'/home.name)}
        and ready['job']==pub.binding(home/'authoring-job.json'),
        'completed NAT remaining job differs')
    checkpoint,value=D.successful(ready['outcome'],env)
    pub.require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY',
        'original NAT enqueue has not completed')
    return D


def normalization_dispatcher(pub,pinned,plan,ready,job):
    """Read a completed original refresh and final READY, without re-entering it."""
    a=plan['completed_normalization']
    pub.require(set(a)=={'reader','normalization_context'},'closed completed normalization reader required')
    pub.require(ready['status']=='PUBLICATION_READY' and ready['workbook']==plan['workbook']
        and ready['plan']==a['normalization_context']['plan']
        and ready['publication_performed']is False and ready['automatic_retry']is False,
        'actual completed normalization READY required')
    M=pub.load('publication_completed_normalization',a['reader'])
    p=dict(workbook=plan['workbook'],fresh_plan=plan['fresh_authoring'],original_job=ready['job'],
        normalization_context=a['normalization_context'])
    fresh,selected,root,config,handoff=M.authoring_context(p)
    pub.require(selected==job and str(root)==plan['code_root'] and config==ready['config']
        and handoff is not None,'completed normalization source or current job changed')
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
        'unknown original fresh author helper')
    D=pinned.load(pub,job['driver'],plan['author_helper'],namespace='fleet_publication_dispatcher')
    env=D.setup(job);C,I,P,W,R,actual_config,actual_root,controls=env
    pub.require(actual_root==root and job['config']==config and actual_config['workbook']==plan['workbook']
        and C.SECONDS==3300 and C.MEMORY==8589934592,'original normalization publication controls changed')
    refreshed=M.completed_refresh(p,D,env,handoff[0]);_,bridged=D.successful(handoff[1],env)
    checkpoint,value=D.successful(ready['outcome'],env)
    pub.require(refreshed['status']=='IMPORTED' and bridged['status']=='STEP_COMPLETE'
        and bridged['publication_ready']is False and value['status']=='PUBLICATION_READY'
        and checkpoint==ready['checkpoint'],'original completed refresh/bridge/READY differs')
    return D


def semantic_dispatcher(pub,pinned,plan,ready,job):
    """Qualify a completed original semantic revision and its actual enqueue.

    The retained loader validates source history and completed takeover custody;
    this reader grants no renewal, authoring, or review authority.
    """
    from pathlib import Path
    a=plan['semantic_continuation']
    if 'completed_reader'in a:
        return pub.load('completed_semantic_publication_reader',a['completed_reader']).dispatcher(pub,pinned,plan,ready,job)
    pub.require(set(a)-{'tail'}=={'reader','plan'},'closed completed semantic reader required')
    tail=a.get('tail')
    if tail is not None:pub.require(set(tail)=={'helper','plan'},'closed completed semantic tail reader required')
    selected=pub.checked(a['plan'])
    pub.require(selected['helper']==a['reader'] and selected['workbook']==plan['workbook']
        and selected['source']['sha256']==plan['source_sha256']
        and 'completed_takeover' in selected,
        'semantic publication source or completed custody differs')
    pub.require(ready['status']=='PUBLICATION_READY' and ready['workbook']==plan['workbook']
        and ready['continuation']==(tail['plan']if tail is not None else a['plan']) and ready['plan']==selected['fresh_plan']
        and ready['plan']==plan['fresh_authoring'] and ready['publication_performed']is False
        and ready['automatic_retry']is False and ready['config']==job['config'],
        'actual completed semantic READY required')
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
        'unknown original semantic author helper')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='fleet_publication_original_dispatcher')
    M=pub.load('publication_completed_semantic',a['reader'])
    if 'retained_context'not in selected:
        pub.require('history'in plan and not any(k in plan for k in ('continuation_history','source_continuation','legacy_research_history')),
            'ordinary semantic continuation requires original fresh history')
        entry,call=M.lease_context(selected)
        pub.require(entry==a['reader'] and call==dict(function='load',
            args=[{k:selected[k]for k in ('workbook','fresh_plan','original_job')}],kwargs={}),
            'ordinary semantic publication lost its exact fresh context')
    else:
        pub.require('history'not in plan,'retained semantic context cannot acquire fresh history')
    if tail is None:
        p,fresh,original,D,env,worker,selection,lease,recipe,S,L=M.context(a['plan'],continued=True)
        manager=L.manager;tail_selected=None
    else:
        tail_selected=pub.checked(tail['plan'])
        pub.require(tail_selected['helper']==tail['helper']
            and tail_selected['semantic_context']==dict(helper=a['reader'],plan=a['plan']),
            'semantic tail detached from original revision')
        Q=pub.load('publication_completed_semantic_tail',tail['helper'])
        current,fresh,original,D,env,_,_=Q.tail_context(tail['plan'],continued=True)
        manager=D._semantic_tail_manager;p=selected
    try:
        if tail is not None:pub.require(current==tail_selected,'selected semantic tail changed')
        pub.require(p==selected and str(env[6])==plan['code_root']
            and original['config']==job['config'] and job['driver']==original['driver'],
            'semantic publication lost original dispatcher/source context')
        home=Path(selected['output']);first=pub.binding(home/'first-step/outcome.json')
        guardrow=pub.binding(home/'permit-resource.json')
        active_home=Path(tail_selected['output'])if tail_selected is not None else home
        start=tail_selected['outcome']if tail_selected is not None else first
        pub.require(job=={**original,'outcome':start,'operations':str(Path(env[5]['run_root'])/'dispatcher-v2'/active_home.name)},
            'semantic publication changed original remaining job')
        if tail_selected is None:pub.require(ready['permit_guard']==guardrow,'semantic permit guard changed')
        else:
            pub.require(ready['intent']==pub.binding(active_home/'intent.json')
                and pub.checked(ready['intent'])==dict(plan=tail['plan'],prior_completed=start,automatic_retry=False),
                'semantic tail lost original completed review handoff')
        guard=pub.checked(guardrow)
        pub.require(guard['returncode']==0 and guard['failure']is None
            and guard['models_dispatched']==0 and guard['seconds']==3300
            and guard['memory_bytes']==8589934592
            and pub.checked(guard['intent'])==dict(plan=a['plan'],helper=a['reader'],automatic_retry=False),
            'semantic permit did not complete under its original guard')
        _,started=D.successful(first,env)
        pub.require(started['status']=='STEP_COMPLETE' and started['next_step']=='dialogue-paraphraser',
            'original second writer completion missing')
        checkpoint,value=D.successful(ready['outcome'],env)
        pub.require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY',
            'original semantic enqueue did not qualify')
    except BaseException:
        manager.__exit__(*__import__('sys').exc_info());raise
    # The completed-history dependency selection must remain installed through
    # the original P proof and owning child construction in this process.
    D._semantic_publication_manager=manager
    return D


def source_revision_dispatcher(pub,pinned,plan,ready,job):
    """Use the actual adopted derivative and completed original enqueue."""
    from pathlib import Path
    a=plan['source_revision']
    pub.require(set(a)in ({'reader','plan','outcome'},{'reader','plan','outcome','completed_reader'}),'closed completed derivative reader required')
    selected=pub.checked(a['plan']);actual=pub.checked(a['outcome'])
    pub.require(selected['helper']==a['reader'] and selected['workbook']==plan['workbook']
        and selected['source']['sha256']==plan['source_sha256']
        and selected['authoring_job']==ready['job'] and job['config']==ready['config'],
        'completed derivative source/job differs')
    pub.require(actual['status']=='COMPLETED_OPERATION' and actual['result']==ready['checkpoint']
        and a['outcome']==ready['outcome'] and ready['plan']==a['plan']
        and ready['source_revision_outcome']==a['outcome']
        and Path(a['outcome']['path'])==Path(selected['operations'])/('000'if selected.get('continuation',{}).get('completed_outcomes')else'002')/'outcome.json'
        and (len(selected['continuation']['completed_outcomes'])==2 if selected.get('continuation',{}).get('completed_outcomes')else a.get('completed_reader')is not None),
        'actual derivative enqueue completion required')
    pub.require(plan['author_helper']['sha256']=='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666',
        'unknown original fresh author helper')
    pinned.load(pub,job['driver'],plan['author_helper'],namespace='fleet_publication_original_dispatcher')
    M=pub.load('publication_completed_source_revision',a['reader'])
    intent=pub.binding(Path(a['outcome']['path']).parent/'intent.json')
    from contextlib import nullcontext
    scope=nullcontext()
    if a.get('completed_reader')is not None:
        Q=pub.load('completed_derivative_readiness',a['completed_reader'])
        scope=Q.installed(M,selected,ready)
    with scope as bind_dispatcher:
        p,q,proof,warnings,env,D,w,selection,lease,validate,runner=M.context(a['plan'],probe=True,owning_intent=intent)
        if bind_dispatcher is not None:bind_dispatcher(D,env)
    pub.require(p==selected and env[6]==Path(plan['code_root'])
        and D.setup(job)==env,
        'derivative publication lost actual source/review context')
    manager=None
    try:
        if a.get('completed_reader')is not None:
            manager=checkpoint_publication_scope(env[2],env[3],dict(original=str(env[2].ROOT),selected=str(env[6])))
            manager.__enter__();D._source_revision_publication_scope=manager
        checkpoint,value=D.successful(ready['outcome'],env)
        pub.require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY',
            'original derivative enqueue did not qualify')
        return D
    except BaseException:
        if manager is not None:manager.__exit__(*__import__('sys').exc_info())
        if hasattr(D,'_derivative_context_manager'):D._derivative_context_manager.__exit__(*__import__('sys').exc_info())
        raise


def projected_request(pub, producer, request, plan):
    selected=plan.get('queued_lease')
    if selected is None:return request
    pub.require(set(selected)=={'reader','completion'},'closed queued publication projection required')
    reader=pub.load('fresh_queued_lease_reader',selected['reader'])
    value=pub.checked(selected['completion'])
    if value.get('schema_version')=='harbor-queued-publication-history/v1':profile=value
    else:profile=pub.checked(value['intent'])['profile']
    pub.require(profile['workbook']==plan['workbook'] and profile['source_sha256']==plan['source_sha256'],
                'queued lease projection detached from publication source')
    return reader.projected(producer,request,selected['completion'])


def reader_evidence(pub,plan):
    rows=[*pub.FRESH_DEPENDENCIES.values(),plan['author_helper']]
    if plan.get('completed_pre_dispatch')is not None:
        M=pub.load('completed_pre_dispatch_dependencies',plan['completed_pre_dispatch']['reader'])
        rows.extend(M.dependencies(pub,plan))
    if plan.get('disclosure_continuation')is not None:
        M=pub.load('completed_disclosure_dependencies',plan['disclosure_continuation']['completed_reader'])
        rows.extend(M.dependencies(pub,plan))
    if any(plan.get(k)is not None for k in ('model_tail','completed_checkpoint')):
        key='completed_checkpoint'if plan.get('completed_checkpoint')is not None else 'model_tail'
        a=plan[key];(checkpoint_history_binding if key=='completed_checkpoint'else model_history_binding)(pub,plan);selected=pub.checked(a['plan'])
        rows.extend(a.values())
        def add(value):
            if isinstance(value,dict):
                if set(value)=={'path','sha256'}:rows.append(value)
                else:
                    for child in value.values():add(child)
            elif isinstance(value,list):
                for child in value:add(child)
        if key=='completed_checkpoint':
            bp=pub.checked(selected['bridge_profile']);completion=pub.checked(bp['completion'])
            archived=completion['after']
            pub.require(archived['sha256']==selected['tracker']['sha256']
                and archived['path']!=selected['tracker']['path']
                and pub.binding(archived['path'])==archived,
                'completed takeover requires its actual immutable tracker archive')
            add({k:v for k,v in selected.items()if k!='tracker'})
            rows.extend([bp['completion'],archived])
        else:add(selected)
    elif plan.get('preserved_gate')is not None:
        a=plan['preserved_gate'];selected=pub.checked(a['plan'])
        rows += [*a.values(),*[selected[k]for k in ('previous','core','profile','utilities')],selected['semantic_archive']['helper']]
        if 'settled_repair'in selected:rows.append(selected['settled_repair'])
    elif not(plan.get('source_revision',{}).get('completed_reader')is not None and 'fresh_authoring'not in plan):rows.append(plan['fresh_authoring'])
    if plan.get('publication_resume') is not None:
        selected=resume_selection(pub,plan)
        rows += [plan['publication_resume'],*[selected[k] for k in ('prior_plan','intent','failure_proof','reconciliation','policy')]]
    if plan.get('queued_lease') is not None:
        rows += [plan['queued_lease']['reader'],plan['queued_lease']['completion']]
    if plan.get('completed_normalization') is not None:
        a=plan['completed_normalization'];rows += [a['reader'],*a['normalization_context'].values()]
    if plan.get('source_revision') is not None:
        a=plan['source_revision'];M=pub.load('publication_derivative_dependencies',a['reader']);selected=pub.checked(a['plan'])
        pub.require(selected['helper']==a['reader'],'derivative dependency reader differs')
        rows += [*a.values(),*M.dependencies(selected)]
    if plan.get('semantic_continuation') is not None:
        a=plan['semantic_continuation'];selected=pub.checked(a['plan'])
        pub.require(selected['helper']==a['reader'],'semantic dependency reader differs')
        if a.get('completed_reader') is not None:
            M=pub.load('completed_semantic_publication_dependencies',a['completed_reader'])
            if (selected.get('retained_context',{}).get('kind')in {'completed_disclosure','completed_pre_dispatch'} or
                (selected.get('schema_version')=='fleet-completed-instruction-continuation/v1' and ('checkpoint_lease'in selected or 'completed_enqueue'in selected or ('checkpoint_takeover'in selected and 'interrupted_commit'in selected))) or
                (selected.get('schema_version')=='fleet-ordinary-mechanical-dialogue/v1' and 'semantic_context'in selected)): 
                rows += M.dependencies(pub,plan)
            else:
                M.history(pub,plan);physical=pub.checked(selected['physical_context'])
                rows += [*a.values(),selected['physical_context']]
                rows += [selected[k]for k in ('authorization','capacity_policy','renewal_module',
                    'fresh_plan','original_job','outcome','result','descriptor','blocked')]
                rows += [physical[k]for k in ('helper','config','snapshot','old_snapshot','fresh_plan',
                    'original_job','outcome','result','preview','renewal','takeover')]
        else:
            retained=selected.get('retained_context');takeover=selected['completed_takeover']
            rows += [*[value for key,value in a.items()if key!='tail'],selected['authorization'],selected['capacity_policy'],
                *([retained[k]for k in ('helper','plan','config','snapshot')]if retained is not None
                  else [selected['fresh_plan'],selected['original_job']]),*takeover.values()]
            if a.get('tail')is not None:
                rows += list(a['tail'].values())
    if plan.get('snapshot_inventory')is not None:rows.append(plan['snapshot_inventory'])
    if plan.get('validation_runtime')is not None:
        selected=pub.checked(plan['validation_runtime'])
        rows += [plan['validation_runtime'],selected['pair_cache'],selected['original_pair'],selected['original_proof']]
    if plan.get('mechanical_tail')is not None:
        a=plan['mechanical_tail'];selected=pub.checked(a['plan']);old=pub.checked(selected['original'])
        pub.require(selected['helper']==a['reader'],'mechanical dependency reader differs')
        rows += [*a.values(),selected['original'],old['helper'],selected['review_outcome'],selected['review_result'],selected['failed_outcome'],*[value for key,value in selected['instruction_archive'].items()if key!='original']]
    if plan.get('naturalization_continuation')is not None:
        a=plan['naturalization_continuation'];M=pub.load('nat_publication_dependencies',a['reader'])
        selected=pub.checked(a['plan']);pub.require(selected['entry']==a['reader'],'NAT dependency reader differs')
        rows += [*a.values(),*M.evidence(selected)]
    return rows


def install_validation(pub,plan,env):
    """Scope exact-input pair reuse around the unchanged original proof."""
    if plan.get('validation_runtime') is None:return
    import ast,inspect
    from pathlib import Path
    from scripts.batch import harbor_completed_lanes as lanes
    row=plan['validation_runtime'];selected=pub.checked(row)
    pub.require(set(selected)=={'schema_version','workbook','source_sha256','code_root','original_pair','original_proof','pair_cache'}
        and selected['schema_version']=='fleet-publication-pair-scope/v1'
        and all(selected[k]==plan[k]for k in ('workbook','source_sha256','code_root')),
        'publication validation scope differs from exact source')
    P=env[2];original=P.ready._proof
    if getattr(original,'_publication_pair_profile',None)==row:return
    pub.require(not hasattr(original,'_publication_pair_profile'),'another publication validation scope is installed')
    root=Path(plan['code_root'])
    pub.require(selected['original_pair']==pub.binding(root/'scripts/batch/harbor_completed_lanes.py')
        and selected['original_proof']==pub.binding(root/'scripts/batch/harbor_ready_transitions.py')
        and pub.binding(inspect.getsourcefile(lanes.pair_and_audit))==selected['original_pair']
        and pub.binding(inspect.getsourcefile(original))==selected['original_proof'],
        'original publication validation implementation changed')
    cache=pub.load('publication_selected_pair_scope',selected['pair_cache'])
    original_pair=lanes.pair_and_audit
    def body(function):
        import textwrap
        node=ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
        node.name='exact_pair_validator';return ast.dump(node,include_attributes=False)
    pub.require(body(cache._pair_and_audit_uncached)==body(original_pair),
        'selected pair scope changed the original validator')
    # The cache delegates the actual original function with its original globals.
    cache._pair_and_audit_uncached=original_pair
    def scoped(before,selection,**kwargs):
        pub.require(before['workbook_id']==selected['workbook']
            and before['bindings']['original_raw_source']['sha256']==selected['source_sha256'],
            'publication proof detached from selected source')
        previous=lanes.pair_and_audit
        pub.require(previous is original_pair or previous is cache.pair_and_audit,
            'pair validator changed during publication')
        lanes.pair_and_audit=cache.pair_and_audit
        try:
            with cache.pair_validation_scope():return original(before,selection,**kwargs)
        finally:lanes.pair_and_audit=previous
    scoped._publication_pair_profile=row
    P.ready._proof=scoped


def resume_selection(pub,plan):
    """Authenticate one stopped, unspawned owning intent; never infer absence."""
    from pathlib import Path
    p=pub.checked(plan['publication_resume'])
    pub.require(set(p)=={'schema_version','prior_plan','intent','failure_proof','reconciliation','policy'}
        and p['schema_version']=='fleet-unspawned-publication-resume/v1','closed owning resume selection required')
    old=pub.checked(p['prior_plan']);expected=dict(old,journal=plan['journal'],publication_resume=plan['publication_resume'])
    pub.require('publication_resume' not in old and plan==expected and plan['journal']!=old['journal'],
        'owning resume changed publication work or reused controller')
    proof=pub.checked(p['failure_proof']);intent=pub.checked(p['intent'])
    stopped_unspawned=(proof['schema_version']=='fleet-publication-unspawned-stop/v1'
        and proof['status']=='EXACT_OWNING_INTENT_UNSPAWNED_STOPPED' and proof['owning_child_spawned'] is False)
    stopped_preexecute=(proof['schema_version']=='fleet-publication-preexecute-stop/v1'
        and proof['status']=='EXACT_OWNING_INTENT_PREEXECUTE_STOPPED' and proof['owning_child_spawned'] is True
        and proof.get('owning_execute_entered') is False)
    pub.require((stopped_unspawned or stopped_preexecute) and proof['plan']==p['prior_plan']
        and proof['owning_intent']==p['intent'] and proof['processes']==[]
        and proof['tracker_mutated'] is False
        and proof['output_exists'] is False and proof['publication_root_exists'] is False
        and proof['models_dispatched']==0 and proof['automatic_retry'] is False,
        'original publication was not proved stopped before owning execution')
    pub.require(Path(p['intent']['path'])==Path(old['journal'])/'owning-publication/intent.json'
        and intent['queued_tracker']['sha256']==proof['tracker']['sha256'],'owning intent detached from queued tracker')
    for row in proof['files']:pub.require(pub.binding(row['path'])==row,'original publication evidence changed')
    settled=pub.checked(p['reconciliation']);selection=pub.checked(settled['intent']);op=selection['operation']
    pub.require(selection['proof']==p['failure_proof'] and selection['owning_intent']==p['intent']
        and op['kind']=='publication' and op['state']=='hold' and op['workbook']==plan['workbook']
        and settled['result']==dict(state='abandoned',operation_id=op['operation_id'])
        and settled['owning_intent_preserved'] is True and settled['automatic_retry'] is False,
        'original central publication operation not reconciled')
    policy=pub.checked(p['policy'])
    pub.require(set(policy)=={'schema_version','authorization','workbook','source_sha256','maximum_resumes','minimum_lease_runway_seconds','original_guard_seconds','memory_bytes','expiry_behavior'}
        and policy['schema_version']=='fleet-unspawned-publication-resume-policy/v1'
        and policy['workbook']==plan['workbook'] and policy['source_sha256']==plan['source_sha256']
        and policy['maximum_resumes']==1 and policy['minimum_lease_runway_seconds']==1800
        and policy['original_guard_seconds']==3300 and policy['memory_bytes']==8589934592
        and policy['expiry_behavior']=='original owning lease checks fail closed; no retry',
        'bounded owning resume policy differs')
    pub.require(policy['authorization']['sha256']=='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
        and pub.binding(policy['authorization']['path'])==policy['authorization'],'explicit operational authorization required')
    return p


def publication_arguments(pub,producer,request,plan):
    from pathlib import Path
    if plan.get('publication_resume') is None:return Path(plan['journal'])/'owning-publication',{}
    selected=resume_selection(pub,plan);intent=pub.checked(selected['intent'])
    pub.require(intent['request']==producer.encode(request) and intent['code']==producer._codes(),
        'original owning request or code changed on resume')
    return Path(selected['intent']['path']).parent,dict(resume=selected['intent'])


def enforce_publication_wait(pub,plan,selected):
    """Apply the explicit recovery runway after admission, preserving defaults."""
    if plan.get('publication_resume') is None:
        return pub.publication_wait.enforce_after_slot(selected,checked=pub.checked,require=pub.require)
    import time
    resume=resume_selection(pub,plan);policy=pub.checked(resume['policy'])
    proof=pub.checked(resume['failure_proof']);tracker=pub.checked(proof['tracker'])
    pub.require(selected['policy']==plan['publication_wait']
        and selected['value']['lease_tracker']==proof['tracker'],'resume admission tracker differs')
    pub.require(tracker['workbook_id']==plan['workbook']
        and tracker['bindings']['original_raw_source']['sha256']==plan['source_sha256']
        and tracker['execution_state']=='publication_queued' and tracker['terminal_verdict']=='PUBLICATION_READY'
        and tracker['promotion_failure'] is None,'resume publication identity changed')
    lease=tracker['leases']['task_agent'];now=time.time_ns()
    pub.require(isinstance(lease,dict) and type(lease.get('expires_at'))is int
        and lease['expires_at']-now>policy['minimum_lease_runway_seconds']*10**9,
        'insufficient native lease after resumed publication slot acquired')
    return dict(policy=resume['policy'],remaining_seconds=(lease['expires_at']-now)/10**9)


def publish(pub,plan_binding,plan,env,request,adapter):
    install_validation(pub,plan,env)
    producer=env[2];request=projected_request(pub,producer,request,plan)
    runner=producer.run
    if any(plan.get(k)is not None for k in ('queued_lease','completed_normalization','source_revision','semantic_continuation','naturalization_continuation','mechanical_tail','validation_runtime','preserved_gate','model_tail','completed_checkpoint','disclosure_continuation','completed_pre_dispatch')):
        runner=adapter.select(producer,reader_plan=plan_binding,child_wrapper=pub.binding(pub.__file__),reader_evidence=reader_evidence(pub,plan))
    journal,arguments=publication_arguments(pub,producer,request,plan)
    return runner(request,journal,**arguments)


def verify(pub,driver,job,env,directory,plan):
    if plan.get('snapshot_inventory')is None:return driver.verify(job,env,directory)
    import sys
    inventory=pub.load('publication_exact_snapshot_inventory',plan['snapshot_inventory'])
    verifier=pub.load('publication_original_independent_verifier',driver.VERIFIER)
    selected=pub.checked(pub.checked(job['config'])['code']);original=verifier.code_snapshot
    verifier.code_snapshot=inventory.ordered_reader(original,selected);argv=sys.argv
    try:
        sys.argv=[driver.VERIFIER['path'],'--config',job['config']['path'],'--config-sha256',job['config']['sha256'],'--report',str(directory/'independent-verification.json')]
        verifier.main()
    finally:sys.argv=argv;verifier.code_snapshot=original


def publication_child(pub,adapter):
    import argparse,json,os,sys
    from pathlib import Path
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['publication-child'])
    for name in ('plan','plan-sha256','intent','intent-sha256','adapter'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();row=dict(path=args.plan,sha256=args.plan_sha256);plan=pub.checked(row)
    root=Path(plan['code_root']);sys.path.insert(0,str(root));history=pub.history_binding(plan)
    prefix=history_prefix(plan)
    pub.require(not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'publication requires live custody')
    pub.require(os.environ.get(prefix+'PATH')==history['path'] and os.environ.get(prefix+'SHA256')==history['sha256']
        and os.environ.get('HARBOR_FLEET_WORKBOOK')==plan['workbook'],'queued publication child history differs')
    history_survey(pub,plan,history,selected_history_survey(plan))
    if not any(plan.get(k)is not None for k in ('semantic_continuation','mechanical_tail','preserved_gate','model_tail','completed_checkpoint','disclosure_continuation','source_revision','completed_pre_dispatch')):pub.publication_only()
    ready=ready_value(pub,plan);job=pub.checked(ready['job']);D=dispatcher(pub,pub.pinned_dispatcher,plan,ready,job)
    if any(plan.get(k)is not None for k in ('semantic_continuation','mechanical_tail','preserved_gate','model_tail','completed_checkpoint','disclosure_continuation','source_revision','completed_pre_dispatch')):pub.publication_only()
    env=D.setup(job)
    install_validation(pub,plan,env)
    checkpoint,value=D.successful(ready['outcome'],env);pub.require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY','queued publication child READY differs')
    P=env[2];request=projected_request(pub,P,D.publication_request(env,value),plan)
    intent=dict(path=args.intent,sha256=args.intent_sha256)
    adapter.validate(P,reader_plan=row,child_wrapper=pub.binding(pub.__file__),reader_evidence=reader_evidence(pub,plan),intent=intent,sidecar=args.adapter)
    pub.require(pub.checked(intent)['request']==P.encode(request),'owning queued publication request differs')
    if plan.get('publication_resume') is not None:
        _,selected=publication_arguments(pub,P,request,plan)
        pub.require(selected['resume']==intent,'child does not use the exact retained owning intent')
    print(json.dumps(P.execute(intent)))
