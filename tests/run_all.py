"""Runs the whole browser test suite: one server, every test, a pass/fail summary, non-zero exit on any failure.

    python tests/run_all.py                 all tests
    python tests/run_all.py start places    only these (names without the test_ prefix)
    python tests/run_all.py --list          show the tests

A test fails if it exits non-zero or prints a SOFTFAIL line (the older tests report failed checks that way).
Logs are written to tests/out/<name>.log; screenshots and PDFs land in tests/out/ too.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

ORDER = ["start", "places", "region", "addresses", "share", "labels", "step5", "step6", "step7", "step8", "step9",
         "step11", "trace", "meter", "header"]
TIMEOUT_S = 900


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main(argv):
    if "--list" in argv:
        print("\n".join(ORDER))
        return 0
    names = [a for a in argv if not a.startswith("-")] or ORDER
    unknown = [n for n in names if n not in ORDER]
    if unknown:
        print(f"unknown test(s): {', '.join(unknown)}; try --list")
        return 2
    port = free_port()
    server = subprocess.Popen([harness.PYTHON, harness.SERVER_SCRIPT, str(port)], cwd=harness.REPO,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = []
    try:
        harness.wait_until_reachable(port)
        print(f"server on http://127.0.0.1:{port}/ rooted at {harness.REPO}")
        env = dict(os.environ, WARN_TEST_PORT=str(port), PYTHONIOENCODING="utf-8")
        for name in names:
            script = Path(__file__).resolve().parent / f"test_{name}.py"
            log = Path(harness.OUT) / f"{name}.log"
            started = time.time()
            try:
                done = subprocess.run([harness.PYTHON, str(script)], cwd=harness.REPO, env=env, capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
                output, code = done.stdout + done.stderr, done.returncode
            except subprocess.TimeoutExpired as error:
                output, code = (error.stdout or "") + (error.stderr or "") + f"\nTIMEOUT after {TIMEOUT_S}s", 1
            log.write_text(output, encoding="utf-8")
            soft = output.count("SOFTFAIL")
            ok = code == 0 and soft == 0
            results.append((name, ok, code, soft, time.time() - started))
            print(f"{'PASS' if ok else 'FAIL'}  {name:<10} {time.time() - started:5.0f}s"
                  + ("" if ok else f"  (exit {code}, {soft} soft failure(s); see {log})"))
    finally:
        server.terminate()
    passed = sum(1 for r in results if r[1])
    print(f"\n{passed} of {len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
