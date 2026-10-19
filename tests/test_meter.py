"""WARN Meter (meter.html) is its own unlisted page; the Map and Alerts are plain and know nothing about it.

Static: driver.js is only ever reached by app.js's dynamic import on data-app="meter"; no page or manifest or the start
page mentions meter.html. Browser: the Map and Alerts, even with old driver-mode flags and trip data in localStorage,
never request driver.js or trace.js and show nothing about fares, tariffs or trips, and seven taps unlock nothing;
meter.html has the full meter, tariff fields, Trips and the planner fare, picks up existing data, and works offline from
its own cached copy.
"""
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8780)
ROOT = f"http://127.0.0.1:{PORT}"
KENAI = [60.5603, -151.2235]
SOLDOTNA = [60.4864, -151.0583]
MENTION = r"\b(fares?|tariffs?|trips?|meter|taxi)\b"

# Old data and flags, as they sit in a phone that used the previous driver mode.
OLD_STATE = """
(() => {
  if (sessionStorage.getItem("seeded")) return;
  sessionStorage.setItem("seeded", "1");
  localStorage.setItem("warn-map-driver-mode", "1");
  localStorage.setItem("warn-map-tariff-start", "4.10");
  localStorage.setItem("warn-map-trips", JSON.stringify([{ startTime: 1760000000000, endTime: 1760000900000, miles: 6.84,
    waitSeconds: 72, fare: 23.45, startPlace: "Kenai", endPlace: "Soldotna" }]));
})();
"""


def read(path):
    return open(os.path.join(REPO, path), encoding="utf-8").read()


def static_graph(entry):
    seen, todo = set(), [os.path.normpath(os.path.join(REPO, entry))]
    while todo:
        path = todo.pop()
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        for target in re.findall(r'''(?:import|export)[^"';(]*?from\s*["']([^"']+)["']''', open(path, encoding="utf-8").read()):
            if target.startswith("."):
                todo.append(os.path.normpath(os.path.join(os.path.dirname(path), target)))
    return {os.path.relpath(p, REPO).replace("\\", "/") for p in seen}


def mentions(page):
    return page.evaluate("""(pattern) => {
      const re = new RegExp(pattern, "i");
      const hits = [];
      for (const el of document.querySelectorAll("body *")) {
        const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ");
        if (re.test(el.id) || re.test(own) || re.test(el.getAttribute("aria-label") || "")) hits.push(`${el.tagName}#${el.id}: ${own.trim().slice(0, 60)}`);
      }
      return hits;
    }""", MENTION)


