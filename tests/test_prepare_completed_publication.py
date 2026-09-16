from pathlib import Path
import ast
from scripts.fleet.prepare_completed_publication import completed_probe

def test_completed_probe_uses_selected_history_and_closes_before_success():
 root=Path(__file__).resolve().parents[1]
 template=(root/'diagnostics/vm-fleet-20260912/control/source-first/0010/publication-001/native-ready-base-001/probe.py').read_text()
 text=completed_probe(template,{'path':'/publisher','sha256':'x'},{'path':'/plan','sha256':'y'})
 ast.parse(text)
 assert "cert['roots']"not in text and 'selected_history_survey(plan)'in text
 assert text.index('D=pub.fresh_publication.dispatcher')<text.index('pub.publication_only()')
 assert text.index('P.ready._proof')<text.index('D._model_publication_manager.__exit__')<text.index("print(json.dumps(dict(status='ACTUAL_READY")
 assert 'P.schema.cas_mutate=no'in text

def test_aggregate_probe_closes_both_selected_scopes_after_original_proof():
 root=Path(__file__).resolve().parents[1]
 template=(root/'diagnostics/vm-fleet-20260912/control/source-first/0010/publication-001/native-ready-base-001/probe.py').read_text()
 text=completed_probe(template,{'path':'/publisher','sha256':'x'},{'path':'/plan','sha256':'y'},aggregate=True)
 ast.parse(text)
 assert 'D._model_publication_manager'not in text
 assert text.index('P.ready._proof')<text.index('D._aggregate_publication_scope.__exit__')<text.index('D._aggregate_publication_manager.__exit__')<text.index("print(json.dumps(dict(status='ACTUAL_READY")
 assert 'P.schema.cas_mutate=no'in text

def test_source_revision_probe_selects_actual_reader_before_qualification():
 import pytest
 root=Path(__file__).resolve().parents[1]
 template=(root/'diagnostics/vm-fleet-20260912/control/source-first/0010/publication-001/native-ready-base-001/probe.py').read_text()
 text=completed_probe(template,{'path':'/publisher','sha256':'x'},{'path':'/plan','sha256':'y'},source_revision=True)
 ast.parse(text)
 assert 'D._model_publication_manager'not in text and 'D._aggregate_publication_manager'not in text
 assert text.index('reader_evidence(pub,plan)')<text.index('publication_child_adapter.select')<text.index('P.ready._proof')
 assert text.index('P.ready._proof')<text.index('D._disclosure_publication_scope.__exit__')<text.index('D._disclosure_publication_manager.__exit__')<text.index("print(json.dumps(dict(status='ACTUAL_READY")
 with pytest.raises(ValueError,match='one completed'):
  completed_probe(template,{}, {},aggregate=True,source_revision=True)

def test_semantic_probe_checks_cold_adapter_and_closes_retained_roots():
 import pytest
 root=Path(__file__).resolve().parents[1]
 template=(root/'diagnostics/vm-fleet-20260912/control/source-first/0010/publication-001/native-ready-base-001/probe.py').read_text()
 text=completed_probe(template,{'path':'/publisher','sha256':'x'},{'path':'/plan','sha256':'y'},semantic=True)
 ast.parse(text)
 assert 'D._model_publication_manager'not in text
 assert text.index('publication_child_adapter.select')<text.index('P.ready._proof')<text.index('D._semantic_publication_scope.__exit__')<text.index('D._semantic_publication_manager.__exit__')
 with pytest.raises(ValueError):completed_probe(template,{}, {},aggregate=True,semantic=True)


def test_derivative_probe_reads_actual_ready_and_closes_actual_p_scope():
 import pytest
 root=Path(__file__).resolve().parents[1]
 template=(root/'diagnostics/vm-fleet-20260912/control/source-first/0010/publication-001/native-ready-base-001/probe.py').read_text()
 text=completed_probe(template,{'path':'/publisher','sha256':'x'},{'path':'/plan','sha256':'y'},derivative=True)
 ast.parse(text)
 assert "ready=pub.fresh_publication.ready_value(pub,plan)"in text
 assert 'D._model_publication_manager'not in text
 assert text.index('publication_child_adapter.select')<text.index('P.ready._proof')<text.index('D._source_revision_publication_scope.__exit__')<text.index('D._derivative_context_manager.__exit__')
 with pytest.raises(ValueError):completed_probe(template,{}, {},semantic=True,derivative=True)
