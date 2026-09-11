from pathlib import Path
from dataclasses import replace
import pytest
from scripts.batch import harbor_environment_refresh as F
from scripts.batch import harbor_environment_worker as W
from scripts.batch.harbor_admission import read,record
from test_audit_inventory_lineage import audit_chain,retained,worker,selected,setup,save
import xl_audit_equivalence as Q
import xl_audit_renderer_recovery as R
import xl_variable_source_audit as A
import xl_audit_inventory_upgrade as I
import xl_audit_inventory_lineage as L


@pytest.fixture
def prefix(audit_chain,tmp_path):
    ob,nb,or_,oq,s,seen=audit_chain
    old=W._selection(W._content(s,ob,()));new=replace(old,content=replace(old.content,bindings=nb))
    evidence=[dict(path=str(Path(m.__file__).resolve()),sha256=sha) for m,sha in ((Q,L.LOCATOR_BEFORE),(R,L.RENDERER_BEFORE),(A,I.BEFORE_SHA256))]
    evidence.append(ob['variable_source_audit'])
    s.run.mkdir(exist_ok=True)
    checkpoint=save(s.run/'environment.json',dict(selection=F.encode(old),evidence=evidence))
    prior=save(tmp_path/'prior.json',dict(status='ENVIRONMENT_READY',step='environment',selection=F.encode(old),evidence=evidence))
    return s,old,new,prior,checkpoint,evidence,seen


def test_exact_archival_roles_need_current_wrapper_and_default_stays_strict(prefix):
    s,old,new,prior,checkpoint,evidence,seen=prefix;count=len(seen)
    before={x['path']:Path(x['path']).read_bytes() for x in (prior,checkpoint,old.content.bindings['variable_source_audit'])}
    with pytest.raises(ValueError):F.predecessor(s,prior)
    assert F.predecessor(s,prior,current=new)==(old,'environment',checkpoint)
    roles=F.historical_audit_codes(old,new)
    assert roles=={x['path']:x['sha256'] for x in evidence[:3]}
    assert len(seen)==count and all(Path(p).read_bytes()==v for p,v in before.items())


@pytest.mark.parametrize('target',['audit_text','raw_source','unknown_code','missing_wrapper','wrong_old_pair','altered_archive','wrong_old_code_hash'])
def test_only_three_reviewed_roles_can_be_reconciled(prefix,tmp_path,target):
    s,old,new,prior,checkpoint,evidence,seen=prefix
    if target in {'audit_text','raw_source'}:
        key='variable_source_audit' if target=='audit_text' else 'source_file'
        path=Path(old.content.bindings[key]['path']);path.chmod(0o600);path.write_bytes(path.read_bytes()+b'changed')
    elif target=='unknown_code':
        path=tmp_path/'unknown.py';path.write_text('old');binding=record(path);path.write_text('new')
        for item in (prior,checkpoint):
            v=read(item);v['evidence'].append(binding);updated=save(Path(item['path']),v)
            if item==prior:prior=updated
    elif target=='missing_wrapper':new=replace(new,content=replace(new.content,bindings=old.content.bindings))
    elif target=='wrong_old_pair':
        b=dict(new.content.bindings);v=read(b['audit_equivalence']);v['previous']=b['audit_validator_upgrade'];b['audit_equivalence']=save(tmp_path/'wrong-wrapper.json',v);new=replace(new,content=replace(new.content,bindings=b))
    elif target=='altered_archive':
        b=dict(new.content.bindings);v=read(b['audit_equivalence']);archive=v['original_locator_code'];p=tmp_path/'archive.py.txt';p.write_bytes(Path(archive['path']).read_bytes());v['original_locator_code']=record(p);p.write_bytes(b'altered')
        b['audit_equivalence']=save(tmp_path/'altered-archive-wrapper.json',v);new=replace(new,content=replace(new.content,bindings=b))
    else:
        for item in (prior,checkpoint):
            v=read(item);v['evidence'][0]['sha256']='0'*64;updated=save(Path(item['path']),v)
            if item==prior:prior=updated
    with pytest.raises((ValueError,KeyError)):F.predecessor(s,prior,current=new)
