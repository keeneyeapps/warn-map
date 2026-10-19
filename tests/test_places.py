"""Named places in destination entry: ranking, place-set slots, tap-to-call, share text, offline."""
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8769)
BASE = f"http://127.0.0.1:{PORT}/?test=1"
FIX = {"latitude": 60.5544, "longitude": -151.2583}  # Kenai, west of the Kenai Safeway

INIT = """
(() => {
  window.__shared = [];
  navigator.share = (data) => { window.__shared.push(data.text); return Promise.resolve(); };
  const fix = { coords: { latitude: %(latitude)s, longitude: %(longitude)s, accuracy: 10, heading: null, speed: null },
                timestamp: Date.now() };
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: {
      getCurrentPosition(success) { setTimeout(() => success(fix), 10); },
      watchPosition(success) { setTimeout(() => success(fix), 10); return 1; },
      clearWatch() {},
    },
  });
})();
""" % FIX


def serve():
    return start_server(PORT)


def suggestions_for(page, slot, text):
    page.locator(f"#slot-{slot}").click()
    page.locator("#address-sheet").wait_for(state="visible")
    page.locator("#address-input").fill("")
    page.locator("#address-input").type(text, delay=25)
    page.wait_for_function(
        "() => document.querySelectorAll('.address-suggestion').length > 0"
        " || /No matching/.test(document.querySelector('#address-sheet-status').textContent)", timeout=15000)
    page.wait_for_timeout(300)
    return page.locator(".address-suggestion").all_inner_texts()


def dial_links(page):
    return page.evaluate("""() => [...document.querySelectorAll('a[href]')]
      .map((a) => a.getAttribute('href')).filter((h) => /^(tel|sms|mailto|callto|facetime):/i.test(h))""")


