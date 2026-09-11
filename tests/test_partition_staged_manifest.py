"""Multiline manifest and shard IDs agree under the minimal staged S successor."""
from pathlib import Path
import importlib.util,sys

def test_staged_manifest_retains_entire_multiline_review_unit(tmp_path,monkeypatch):
    import scripts.batch as package
    import test_disclosure_partition_review as original
    from scripts.batch import harbor_disclosure_partition_evidence as E
    path=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/0184-partition-reviewed-overlay-v2/scripts/batch/harbor_semantic_readiness.py'
    spec=importlib.util.spec_from_file_location('scripts.batch.harbor_semantic_readiness',path)
    S=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,S);monkeypatch.setattr(package,'harbor_semantic_readiness',S)
    spec.loader.exec_module(S);monkeypatch.setattr(original,'S',S);monkeypatch.setattr(E,'semantic',S)
    original.test_multiline_disclosure_is_one_complete_review_unit(tmp_path)
