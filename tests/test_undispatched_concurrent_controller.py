from pathlib import Path
import ast
import copy
import importlib.util
import json
import pytest
from scripts.fleet.coordinator import Coordinator

ROOT=Path(__file__).resolve().parents[1]
HOME=ROOT/'diagnostics/vm-fleet-20260912/control/undispatched-recovery-001'
PATH=HOME/'frozen-concurrent-002/implementation/scripts/fleet/launch_job.py'

def controller():
 spec=importlib.util.spec_from_file_location('undispatched_concurrent_controller',PATH);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_all_policy_and_spawn_functions_equal_reviewed_parent():
 m=controller();report=json.loads((HOME/'concurrent-controller-002.json').read_bytes())
 funcs=lambda p:{n.name:ast.dump(n,include_attributes=False)for n in ast.parse(Path(p).read_text()).body if isinstance(n,ast.FunctionDef)}
 assert funcs(PATH)==funcs(report['parent']['path'])
 for row in m.KNOWN_OTHERS.values():
  job=m.checked(row['job']);assert job['version']==row['version']and job['source']==row['source']
 for wb,row in m.CLOSED_TARGETS.items():
  plan=json.loads((HOME/f'{wb}-selection-001/launch-plan.json').read_bytes());assert all(plan[k]==row[k]for k in row)


@pytest.fixture
def state(tmp_path):
 m=controller();c=Coordinator(tmp_path/'coordinator.sqlite');other=m.KNOWN_OTHERS['0190'];target=m.checked(m.CLOSED_TARGETS['0173']['job'])
 c.enroll('0190',other['source']['sha256'],'fixture');c.reconcile('0190','fixture stopped');claim=c.claim('0190','vm7/slot2','old',other['version']);auth={k:claim[k]for k in ('workbook','owner','token','fence')}
 c.begin('model','fixture-other-call',request_sha256='b'*64,**auth)
 return m,c,target,auth


def test_admission_never_grants_a_second_model_slot(state):
 m,c,target,auth=state;assert m.operation_admission(c.status(),target)['model_permission_granted']is False
 c.enroll('0173',target['source']['sha256'],'fixture');c.reconcile('0173','fixture stopped');claim=c.claim('0173','vm4/slot2','new',target['version']);new={k:claim[k]for k in ('workbook','owner','token','fence')}
 result=c.begin('model','fixture-new-call',request_sha256='c'*64,**new)
 assert result['execute']is False and result['busy']is True
 assert len([op for op in c.status()['operations']if op['state']=='active'])==1


@pytest.mark.parametrize('change',['hold','publication','same','unknown','owner','fence','source','version','expired','terminal','duplicate'])
def test_uncertain_unbound_or_nonmodel_operation_blocks(state,change):
 m,c,target,_=state;s=copy.deepcopy(c.status());op=s['operations'][0];w=s['workbooks'][0]
 if change=='hold':op['state']='hold'
 elif change=='publication':op['kind']='publication'
 elif change=='same':op['workbook']='0173'
 elif change=='unknown':op['state']='unknown'
 elif change=='owner':op['owner']='different'
 elif change=='fence':op['fence']+=1
 elif change=='source':w['source_sha256']='f'*64
 elif change=='version':w['version']='unreviewed'
 elif change=='expired':op['expires']=s['at']-1
 elif change=='terminal':op['evidence']='uncertain'
 else:s['operations'].append(copy.deepcopy(op))
 with pytest.raises(ValueError):m.operation_admission(s,target)
