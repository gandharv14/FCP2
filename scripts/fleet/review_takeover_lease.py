"""Use an authenticated completed expiry replacement for an unchanged review.

This reader never renews custody or changes the rejected review. It validates
all original takeover history and returns the current lease for the same source.
"""
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import importlib, importlib.util, sys, hashlib, json

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical takeover evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'takeover evidence changed');return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'takeover implementation changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);M=importlib.util.module_from_spec(spec);sys.modules[name]=M;spec.loader.exec_module(M);return M

def project(p,selected,value,selection,old_lease):
    require(set(selected)=={'helper','profile','runtime','completion'}and selected['helper']==binding(__file__),'closed completed review takeover required')
    profile=read(selected['profile']);complete=read(selected['completion']);stop=read(complete['stopped_owner_proof'])
    require(profile['schema_version']=='harbor-allowance-takeover-direct/v1'
        and set(profile)-{'layout'}=={'schema_version','original_snapshot','shared','allowance','selection'}
        and set(profile['shared'])=={'lifecycle','takeover','allowance','stdlib'}
        and profile['selection']['checkpoint']==p['result']
        and profile['selection']['guard']==read(p['outcome'])['resource']
        and profile['selection']['workbook']==p['workbook']
        and profile['selection']['owner']==p['owner']
        and p['tracker']==dict(path=profile['selection']['tracker']['path'],sha256=complete['after']['sha256']),
        'takeover detached from exact rejected review or current owner')
    package=importlib.import_module('scripts.batch')
    with ExitStack()as stack:
        for short,key in (('harbor_tracker_lifecycle','lifecycle'),('harbor_continuation_allowance','allowance')):
            name='scripts.batch.'+short;prior=sys.modules.get(name);prior_attribute=getattr(package,short,None)
            def restore(name=name,short=short,prior=prior,attr=prior_attribute):
                if prior is None:sys.modules.pop(name,None)
                else:sys.modules[name]=prior
                if attr is None:
                    if hasattr(package,short):delattr(package,short)
                else:setattr(package,short,attr)
            stack.callback(restore)
            setattr(package,short,module(name,profile['shared'][key]))
        T=module('_review_completed_takeover',profile['shared']['takeover'])
        if 'layout'in profile:module('_review_takeover_runtime',selected['runtime']).select_layout(T,profile['layout'])
        else:require(binding(selected['runtime']['path'])==selected['runtime'],'takeover runtime changed')
        before,after,new_lease=T.validate_completion(selected['completion'])
        require(T._selection_value(T._selection(profile['selection']))==stop['selection']
            and stop['before']['sha256']==profile['selection']['tracker']['sha256']
            and old_lease.sha256==stop['before']['sha256']and old_lease.revision==before['revision']
            and (old_lease.owner,old_lease.token)==(before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'])
            and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']==p['source']
            and before['bindings']['source_file']==after['bindings']['source_file']==p['golden']
            and binding(p['tracker']['path'])==p['tracker']and read(p['tracker'])==after,
            'completed takeover changed reviewed source or live custody')
        return value,selection,type(old_lease)(**asdict(new_lease))
