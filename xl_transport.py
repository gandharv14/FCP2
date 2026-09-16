"""Bounded transport retries; response parsing and semantic review stay outside."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
from contextlib import contextmanager
import fcntl
import http.client
import math
import os
from pathlib import Path
import socket
import ssl
import time
import urllib.error
import urllib.request


@contextmanager
def _local_model_slot():
    """Hold a campaign-wide process lock for one complete model operation.

    The root dispatcher supplies the same absolute directory to every worker.
    File descriptors release slots on exceptions or process death. Never delete
    or replace the slot files while a campaign is running.
    """
    configured = os.environ.get("HARBOR_MODEL_SLOT_ROOT")
    if configured is None:
        yield
        return
    root = Path(configured)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("model slot root must be an absolute path without symlinks")
    capacity = os.environ.get("HARBOR_MODEL_SLOTS", "2")
    if capacity not in {"1", "2"}:
        raise ValueError("model concurrency must be one or two")
    root.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    # Pin the cap once under a separate lock. Mixed worker configurations must
    # fail instead of quietly acquiring different sets of slots.
    config_fd = os.open(root / "capacity", flags, 0o600)
    try:
        fcntl.flock(config_fd, fcntl.LOCK_EX)
        current = os.read(config_fd, 32)
        expected = (capacity + "\n").encode("ascii")
        if current and current != expected:
            raise ValueError("model concurrency differs from the campaign capacity")
        if not current:
            os.write(config_fd, expected)
            os.fsync(config_fd)
    finally:
        os.close(config_fd)
    # A prospective shared host gate replaces only physical serialization.
    # Validate the original campaign capacity above so historical evidence is
    # still truthful; never rewrite its capacity file or model request plans.
    from scripts.fleet import physical_model_capacity
    physical = physical_model_capacity.selected()
    if physical is not None:
        with physical_model_capacity.slot(physical):
            yield
        return
    held = None
    try:
        while held is None:
            for index in range(int(capacity)):
                fd = os.open(root / f"slot-{index}.lock", flags, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                except BaseException:
                    os.close(fd)
                    raise
                else:
                    held = fd
                    break
            if held is None:
                time.sleep(0.1)
        yield
    finally:
        if held is not None:
            os.close(held)


@contextmanager
def model_slot(*, operation_id=None, request_sha256=None, outcome_path=None):
    """Optional durable fleet gate supplements the original local process lock."""
    from scripts.fleet import model_gate
    with _local_model_slot():
        if model_gate.configured():
            if not all((operation_id, request_sha256, outcome_path)):
                raise ValueError('fleet model calls require durable operation and outcome identity')
            with model_gate.slot(operation_id=operation_id,request_sha256=request_sha256,outcome_path=outcome_path):
                yield
        else:
            yield


def read_response(request, *, timeout):
    """Hold the configured fleet slot across one request and its transport retries."""
    from scripts.fleet import model_gate
    if model_gate.configured():
        return model_gate.read_response(request,timeout=timeout,local_slot=_local_model_slot)
    with model_slot():
        return _read_response(request, timeout=timeout)


def _transient_open_failure(exc):
    """Retry only typed transient failures before a response is available."""
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(cause, (ssl.SSLError, http.client.HTTPException)):
        return False
    if isinstance(cause, socket.gaierror):
        return cause.errno == socket.EAI_AGAIN
    return isinstance(cause, (TimeoutError, ConnectionRefusedError,
                              ConnectionResetError, ConnectionAbortedError,
                              BrokenPipeError))


def _read_response(request, *, timeout):
    """Retry transient opens at most three times; never replay a failed read."""
    for attempt in range(4):
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                # HTTPError owns a response stream even when no retry follows.
                # Retaining the exception must not retain an open connection.
                exc.close()
                raise
            delay = 2 ** (attempt + 1)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after:
                try:
                    parsed = float(retry_after)
                except ValueError:
                    try:
                        parsed = parsedate_to_datetime(retry_after).timestamp() - time.time()
                    except (ValueError, TypeError, OverflowError):
                        parsed = delay
                if math.isfinite(parsed):
                    delay = parsed
            exc.close()
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if attempt == 3 or not _transient_open_failure(exc):
                raise
            delay = 2 ** (attempt + 1)
        else:
            # Once reading starts the provider may have completed the model
            # operation. Propagate partial-response failures without resending.
            with response:
                return response.read()
        time.sleep(min(60, max(0, delay)))
