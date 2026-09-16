"""Read-only monitoring of the already-dispatched, frozen audit evaluation."""
import argparse
import importlib
import json
import os
from pathlib import Path
import signal
import time
from datetime import datetime, timezone

import recursion_campaign as c
import recursion_hardening_campaign as campaign
import recursion_hardening_report as report
import recursion_hardening_task_report as task_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles', type=int, default=160)
    parser.add_argument('--interval-seconds', type=int, default=180)
    args = parser.parse_args()
    assert args.cycles > 0 and args.interval_seconds >= 30
    process_path = campaign.WORK / 'results/collector-process.json'
    process = {'pid': os.getpid(), 'script': str(Path(__file__).resolve()),
               'startedAt': datetime.now(timezone.utc).isoformat(),
               'status': 'running', 'readOnlyRemoteOperations': True}
    c.save(process_path, process)
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    errors = 0
    try:
        for _ in range(args.cycles):
            if stopping:
                break
            try:
                campaign.collect()
                importlib.reload(report).render()
                importlib.reload(task_report).render()
                errors = 0
            except RuntimeError as error:
                errors += 1
                print(json.dumps({'event': 'collection_read_error', 'consecutiveCycles': errors,
                                  'message': str(error)[:400]}), flush=True)
                transient = any(s in str(error) for s in (
                    'Internal server error', 'Service Unavailable', 'Gateway',
                    'timed out', 'ECONNRESET', 'ETIMEDOUT', 'fetch failed'))
                if not transient or errors >= 3:
                    raise
            if not errors and c.read(campaign.WORK / 'results/summary.json')['allAttemptsTerminal']:
                print(json.dumps({'event': 'all_active_attempts_terminal'}), flush=True)
                break
            deadline = time.monotonic() + (300 if errors else args.interval_seconds)
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(5, max(0, deadline - time.monotonic())))
    finally:
        process.update(status='stopped', stoppedAt=datetime.now(timezone.utc).isoformat())
        c.save(process_path, process)


if __name__ == '__main__':
    main()
