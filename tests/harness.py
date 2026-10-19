"""Shared helpers for the browser tests: repo paths, the test server, and output locations.

Nothing here knows where the repo lives. REPO is two levels up from this file (override with WARN_REPO to test
another checkout, e.g. an export of an older commit), and every test output goes to tests/out/ (git-ignored).

Server: run_all.py starts one server and exports WARN_TEST_PORT; a test run on its own (python tests/test_x.py)
starts and stops its own server on its default port instead.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = os.environ.get("WARN_REPO") or str(HERE.parent)
OUT = str(HERE / "out")
SHOTS = OUT          # screenshots, PDFs and logs all land in tests/out/
SCRATCH = OUT
PYTHON = sys.executable
CHANNEL = os.environ.get("WARN_TEST_CHANNEL", "msedge") or None   # "" = Playwright's bundled Chromium
SERVER_SCRIPT = str(HERE / "rangeserve.py")
os.makedirs(OUT, exist_ok=True)


def port_for(default):
    """The shared server's port when run_all.py set one, else this test's own default."""
    return int(os.environ.get("WARN_TEST_PORT") or default)


def base_url(default_port):
    return f"http://127.0.0.1:{port_for(default_port)}"


def wait_until_reachable(port, timeout=30.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as error:  # still starting
            last = error
        time.sleep(0.25)
    raise RuntimeError(f"test server on port {port} never became reachable: {last}")


class _SharedServer:
    """run_all.py owns the server; a test that finds WARN_TEST_PORT set has nothing to start or stop."""

    def terminate(self):
        pass

    def wait(self, timeout=None):
        pass


def start_server(port):
    """Starts the threaded Range-capable server rooted at REPO and waits until it answers; returns a handle with
    terminate(). Under run_all.py it returns a no-op handle for the shared server."""
    if os.environ.get("WARN_TEST_PORT"):
        return _SharedServer()
    proc = subprocess.Popen([PYTHON, SERVER_SCRIPT, str(port)], cwd=REPO,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_until_reachable(port)
    return proc
