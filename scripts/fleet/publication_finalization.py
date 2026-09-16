"""Fleet adapter for an exact stopped, retained publication finalization."""
from pathlib import Path
import sys

FIELDS = {'schema_version', 'original_plan', 'original_publisher', 'intent', 'failure',
          'finalizer', 'lifecycle', 'pair_lanes', 'pair_ready', 'digest_cache', 'authorization', 'resource_policy'}
SCHEMA = 'fleet-retained-publication-finalization/v1'
AUTH = 'acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'


def selection(pub, plan):
    row = plan['finalization']; profile = pub.checked(row)
    pub.require(set(profile) - {'lifecycle_allowance'} == FIELDS and profile['schema_version'] == SCHEMA,
                'closed publication finalization selection required')
    pub.require(profile['authorization']['sha256'] == AUTH
                and pub.binding(profile['authorization']['path']) == profile['authorization'],
                'explicit operational recovery authorization required')
    policy = pub.checked(profile['resource_policy'])
    pub.require(policy['schema_version'] == 'retained-publication-resource-policy/v1'
                and policy['authorization'] == profile['authorization']
                and policy['wall_seconds'] == 4500 and policy['memory_bytes'] == 8589934592
                and policy['disk_reserve_bytes'] == 8589934592 and policy['maximum_wait_seconds'] == 120,
                'bounded finalization resource policy differs')
    original = pub.checked(profile['original_plan'])
    expected = dict(original); expected.pop('publication_wait', None)
    expected.update(journal=plan['journal'], finalization=row)
    pub.require(plan == expected and plan['journal'] != original['journal'],
                'finalization changed original workbook or publication selection')
    pub.require(Path(profile['intent']['path']).parent == Path(original['journal']) / 'owning-publication',
                'finalization intent is not original owning journal')
    for key in ('original_publisher', 'finalizer', 'lifecycle', 'pair_lanes', 'pair_ready', 'digest_cache'):
        pub.require(pub.binding(profile[key]['path']) == profile[key], 'finalization implementation changed: ' + key)
    if 'lifecycle_allowance' in profile:
        pub.require(pub.binding(profile['lifecycle_allowance']['path']) == profile['lifecycle_allowance'],
                    'selected lifecycle allowance reader changed')
    return profile


def install(pub, profile, P):
    """Explicitly selected performance and expired-release code; no file rewrite."""
    cache = pub.load('selected_finalization_digest', profile['digest_cache'])
    original = P.lifecycle.checked_record
    for module in list(sys.modules.values()):
        if module is None or module is sys.modules[__name__]:
            continue
        for name, value in list(vars(module).items()):
            if value is original:
                setattr(module, name, cache.checked_record)
    lanes = pub.load('selected_finalization_pair', profile['pair_lanes'])
    ready = pub.load('selected_finalization_readiness', profile['pair_ready'])
    from scripts.batch import harbor_completed_lanes as owning_lanes
    owning_lanes.pair_and_audit = lanes.pair_and_audit
    owning_lanes.pair_validation_scope = lanes.pair_validation_scope
    P.qualification.transitions._proof = ready._proof
    if 'lifecycle_allowance' in profile:
        allowance = pub.load('scripts.batch.harbor_continuation_allowance', profile['lifecycle_allowance'])
        import scripts.batch as package
        package.harbor_continuation_allowance = allowance
    lifecycle = pub.load('selected_finalization_lifecycle', profile['lifecycle'])
    finalizer = pub.load('selected_finalization_primitive', profile['finalizer'])
    return lifecycle, finalizer


def remote_child(pub, plan_binding, status_validator):
    plan = pub.checked(plan_binding); profile = selection(pub, plan)
    plan, ready, D, env, request, scope = pub.warning_publication.context(
        pub, plan_binding, live=True, status_validator=status_validator)
    try:
        P = env[2]; lifecycle, finalizer = install(pub, profile, P)
        publication = finalizer.finalize(P, lifecycle, request, intent=profile['intent'],
            failure=profile['failure'], original_plan=profile['original_plan'],
            original_publisher=profile['original_publisher'], directory=Path(plan['journal']) / 'finalization')
        destination = Path(plan['journal']) / 'independent'; destination.mkdir()
        D.verify(dict(config=ready['config']), env, destination)
        verification = pub.binding(destination / 'independent-verification.json')
        package = pub.pack(Path(env[5]['output']), verification, publication, Path(plan['journal']) / 'export')
        return pub.retain(Path(plan['journal']) / 'result.json', dict(status='REMOTE_VERIFIED',
            workbook=plan['workbook'], plan=plan_binding, publication=publication, verification=verification,
            package=package, finalization=plan['finalization'], model_calls=0, automatic_retry=False))
    finally:
        scope.__exit__(None, None, None)


def wait_configuration(pub, plan):
    profile = selection(pub, plan)
    policy = pub.checked(profile['resource_policy'])
    # Terminal finalization has no live authoring lease to extend; exact stopped
    # ownership is revalidated under the original locks before any release.
    return dict(maximum_wait_seconds=policy['maximum_wait_seconds'], policy=None)


