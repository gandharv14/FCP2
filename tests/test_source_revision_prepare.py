"""The data preparer preserves unrelated original runtime functions."""
from pathlib import Path
import ast
import pytest
from scripts.fleet.source_revision_prepare import source_dispatch,producer_dispatch,lease_dispatch


def functions(text):
 return {n.name:ast.dump(n)for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}


@pytest.mark.parametrize('filename,transform,changed',[
 ('harbor_disclosure_revision.py',source_dispatch,{'validate','validate_task','validate_review_history'}),
 ('harbor_instruction_lease_renewal.py',lease_dispatch,{'_retained_lease_adapter'}),
])
def test_selected_hooks_preserve_all_other_original_runtime_functions(filename,transform,changed):
 root=Path(__file__).resolve().parents[1]
 original=(root/'scripts/batch'/filename).read_text()
 # Current checked-in readers may already contain the supported dispatch.
 selected=transform(original)
 a,b=functions(original),functions(selected)
 assert set(a)==set(b)
 assert {n for n in a if a[n]!=b[n]}<=changed
 assert transform(selected)==selected


def test_changed_original_worker_boundary_is_not_silently_rewritten():
 with pytest.raises(ValueError,match='original task producer boundary'):
  producer_dispatch('def task(worker, selection, tracker):\n    return something_else()\n')


def test_original_producer_redirect_preserves_other_task_arguments_and_reader():
 original="def task(worker, selection, tracker):\n    return producer.Task(tracker, selection.stage, worker.run/'producer', selection.policy)\n\ndef read_original(x):\n    return x['evidence']\n"
 selected=producer_dispatch(original)
 assert functions(original)['read_original']==functions(selected)['read_original']
 assert "producer.Task(tracker, selection.stage, producer_run(worker), selection.policy)"in selected
 assert producer_dispatch(selected)==selected
