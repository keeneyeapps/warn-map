"""Per-trip GPS trace (js/trace.js): one row per fix, exact distance and wait kept, trimming, export, delete, quota.

A simulated trip along the Kenai Spur with a stop and four bad fixes (low accuracy, a jump, a clock going backwards,
no position) must leave one trace row per fix, the exact (unrounded) distance and wait in the trip record, and a CSV
that matches. Nothing is uploaded: the only requests are the app's own files and the hazards GET.
"""
import csv
import io
import json
import math
import os
import re
import sys
import time
from collections import Counter
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8779)
BASE = f"http://127.0.0.1:{PORT}/?test=1"
METER = f"http://127.0.0.1:{PORT}/meter.html?test=1"
KENAI = [60.5603, -151.2235]
SOLDOTNA = [60.4864, -151.0583]
COLUMNS = ["t_s", "lat", "lon", "accuracy_m", "speed_ms", "interval_s", "straight_m", "road_m", "added_m", "slow",
           "wait_added_s", "reject"]

INIT = """
(() => {
  navigator.share = () => Promise.resolve();
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


def build_fixes(line):
    """16 moving fixes, a 4-fix stop after the 8th, and four bad fixes. Returns (fixes, expected reject counts)."""
    moving = [point_at(line, 120 + i * 650) for i in range(16)]
    fixes, clock = [], int(time.time() * 1000)

    def fix(point, ts, **extra):
        coords = {"latitude": point[1], "longitude": point[0], "speed": 20, **extra}
        return {"coords": coords, "timestamp": ts}

    for i, point in enumerate(moving):
        if i > 0:
            clock += int(hav(moving[i - 1], point) / 20 * 1000)
        fixes.append(fix(point, clock, speed=20 if i else 0))
        if i == 3:
            fixes.append(fix(point, clock + 500, accuracy=80))                          # low_accuracy
        if i == 5:
            fixes.append(fix([point[0], point[1] + 0.5], clock + 1000))                 # speed_jump (~55 km in 1 s)
        if i == 7:
            for k in range(1, 5):                                                       # the stop: 4 fixes, 45 s apart
                fixes.append(fix(point, clock + 45000 * k, speed=0))
            clock += 180000
        if i == 9:
            fixes.append(fix(point, clock - 5000))                                      # bad_time (clock goes backwards)
        if i == 10:
            fixes.append({"coords": {"latitude": None, "longitude": None, "speed": None}, "timestamp": clock + 200})
    return fixes, Counter({"low_accuracy": 1, "speed_jump": 1, "bad_time": 1, "no_position": 1, "": len(fixes) - 4})


def parse_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    return rows[0], rows[1:]


def main():
    out, console, external, failures = {}, [], [], []
    downloads = []

    def check(condition, what):
        if not condition:
            failures.append(what)

    proc = start_server(PORT)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)
            context = browser.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True)
            context.add_init_script(INIT)
            context.on("request", lambda req: external.append((req.method, req.url.split("?")[0]))
                       if urlparse(req.url).scheme in ("http", "https")      # the browser's own edge:// UI is not the page
                       and urlparse(req.url).hostname not in ("localhost", "127.0.0.1") else None)
            page = context.new_page()
            page.on("console", lambda m: console.append((m.type, m.text[:200])) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(("pageerror", str(e)[:300])))
            page.on("download", lambda d: downloads.append(d.suggested_filename))

            # 1. The Map: no trace UI and nothing stored.
            page.goto(BASE)
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
            page.wait_for_load_state("networkidle")
            page.locator("#settings-toggle").click()
            check(page.locator("#driver-trace-note, .driver-export-trace, .driver-delete-traces").count() == 0,
                  "Map: no trace note or buttons in the page")
            check(page.evaluate("() => Object.keys(localStorage).filter((k) => k.startsWith('warn-map-trace'))") == [],
                  "Map: no trace keys stored")

            # 2. A simulated trip, with a reload in the middle.
            page.goto(METER)
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__ && !!window.__WARN_DRIVER_TEST__", timeout=60000)
            page.wait_for_load_state("networkidle")
            page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            line = page.evaluate("() => window.__WARN_MAP_TEST__.routeFeature().geometry.coordinates")
            page.locator("#clear-route").click()
            fixes, expected_rejects = build_fixes(line)
            out["fixes emitted"] = len(fixes)
            check(len(fixes) == 24, "24 fixes emitted")

            page.locator("#start-trip").click()
            for fix in fixes[:14]:
                page.evaluate("(fix) => window.__emitFix(fix)", fix)
                page.wait_for_timeout(250)
            page.wait_for_timeout(500)
            before = page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()")
            check(before["traceRows"] == 14, f"one trace row per fix so far (14), got {before['traceRows']}")
            page.wait_for_timeout(11000)  # the running trace is saved with the trip every 10 s
            page.reload()
            page.wait_for_function("() => !!window.__WARN_DRIVER_TEST__", timeout=60000)
            page.wait_for_timeout(1000)
            after = page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()")
            check(after is not None and after["traceRows"] == 14, "the running trace survives a reload (14 rows)")
            for fix in fixes[14:]:
                page.evaluate("(fix) => window.__emitFix(fix)", fix)
                page.wait_for_timeout(250)
            page.wait_for_timeout(1500)
            final = page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()")
            out["final state"] = final
            check(final["traceRows"] == len(fixes), f"one trace row per fix: {final['traceRows']} of {len(fixes)}")

            page.locator("#end-trip").click()
            page.locator("#driver-trip-summary").wait_for(timeout=10000)
            page.locator(".driver-trip .driver-button", has_text="Dismiss").click()
            record = page.evaluate("() => JSON.parse(localStorage.getItem('warn-map-trips'))[0]")
            out["record"] = record
            check(abs(record["metersExact"] - final["meters"]) < 1e-6, "record stores the exact distance in metres")
            check(abs(record["waitSecondsExact"] - final["waitSeconds"]) < 1e-6, "record stores the exact wait seconds")
            check(record["miles"] == round(record["metersExact"] / 1609.344, 2), "rounded miles still derive from the exact metres")
            check(record["waitSeconds"] == round(record["waitSecondsExact"]), "rounded wait still derives from the exact seconds")
            check(not float(record["metersExact"]).is_integer() and record["metersExact"] > 5000, "exact metres is a real unrounded figure")
            fare_now = page.evaluate("""async (r) => (await import("./js/driver.js")).fareFor(r.metersExact / 1609.344, r.waitSecondsExact)""", record)
            check(abs(record["fare"] - fare_now) < 1e-9, "fare is unchanged: the same formula over the exact figures")
            check(page.evaluate("() => localStorage.getItem('warn-map-trace-running')") is None, "running trace cleared at the end")

            traces = page.evaluate("() => JSON.parse(localStorage.getItem('warn-map-traces'))")
            check(len(traces) == 1 and traces[0]["startTime"] == record["startTime"], "one saved trace, keyed to the trip")
            header, rows = parse_csv(traces[0]["csv"])
            out["csv header"] = header
            out["csv first rows"] = rows[:3]
            check(header == COLUMNS, "CSV columns")
            check(len(rows) == len(fixes) == traces[0]["rows"], "CSV has one row per fix")
            rejects = Counter(r[11] for r in rows)
            check(rejects == expected_rejects, f"reject reasons {dict(rejects)}")
            accepted = [r for r in rows if r[11] == ""]
            check(abs(sum(float(r[8]) for r in accepted) - record["metersExact"]) < 0.001 * len(rows),
                  "the added_m column sums to the exact distance")
            check(abs(sum(float(r[10]) for r in accepted if r[10]) - record["waitSecondsExact"]) < 0.01 * len(rows),
                  "the wait_added_s column sums to the exact wait")
            check(accepted[0][8] == "0.000" and accepted[0][6] == "", "first accepted fix adds 0 and has no straight distance")
            check(any(r[7] != "" for r in accepted), "snapped (road) metres recorded")
            jump = next(r for r in rows if r[11] == "speed_jump")
            check(float(jump[6]) > 50000 and jump[8] == "", "a rejected jump records its straight distance and adds nothing")
            check(next(r for r in rows if r[11] == "no_position")[1] == "", "a fix with no position has blank lat/lon")
            check(all(re.fullmatch(r"-?\d+\.\d{5}", r[1]) for r in accepted), "positions have 5 decimals")
            check(any(r[9] == "1" for r in accepted) and any(r[9] == "0" for r in accepted), "slow flag recorded both ways")

            # 3. The trip list: buttons, the on-device note, export on tap only, delete.
            page.locator("#settings-toggle").click()
            check(page.locator("#driver-trace-note").count() == 1
                  and "never uploaded" in page.locator("#driver-trace-note").inner_text(), "on-device note shown on the meter page")
            check(page.locator(".driver-trip-list li .driver-export-trace").count() == 1
                  and page.locator(".driver-trip-list li .driver-delete-traces").count() == 1, "Export trace and Delete traces on the entry")
            page.screenshot(path=os.path.join(SHOTS, "trace-trips-list.png"))
            check(downloads == [], "nothing is downloaded until the button is tapped")
            with page.expect_download() as info:
                page.locator(".driver-export-trace").click()
            download = info.value
            path = os.path.join(SCRATCH, "trace-export.csv")
            download.save_as(path)
            text = open(path, encoding="utf-8").read()
            out["download name"] = download.suggested_filename
            check(re.fullmatch(r"warn-trace-\d{8}-\d{4}\.csv", download.suggested_filename) is not None, "export file name")
            check(text == traces[0]["csv"], "the exported file is the stored CSV")
            h2, r2 = parse_csv(text)
            check(h2 == COLUMNS and len(r2) == len(fixes), "export CSV columns and row count")
            check(len(downloads) == 1, "exactly one download, from the tap")

            # 4. Trimming: 12 newer traces leave only the newest 10; the real trip's trace is the oldest and goes.
            result = page.evaluate("""async (start) => {
              const t = await import("./js/trace.js");
              const kept = [];
              for (let i = 1; i <= 12; i++) {
                const log = t.newLog();
                t.appendRow(log, { t: 0, lat: 60.1, lon: -151.1, accuracy: 5, speed: 1 });
                t.storeTrace(start + i * 1000, log);
              }
              return t.loadTraces().map((x) => x.startTime - start);
            }""", record["startTime"])
            out["kept after 12 stores"] = result
            check(result == [12000, 11000, 10000, 9000, 8000, 7000, 6000, 5000, 4000, 3000],
                  "only the newest 10 traces are kept, newest first; the oldest trace went first")

            # 5. The row cap, and the character budget (oldest trace goes first).
            cap = page.evaluate("""async () => {
              const t = await import("./js/trace.js");
              const log = t.newLog();
              for (let i = 0; i < 20005; i++) t.appendRow(log, { t: i, lat: 60.12345, lon: -151.12345, accuracy: 8.1, speed: 12.34, interval: 1, straight: 12.3456, road: 12.4, added: 12.4, slow: false, waitAdded: 0 });
              const stored = t.storeTrace(9e15, log);
              const kept = t.loadTraces();
              return { lines: log.lines.length, dropped: log.dropped, saved: stored.saved, kept: kept.length,
                       newest: kept[0].startTime, rows: kept[0].rows, csvLines: kept[0].csv.split("\\n").length - 1,
                       chars: kept.reduce((s, x) => s + x.csv.length, 0), max: t.MAX_TOTAL_CHARS };
            }""")
            out["row cap"] = cap
            check(cap["lines"] == 20000 and cap["dropped"] == 5, "at most 20,000 rows per trip; the rest are counted")
            check(cap["saved"] and cap["newest"] == 9e15 and cap["rows"] == 20000 and cap["csvLines"] == 20001,
                  "a full 20,000-row trace is stored (header + 20,000 rows)")
            over = page.evaluate("""async () => {
              const t = await import("./js/trace.js");
              const log = t.newLog();
              for (let i = 0; i < 20000; i++) t.appendRow(log, { t: i, lat: 60.12345, lon: -151.12345, accuracy: 8.1, speed: 12.34, interval: 1, straight: 12.3456, road: 12.4, added: 12.4, slow: false, waitAdded: 0 });
              t.storeTrace(9e15 + 1, log); // a second full trace: together over the budget
              const kept = t.loadTraces();
              return { kept: kept.length, newest: kept[0].startTime, chars: kept.reduce((s, x) => s + x.csv.length, 0), max: t.MAX_TOTAL_CHARS };
            }""")
            out["budget"] = over
            check(cap["chars"] <= cap["max"] and over["chars"] <= over["max"] and over["kept"] == 1 and over["newest"] == 9e15 + 1,
                  "over the character budget the oldest traces go first and the newest stays")

            # 6. Delete traces: the button removes every trace, keeps the trip record.
            page.evaluate("""async () => {
              const t = await import("./js/trace.js");
              const log = t.newLog(); t.appendRow(log, { t: 0, lat: 60.1, lon: -151.1 });
              const start = JSON.parse(localStorage.getItem('warn-map-trips'))[0].startTime;
              t.storeTrace(start, log);
            }""")
            page.locator("#settings-toggle").click()
            page.locator("#settings-toggle").click()
            check(page.locator(".driver-delete-traces").count() >= 1, "Delete traces shows for a trip that has a trace")
            page.locator(".driver-delete-traces").first.click()
            check(page.evaluate("() => localStorage.getItem('warn-map-traces')") is None, "Delete traces removes the traces")
            check(page.locator(".driver-export-trace, .driver-delete-traces").count() == 0, "the buttons go with the traces")
            check(page.evaluate("() => JSON.parse(localStorage.getItem('warn-map-trips')).length") == 1, "the trip record stays")

            # 7. Storage full: the trace is lost, the trip is not, and the meter keeps working.
            page.evaluate("""() => {
              const real = Storage.prototype.setItem;
              window.__realSetItem = real;
              Storage.prototype.setItem = function (key, value) {
                if (String(key).startsWith("warn-map-trace")) throw new DOMException("full", "QuotaExceededError");
                return real.call(this, key, value);
              };
            }""")
            page.locator("#start-trip").click()
            now = int(time.time() * 1000)
            for i, point in enumerate([point_at(line, 120 + k * 650) for k in range(4)]):
                page.evaluate("(fix) => window.__emitFix(fix)", {"coords": {"latitude": point[1], "longitude": point[0], "speed": 20},
                                                                 "timestamp": now + i * 30000})
                page.wait_for_timeout(250)
            page.wait_for_timeout(500)
            quota_state = page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()")
            check(quota_state is not None and quota_state["traceRows"] == 4 and quota_state["meters"] > 1000,
                  "storage full: the trip meter keeps counting")
            page.locator("#end-trip").click()
            page.locator("#driver-trip-summary").wait_for(timeout=10000)
            check(page.evaluate("() => JSON.parse(localStorage.getItem('warn-map-trips')).length") == 2,
                  "storage full: the trip record is still saved")
            check(page.evaluate("() => localStorage.getItem('warn-map-traces')") is None, "storage full: no trace stored")
            check("Trace not saved" in page.locator("#location-status").inner_text(), "storage full: the driver is told")
            page.locator(".driver-trip .driver-button", has_text="Dismiss").click()

            # 8. Clear all trips takes the traces with it.
            page.evaluate("""() => { Storage.prototype.setItem = window.__realSetItem; }""")
            page.evaluate("""async () => {
              const t = await import("./js/trace.js");
              const log = t.newLog(); t.appendRow(log, { t: 0, lat: 60.1, lon: -151.1 });
              t.storeTrace(JSON.parse(localStorage.getItem('warn-map-trips'))[0].startTime, log);
            }""")
            page.locator("#settings-toggle").click()
            page.locator("#settings-toggle").click()
            check(page.evaluate("() => localStorage.getItem('warn-map-traces') !== null"), "a trace is stored before Clear all")
            page.locator(".driver-trips .driver-button", has_text="Clear all").click()
            check(page.evaluate("() => localStorage.getItem('warn-map-traces')") is None, "Clear all trips also clears the traces")

            # 9. Cab-meter wait billing on simulated trips: five stops each, cumulative wait floored to whole minutes.
            #    (Stops do not restart the count: five 40 s stops are 200 s = 3 minutes, not zero.)
            table = []
            for seconds, minutes in ((10, 0), (13, 1), (25, 2), (40, 3)):
                points = [point_at(line, 120 + i * 500) for i in range(11)]
                fixes, clock = [], int(time.time() * 1000)
                for i, point in enumerate(points):
                    if i > 0:
                        clock += int(hav(points[i - 1], point) / 20 * 1000)
                    fixes.append({"coords": {"latitude": point[1], "longitude": point[0], "speed": 20 if i else 0}, "timestamp": clock})
                    if i in (1, 3, 5, 7, 9):
                        for k in range(1, seconds + 1):
                            fixes.append({"coords": {"latitude": point[1], "longitude": point[0], "speed": 0}, "timestamp": clock + 1000 * k})
                        clock += 1000 * seconds
                page.locator("#start-trip").click()
                page.evaluate("(all) => { for (const fix of all) window.__emitFix(fix); }", fixes)
                page.wait_for_function("(n) => window.__WARN_DRIVER_TEST__.tripState().traceRows === n", arg=len(fixes), timeout=60000)
                state = page.evaluate("() => window.__WARN_DRIVER_TEST__.tripState()")
                distance_only = page.evaluate("async (m) => (await import('./js/driver.js')).fareFor(m / 1609.344, 0)", state["meters"])
                wait_charge = round(state["fare"] - distance_only, 2)
                table.append({"stops": f"5 x {seconds} s", "wait s": round(state["waitSeconds"], 1), "wait charge": wait_charge,
                              "fare": state["fare"]})
                check(abs(state["waitSeconds"] - 5 * seconds) <= 1, f"5 x {seconds} s stops: about {5 * seconds} s of cumulative wait")
                check(abs(wait_charge - 0.5 * minutes) < 1e-9, f"5 x {seconds} s stops: {minutes} full minute(s) -> ${0.5 * minutes:.2f} wait")
                page.locator("#end-trip").click()
                page.locator("#driver-trip-summary").wait_for(timeout=10000)
                saved = page.evaluate("() => JSON.parse(localStorage.getItem('warn-map-trips'))[0]")
                check(abs(saved["fare"] - state["fare"]) < 1e-9, f"5 x {seconds} s stops: the saved fare equals the live fare")
                check(f"${state['fare']:.2f}" in page.locator("#driver-trip-summary").inner_text(), f"5 x {seconds} s stops: the summary shows the fare")
                page.locator(".driver-trip .driver-button", has_text="Dismiss").click()
            out["wait table"] = table
            browser.close()
    finally:
        proc.terminate()

    out["console"] = console
    out["external"] = sorted(set(external))
    check(not console, "console clean")
    check(all(m in ("GET", "OPTIONS") and u.endswith("/rest/v1/public_hazards") for m, u in set(external)),
          "nothing leaves the device but the hazards GET")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    for failure in failures:
        print("SOFTFAIL", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
