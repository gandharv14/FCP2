from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_disclosure_round_adoption as A,harbor_lease_takeover as T
from scripts.fleet import disclosure_adoption_continue as S
from scripts.batch.harbor_admission import read,record
from test_root_lease_takeover import setup,save

@pytest.mark.parametrize('defect',[None,'foreign_source','foreign_selection','altered_before','altered_after','live_drift'])
def test_actual_completed_takeover_replaces_expired_adoption_custody(setup,tmp_path,defect):
    selected,d,before,old=setup;lease,complete=T.replace_expired(selected,d,clock=lambda:120)
    done=read(complete);stop=read(done['stopped_owner_proof']);worker=SimpleNamespace(context=SimpleNamespace(tracker_root=Path(selected.tracker['path']).parent))
    plan=save(tmp_path/'round.json',{});result=save(tmp_path/'result.json',{})
    tp=dict(schema_version='harbor-allowance-takeover-direct/v1',selection=T._selection_value(selected),shared=dict(takeover=record(T.__file__),lifecycle=record(T.life.__file__)))
    if defect=='foreign_selection':tp['selection']['owner']='different'
    takeover_plan=save(tmp_path/'takeover-plan.json',tp)
    authority_value=dict(adoption=record(A.__file__),context=dict(plan=plan),round_result=result,source=before['bindings']['original_raw_source'],workbook=before['workbook_id'],owner=lease.owner,renewal_module=None,completed_takeover=dict(runtime=record(T.__file__),plan=takeover_plan,completion=complete))
    if defect=='foreign_source':authority_value['source']={**authority_value['source'],'sha256':'f'*64}
    authority=save(tmp_path/'authority.json',authority_value)
    certificate=save(tmp_path/'certificate.json',dict(schema_version=A.TAKEOVER,implementation=record(A.__file__),authority=authority,round_plan=plan,round_result=result,module=record(T.__file__),completion=complete,before=stop['before'],after=done['after'],historical_records=[],historical_instruction=None,takeover_plan=takeover_plan,layout_runtime=record(T.__file__)))
    if defect=='altered_before':Path(stop['before']['path']).write_text('{}')
    elif defect=='altered_after':Path(done['after']['path']).write_text('{}')
    elif defect=='live_drift':Path(selected.tracker['path']).write_text('{}')
    if defect is None:
        assert A.validate_renewal(certificate,worker,lease,plan,result,live=True)==read(certificate)
        assert S.takeover_custody(authority_value,worker,old,stop['before'])==(lease,stop['before'],done['after'])
        assert old.token!=lease.token and old.owner!=lease.owner
    else:
        with pytest.raises((ValueError,FileNotFoundError)):A.validate_renewal(certificate,worker,lease,plan,result,live=True)