def main():
    out, console, external, failures = {}, [], [], []
    phase = ["online"]

    def check(ok, what):
        if not ok:
            failures.append(what)

    proc = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_init_script(INIT)
            context.on("request", lambda req: external.append((req.method, req.url.split("?")[0]))
                       if urlparse(req.url).hostname not in ("localhost", "127.0.0.1")
                       and not req.url.startswith(("data:", "blob:")) else None)
            page = context.new_page()
            page.on("console", lambda m: console.append((phase[0], m.type, m.text[:160])) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append((phase[0], "pageerror", str(e))))
            page.goto(BASE)
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
            page.wait_for_load_state("networkidle")

            central = suggestions_for(page, "destination", "central pen")
            out["central pen"] = central
            check(central and central[0] == "Central Peninsula Hospital · Hospital, Soldotna", "central pen first")
            page.keyboard.press("Escape")

            post = suggestions_for(page, "destination", "soldotna post")
            out["soldotna post"] = post
            check(post and post[0] == "Soldotna Post Office · Post office, Soldotna", "soldotna post first")
            page.keyboard.press("Escape")

            no_location = suggestions_for(page, "destination", "safeway")
            out["safeway (no location)"] = no_location
            page.keyboard.press("Escape")
            page.locator("#locate").click()
            page.wait_for_timeout(1500)
            safeway = suggestions_for(page, "destination", "safeway")
            out["safeway (location in Kenai)"] = safeway
            stores = [s for s in safeway if s.startswith("Safeway · Grocery, ")]
            check(stores[:2] == ["Safeway · Grocery, Kenai", "Safeway · Grocery, Soldotna"], "safeway stores nearest first")

            # Choose the Kenai Safeway: slot shows the place name, the plan row has a tap-to-call link.
            page.locator(".address-suggestion", has_text="Safeway · Grocery, Kenai").click()
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            out["destination slot"] = page.locator("#slot-destination").get_attribute("aria-label")
            check(out["destination slot"] == "Destination: Safeway", "slot shows place name")
            out["destination row"] = page.locator(".route-leg-destination .route-leg-label").inner_text()
            check(out["destination row"] == "Destination · Safeway", "plan row shows place name")
            call = page.locator(".route-leg-destination .route-leg-call")
            out["row call link"] = [call.inner_text(), call.get_attribute("href"), call.get_attribute("aria-label")]
            check(out["row call link"][1] == "tel:+19072836300", "row tel link")

            # A stop from a place without a phone: no call link.
            hospital = suggestions_for(page, "stops", "central pen")
            page.locator(".address-suggestion").first.click()
            page.wait_for_function("() => document.querySelectorAll('.route-leg-distance').length === 2", timeout=30000)
            out["stop row"] = page.locator(".route-leg-stop .route-leg-label").inner_text()
            out["stop call links"] = page.locator(".route-leg-stop .route-leg-call").count()

            # Marker popup: name, type, address, call link.
            page.locator(".route-marker-destination").dispatch_event("click")
            page.locator(".place-popup").wait_for(timeout=5000)
            out["popup"] = page.locator(".place-popup").inner_text().split("\n")
            out["popup call href"] = page.locator(".place-popup .place-call").get_attribute("href")
            check(out["popup call href"] == "tel:+19072836300", "popup tel link")
            out["dial links on page"] = dial_links(page)
            # The Safeway row and popup, and the hospital stop row (its phone comes from places_overrides).
            check(sorted(out["dial links on page"]) == ["tel:+19072836300", "tel:+19072836300", "tel:+19077144404"],
                  "only the place rows and popup call links dial")
            page.screenshot(path=os.path.join(SHOTS, "places-call-link.png"))
            page.locator(".maplibregl-popup-close-button").dispatch_event("click")

            page.locator(".route-share-button").click()
            page.wait_for_function("() => window.__shared.length === 1")
            out["share first line"] = page.evaluate("() => window.__shared[0].split('\\n')[0]")
            check(out["share first line"].endswith("Central Peninsula Hospital → Safeway"), "share uses place names")

            kalif = suggestions_for(page, "destination", "35235 kalif")
            out["35235 kalif"] = kalif
            check(kalif and kalif[0] == "35235 Kalifornsky Beach Rd, Kenai", "address first")
            page.keyboard.press("Escape")

            # Offline after one load.
            page.wait_for_function("""async () => {
              const name = (await caches.keys()).find((n) => n.startsWith("warn-map-shell-"));
              if (!name) return false;
              const keys = (await (await caches.open(name)).keys()).map((r) => new URL(r.url).pathname);
              return keys.some((p) => p.endsWith("-places.json")) && keys.some((p) => p.endsWith("-addresses.json"));
            }""", timeout=60000)
            out["cache"] = page.evaluate("""async () => {
              const name = (await caches.keys()).find((n) => n.startsWith("warn-map-shell-"));
              const keys = (await (await caches.open(name)).keys()).map((r) => new URL(r.url).pathname);
              return [name, keys.filter((p) => p.endsWith(".json") && !p.startsWith("/vendor/")).sort()];
            }""")
            context.set_offline(True)
            phase[0] = "offline"
            page.reload()
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
            offline = suggestions_for(page, "destination", "central pen")
            out["offline central pen"] = offline
            page.keyboard.press("Escape")
            check(offline and offline[0] == "Central Peninsula Hospital · Hospital, Soldotna", "offline places")
            offline_kalif = suggestions_for(page, "destination", "35235 kalif")
            out["offline 35235 kalif"] = offline_kalif[:2]
            check(offline_kalif and offline_kalif[0] == "35235 Kalifornsky Beach Rd, Kenai", "offline address")
            browser.close()
    finally:
        proc.terminate()

    # Going offline aborts the hazard poll and tiles by design; those are not app errors.
    out["console"] = console
    out["external"] = sorted(set(external))
    # Offline, tiles (network-only by design) and the hazard poll fail; online the console must be clean.
    check(not [c for c in console if c[0] == "online"], "console clean online")
    # OPTIONS is the browser's CORS preflight for that same GET (it sends apikey/Authorization headers).
    check(all(m in ("GET", "OPTIONS") and u.endswith("/rest/v1/public_hazards") for m, u in set(external)),
          "only the Supabase hazards GET (and its preflight)")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
