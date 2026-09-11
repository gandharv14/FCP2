"""Root dispatcher facade for fixed lane, notes and final-status operations.

No CLI: construct typed root-selected evidence and authority in trusted code.
No workbook/model/container jobs or immutable artifact publication occur here.
"""
from scripts.batch.harbor_ready_transitions import ReadyTransitions
from scripts.batch.harbor_failure_snapshot import FailureSnapshots


class HarborLaneLifecycle(ReadyTransitions, FailureSnapshots):
    """Complete/record, snapshot/begin repair, clear/resume/enqueue/finalize."""
