"""Build a narrow 0190 fleet successor; no task execution or evidence mutation.

Original snapshot, review and Call bytes remain authoritative. Every added or
changed transport byte is an exact patch witness. Only two pinned, completed
legacy Calls gain read-only inspection under the prospective fleet environment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]
Q=ROOT/'diagnostics/authoring-queue-extension-20260911T185027Z'
PREVIOUS={'path':str(Q/'0190-existing-review-snapshot-v5.json'),'sha256':'e49370d9751610c3b9b9c4f5afa1d4898f36049e8a6175eae64ad8c5bf4d19f3'}
READER='scripts/batch/harbor_normalization_existing_review.py'
EXECUTOR='scripts/batch/harbor_model_executor.py'
TRANSPORT='xl_transport.py'
ADD=('scripts/fleet/coordinator.py','scripts/fleet/model_gate.py')


def require(ok,message):
    if not ok:raise ValueError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def record(path):return dict(path=str(Path(path).absolute()),sha256=sha(Path(path).read_bytes()))
def read(bound):
    require(record(bound['path'])==bound,'bound evidence changed')
    return json.loads(Path(bound['path']).read_bytes())
def keep(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(value if isinstance(value,bytes) else canonical(value))
    return record(path)


def apply(raw,edits):
    text=raw.decode()
    for old,new in edits:
        require(text.count(old)==1,'reviewed patch context must occur exactly once')
        text=text.replace(old,new,1)
    return text.encode()


def fleet_edits():
    executor=[
      ("    return plan, raw, request, evidence\n", "    if os.environ.get('HARBOR_FLEET_URL'):\n        require(campaign.model_slots == 1, 'fleet global model limit must remain one')\n        plan['max_transport_retries']=0\n        plan['fleet_transport_policy']='single-dispatch-durable-global-gate/v1'\n    return plan, raw, request, evidence\n"),
      ("    plan, body, expected_request, budget_raw = prepared_plan(prepared, campaign)\n", "    require(os.environ.get('HARBOR_FLEET_URL'), '0190 successor requires live fleet model gating')\n    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only continuation cannot dispatch a model')\n    plan, body, expected_request, budget_raw = prepared_plan(prepared, campaign)\n"),
      ("            with xl_transport.model_slot():\n", "            slot_options = dict(operation_id=prepared.attempt_id + ('-physical-repair-01' if prepared.physical_repair is not None else ''),\n                                request_sha256=sha(body), outcome_path=root/'outcome.json')\n            with xl_transport.model_slot(**slot_options):\n"),
      ("    with scope_lock(root.parent, create=False):\n        return inspect_locked(root, plan, request,original_renderer_code=original_renderer_code)\n", "    with scope_lock(root.parent, create=False):\n        if os.environ.get('HARBOR_FLEET_URL') and 'fleet_transport_policy' not in strict_json((root/'plan.json').read_bytes()):\n            from scripts.batch.harbor_normalization_existing_review import fleet_legacy_plan\n            plan=fleet_legacy_plan(root,plan)\n        return inspect_locked(root, plan, request,original_renderer_code=original_renderer_code)\n")]
    transport=[
      ('def model_slot():\n','def _local_model_slot():\n'),
      ('def read_response(request, *, timeout):\n', '''@contextmanager
def model_slot(*, operation_id=None, request_sha256=None, outcome_path=None):
    """New transport always requires the durable fleet gate, plus local exclusion."""
    from scripts.fleet import model_gate
    if not model_gate.configured():
        raise ValueError('0190 successor requires live fleet model gating')
    if not all((operation_id, request_sha256, outcome_path)):
        raise ValueError('fleet model calls require durable operation and outcome identity')
    with _local_model_slot(), model_gate.slot(operation_id=operation_id, request_sha256=request_sha256, outcome_path=outcome_path):
        yield


def read_response(request, *, timeout):
'''),
      ('    with model_slot():\n        return _read_response(request, timeout=timeout)\n', '''    from scripts.fleet import model_gate
    if not model_gate.configured():
        raise ValueError('0190 successor requires live fleet model gating')
    return model_gate.read_response(request,timeout=timeout,local_slot=_local_model_slot)
''')]
    return {EXECUTOR:executor,TRANSPORT:transport}


EXTENSION='''

# This successor is prospective fleet execution plus exact historical inspection.
# The selected full snapshot binds this reader; the profile binds every other
# delta and every allowed original completed Call. No historical journal changes.
FLEET_PROFILE = __PROFILE__
FLEET_PREVIOUS_SHA = 'e49370d9751610c3b9b9c4f5afa1d4898f36049e8a6175eae64ad8c5bf4d19f3'
FLEET_CHANGED = {'scripts/batch/harbor_model_executor.py','xl_transport.py'}
FLEET_ADDED = {'scripts/fleet/coordinator.py','scripts/fleet/model_gate.py'}


def fleet_profile():
 value=read(FLEET_PROFILE)
 require(set(value)=={'schema_version','previous','previous_reader','changes','additions','legacy_calls','semantic_budget_reset'}
         and value['schema_version']=='0190-reviewed-fleet-successor/v1'
         and value['semantic_budget_reset'] is False,'unsupported fleet successor profile')
 require(value['previous']['sha256']==FLEET_PREVIOUS_SHA,'unreviewed fleet predecessor')
 require(set(value['changes'])==FLEET_CHANGED and set(value['additions'])==FLEET_ADDED,'unreviewed fleet file roles')
 return value


def _fleet_predecessor(now):
 value=fleet_profile();previous=read(value['previous']);root=Path(previous['root']);before=inventory(root)
 require(value['previous_reader']==record(root/SELF),'previous reader changed')
 require(set(now)==set(before)|FLEET_ADDED,'fleet successor code identities differ')
 expected=dict(before)
 for name,change in value['changes'].items():
  require(set(change)=={'before','after','edits'} and change['before']==record(root/name),'fleet patch predecessor differs')
  raw=N.raw(change['before']).decode()
  for old,new in change['edits']:
   require(raw.count(old)==1,'fleet patch witness is not exact')
   raw=raw.replace(old,new,1)
  require(raw.encode()==N.raw(change['after']),'fleet patch output differs from deterministic witness')
  expected[name]=change['after']['sha256']
 for name,binding in value['additions'].items():
  N.raw(binding);expected[name]=binding['sha256']
 # SELF remains bound by the root-selected immutable full snapshot, exactly as
 # in the predecessor's profile(). All other bytes must match this derivation.
 expected[SELF]=now[SELF]
 require(expected==now,'unreviewed fleet successor code changes')
 return before


def fleet_legacy_plan(root,current):
 """Read-only compatibility for exactly pinned completed pre-fleet Calls."""
 _fleet_predecessor(inventory(ROOT));value=fleet_profile();key=str(Path(root))
 require(key in value['legacy_calls'],'legacy call is not in the explicit completed-call profile')
 row=value['legacy_calls'][key]
 require(set(row)=={'plan','outcome','request','submitted','response','completed','parsed'},'legacy call profile fields changed')
 for binding in row.values():N.raw(binding)
 require(row['plan']==record(Path(root)/'plan.json') and row['outcome']==record(Path(root)/'outcome.json'),'legacy call location changed')
 require(read(row['outcome'])['status']=='completed','historical incomplete/uncertain call cannot use fleet inspection')
 legacy=read(row['plan']);require('fleet_transport_policy' not in legacy,'legacy call already has prospective fleet provenance')
 expected=dict(legacy);expected['fleet_transport_policy']='single-dispatch-durable-global-gate/v1';expected['max_transport_retries']=0
 require(E.canonical(current)==E.canonical(expected),'historical identity, request, configuration or budget changed')
 return legacy
'''


def build(output):
    output=Path(output).absolute();require(not output.exists(),'build already exists; use an explicit new version')
    previous=read(PREVIOUS);old=Path(previous['root']);inventory=read(record(old/'.pipeline-code.json'))
    actual={str(p.relative_to(old)):sha(p.read_bytes()) for p in old.rglob('*') if p.is_file() and p.name!='.pipeline-code.json' and not {'__pycache__','.pytest_cache'}&set(p.parts)}
    require(actual==inventory and sha(canonical(inventory))==old.name,'entire predecessor snapshot changed')
    output.mkdir(parents=True);candidate=output/'candidate';candidate.mkdir()
    for name,digest in inventory.items():
        p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old/name,p)
    changes={}
    for name,edits in fleet_edits().items():
        raw=apply((old/name).read_bytes(),edits);(candidate/name).write_bytes(raw)
        witness=keep(output/'witnesses'/name,raw)
        changes[name]=dict(before=record(old/name),after=witness,edits=edits)
    additions={}
    for name in ADD:
        raw=(ROOT/name).read_bytes();p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        additions[name]=keep(output/'witnesses'/name,raw)
    campaign=Q/'authoring/0190-generated-02/model-campaign';legacy={}
    for p in sorted(campaign.rglob('plan.json')):
        root=p.parent;outcome=read(record(root/'outcome.json'))
        require(outcome['status']=='completed','unknown historical model response must remain held')
        names=dict(plan='plan.json',outcome='outcome.json',request='request.json',submitted='submitted.json',response='response.raw.json',completed='completed.json',parsed='parsed.json')
        if not (root/'parsed.json').exists():names['parsed']='content.txt'
        legacy[str(root)]={k:record(root/name) for k,name in names.items()}
    require(len(legacy)==2,'exact reviewed0190 legacy call inventory changed')
    profile=keep(output/'profile.json',dict(schema_version='0190-reviewed-fleet-successor/v1',previous=PREVIOUS,
        previous_reader=record(old/READER),changes=changes,additions=additions,legacy_calls=legacy,semantic_budget_reset=False))
    edits=[("base=read(old_snapshot);old=inventory(Path(base['root']));now=inventory(ROOT)", "base=read(old_snapshot);old=inventory(Path(base['root']));actual=inventory(ROOT);now=_fleet_predecessor(actual)"),
           ("expected[SELF]=record(Path(__file__))['sha256']", "expected[SELF]=now[SELF]"),
           ('return base,old,now\n','return base,old,actual\n'),
           ('def validate_receipt_inventory(binding,value,now):\n','def validate_receipt_inventory(binding,value,now):\n now=_fleet_predecessor(now)\n')]
    raw=apply((old/READER).read_bytes(),edits)+EXTENSION.replace('__PROFILE__',repr(profile)).encode()
    (candidate/READER).write_bytes(raw)
    keep(output/'reader-patch.json',dict(before=record(old/READER),edits=edits,extension=EXTENSION.replace('__PROFILE__',repr(profile)),after_sha256=sha(raw)))
    files={str(p.relative_to(candidate)):sha(p.read_bytes()) for p in sorted(candidate.rglob('*')) if p.is_file()}
    fingerprint=sha(canonical(files));keep(candidate/'.pipeline-code.json',files)
    code=output/'code'/fingerprint;code.parent.mkdir();candidate.rename(code)
    snapshot=keep(output/'snapshot.json',dict(schema_version='authoring-fleet-code-snapshot/v1',root=str(code),fingerprint=fingerprint,
        files_count=len(files),previous=PREVIOUS,profile=profile,reviewed_patch=record(output/'reader-patch.json'),existing_roots_unchanged=True,model_calls=0))
    helper=(ROOT/'scripts/fleet/recover_0190.py').read_text()
    original="SNAPSHOT={'path':str(BASE/'0190-existing-review-snapshot-v5.json'),'sha256':'e49370d9751610c3b9b9c4f5afa1d4898f36049e8a6175eae64ad8c5bf4d19f3'}"
    require(helper.count(original)==1,'known recovery helper binding changed')
    keep(output/'recover_0190.py',helper.replace(original,'SNAPSHOT='+repr(snapshot),1).encode())
    return keep(output/'build.json',dict(snapshot=snapshot,profile=profile,helper=record(output/'recover_0190.py'),builder=record(__file__),
        preserved_legacy_calls=legacy,code_changed_roles=[READER,*changes,*additions],model_calls=0,task_artifacts_modified=False,semantic_budget_reset=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(a.output)))
