from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.heartbeat_monitor import env_int, heartbeat_failure_reason


class HeartbeatMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env_patch = patch.dict(
            os.environ,
            {
                "HEARTBEAT_INTERVAL_SEC": "60",
                "HEARTBEAT_STALE_SEC": "300",
                "SUPERVISOR_HEARTBEAT_TIMEOUT_SEC": "420",
                "SUPERVISOR_MAX_IDLE_SEC": "600",
            },
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def _heartbeat_file(self) -> Path:
        return Path(self.temp_dir.name) / "poopbot_heartbeat.json"

    def _write_heartbeat(self, *, last_received: int, last_handled: int, fake_now: float = 10_000.0, age_sec: int = 0) -> None:
        path = self._heartbeat_file()
        path.write_text(
            json.dumps(
                {
                    "ts_utc": "2026-03-23T00:00:00+00:00",
                    "last_received_sec": last_received,
                    "last_handled_sec": last_handled,
                }
            ),
            encoding="utf-8",
        )
        mtime = fake_now - age_sec
        os.utime(path, (mtime, mtime))

    def test_env_int_uses_default_for_invalid_values(self) -> None:
        with patch.dict(os.environ, {"X": "oops"}, clear=False):
            assert env_int("X", 7) == 7

    def test_heartbeat_failure_reason_is_missing_without_file(self) -> None:
        with patch("app.heartbeat_monitor.tempfile.gettempdir", return_value=self.temp_dir.name):
            assert heartbeat_failure_reason() == "heartbeat_missing"

    def test_heartbeat_failure_reason_reports_stale_file(self) -> None:
        self._write_heartbeat(last_received=10, last_handled=10, age_sec=500)
        with patch("app.heartbeat_monitor.tempfile.gettempdir", return_value=self.temp_dir.name):
            with patch("app.heartbeat_monitor.time.time", return_value=10_000.0):
                assert heartbeat_failure_reason() == "heartbeat_stale age=500 timeout=420"

    def test_heartbeat_failure_reason_reports_polling_idle(self) -> None:
        self._write_heartbeat(last_received=700, last_handled=700, age_sec=10)
        with patch("app.heartbeat_monitor.tempfile.gettempdir", return_value=self.temp_dir.name):
            with patch("app.heartbeat_monitor.time.time", return_value=10_000.0):
                assert heartbeat_failure_reason() == "polling_idle last_received=700 last_handled=700 limit=600"

    def test_heartbeat_failure_reason_ignores_partial_idle(self) -> None:
        self._write_heartbeat(last_received=700, last_handled=30, age_sec=10)
        with patch("app.heartbeat_monitor.tempfile.gettempdir", return_value=self.temp_dir.name):
            with patch("app.heartbeat_monitor.time.time", return_value=10_000.0):
                assert heartbeat_failure_reason() is None

    def test_heartbeat_failure_reason_treats_broken_json_as_missing(self) -> None:
        path = self._heartbeat_file()
        path.write_text("{broken", encoding="utf-8")
        os.utime(path, (10_000.0, 10_000.0))
        with patch("app.heartbeat_monitor.tempfile.gettempdir", return_value=self.temp_dir.name):
            with patch("app.heartbeat_monitor.time.time", return_value=10_000.0):
                assert heartbeat_failure_reason() == "heartbeat_missing"

    def test_heartbeat_failure_reason_is_none_for_fresh_active_heartbeat(self) -> None:
        self._write_heartbeat(last_received=30, last_handled=20, age_sec=10)
        with patch("app.heartbeat_monitor.tempfile.gettempdir", return_value=self.temp_dir.name):
            with patch("app.heartbeat_monitor.time.time", return_value=10_000.0):
                assert heartbeat_failure_reason() is None


if __name__ == "__main__":
    unittest.main()
