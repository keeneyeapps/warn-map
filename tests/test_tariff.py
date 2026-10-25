"""WARN Meter's tariff starts blank. A fresh install has no tariff: distance and wait only, the set-your-tariff prompt,
no planner fare line, empty fields with example placeholders, and no tariff value written. An install that already
saved a tariff keeps every stored value byte for byte and shows the estimated fare as before; one that saved only
some values keeps them too, and shows no fare until all five are set."""
import json
import re
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, port_for, start_server

PORT = port_for(8783)
METER = f"http://127.0.0.1:{PORT}/meter.html?test=1"
KENAI = [60.5603, -151.2235]
SOLDOTNA = [60.4864, -151.0583]
PROMPT = "Set your tariff to see an estimated fare"
ESTIMATE_NOTE = "Estimate only. Not a certified taximeter. Not for charging passengers."
TARIFF_KEYS = ["warn-map-tariff-start", "warn-map-tariff-increment", "warn-map-fare-rate",
               "warn-map-tariff-wait-rate", "warn-map-tariff-wait-below"]
EXAMPLES = {"start": "3.75", "increment": "0.20", "ratePerMile": "2.80", "waitPerMinute": "0.50", "waitBelowMph": "12"}

# Exactly as a driver's tablet may hold them (typed text, so "0.2" rather than "0.20").
SAVED = {"warn-map-tariff-start": "3.75", "warn-map-tariff-increment": "0.2", "warn-map-fare-rate": "2.8",
         "warn-map-tariff-wait-rate": "0.5", "warn-map-tariff-wait-below": "12"}
PARTIAL = {"warn-map-fare-rate": "2.80"}  # the planner's earlier rate-per-mile setting, nothing else

# Geolocation under test control (window.__emitFix feeds every watcher) and the share sheet captured.
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


def seed_script(values):
    """Stores `values` once per browser context (a reload must not re-seed what the app may have changed)."""
    return """
(() => {
  if (sessionStorage.getItem("seeded")) return;
  sessionStorage.setItem("seeded", "1");
  for (const [key, value] of Object.entries(%s)) localStorage.setItem(key, value);
})();
""" % json.dumps(values)


def stored_tariff(page):
    return page.evaluate("(keys) => Object.fromEntries(keys.map((k) => [k, localStorage.getItem(k)]))", TARIFF_KEYS)


def open_meter(browser, seed=None):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(INIT)
    if seed is not None:
        context.add_init_script(seed_script(seed))
    page = context.new_page()
    page.goto(METER)
    page.wait_for_function("() => !!window.__WARN_MAP_TEST__ && !!window.__WARN_DRIVER_TEST__", timeout=60000)
    page.wait_for_load_state("networkidle")
    return context, page


def plan_route(page):
    page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
    page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
    page.wait_for_timeout(300)
    fare = page.locator(".route-fare").inner_text() if page.locator(".route-fare").count() else None
    total = page.locator(".route-total-distance").inner_text()
    share = page.evaluate("() => window.__WARN_MAP_TEST__.shareText()")
    page.locator("#clear-route").click()
    return fare, total, share


def drive_trip(page):
    """A short trip: five moving fixes, then a 3-minute stop (four fixes 45 s apart), ended and shared."""
    t0 = int(time.time() * 1000)
    fixes = []
    for i in range(5):
        fixes.append({"coords": {"latitude": 60.5603 - i * 0.002, "longitude": -151.2235 + i * 0.004,
                                 "speed": 20 if i else 0}, "timestamp": t0 + i * 15000})
    for k in range(1, 5):
        fixes.append({"coords": {"latitude": fixes[-1]["coords"]["latitude"], "longitude": fixes[-1]["coords"]["longitude"],
                                 "speed": 0}, "timestamp": t0 + 60000 + 45000 * k})
    page.locator("#start-trip").click()
    for fix in fixes:
        page.evaluate("(fix) => window.__emitFix(fix)", fix)
        page.wait_for_timeout(250)
    page.wait_for_timeout(1000)
    result = {
        "state": page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()"),
        "live": page.locator("#driver-trip-live").inner_text(),
        "note": page.locator("#driver-estimate-note").inner_text(),
        "prompt while running": page.locator("#driver-tariff-prompt").count(),
    }
    page.locator("#end-trip").click()
    page.locator("#driver-trip-summary").wait_for(timeout=10000)
    result["summary"] = page.locator("#driver-trip-summary").inner_text().split("\n")
    result["prompt on summary"] = page.locator("#driver-tariff-prompt").count()
    page.locator(".driver-trip .driver-button", has_text="Share").click()
    page.wait_for_function("() => window.__shared.length >= 1")
    result["share"] = page.evaluate("() => window.__shared.at(-1)").split("\n")
    result["record"] = page.evaluate("() => JSON.parse(localStorage.getItem('warn-map-trips'))[0]")
    page.locator(".driver-trip .driver-button", has_text="Dismiss").click()
    return result


