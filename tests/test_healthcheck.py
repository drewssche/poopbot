from __future__ import annotations

import unittest
from unittest.mock import patch

from app import healthcheck


class HealthcheckTests(unittest.TestCase):
    def test_main_exits_zero_when_healthy(self) -> None:
        with patch("app.healthcheck.heartbeat_failure_reason", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                healthcheck.main()
        assert ctx.exception.code == 0

    def test_main_exits_one_when_unhealthy(self) -> None:
        with patch("app.healthcheck.heartbeat_failure_reason", return_value="heartbeat_stale"):
            with self.assertRaises(SystemExit) as ctx:
                healthcheck.main()
        assert ctx.exception.code == 1


if __name__ == "__main__":
    unittest.main()
