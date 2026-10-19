import os
"""Live site check: type "safeway" into the Destination slot on a phone-sized viewport."""
import json
import sys

from playwright.sync_api import sync_playwright

URL = "https://keeneyeapps.github.io/warn-map/"


def type_destination(page, text):
    page.locator("#slot-destination").click()
    page.locator("#address-sheet").wait_for(state="visible")
    page.locator("#address-input").fill("")
    page.locator("#address-input").type(text, delay=40)
    page.wait_for_function(
        "() => document.querySelectorAll('.address-suggestion').length > 0"
        " || /No matching|unavailable/.test(document.querySelector('#address-sheet-status').textContent)",
        timeout=20000)
    page.wait_for_timeout(500)
    rows = page.locator(".address-suggestion").all_inner_texts()
    status = page.locator("#address-sheet-status").inner_text()
    page.keyboard.press("Escape")
    return rows, status


def main():
    out, console, requests = {}, [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get("WARN_TEST_CHANNEL", "msedge") or None, headless=True)
        context = browser.new_context(**pw.devices["Pixel 7"])
        page = context.new_page()
        page.on("console", lambda m: console.append((m.type, m.text[:200])) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: console.append(("pageerror", str(e)[:300])))
        page.on("response", lambda r: requests.append((r.status, r.url.replace(URL, "/"), r.from_service_worker))
                if any(k in r.url for k in ("addresses", "places", "region", "sw.js")) else None)
        page.goto(URL)
        page.wait_for_function("() => document.querySelector('#slot-destination')", timeout=60000)
        page.wait_for_load_state("networkidle")
        out["first load"] = type_destination(page, "safeway")
        out["sw"] = page.evaluate("""async () => {
          await navigator.serviceWorker.ready;
          const reg = await navigator.serviceWorker.getRegistration();
          return { script: reg?.active?.scriptURL, caches: await caches.keys() };
        }""")
        page.reload()
        page.wait_for_load_state("networkidle")
        out["after reload"] = type_destination(page, "safeway")
        out["requests"] = requests
        out["console"] = console
        browser.close()
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
