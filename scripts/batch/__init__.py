"""Generic, fail-closed batch execution helpers."""

from .hardened_runner import (
    ConversionTimeout,
    LedgerError,
    ReadyRecord,
    ReadyRecordError,
    allocate_attempt_directory,
    convert_and_publish,
    discover_ready_records,
    publish_diagnostic_snapshot,
    reconcile_expected_ledger,
    run_ready_attempt,
    run_ready_batch,
    validate_xlsx,
)

__all__ = [
    "ConversionTimeout",
    "LedgerError",
    "ReadyRecord",
    "ReadyRecordError",
    "allocate_attempt_directory",
    "convert_and_publish",
    "discover_ready_records",
    "publish_diagnostic_snapshot",
    "reconcile_expected_ledger",
    "run_ready_attempt",
    "run_ready_batch",
    "validate_xlsx",
]
