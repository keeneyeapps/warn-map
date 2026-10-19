"""Driver tools: hidden by default, ?driver and the version-tap toggle, tariff formula, trip meter, resume."""
import json
import math
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8774)
BASE = f"http://127.0.0.1:{PORT}/?test=1"
KENAI = [60.5603, -151.2235]
SOLDOTNA = [60.4864, -151.0583]

# Geolocation under test control: window.__emitFix(fix) feeds every watcher.
INIT = """
(() => {
  window.__shared = [];
  navigator.share = (data) => { window.__shared.push(data.text); return Promise.resolve(); };
  const watchers = new Map();
  let nextId = 1;
  const kenai = { coords: { latitude: 60.5603, longitude: -151.2235, accuracy: 10, speed: null, heading: null }, timestamp: Date.now() };
  window.__emitFix = (fix) => {
    const position = { coords: { accuracy: 8, heading: null, speed: null, ...fix.coords }, timestamp: fix.timestamp };
    for (const callback of watchers.values()) callback(position);
  };
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: {
      getCurrentPosition(success) { setTimeout(() => success(kenai), 10); },
      watchPosition(success) { const id = nextId++; watchers.set(id, success); return id; },
      clearWatch(id) { watchers.delete(id); },
    },
  });
})();
"""

MENTION = r"\b(fares?|tariffs?|trips?)\b"


def serve():
    return start_server(PORT)


def mentions(page):
    """Elements whose id, or own text, mentions fare, tariff, or trip."""
    return page.evaluate("""(pattern) => {
      const re = new RegExp(pattern, "i");
      const hits = [];
      for (const el of document.querySelectorAll("*")) {
        const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ");
        if (re.test(el.id) || re.test(own)) hits.push(`${el.tagName}#${el.id}: ${own.trim().slice(0, 60)}`);
      }
      return hits;
    }""", MENTION)


def hav(a, b):
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    v = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(v))


def point_at(line, metres):
    walked = 0
    for a, b in zip(line, line[1:]):
        step = hav(a, b)
        if walked + step >= metres:
            f = (metres - walked) / step if step else 0
            return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]
        walked += step
    return line[-1]


def tap_version(page, times=7):
    for _ in range(times):
        page.locator("#app-version-line").click()


