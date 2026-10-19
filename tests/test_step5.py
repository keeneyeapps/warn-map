"""Icons, boundary-aware place ranking, place overrides, covered-marker popups, and tap-to-place."""
import json
import math
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8771)
BASE = f"http://127.0.0.1:{PORT}/?test=1"
FIX = {"latitude": 60.5544, "longitude": -151.2583}  # Kenai
HOSPITAL = [60.49321, -151.07805]  # [lat, lon], the GeoHub point
MOOSE_YELLOW = (255, 210, 0)

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


def suggestions_for(page, slot, text, close=True):
    page.locator(f"#slot-{slot}").click()
    page.locator("#address-sheet").wait_for(state="visible")
    page.locator("#address-input").fill("")
    page.locator("#address-input").type(text, delay=25)
    page.wait_for_function(
        "() => document.querySelectorAll('.address-suggestion').length > 0"
        " || /No matching|unavailable/.test(document.querySelector('#address-sheet-status').textContent)", timeout=15000)
    page.wait_for_timeout(300)
    rows = page.locator(".address-suggestion").all_inner_texts()
    if close:
        page.keyboard.press("Escape")
    return rows


def main():
    out, console, external, failures = {}, [], [], []
    phase = ["online"]

    def check(condition, what):
        if not condition:
            failures.append(what)

    icons = subprocess.run([PYTHON, os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_icons.py")],
                           capture_output=True, text=True)
    out["icons"] = json.loads(icons.stdout)
    check(icons.returncode == 0, "icon sizes and maskable safe zone")

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
            page.on("console", lambda m: console.append((phase[0], m.type, m.text[:200]))
                    if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append((phase[0], "pageerror", str(e)[:300])))
            page.goto(BASE)
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
            page.wait_for_load_state("networkidle")
            page.locator("#locate").click()
            page.wait_for_timeout(1500)

            walmart = suggestions_for(page, "destination", "walmart")
            out["walmart (Kenai location)"] = walmart
            outside_cities = ("Anchorage", "Eagle River", "Wasilla")
            first_outside = next((i for i, s in enumerate(walmart) if s.endswith(outside_cities)), len(walmart))
            check(walmart and walmart[0] == "Walmart Supercenter · Grocery, Kenai", "walmart: Kenai first")
            check(all(not s.endswith(outside_cities) for s in walmart[:first_outside])
                  and all(s.endswith(outside_cities) for s in walmart[first_outside:]), "walmart: outside after inside")
            inside_count = first_outside
            check(inside_count < 3 or first_outside == len(walmart), "walmart: outside only when < 3 inside")

            safeway = suggestions_for(page, "destination", "safeway")
            out["safeway (Kenai location)"] = safeway
            check("Safeway · Gas station" not in safeway, "safeway: outside gas station hidden (>= 3 inside)")
            fred = suggestions_for(page, "destination", "fred meyer")
            out["fred meyer (Kenai location)"] = fred
            check(not any(s.endswith(outside_cities) or s.endswith("Fred Meyer · Gas station") for s in fred),
                  "fred meyer: no outside results (>= 3 inside)")

            central = suggestions_for(page, "destination", "central pen", close=False)
            out["central pen"] = central
            check(central and central[0] == "Central Peninsula Hospital · Hospital, Soldotna", "central pen first")
            page.locator(".address-suggestion").first.click()
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            call = page.locator(".route-leg-destination .route-leg-call")
            out["hospital call link"] = [call.inner_text(), call.get_attribute("href")]
            check(out["hospital call link"][1] == "tel:+19077144404", "hospital tel link from override")

            # The destination marker under the open route panel: its popup folds the panel.
            out["panel open before"] = page.locator("#route-panel-body").is_visible()
            marker_box = page.locator(".route-marker-destination").bounding_box()
            panel_box = page.locator("#route-hazards-panel").bounding_box()
            out["marker under panel"] = (marker_box["y"] < panel_box["y"] + panel_box["height"]
                                         and marker_box["x"] < panel_box["x"] + panel_box["width"])
            page.locator(".route-marker-destination").dispatch_event("click")
            page.locator(".place-popup").wait_for(timeout=5000)
            page.wait_for_timeout(300)
            out["panel open after popup"] = page.locator("#route-panel-body").is_visible()
            out["toggle expanded"] = page.locator("#route-hazards-toggle").get_attribute("aria-expanded")
            check(not out["marker under panel"] or not out["panel open after popup"], "covered marker folds the panel")
            out["marker popup"] = [line for line in page.locator(".place-popup").inner_text().split("\n") if line]
            page.screenshot(path=os.path.join(SHOTS, "step5-marker-popup.png"))
            page.locator(".maplibregl-popup-close-button").click()  # a real click: the popup is no longer covered
            page.wait_for_timeout(300)
            out["panel open after popup closed"] = page.locator("#route-panel-body").is_visible()
            check(out["panel open after popup closed"], "panel unfolds when the popup closes")

            # A plain tap near an indexed place opens it, with Route here.
            page.locator("#clear-route").click()
            page.wait_for_timeout(500)
            x, y = page.evaluate("(p) => window.__WARN_MAP_TEST__.project(p)", HOSPITAL)
            page.mouse.click(x, y)
            page.locator(".place-popup").wait_for(timeout=5000)
            out["tapped popup"] = [line for line in page.locator(".place-popup").inner_text().split("\n") if line]
            check(out["tapped popup"][:2] == ["Central Peninsula Hospital", "Hospital"], "tap opens the hospital")
            check(page.locator(".place-popup .place-call").get_attribute("href") == "tel:+19077144404",
                  "tapped popup call link")
            page.screenshot(path=os.path.join(SHOTS, "step5-tapped-place.png"))
            page.locator(".place-route-here").click()
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            out["destination after Route here"] = page.locator("#slot-destination").get_attribute("aria-label")
            check(out["destination after Route here"] == "Destination: Central Peninsula Hospital", "Route here")

            # A tap far from any place opens nothing.
            page.locator("#clear-route").click()
            page.wait_for_timeout(500)
            x, y = page.evaluate("(p) => window.__WARN_MAP_TEST__.project(p)", [60.40, -150.70])
            page.mouse.click(x, y)
            page.wait_for_timeout(800)
            out["popups after empty tap"] = page.locator(".place-popup").count()
            check(out["popups after empty tap"] == 0, "empty tap opens nothing")

            out["dial links"] = page.evaluate("""() => [...document.querySelectorAll('a[href]')]
              .map((a) => a.getAttribute('href')).filter((h) => /^(tel|sms|mailto):/i.test(h))""")
            browser.close()
    finally:
        proc.terminate()

    out["console"] = console
    out["external"] = sorted(set(external))
    check(not console, "console clean")
    check(all(m in ("GET", "OPTIONS") and u.endswith("/rest/v1/public_hazards") for m, u in set(external)),
          "only the Supabase hazards GET (and its CORS preflight)")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
