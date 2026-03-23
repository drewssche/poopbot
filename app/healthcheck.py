from __future__ import annotations

import sys

from app.heartbeat_monitor import heartbeat_failure_reason


def main() -> None:
    sys.exit(0 if heartbeat_failure_reason() is None else 1)


if __name__ == "__main__":
    main()
