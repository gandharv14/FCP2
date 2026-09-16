"""Read-only native lease runway gate for an explicitly selected guarded step.

The owning worker must repeat its existing final lease check. This gate adds
launch margin; it neither renews custody nor extends a task timeout.
"""
import hashlib,json,math,time
from pathlib import Path
from scripts.fleet.migration import binding,checked
MEMORY=8589934592
SECONDS=3300

def require(v,m):
    if not v:raise ValueError(m)

def identity(agent):
    return hashlib.sha256(json.dumps({k:agent[k] for k in ('owner','token')},sort_keys=True,separators=(',',':')).encode()).hexdigest()

def validate(profile,tracker,actual_binding,*,now_ns):
    require(set(profile)=={'schema_version','workbook','tracker','revision','lease_identity_sha256','guard_seconds','memory_bytes','launch_buffer_seconds'},'closed lease runway profile required')
    require(profile['schema_version']=='fleet-native-lease-runway/v1' and profile['workbook']=='0017','explicit reviewed runway workbook required')
    require(type(profile['guard_seconds']) is int and profile['guard_seconds']==SECONDS and type(profile['memory_bytes']) is int and profile['memory_bytes']==MEMORY,'original3300s8GiB guard required')
    buffer=profile['launch_buffer_seconds'];require(type(buffer) is int and 60<=buffer<=600,'explicit bounded launch margin required')
    require(type(now_ns) is int and now_ns>0,'native integer wall clock required')
    require(actual_binding==profile['tracker'],'native tracker bytes changed')
    require(type(profile['revision']) is int and tracker.get('revision')==profile['revision'],'native tracker revision changed')
    require(tracker.get('workbook_id',tracker.get('workbook'))==profile['workbook'],'native workbook changed')
    agent=tracker['leases']['task_agent'];require(isinstance(agent.get('owner'),str) and agent['owner'] and isinstance(agent.get('token'),str) and agent['token'],'complete native lease identity required')
    require(identity(agent)==profile['lease_identity_sha256'],'native owner or token changed')
    expiry=agent['expires_at'];require(type(expiry) is int and expiry>0,'native integer lease expiry required')
    remaining=expiry-now_ns;required=(SECONDS+30+buffer)*10**9
    require(remaining>required,'native lease has insufficient runway; explicit owning renewal required')
    return dict(schema_version='fleet-native-lease-runway-observation/v1',status='PASS',workbook=profile['workbook'],tracker=actual_binding,revision=profile['revision'],lease_identity_sha256=profile['lease_identity_sha256'],observed_at_ns=now_ns,expires_at_ns=expiry,remaining_ns=remaining,required_ns=required,guard_seconds=SECONDS,memory_bytes=MEMORY,launch_buffer_seconds=buffer,renewal_performed=False,models_dispatched=0)

def inspect(profile):
    # Hash before decoding and once after checking, refusing concurrent CAS.
    value=checked(profile['tracker']);observed=validate(profile,value,binding(profile['tracker']['path']),now_ns=time.time_ns())
    require(binding(profile['tracker']['path'])==profile['tracker'],'native tracker changed during runway observation')
    return observed
