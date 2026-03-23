from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from app.heartbeat_monitor import env_int, heartbeat_failure_reason


def _terminate_child(child: subprocess.Popen[bytes], grace_sec: int) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    deadline = time.time() + max(1, grace_sec)
    while time.time() < deadline:
        if child.poll() is not None:
            return
        time.sleep(0.2)
    if child.poll() is None:
        child.kill()


def main() -> None:
    check_interval_sec = env_int("SUPERVISOR_CHECK_INTERVAL_SEC", 30)
    startup_grace_sec = env_int("SUPERVISOR_STARTUP_GRACE_SEC", 180)
    stop_grace_sec = env_int("SUPERVISOR_STOP_GRACE_SEC", 20)
    child = subprocess.Popen([sys.executable, "-m", "app.main"])
    started_at = time.time()
    stopping = False

    def _handle_signal(signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        if child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    while True:
        code = child.poll()
        if code is not None:
            raise SystemExit(code)

        if not stopping and (time.time() - started_at) >= startup_grace_sec:
            reason = heartbeat_failure_reason()
            if reason is not None:
                print(f"Supervisor restart: {reason}", file=sys.stderr, flush=True)
                _terminate_child(child, stop_grace_sec)
                raise SystemExit(1)

        time.sleep(max(5, check_interval_sec))


if __name__ == "__main__":
    main()
