"""Cheap production memory telemetry; allocation tracing is explicit profiling.

These observations do not enforce or replace the owning process resource guard.
Traced Python allocations and process RSS deliberately use separate fields.
"""
import sys
import time
import tracemalloc


def process_peak_rss_bytes():
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError):
        return None
    if sys.platform == "darwin":
        return int(peak)
    if sys.platform.startswith("linux"):
        return int(peak * 1024)
    return None


class VerificationMetrics:
    def __init__(self, *, trace_allocations=False):
        self.requested = trace_allocations
        self.result = None

    def __enter__(self):
        self.owned_trace = self.requested and not tracemalloc.is_tracing()
        if self.owned_trace:
            tracemalloc.start()
        self.tracing = tracemalloc.is_tracing()
        if self.tracing:
            self.memory_before, _ = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exception):
        runtime = time.perf_counter() - self.started
        peak = None
        try:
            if self.tracing:
                _, traced_peak = tracemalloc.get_traced_memory()
                peak = max(0, int(traced_peak - self.memory_before))
        finally:
            if self.owned_trace:
                tracemalloc.stop()
        self.result = {
            "verifier_runtime_s": runtime,
            "peak_memory_bytes": peak,
            "python_allocation_tracing_enabled": self.tracing,
            "process_peak_rss_bytes": process_peak_rss_bytes(),
            "process_peak_rss_scope": "process_lifetime",
        }