def main():
    out, console, external, failures = {}, [], [], []

    def check(condition, what):
        if not condition:
            failures.append(what)

    proc = start_server(PORT)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)

            def watch(context, page, name):
                context.on("request", lambda req: external.append((req.method, req.url.split("?")[0]))
                           if urlparse(req.url).hostname not in ("localhost", "127.0.0.1")
                           and not req.url.startswith(("data:", "blob:")) else None)
                page.on("console", lambda m: console.append((name, m.type, m.text[:200]))
                        if m.type in ("error", "warning") else None)
                page.on("pageerror", lambda e: console.append((name, "pageerror", str(e)[:300])))

            # ---- 1. A fresh install: no tariff anywhere ----
            context, page = open_meter(browser)
            watch(context, page, "fresh")
            fresh = out.setdefault("fresh", {})
            check(all(v is None for v in stored_tariff(page).values()), "fresh: no tariff value stored on load")
            check(page.evaluate("async () => (await import('./js/driver.js')).tariff()") is None, "fresh: tariff() is null")
            check(page.evaluate("async () => (await import('./js/driver.js')).fareFor(5, 120)") is None, "fresh: no fare")
            fresh["prompt"] = page.locator("#driver-tariff-prompt").inner_text() if page.locator("#driver-tariff-prompt").count() else None
            check(fresh["prompt"] == PROMPT, "fresh: the set-your-tariff prompt beside Start trip")
            fresh["planner fare"], fresh["route total"], fresh["route share"] = plan_route(page)
            check(fresh["planner fare"] is None, "fresh: no planner fare line")
            check(not re.search(r"fare|\$|Estimate", fresh["route share"], re.I), "fresh: route share text has no fare and no fare note")

            page.locator("#settings-toggle").click()
            fresh["labels"] = page.locator("#driver-tariff label").all_inner_texts()
            fresh["values"] = [page.locator(f"#tariff-{k}").input_value() for k in EXAMPLES]
            fresh["placeholders"] = [page.locator(f"#tariff-{k}").get_attribute("placeholder") for k in EXAMPLES]
            check(fresh["values"] == [""] * 5, "fresh: all five tariff fields are empty")
            check(fresh["placeholders"] == [f"Example: {v}" for v in EXAMPLES.values()],
                  "fresh: each field's placeholder is the example, labelled 'Example:'")
            check(not any("default" in label.lower() for label in fresh["labels"]), "fresh: no label calls a value a default")
            check("example" in page.locator("#driver-tariff-note").inner_text().lower(),
                  "fresh: the tariff section says the greyed numbers are only an example")
            page.locator("#settings-toggle").click()

            trip = drive_trip(page)
            fresh["trip"] = trip
            check(trip["state"] is not None and trip["state"]["meters"] > 500 and trip["state"]["fare"] is None,
                  "fresh: the trip meter counts distance with no fare")
            check(trip["state"]["waitSeconds"] >= 170, "fresh: the trip meter counts wait time")
            check(trip["live"].startswith("Trip ") and " mi · wait " in trip["live"] and "$" not in trip["live"]
                  and "fare" not in trip["live"].lower(), "fresh: the live meter shows distance and wait only")
            check(trip["note"] == ESTIMATE_NOTE, "fresh: the estimate note on the live meter")
            check(trip["prompt while running"] == 1 and trip["prompt on summary"] == 1, "fresh: the prompt while running and on the summary")
            check(not any("$" in line or "fare" in line.lower() for line in trip["summary"] + trip["share"]),
                  "fresh: no fare in the summary or the trip share text")
            check(trip["summary"][-1] == ESTIMATE_NOTE and trip["share"][-1] == ESTIMATE_NOTE,
                  "fresh: summary and share text end with the estimate note")
            check(trip["record"]["fare"] is None and trip["record"]["miles"] > 0, "fresh: the saved trip has distance and no fare")
            page.locator("#settings-toggle").click()
            fresh["trips list"] = page.locator(".driver-trip-list li .driver-trip-text").all_inner_texts()
            check(len(fresh["trips list"]) == 1 and "$" not in fresh["trips list"][0], "fresh: the Trips list shows no fare")
            check(all(v is None for v in stored_tariff(page).values()), "fresh: still no tariff value stored after a trip")

            # The driver types their own tariff: four values are not enough, the fifth turns the fare on.
            mine = {"start": "4.00", "increment": "0.25", "ratePerMile": "3.00", "waitPerMinute": "0.60", "waitBelowMph": "10"}
            for key in list(mine)[:4]:
                page.locator(f"#tariff-{key}").fill(mine[key])
            check(page.locator("#driver-tariff-prompt").count() == 1, "four of five values: the prompt stays")
            page.locator("#settings-toggle").click()
            check(plan_route(page)[0] is None, "four of five values: still no planner fare line")
            page.locator("#settings-toggle").click()
            page.locator("#tariff-waitBelowMph").fill(mine["waitBelowMph"])
            check(page.locator("#driver-tariff-prompt").count() == 0, "all five values: the prompt goes")
            page.locator("#settings-toggle").click()
            fare, total, share = plan_route(page)
            expected = page.evaluate("async (miles) => `Est. fare $${(await import('./js/driver.js')).fareFor(miles).toFixed(2)}`",
                                     float(re.search(r"(\d+\.\d) mi", total).group(1)))
            fresh["own tariff fare"] = fare
            check(fare == expected and expected.startswith("Est. fare $"), "all five values: the planner fare line at the driver's tariff")
            check(share.split("\n")[-2:] == [expected, ESTIMATE_NOTE], "all five values: route share ends with the fare and the note")
            context.close()

            # ---- 2. An install with a saved tariff (a driver's own tablet): kept byte for byte, fare as before ----
            context, page = open_meter(browser, SAVED)
            watch(context, page, "saved")
            saved = out.setdefault("saved", {})
            check(stored_tariff(page) == SAVED, "saved: stored values unchanged on load")
            saved["tariff"] = page.evaluate("async () => (await import('./js/driver.js')).tariff()")
            check(saved["tariff"] == {"start": 3.75, "increment": 0.2, "ratePerMile": 2.8, "waitPerMinute": 0.5, "waitBelowMph": 12},
                  "saved: the saved values are the active tariff")
            # The same numbers the meter gave before the update (step8's fixtures at this tariff).
            saved["fares"] = page.evaluate("""async () => { const d = await import('./js/driver.js');
              return [d.fareFor(0.25, 0), d.fareFor(0, 180), d.fareFor(26.0714, 0), d.fareFor(6.84, 72)]; }""")
            check(saved["fares"] == [4.55, 5.25, 76.75, 23.45], "saved: fares unchanged from before the update")
            check(page.locator("#driver-tariff-prompt").count() == 0, "saved: no set-your-tariff prompt")
            saved["planner fare"], total, _ = plan_route(page)
            expected = page.evaluate("async (miles) => `Est. fare $${(await import('./js/driver.js')).fareFor(miles).toFixed(2)}`",
                                     float(re.search(r"(\d+\.\d) mi", total).group(1)))
            check(saved["planner fare"] == expected, "saved: the planner fare line at the saved tariff")
            page.locator("#settings-toggle").click()
            saved["values"] = [page.locator(f"#tariff-{k}").input_value() for k in EXAMPLES]
            check(saved["values"] == list(SAVED.values()), "saved: the fields show the saved values as stored")
            page.locator("#settings-toggle").click()
            trip = drive_trip(page)
            saved["trip"] = {k: trip[k] for k in ("live", "summary", "share")}
            check(re.search(r"Est\. fare \$\d+\.\d\d$", trip["live"]) is not None, "saved: the live meter shows the estimated fare")
            check(trip["prompt while running"] == 0 and trip["prompt on summary"] == 0, "saved: no prompt during or after a trip")
            check(any(line.startswith("Est. fare $") for line in trip["summary"]) and trip["share"][-1] == ESTIMATE_NOTE,
                  "saved: summary has the fare, share ends with the note")
            check(isinstance(trip["record"]["fare"], (int, float)), "saved: the saved trip has a fare")
            page.reload()
            page.wait_for_function("() => !!window.__WARN_DRIVER_TEST__", timeout=60000)
            check(stored_tariff(page) == SAVED, "saved: stored values unchanged after a trip and a reload")
            context.close()

            # ---- 3. An install with only some values saved: kept as they are, no fare until all five are set ----
            context, page = open_meter(browser, PARTIAL)
            watch(context, page, "partial")
            partial = out.setdefault("partial", {})
            check(stored_tariff(page) == {k: PARTIAL.get(k) for k in TARIFF_KEYS}, "partial: stored values unchanged on load")
            check(page.locator("#driver-tariff-prompt").count() == 1, "partial: the set-your-tariff prompt")
            partial["planner fare"] = plan_route(page)[0]
            check(partial["planner fare"] is None, "partial: no planner fare line")
            page.locator("#settings-toggle").click()
            check(page.locator("#tariff-ratePerMile").input_value() == "2.80", "partial: the saved rate shows in its field")
            check(stored_tariff(page) == {k: PARTIAL.get(k) for k in TARIFF_KEYS}, "partial: still unchanged after use")
            context.close()
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
