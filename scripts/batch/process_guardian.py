"""Private POSIX launch/parent-liveness protocol; stdlib only, no workbook access.

A command cannot execute until its dedicated process group is registered with
an independent guardian. The controller alone owns the liveness pipe writer.
"""
from __future__ import annotations

import errno
import os
import select
import signal
import sys
import time


def alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(group: int, grace: float) -> None:
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + max(0.0, grace)
    while alive(group) and time.monotonic() < deadline:
        time.sleep(0.01)
    if alive(group):
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass


def guard(read_fd: int, ack_fd: int, grace: float) -> None:
    # Registration is intentionally tiny. An incomplete registration means no
    # GO was sent: the launcher independently exits on its start-pipe EOF.
    registration = bytearray()
    while not registration.endswith(b"\n"):
        char = os.read(read_fd, 1)
        if not char:
            return
        registration.extend(char)
        if len(registration) > 32:
            raise ValueError("invalid guardian registration")
    group = int(registration)
    if group <= 1 or group == os.getpgrp():
        raise ValueError("invalid guarded process group")
    os.write(ack_fd, b"R")
    os.close(ack_fd)
    while True:
        readable, _, _ = select.select([read_fd], [], [], 0.1)
        if readable:
            action = os.read(read_fd, 1)
            if action == b"D":
                return  # Explicit disarm after the whole group has stopped.
            terminate(group, grace)  # EOF or malformed protocol fails closed.
            return
        if not alive(group):
            return  # Do not retain a stale, potentially reusable group ID.


def launch(gate_fd: int, error_fd: int, command: list[str]) -> None:
    # Both descriptors must disappear on exec. The liveness writer was never
    # inherited, so grandchildren cannot accidentally keep the watchdog alive.
    os.set_inheritable(error_fd, False)
    permitted = os.read(gate_fd, 1) == b"G"
    os.close(gate_fd)
    if not permitted:
        return
    try:
        os.execvpe(command[0], command, os.environ)
    except OSError as error:
        os.write(error_fd, str(error.errno or errno.EIO).encode("ascii"))
        os.close(error_fd)
        raise SystemExit(127)


def main() -> None:
    mode = sys.argv[1]
    if mode == "guard":
        guard(int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]))
    elif mode == "launch":
        launch(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:])
    else:
        raise ValueError("unknown guardian mode")


if __name__ == "__main__":
    main()