def main():
    out, console, failures = {}, [], []

    def check(condition, what):
        if not condition:
            failures.append(what)

    # ---- static rules ----
    check("js/driver.js" not in static_graph("js/app.js") and "js/trace.js" not in static_graph("js/app.js"),
          "app.js's static graph has no driver.js or trace.js")
    importers = {}
    for name in os.listdir(os.path.join(REPO, "js")):
        for target in re.findall(r'''(?:import|export)[^"';(]*?from\s*["']\./([^"']+)["']''', read(f"js/{name}")):
            importers.setdefault(target, []).append(name)
    check(importers.get("driver.js", []) == [], "nothing statically imports driver.js")
    check(importers.get("trace.js", []) == ["driver.js"], "only driver.js imports trace.js")
    app = read("js/app.js")
    at = app.find('import("./driver.js")')
    check(app.count('import("./driver.js")') == 1 and 0 < at - app.find('APP === "meter"') < 600,
          "app.js loads driver.js once, inside the data-app=meter branch")
    check("driver.js" not in read("js/routing.js"), "routing.js does not import driver.js")
    check("isDriverMode" not in read("js/routing.js") + app + read("js/driver.js") and "applyDriverQuery" not in app + read("js/driver.js"),
          "no driver-mode flag check or ?driver query handler remains")
    for page in ("meter.html", "index.html", "warn.html"):
        check(page != "meter.html" or 'data-app="meter"' in read(page), f"{page} marker")
    check('data-app="meter"' in read("meter.html") and 'data-app-name="WARN Meter"' in read("meter.html")
          and "<title>WARN Meter</title>" in read("meter.html") and 'href="./meter.webmanifest"' in read("meter.html")
          and "About WARN Meter" in read("meter.html"), "meter.html name, title, manifest and About heading")
    # meter.html differs from index.html in exactly the allowed lines.
    a, b = read("index.html").splitlines(), read("meter.html").splitlines()
    check(len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 5, "meter.html differs from index.html in exactly 5 lines")
    for name in ("index.html", "warn.html", "start.html", "manifest.webmanifest", "warn.webmanifest"):
        check("meter.html" not in read(name) and "meter.webmanifest" not in read(name) and "WARN Meter" not in read(name),
              f"{name} does not mention the meter page")
    check("meter" not in read("start.html").lower(), "start.html has no 'meter' anywhere")
    precache = read("js/precache-list.js")
    check('"./meter.html"' in precache and '"./meter.webmanifest"' in precache, "the meter page and manifest are precached")

    proc = start_server(PORT)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)

            def open_page(path, seeded=True):
                context = browser.new_context(viewport={"width": 390, "height": 844})
                if seeded:
                    context.add_init_script(OLD_STATE)
                requests = []
                page = context.new_page()
                # The page's own requests only: the service worker precaches driver.js for offline use, which is not
                # the page loading it.
                page.on("request", lambda req: requests.append(urlparse(req.url).path))
                page.on("pageerror", lambda e: console.append((path, "pageerror", str(e)[:200])))
                page.goto(f"{ROOT}/{path}")
                page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
                page.wait_for_load_state("networkidle")
                return context, page, requests

            # ---- the Map and Alerts, with old flags and data present ----
            for name, path in (("map", "?test=1"), ("alerts", "warn.html?test=1"), ("map ?driver", "?test=1&driver")):
                context, page, requests = open_page(path)
                page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
                page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
                page.locator("#settings-toggle").click()
                for _ in range(7):
                    page.locator("#app-version-line").click()
                page.wait_for_timeout(500)
                loaded = sorted({p for p in requests if p.endswith(("/driver.js", "/trace.js"))})
                check(loaded == [], f"{name}: driver.js and trace.js are never requested ({loaded})")
                check(page.locator("#driver-settings, #driver-trip, #start-trip, #driver-trace-note").count() == 0
                      and page.locator(".route-fare").count() == 0, f"{name}: no meter UI and no fare line, with old flags set and 7 taps")
                found = mentions(page)
                out[f"{name} mentions"] = found
                check(found == [], f"{name}: nothing about fares, tariffs, trips or a meter in the page")
                check(page.evaluate("() => window.__WARN_DRIVER_TEST__") is None, f"{name}: no driver test hook")
                context.close()

            # ---- the meter page ----
            context, page, requests = open_page("meter.html?test=1")
            page.wait_for_function("() => !!window.__WARN_DRIVER_TEST__", timeout=60000)
            check(page.title().startswith("WARN Meter"), "meter page title")
            check(page.evaluate("() => document.documentElement.dataset.app") == "meter", "meter page data-app")
            check(page.evaluate("() => document.querySelector('link[rel=manifest]').getAttribute('href')") == "./meter.webmanifest", "meter page manifest link")
            check(any(p.endswith("/driver.js") for p in requests) and any(p.endswith("/trace.js") for p in requests), "meter page loads driver.js and trace.js")
            check(page.locator("#start-trip").count() == 1, "meter page: Start trip")
            page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
            page.wait_for_function("() => document.querySelector('.route-fare')", timeout=30000)
            check(page.locator(".route-fare").inner_text().startswith("Fare $"), "meter page: planner fare line")
            page.locator("#settings-toggle").click()
            check(page.locator("#driver-tariff input").count() == 5, "meter page: five tariff fields")
            check(page.locator("#tariff-start").input_value() == "4.10", "meter page: existing tariff value carries over")
            check(page.locator(".driver-trip-list li").count() == 1 and "6.8 mi" in page.locator(".driver-trip-list li").inner_text(),
                  "meter page: existing saved trip carries over")
            check(page.locator("#driver-trace-note").count() == 1, "meter page: the on-device trace note")
            page.screenshot(path=os.path.join(SHOTS, "meter-page.png"))
            context.close()

            # ---- offline: meter.html from its own cached copy ----
            context, page, _ = open_page("meter.html?test=1", seeded=False)
            page.evaluate("() => navigator.serviceWorker.ready")
            page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=30000)
            page.wait_for_load_state("networkidle")
            cached = page.evaluate("""async () => {
              const cache = await caches.open((await caches.keys()).find((n) => n.startsWith("warn-map-shell-")));
              return (await cache.keys()).map((r) => new URL(r.url).pathname).filter((p) => /meter|warn\\.html|driver|trace/.test(p));
            }""")
            out["cached meter files"] = cached
            check(all(any(p.endswith(f) for p in cached) for f in ("/meter.html", "/meter.webmanifest", "/js/driver.js", "/js/trace.js")),
                  "the meter page, its manifest, driver.js and trace.js are in the shell cache")
            context.set_offline(True)
            page.reload()
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__ && !!window.__WARN_DRIVER_TEST__", timeout=60000)
            check(page.title().startswith("WARN Meter") and page.locator("#start-trip").count() == 1,
                  "offline: /meter.html is served as the meter page, not the map")
            context.close()
            browser.close()
    finally:
        proc.terminate()

    out["console"] = console
    check(not console, "no page errors")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    for failure in failures:
        print("SOFTFAIL", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