def main():
    out, console, external, failures = {}, [], [], []
    phase = ["normal"]

    def check(condition, what):
        if not condition:
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
            page.on("console", lambda m: console.append((phase[0], m.type, m.text[:200]))
                    if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append((phase[0], "pageerror", str(e)[:300])))

            # 1. Normal use: nothing about fares, tariffs, or trips exists in the page, even with settings open.
            page.goto(BASE)
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
            page.wait_for_load_state("networkidle")
            page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            page.locator("#settings-toggle").click()
            out["normal mentions"] = mentions(page)
            check(out["normal mentions"] == [], "normal mode: no fare/tariff/trip in the DOM")
            out["normal share"] = page.evaluate("() => window.__WARN_MAP_TEST__.shareText()")
            check(not re.search(MENTION, out["normal share"], re.I), "normal mode: share text has no fare")

            # 2. Seven taps on the version line turn driver tools on, and seven more turn them off.
            tap_version(page)
            out["toast on"] = page.locator("#location-status").inner_text()
            check(out["toast on"] == "Driver tools on", "toast: Driver tools on")
            check(page.locator("#driver-settings").count() == 1 and page.locator("#start-trip").count() == 1,
                  "taps: driver tools appear")
            out["planner fare"] = page.locator(".route-fare").inner_text() if page.locator(".route-fare").count() else None
            tap_version(page)
            out["toast off"] = page.locator("#location-status").inner_text()
            check(out["toast off"] == "Driver tools off", "toast: Driver tools off")
            check(mentions(page) == [], "taps: driver tools removed again")
            page.locator("#settings-toggle").click()

            # 3. ?driver turns the tools on once and is removed from the address.
            phase[0] = "driver"
            page.goto(BASE + "&driver")
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__ && !!window.__WARN_DRIVER_TEST__", timeout=60000)
            page.wait_for_load_state("networkidle")
            out["url after ?driver"] = page.url
            check("driver" not in page.url, "?driver removed from the URL")
            page.locator("#settings-toggle").click()
            out["tariff labels"] = page.locator("#driver-tariff label").all_inner_texts()
            check(len(out["tariff labels"]) == 5, "five tariff settings")
            page.screenshot(path=os.path.join(SHOTS, "step8-tariff.png"))
            page.locator("#settings-toggle").click()

            # 4. The formula, after a cab meter: 1/14-mile steps (half-up) plus $0.50 per full cumulative minute of wait.
            out["formula"] = page.evaluate("""async () => {
              const d = await import("./js/driver.js");
              const fixtures = [
                [26.0714, 0], [6.84, 72], [26.3, 56], [26.25, 56], [26.2975, 56],
                [0, 59], [0, 60], [0, 119], [0, 120], [0.25, 0], [0, 180], [0.25, 180],
              ];
              return { tariff: d.tariff(), steps_0_25mi: d.distanceUnits(0.25), minutes_180: d.waitMinutes(180),
                       fares: fixtures.map(([m, w]) => [m, w, d.fareFor(m, w)]) };
            }""")
            f = out["formula"]
            fare = {(m, w): v for m, w, v in f["fares"]}
            close = lambda a, b: abs(a - b) < 1e-9
            check(f["steps_0_25mi"] == 4 and close(fare[(0.25, 0)], 4.55), "0.25 mi = 3.5 steps -> 4 -> $4.55")
            check(f["minutes_180"] == 3 and close(fare[(0, 180)], 5.25), "180 s = 3 full minutes -> $1.50 -> $5.25")
            check(close(fare[(0.25, 180)], 6.05), "0.25 mi + 180 s -> 4 steps + 3 minutes -> $6.05")
            # Regression fixtures from real trips, default tariff.
            check(close(fare[(26.0714, 0)], 76.75), "iCabbi 26.0714 mi, no wait -> $76.75")
            check(close(fare[(6.84, 72)], 23.45), "iCabbi 6.84 mi, 72 s wait (one full minute) -> $23.45")
            check(all(close(fare[(m, 56)], 77.35) for m in (26.3, 26.25, 26.2975)),
                  "the 26.3 mi, 56 s trip -> $77.35 (the app's distance over-read stays visible)")
            for seconds, extra in ((59, 0.0), (60, 0.5), (119, 0.5), (120, 1.0)):
                check(close(fare[(0, seconds)], 3.75 + extra), f"pure wait {seconds} s -> ${extra:.2f}")

            # 5. A trip: 16 fixes along the Spur plus a 3-minute stop (4 fixes), with a reload in the middle.
            page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            line = page.evaluate("() => window.__WARN_MAP_TEST__.routeFeature().geometry.coordinates")
            page.locator("#clear-route").click()
            moving = [point_at(line, 120 + i * 650) for i in range(16)]
            t0 = int(time.time() * 1000)
            fixes, clock = [], t0
            for i, point in enumerate(moving):
                if i > 0:
                    clock += int(hav(moving[i - 1], point) / 20 * 1000)  # 20 m/s, about 45 mph
                fixes.append({"coords": {"latitude": point[1], "longitude": point[0], "speed": 20 if i else 0},
                              "timestamp": clock})
                if i == 7:
                    for k in range(1, 5):  # the stop: four fixes 45 s apart at the same place
                        fixes.append({"coords": {"latitude": point[1], "longitude": point[0], "speed": 0},
                                      "timestamp": clock + 45000 * k})
                    clock += 180000
            check(len(fixes) == 20, "20 fixes")

            page.locator("#start-trip").click()
            for fix in fixes[:12]:
                page.evaluate("(fix) => window.__emitFix(fix)", fix)
                page.wait_for_timeout(250)
            page.wait_for_timeout(1000)
            before_reload = page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()")
            out["before reload"] = before_reload
            page.wait_for_timeout(11000)  # the running trip is saved every 10 s
            page.reload()
            page.wait_for_function("() => !!window.__WARN_DRIVER_TEST__", timeout=60000)
            page.wait_for_timeout(1000)
            after_reload = page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()")
            out["after reload"] = after_reload
            check(after_reload is not None and abs(after_reload["meters"] - before_reload["meters"]) < 0.01
                  and after_reload["waitSeconds"] == before_reload["waitSeconds"], "trip resumes after reload")
            out["live line after reload"] = page.locator("#driver-trip-live").inner_text()
            for fix in fixes[12:]:
                page.evaluate("(fix) => window.__emitFix(fix)", fix)
                page.wait_for_timeout(250)
            page.wait_for_timeout(1500)
            final = page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()")
            out["final"] = final
            out["live line"] = page.locator("#driver-trip-live").inner_text()
            page.screenshot(path=os.path.join(SHOTS, "step8-trip-live.png"))

            # The snapped path: the road distance from the first fix to the last (all along the Spur).
            path = page.evaluate("""([s, d]) => window.__WARN_MAP_TEST__.routeWithBlockedPoints(s, d, [])
              .then((r) => r.route.totalDistanceM)""", [[moving[0][1], moving[0][0]], [moving[-1][1], moving[-1][0]]])
            out["snapped path m"] = path
            out["trip m"] = final["meters"]
            out["difference %"] = round(abs(final["meters"] - path) / path * 100, 3)
            check(out["difference %"] <= 2, "trip miles within 2% of the snapped path")
            check(abs(final["waitSeconds"] - 180) <= 1, "wait about 180 s")
            expected = page.evaluate("""async (state) => (await import("./js/driver.js"))
              .fareFor(state.meters / 1609.344, state.waitSeconds)""", final)
            out["expected fare"] = expected
            check(abs(final["fare"] - expected) < 1e-9, "fare matches the formula")
            check(f"${expected:.2f}" in out["live line"], "live line shows the fare")

            page.locator("#end-trip").click()
            page.locator("#driver-trip-summary").wait_for(timeout=10000)
            out["summary"] = page.locator("#driver-trip-summary").inner_text().split("\n")
            page.locator(".driver-trip .driver-button", has_text="Share").click()
            page.wait_for_function("() => window.__shared.length >= 1")
            out["share"] = page.evaluate("() => window.__shared.at(-1)").split("\n")
            page.locator(".driver-trip .driver-button", has_text="Dismiss").click()
            check(page.locator("#start-trip").count() == 1, "Start trip after dismiss")
            out["saved trips"] = page.evaluate("() => JSON.parse(localStorage.getItem('warn-map-trips'))")
            check(len(out["saved trips"]) == 1 and abs(out["saved trips"][0]["fare"] - expected) < 1e-9, "trip saved")
            check(page.evaluate("() => localStorage.getItem('warn-map-trip-running')") is None, "running state cleared")
            page.locator("#settings-toggle").click()
            out["trips list"] = page.locator(".driver-trip-list li").all_inner_texts()
            page.screenshot(path=os.path.join(SHOTS, "step8-trips-list.png"))
            page.locator(".driver-trips .driver-button", has_text="Clear all").click()
            check(page.locator(".driver-trip-list li").count() == 0, "Clear all")
            browser.close()
    finally:
        proc.terminate()

    out["console"] = console
    out["external"] = sorted(set(external))
    check(not console, "console clean")
    check(all(m in ("GET", "OPTIONS") and u.endswith("/rest/v1/public_hazards") for m, u in set(external)),
          "only the Supabase hazards GET")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