VERIFY_SCHEMA = 'fleet-retained-publication-verification/v1'
VERIFY_FIELDS = {'schema_version', 'helper', 'publisher', 'implementation', 'plan',
                 'publication', 'resource', 'tracker', 'inventory', 'directory'}


def independent_verify(pub, D, config, destination, inventory_binding):
    """Run the original verifier with a declared, lossless inventory reader.

    Only JSON row ordering is normalized. The original verifier still performs
    every current qualification, immutable-generation, and delivered-byte check.
    """
    inventory = pub.load('publication_inventory_comparison', inventory_binding)
    verifier = pub.load('publication_independent_verifier', D.VERIFIER)
    selected = pub.checked(pub.checked(config)['code'])
    original = verifier.code_snapshot
    verifier.code_snapshot = inventory.ordered_reader(original, selected)
    argv = sys.argv
    try:
        sys.argv = [D.VERIFIER['path'], '--config', config['path'],
                    '--config-sha256', config['sha256'], '--report',
                    str(destination / 'independent-verification.json')]
        verifier.main()
    finally:
        sys.argv = argv
        verifier.code_snapshot = original


def verification_export(pub, profile_binding):
    """Finish independent verification/export of an already released promotion.

    This entry point has no publication, lease, authoring, or claim operation.
    It preserves the original result and uses a separate single-use namespace.
    """
    p = pub.checked(profile_binding)
    pub.require(set(p) == VERIFY_FIELDS and p['schema_version'] == VERIFY_SCHEMA
                and p['helper'] == pub.binding(__file__)
                and p['publisher'] == pub.binding(pub.__file__),
                'closed verification-only selection required')
    implementation = pub.checked(p['implementation'])
    pub.require(implementation['publisher'] == p['publisher'], 'publisher closure differs')
    for row in implementation['dependencies'].values():
        pub.require(pub.binding(row['path']) == row, 'publisher dependency changed')
    plan = pub.checked(p['plan'])
    owning = Path(plan['journal']) / 'owning-publication'
    result = pub.checked(p['publication']); resource = pub.checked(p['resource'])
    pub.require(p['publication']['path'] == str(owning / 'result.json')
                and Path(p['resource']['path']).parent.parent == owning
                and Path(p['resource']['path']).name == 'resource.json'
                and resource['returncode'] == 0 and resource['failure'] is None
                and resource['intent']['path'] == str(owning / 'intent.json')
                and result['status'] == 'PROMOTED' and result['automatic_retry'] is False
                and result['tracker'] == p['tracker'],
                'successful original owning promotion required')
    destination = Path(p['directory'])
    pub.require(destination.is_absolute() and destination.parent == owning.parent.parent
                and destination != owning.parent and not destination.exists(),
                'fresh independent verification namespace required')
    plan, ready, D, env, request, scope = pub.warning_publication.context(
        pub, p['plan'], live=False, status_validator=pub.publication_status.validate_status)
    try:
        P = env[2]
        tracker = request.tracker_root / (plan['workbook'] + '.json')
        current = pub.checked(p['tracker'])
        pub.require(pub.binding(tracker) == p['tracker']
                    and current['execution_state'] == 'PROMOTED'
                    and all(value is None for value in current['leases'].values())
                    and current['bindings']['original_raw_source']['sha256'] == plan['source_sha256']
                    and pub.checked(resource['intent'])['request'] == P.encode(request),
                    'exact released promotion and original request required')
        def forbidden(*args, **kwargs):
            raise RuntimeError('verification-only continuation cannot publish or renew')
        for name in ('run', 'execute', '_lease_step', '_run_publication'):
            if hasattr(P, name): setattr(P, name, forbidden)
        destination.mkdir()
        pub.retain(destination / 'intent.json', dict(profile=profile_binding,
            publication=p['publication'], tracker=p['tracker'], automatic_retry=False))
        independent = destination / 'independent'; independent.mkdir()
        independent_verify(pub, D, ready['config'], independent, p['inventory'])
        verification = pub.binding(independent / 'independent-verification.json')
        pub.require(pub.checked(verification)['tracker'] == p['tracker']
                    and pub.binding(tracker) == p['tracker'],
                    'released promotion changed during independent verification')
        package = pub.pack(Path(env[5]['output']), verification, p['publication'], destination / 'export')
        return pub.retain(destination / 'result.json', dict(status='REMOTE_VERIFIED',
            workbook=plan['workbook'], plan=p['plan'], publication=p['publication'],
            verification=verification, package=package, verification_continuation=profile_binding,
            model_calls=0, automatic_retry=False))
    finally:
        scope.__exit__(None, None, None)


SOURCE_SCHEMA='fleet-retained-source-publication-finalization/v1'
SOURCE_FIELDS={'schema_version','original_plan','original_publisher','intent','failure','finalizer','lifecycle','authorization','resource_policy','context_reader','qualification_plan','qualification_implementation'}

