from pathlib import Path
import json
import pytest
from scripts.fleet import criterion_completed_tail as T


@pytest.fixture(autouse=True)
def proc_fixture(tmp_path,monkeypatch):
 root=tmp_path/'proc';root.mkdir();original=Path.iterdir
 def iterdir(path):return original(root if str(path)=='/proc'else path)
 monkeypatch.setattr(Path,'iterdir',iterdir)
 return root


def keep(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return T.binding(p)


def stopped(tmp):
 job=keep(tmp/'job.json',dict(workbook='0199',version='code',source={'sha256':'source'},run=str(tmp/'prior-worker')))
 worker_intent=keep(tmp/'worker-intent.json',dict(job=job))
 outer=keep(tmp/'outcome.json',dict(intent=worker_intent,status='RECONCILIATION_REQUIRED',failure_type='TimeoutError',returncode=None,automatic_retry=False))
 descriptor=keep(tmp/'descriptor.json',{});checkpoint=keep(tmp/'runtime-result.json',{})
 ops=tmp/'ops';d=ops/'002';d.mkdir(parents=True);intent=keep(d/'intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=checkpoint)))
 keep(d/'declared-runtime-adapter.json',{});(d/'step.log').write_bytes(b'')
 p=dict(prior_worker_outcome=outer,prior_worker_job=job,workbook='0199',version='code',source={'sha256':'source'},interrupted_intent=intent)
 return p,dict(operations=str(ops)),dict(descriptor=descriptor),checkpoint


def test_authentic_stopped_timeout_without_later_guard_evidence_is_inspectable(tmp_path):
 T.interrupted(*stopped(tmp_path))


@pytest.mark.parametrize('kind',['resource','outcome','log','different_failure','completed_outer','foreign_prior','changed_next_checkpoint'])
def test_partial_or_completed_owning_evidence_cannot_be_replayed(tmp_path,kind):
 p,prior,value,checkpoint=stopped(tmp_path);d=Path(prior['operations'])/'002'
 if kind in {'resource','outcome'}:keep(d/(kind+'.json'),{})
 elif kind=='log':(d/'step.log').write_text('owning child produced new evidence')
 elif kind in {'different_failure','completed_outer'}:
  old=T.read(p['prior_worker_outcome']);old['failure_type']='AdmissionError'if kind=='different_failure'else None
  p['prior_worker_outcome']=keep(Path(p['prior_worker_outcome']['path']),old)
 elif kind=='foreign_prior':p['prior_worker_job']=keep(tmp_path/'foreign.json',T.read(p['prior_worker_job']))
 else:checkpoint=keep(tmp_path/'other-checkpoint.json',{})
 with pytest.raises(ValueError):T.interrupted(p,prior,value,checkpoint)


def test_exact_old_child_argv_blocks_even_when_outer_has_stopped(tmp_path,proc_fixture):
 p,prior,value,checkpoint=stopped(tmp_path);child=proc_fixture/'777777';child.mkdir()
 (child/'stat').write_text('777777 (python) S 0 0')
 (child/'cmdline').write_bytes(('python\0_child\0'+p['interrupted_intent']['path']).encode())
 with pytest.raises(ValueError,match='still active'):T.interrupted(p,prior,value,checkpoint)
