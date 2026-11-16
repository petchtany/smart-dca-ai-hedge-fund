"""Silent Watchdog Server for Smart DCA Dashboard (port 8765).
Runs silently in the background via pythonw.exe and auto-restarts if interrupted.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def is_port_open(port: int = 8765) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    if is_port_open(8765):
        return

    from trade_plan import serve_api

    while True:
        try:
            if not is_port_open(8765):
                serve_api(8765)
        except Exception:
            pass
        time.sleep(3)


if __name__ == "__main__":
    main()
