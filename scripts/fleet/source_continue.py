"""Bridge a retained source-only import into the owning dispatcher, without models.

Run only as a centrally fenced worker after the migration itself has completed.
The native step retains its own guard, and its ordinary outcome must pass the
unchanged owning dispatcher's validator. An uncertain run is never replayed.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def publisher(bound):
    raw = Path(bound['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != bound['sha256']: raise ValueError('bridge plan changed')
    plan = json.loads(raw); selected = plan['publisher']
    if hashlib.sha256(Path(selected['path']).read_bytes()).hexdigest() != selected['sha256']: raise ValueError('publisher changed')
    spec=importlib.util.spec_from_file_location('selected_source_bridge_publisher', selected['path'])
    P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
    P.require(P.binding(selected['path']) == selected, 'publisher path changed')
    return P, plan


def run(bound, *, inspect=False):
    P, plan = publisher(bound)
    fields = {'schema_version', 'workbook', 'source_sha256', 'code_root', 'continuation_history',
              'source_continuation', 'job', 'journal', 'publisher'}
    P.require(set(plan) == fields and plan['schema_version'] == 'fleet-source-native-bridge/v1', 'closed source bridge plan required')
    root = P.safe(plan['code_root']); sys.path.insert(0, str(root))
    P.continuation_binding(plan)
    job = P.checked(plan['job']); boundary = plan['source_continuation']
    P.require(job['config'] == boundary['config'] and not job['models'] and not job['publish'], 'bridge cannot authorize models or publication')
    sys.path.insert(0, str(Path(job['driver']['path']).parent))
    driver = P.load('source_bridge_dispatcher', job['driver'])
    env = driver.setup(job); base, _, _, _, _, config, actual_root, controls = env
    P.require(actual_root == root and base.SECONDS == 3300 and base.MEMORY == 8*1024**3, 'owning code/resource contract changed')
    worker, _, _ = base.selected(boundary['descriptor'])
    value, lease = base._prior(boundary['checkpoint'], boundary['descriptor'])
    P.require(value['status'] == 'IMPORTED' and value['step'] == 'source', 'source-only imported checkpoint required')
    journal = P.safe(plan['journal'])
    P.require(not journal.exists() and journal.parent == Path(config['run_root'])/'native-bridges', 'new dedicated bridge namespace required')
    if inspect:
        return dict(status='SOURCE_BRIDGE_VALIDATED', plan=bound, model_calls=0, new_admission=False)
    P.require(not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'), 'live continuation custody required')
    history = plan['continuation_history']
    P.require(os.environ.get('HARBOR_FLEET_WORKBOOK') == plan['workbook']
        and os.environ.get('HARBOR_FLEET_CONTINUATION_HISTORY_PATH') == history['path']
        and os.environ.get('HARBOR_FLEET_CONTINUATION_HISTORY_SHA256') == history['sha256'], 'bridge history/custody differs')
    from scripts.fleet.continuation_history import projected_survey
    certificate = P.checked(history)
    projected_survey(tuple(map(Path, certificate['roots'])), set(certificate['known_ids']))
    # No journal can be inherited into the child: even an accidental transport
    # dispatch cannot acquire the fleet's global model slot. dispatch=False is
    # also recorded by the native coordinator guard.
    os.environ.pop('HARBOR_FLEET_MODEL_JOURNAL', None)
    for name in ('OPENAI_API_KEY', 'LITELLM_API_KEY', 'ANTHROPIC_API_KEY'):
        os.environ.pop(name, None)
    base.worker._active(worker, lease)
    with driver.fleet_slot(plan['workbook']) as lock_fds:
        journal.mkdir(parents=True)
        P.retain(journal/'intent.json', dict(plan=bound, implementation=P.binding(__file__), automatic_retry=False, model_calls_allowed=0))
        outcome = base.run('step', dict(descriptor=boundary['descriptor'], checkpoint=boundary['checkpoint'], captures=None),
                           controls, journal/'step', dispatch=False, guardian_lock_fds=lock_fds)
        checkpoint, result = driver.successful(outcome, env)
        P.require(result['status'] in {'STEP_COMPLETE', 'PENDING_MODEL', 'RESOURCE_WAIT'}, 'native result requires reconciliation')
        return P.retain(journal/'result.json', dict(status='NATIVE_CONTINUATION_READY', plan=bound, outcome=outcome,
            checkpoint=checkpoint, descriptor=result['descriptor'], model_calls=0, new_admission=False,
            semantic_budget_reset=False, automatic_retry=False))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['inspect','run']);p.add_argument('--plan',type=Path,required=True);p.add_argument('--plan-sha256',required=True)
    a=p.parse_args()
    # The entry point is an isolated selected snapshot, as are pipeline imports.
    import json
    print(json.dumps(run(dict(path=str(a.plan.absolute()),sha256=a.plan_sha256),inspect=a.action=='inspect')))

if __name__=='__main__':main()
