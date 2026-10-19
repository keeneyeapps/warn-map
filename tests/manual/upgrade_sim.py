"""Simulate a returning phone: load an old build at one origin, swap that origin to the new build, reload."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

OLD, NEW = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from harness import CHANNEL, PYTHON, SERVER_SCRIPT, wait_until_reachable  # noqa: E402

PORT = 8770
URL = f"http://127.0.0.1:{PORT}/"


def serve(root):
    proc = subprocess.Popen([PYTHON, SERVER_SCRIPT, str(PORT)], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_until_reachable(PORT)
    return proc


def state(page):
    return page.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return { active: reg?.active?.scriptURL?.replace(location.origin, ""),
               waiting: reg?.waiting?.scriptURL?.replace(location.origin, "") || null,
               installing: reg?.installing?.scriptURL?.replace(location.origin, "") || null,
               caches: await caches.keys(),
               hasPlacesCode: !!(await (await fetch("js/addresses.js")).text()).includes("PLACES_URL") };
    }""")


def search(page, text):
    page.locator("#slot-destination").click()
    page.locator("#address-sheet").wait_for(state="visible")
    page.locator("#address-input").fill("")
    page.locator("#address-input").type(text, delay=30)
    page.wait_for_function(
        "() => document.querySelectorAll('.address-suggestion').length > 0"
        " || /No matching|unavailable/.test(document.querySelector('#address-sheet-status').textContent)",
        timeout=20000)
    page.wait_for_timeout(300)
    rows = page.locator(".address-suggestion").all_inner_texts() or [page.locator("#address-sheet-status").inner_text()]
    page.keyboard.press("Escape")
    return rows


def main():
    out = {}
    profile = tempfile.mkdtemp(prefix="warn-upgrade-")
    with sync_playwright() as pw:
        proc = serve(OLD)
        context = pw.chromium.launch_persistent_context(profile, channel=CHANNEL, headless=True,
                                                         viewport={"width": 390, "height": 844})
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_function("async () => !!(await navigator.serviceWorker.ready).active", timeout=60000)
        page.wait_for_timeout(2000)
        out["old build"] = state(page)
        context.close()
        proc.terminate()
        proc.wait()
        time.sleep(2)

        proc = serve(NEW)
        context = pw.chromium.launch_persistent_context(profile, channel=CHANNEL, headless=True,
                                                         viewport={"width": 390, "height": 844})
        page = context.pages[0] if context.pages else context.new_page()
        for visit in range(1, 4):
            page.goto(URL)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(4000)
            out[f"new build, visit {visit}"] = {**state(page), "safeway": search(page, "safeway")[:3]}
        context.close()
        proc.terminate()
    shutil.rmtree(profile, ignore_errors=True)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
