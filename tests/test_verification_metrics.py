import tracemalloc

import pytest

from xl_seg.verification_metrics import VerificationMetrics
from replay_segmentation_verification import check_rollout_performance


def test_production_metrics_do_not_start_tracing_or_fabricate_allocation_peak():
    assert not tracemalloc.is_tracing()
    with VerificationMetrics() as metrics:
        values = [bytearray(1024) for _ in range(20)]
        assert not tracemalloc.is_tracing()
    assert values and metrics.result["peak_memory_bytes"] is None
    assert metrics.result["python_allocation_tracing_enabled"] is False
    assert metrics.result["process_peak_rss_scope"] == "process_lifetime"
    assert metrics.result["process_peak_rss_bytes"] > 0
    baseline = {"verifier_runtime_p95_s": 1, "peak_memory_bytes": 1000}
    gate = check_rollout_performance(
        {"benchmark": {**metrics.result, "verifier_runtime_p95_s": 0.1}}, baseline
    )
    assert gate["passed"] is False
    assert "current.peak_memory_bytes" in gate["missing_metrics"]


def test_opted_in_tracing_records_allocations_and_stops_after_exception():
    with pytest.raises(RuntimeError), VerificationMetrics(trace_allocations=True) as metrics:
        assert tracemalloc.is_tracing()
        values = [bytearray(1024) for _ in range(100)]
        raise RuntimeError("original failure")
    assert values and not tracemalloc.is_tracing()
    assert metrics.result["peak_memory_bytes"] >= 100 * 1024
    assert metrics.result["python_allocation_tracing_enabled"] is True


def test_external_profiler_remains_enabled():
    tracemalloc.start()
    try:
        with VerificationMetrics() as metrics:
            values = [bytearray(1024) for _ in range(10)]
        assert values and tracemalloc.is_tracing()
        assert metrics.result["python_allocation_tracing_enabled"] is True
        assert metrics.result["peak_memory_bytes"] >= 10 * 1024
    finally:
        tracemalloc.stop()