def source_selection(pub,plan):
    row=plan['finalization'];p=pub.checked(row)
    pub.require(set(p)==SOURCE_FIELDS and p['schema_version']==SOURCE_SCHEMA,'closed completed-source finalization required')
    pub.require(p['authorization']['sha256']==AUTH and pub.binding(p['authorization']['path'])==p['authorization'],'explicit finalization authorization required')
    old=pub.checked(p['original_plan']);expected=dict(old);expected.pop('publication_wait',None);expected.update(journal=plan['journal'],finalization=row)
    pub.require(plan==expected and plan['journal']!=old['journal'] and old.get('source_revision',{}).get('completed_reader')is not None,
        'source finalization changed original qualified task')
    qualified_plan=pub.checked(p['qualification_plan'])
    if qualified_plan!=old:
        pub.require(qualified_plan==dict(old,journal=qualified_plan['journal'],publication_resume=qualified_plan['publication_resume']),
            'qualified context changed original task')
        resume=pub.checked(qualified_plan['publication_resume'])
        pub.require(resume['prior_plan']==p['original_plan'] and resume['intent']==p['intent'],
            'qualified context lost original owning intent')
    qualified_implementation=pub.checked(p['qualification_implementation'])
    dependencies=qualified_implementation['dependencies']
    pub.require(set(dependencies)==set(pub.FRESH_DEPENDENCIES)|{'publication_wait','original_implementation','original_publisher'}
        and {k:dependencies[k]for k in pub.FRESH_DEPENDENCIES}==pub.FRESH_DEPENDENCIES
        and dependencies['publication_wait']==pub.binding(pub.publication_wait.__file__),
        'finalization changed validated qualification dependencies')
    for bound in [qualified_implementation['publisher'],*qualified_implementation['dependencies'].values()]:
        pub.require(pub.binding(bound['path'])==bound,'qualified publication implementation changed')
    policy=pub.checked(p['resource_policy'])
    pub.require(policy==dict(schema_version='fleet-source-publication-finalization-policy/v1',authorization=p['authorization'],wall_seconds=3300,memory_bytes=8589934592,disk_reserve_bytes=8589934592,maximum_wait_seconds=120,maximum_finalizations=1),'bounded source finalization policy differs')
    for key in ('original_publisher','finalizer','lifecycle','context_reader'):
        pub.require(pub.binding(p[key]['path'])==p[key],'source finalization code changed')
    pub.require(Path(p['intent']['path']).parent==Path(old['journal'])/'owning-publication','source finalization lost original owning intent')
    return p


def source_context(pub,plan_binding):
    plan=pub.checked(plan_binding);p=source_selection(pub,plan)
    reader=pub.load('completed_source_publication_context',p['context_reader'])
    original,ready,D,env,request,scope=reader.publication_context(pub,p['original_plan'])
    try:
        P=env[2];pub.publication_only()
        pub.require(P.read(p['intent'])['request']==P.encode(request),'finalization request changed original intent')
        lifecycle=pub.load('source_finalization_lifecycle',p['lifecycle'])
        primitive=pub.load('source_finalization_primitive',p['finalizer'])
        primitive.stopped(P,p['intent'],p['failure'],original_plan=p['original_plan'],original_publisher=p['original_publisher'])
        path=request.tracker_root/(plan['workbook']+'.json');current=P.read(P.record(path))
        pub.require(current['execution_state']=='PROMOTED' and current['promotion_failure']is None,'actual original promotion required')
        lifecycle.validate_lifecycle(current)
        return p,ready,D,env,request,scope,lifecycle,primitive
    except BaseException:
        scope.__exit__(*sys.exc_info());raise


def source_remote_child(pub,plan_binding):
    plan=pub.checked(plan_binding);p,ready,D,env,request,scope,lifecycle,primitive=source_context(pub,plan_binding)
    try:
        P=env[2]
        publication=primitive.finalize(P,lifecycle,request,intent=p['intent'],failure=p['failure'],original_plan=p['original_plan'],original_publisher=p['original_publisher'],directory=Path(plan['journal'])/'finalization')
        destination=Path(plan['journal'])/'independent';destination.mkdir()
        pub.fresh_publication.verify(pub,D,pub.checked(ready['job']),env,destination,plan)
        verification=pub.binding(destination/'independent-verification.json')
        package=pub.pack(Path(env[5]['output']),verification,publication,Path(plan['journal'])/'export')
        return pub.retain(Path(plan['journal'])/'result.json',dict(status='REMOTE_VERIFIED',workbook=plan['workbook'],plan=plan_binding,publication=publication,verification=verification,package=package,finalization=plan['finalization'],model_calls=0,automatic_retry=False))
    finally:scope.__exit__(None,None,None)


def source_wait(pub,plan):
    source_selection(pub,plan)
    return dict(maximum_wait_seconds=120,policy=None)
