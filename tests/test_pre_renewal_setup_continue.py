import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import pre_dispatch_continue as P


def test_only_authenticated_setup_order_changes(tmp_path):
    source=Path('scripts/fleet/advance_authoring_v8.py').read_text()
    new="    snapshot=checked(config['code']);root=Path(snapshot['root']);sys.path.insert(0,str(root))\n    from scripts.fleet.snapshot_inventory import equivalent\n    require(equivalent(code_snapshot(root),snapshot),'frozen code changed')"
    # Comments are deliberately not part of the AST identity.
    original=source.replace("    # Isolated renewal children do not inherit their parent's import path.\n    # Select the hash-bound configuration's root before importing its helpers.\n",'')
    old="    from scripts.fleet.snapshot_inventory import equivalent\n    snapshot=checked(config['code']);root=Path(snapshot['root']);require(equivalent(code_snapshot(root),snapshot),'frozen code changed')"
    assert original.count(new)==1
    a=tmp_path/'old.py';a.write_text(original.replace(new,old));b=tmp_path/'new.py';b.write_text(source)
    P.renewal_dispatcher(P.binding(a),P.binding(b))
    b.write_text(source.replace("seconds>=60","seconds>=0"))
    with pytest.raises(ValueError,match='beyond'):P.renewal_dispatcher(P.binding(a),P.binding(b))


def fixture(tmp_path):
    def keep(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return P.binding(path)
    root=tmp_path/'ops';first=root/'000';failed=root/'001';first.mkdir(parents=True);failed.mkdir()
    driver=keep(tmp_path/'dispatcher.json',{})
    initial=keep(tmp_path/'admission.json',dict(status='ADMITTED'))
    prior=keep(tmp_path/'initial-outcome.json',dict(result=initial))
    job=dict(operations=str(root),outcome=prior,driver=driver);jr=keep(root/'job.json',job)
    cp=keep(first/'result.json',dict(status='LEASE_RENEWAL_REQUIRED'))
    intent=keep(first/'intent.json',dict(action='step',arguments=dict(checkpoint=initial)))
    resource=keep(first/'resource.json',dict(intent=intent));complete=keep(first/'outcome.json',dict(resource=resource,result=cp))
    fi=keep(failed/'intent.json',dict(job=jr,checkpoint=cp,driver=driver,seconds=900))
    log=failed/'step.log';log.write_text("in renewal_child\nin setup\nfrom scripts.fleet.snapshot_inventory import equivalent\nModuleNotFoundError: No module named 'scripts'\n")
    gr=keep(failed/'resource.json',dict(intent=fi,log=P.binding(log),returncode=1,failure=None))
    fail=keep(failed/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=gr,result=None,automatic_retry=False))
    keep(root/'hold.json',dict(outcome=fail))
    p=dict(original_job=jr,failed=fail,renewal_setup=dict(completed=complete))
    seen=[]
    def successful(row,env):
        seen.append(row);v=P.read(row);result=v['result'];return result,P.read(result)
    return p,job,SimpleNamespace(successful=successful),keep,seen


def test_original_completed_prefix_is_checked_before_unused_renewal(tmp_path):
    p,job,D,keep,seen=fixture(tmp_path)
    cp,value=P.renewal_setup_guard(p,job,D,())
    assert value['status']=='LEASE_RENEWAL_REQUIRED'
    assert seen==[job['outcome'],p['renewal_setup']['completed']]


@pytest.mark.parametrize('fault',['dependency','result','later_child','uncertain','different_failure','different_job','skipped_predecessor','wrong_completed'])
def test_refuse_renewal_replay_or_different_history(tmp_path,fault):
    p,job,D,keep,_=fixture(tmp_path);root=Path(job['operations']);last=root/'001'
    if fault in ('dependency','result'):keep(last/(fault+'.json'),{})
    elif fault=='later_child':(root/'002').mkdir()
    elif fault=='wrong_completed':p['renewal_setup']['completed']=job['outcome']
    elif fault=='skipped_predecessor':
        old=P.read(p['renewal_setup']['completed']);gr=P.read(old['resource']);iv=P.read(gr['intent']);iv['arguments']['checkpoint']=p['renewal_setup']['completed'];gr['intent']=keep(root/'000/intent.json',iv);old['resource']=keep(root/'000/resource.json',gr);p['renewal_setup']['completed']=keep(root/'000/outcome.json',old)
    else:
        fail=P.read(p['failed']);gr=P.read(fail['resource'])
        if fault=='uncertain':gr['returncode']=None;gr['failure']={'type':'TimeoutError'}
        elif fault=='different_failure':Path(gr['log']['path']).write_text('other failure');gr['log']=P.binding(gr['log']['path'])
        else:
            iv=P.read(gr['intent']);iv['job']=p['renewal_setup']['completed'];gr['intent']=keep(last/'intent.json',iv)
        fail['resource']=keep(last/'resource.json',gr);p['failed']=keep(last/'outcome.json',fail);keep(root/'hold.json',dict(outcome=p['failed']))
    with pytest.raises(ValueError):P.renewal_setup_guard(p,job,D,())
