"""WARN Drive page, module graph, hazard cards, spoken alerts, mute."""
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8776)
ROOT = f"http://127.0.0.1:{PORT}/"
KENAI = [60.5603, -151.2235]
SOLDOTNA = [60.4864, -151.0583]
MILE = 1609.344

INIT = """
(() => {
  window.__spoken = [];
  const synth = {
    speaking: false,
    speak(utterance) { window.__spoken.push(utterance.text); setTimeout(() => utterance.onend && utterance.onend(), 20); },
    cancel() {},
  };
  Object.defineProperty(window, "speechSynthesis", { configurable: true, value: synth });
  if (!window.SpeechSynthesisUtterance) {
    window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  }
  const realFetch = window.fetch.bind(window);
  window.__mockHazards = [];
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : (input.url || String(input));
    if (window.__mockHazards !== undefined && url.includes("/rest/v1/public_hazards")) {
      return Promise.resolve(new Response(JSON.stringify(window.__mockHazards), {
        status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return realFetch(input, init);
  };
  const watchers = new Map();
  let nextId = 1;
  const start = { coords: { latitude: 60.5603, longitude: -151.2235, accuracy: 8, speed: null, heading: null }, timestamp: Date.now() };
  window.__emitFix = (fix) => {
    const position = { coords: { accuracy: 8, ...fix }, timestamp: Date.now() };
    for (const callback of watchers.values()) callback(position);
  };
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: {
      getCurrentPosition(success) { setTimeout(() => success(start), 10); },
      watchPosition(success) { const id = nextId++; watchers.set(id, success); return id; },
      clearWatch(id) { watchers.delete(id); },
    },
  });
})();
"""


def serve():
    return start_server(PORT)


def hav(a, b):
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    v = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(v))


def bearing(a, b):
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dl = math.radians(b[0] - a[0])
    y = math.sin(dl) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def point_at(line, metres):
    walked = 0
    for a, b in zip(line, line[1:]):
        step = hav(a, b)
        if walked + step >= metres:
            f = (metres - walked) / step if step else 0
            return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]
        walked += step
    return line[-1]


def static_graph(entry):
    """Every module reachable from `entry` by static imports."""
    seen, todo = set(), [os.path.normpath(os.path.join(REPO, entry))]
    while todo:
        path = todo.pop()
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        source = open(path, encoding="utf-8").read()
        for target in re.findall(r'''(?:import|export)[^"';(]*?from\s*["']([^"']+)["']''', source):
            if target.startswith("."):
                todo.append(os.path.normpath(os.path.join(os.path.dirname(path), target)))
    return sorted(os.path.relpath(p, REPO).replace("\\", "/") for p in seen)


def page_entries(page):
    """The module scripts an HTML page loads."""
    html = open(os.path.join(REPO, page), encoding="utf-8").read()
    return re.findall(r'<script[^>]+type="module"[^>]+src="\./([^"]+)"', html)


def page_static_graph(page):
    graph = set()
    for entry in page_entries(page):
        graph.update(static_graph(entry))
    return sorted(graph)


def main():
    out, console, external, failures = {}, {}, [], []

    def check(condition, what):
        if not condition:
            failures.append(what)

    # Module rules (CLAUDE.md "Family tree"), static side: every existing page's static graph.
    pages = sorted(f for f in os.listdir(REPO) if f.endswith(".html"))
    out["pages"] = pages
    graphs = {page: page_static_graph(page) for page in pages}
    out["static graphs"] = graphs
    restricted = {"js/cards.js", "js/alerts.js", "js/report.js"}
    for page, graph in graphs.items():
        allowed = set()
        if page in ("warn.html", "drive.html"):
            allowed |= {"js/cards.js", "js/alerts.js"}
        if page == "drive.html":
            allowed.add("js/report.js")
        check(not (set(graph) & restricted - allowed), f"{page}: static graph has no restricted module beyond {sorted(allowed) or 'none'}")
    # Even on alerts pages the static graph must not pull cards/alerts in: they arrive by the gated dynamic import.
    check(not (set(graphs["index.html"]) & restricted), "index.html static graph has no cards/alerts/report")
    check(not (set(graphs["warn.html"]) & restricted), "warn.html static graph has no cards/alerts/report (dynamic only)")
    # No module statically imports report.js; nothing but alerts.js statically imports cards.js; nothing imports alerts.js.
    importers = {}
    for name in os.listdir(os.path.join(REPO, "js")):
        src = open(os.path.join(REPO, "js", name), encoding="utf-8").read()
        for target in re.findall(r'''(?:import|export)[^"';(]*?from\s*["']\./([^"']+)["']''', src):
            importers.setdefault(target, []).append(name)
    out["static importers"] = {k: importers.get(k, []) for k in ("report.js", "cards.js", "alerts.js")}
    check(importers.get("report.js", []) == [], "nothing statically imports report.js")
    check(importers.get("cards.js", []) == ["alerts.js"], "only alerts.js statically imports cards.js")
    check(importers.get("alerts.js", []) == [], "nothing statically imports alerts.js")
    out["data-app"] = {page: re.search(r'<html[^>]*data-app="([^"]*)"', open(os.path.join(REPO, page), encoding="utf-8").read()) for page in pages}
    out["data-app"] = {k: (m.group(1) if m else None) for k, m in out["data-app"].items()}
    check(out["data-app"].get("index.html") == "map" and out["data-app"].get("warn.html") == "alerts", "page markers: index=map, warn=alerts")

    proc = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)

            def open_page(path, name):
                console[name] = []
                context = browser.new_context(viewport={"width": 390, "height": 844})
                context.add_init_script(INIT)
                context.on("request", lambda req: external.append((req.method, req.url.split("?")[0]))
                           if urlparse(req.url).hostname not in ("localhost", "127.0.0.1")
                           and not req.url.startswith(("data:", "blob:")) else None)
                page = context.new_page()
                scripts = []
                page.on("request", lambda req: scripts.append(urlparse(req.url).path) if req.url.endswith(".js") else None)
                page.on("console", lambda m: console[name].append((m.type, m.text[:200])) if m.type in ("error", "warning") else None)
                page.on("pageerror", lambda e: console[name].append(("pageerror", str(e)[:300])))
                page.goto(ROOT + path)
                page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
                page.wait_for_load_state("networkidle")
                return context, page, scripts

            # index.html (the Map): loads none of report.js, cards.js, alerts.js; no voice strip, no cards; plain popup.
            context, page, scripts = open_page("?test=1", "index")
            out["index scripts"] = sorted(set(scripts))
            check(not any(s.endswith(("/report.js", "/cards.js", "/alerts.js")) for s in scripts),
                  "index.html loads none of report.js, cards.js, alerts.js")
            out["index title"] = page.title()
            out["index why"] = page.locator(".about-why").count()
            check(page.locator("#alert-mute").count() == 0 and page.locator("#alert-cards").count() == 0, "Map: no voice strip, no mute, no card host")
            check(not page.locator("#hazard-status").inner_text().lower().startswith("voice") and "Voice" not in page.locator("#hazard-status").inner_text(), "Map: no Voice text in the strip")
            map_graph_runtime = sorted(set(scripts))
            context.close()

            # warn.html: same shell and modules, its own name and manifest.
            context, page, scripts = open_page("warn.html?test=1", "warn")
            out["warn title"] = page.title()
            out["warn manifest"] = page.locator("link[rel=manifest]").get_attribute("href")
            out["warn scripts"] = sorted(set(scripts))
            check(out["warn title"].startswith("WARN Alerts"), "warn.html title")
            check(any(s.endswith("/cards.js") for s in scripts) and any(s.endswith("/alerts.js") for s in scripts)
                  and not any(s.endswith("/report.js") for s in scripts), "warn.html loads cards.js and alerts.js but not report.js")
            check(out["warn manifest"] == "./warn.webmanifest", "warn.html manifest")
            check(page.locator("#route-bar .route-slot").count() == 3, "warn.html route bar")
            check(page.locator(".about-why").inner_text().startswith("WARN is a warning system, not a navigation app."),
                  "About: why there's a map")
            manifest = page.evaluate("() => fetch('warn.webmanifest').then((r) => r.json())")
            out["warn manifest json"] = {k: manifest[k] for k in ("id", "name", "start_url")}
            check(out["warn manifest json"] == {"id": "/warn-map/warn", "name": "WARN Alerts", "start_url": "./warn.html"},
                  "warn.webmanifest fields")

            # A straight stretch of the Kenai Spur: the driver at `here`, one moose 0.4 mi ahead, one 0.4 mi behind.
            page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            check(page.locator("#route-hazards-panel").is_visible(), "warn.html route panel")
            line = page.evaluate("() => window.__WARN_MAP_TEST__.routeFeature().geometry.coordinates")
            total = sum(hav(a, b) for a, b in zip(line, line[1:]))
            best = None
            for s in range(1200, int(total) - 1200, 50):
                spread = max(abs((bearing(point_at(line, s - 700), point_at(line, s)) - bearing(point_at(line, s), point_at(line, s + 700)) + 540) % 360 - 180), 0)
                if best is None or spread < best[0]:
                    best = (spread, s)
            here_m = best[1]
            here = point_at(line, here_m)
            course = bearing(point_at(line, here_m - 100), point_at(line, here_m + 100))
            ahead = point_at(line, here_m + 0.4 * MILE)
            behind = point_at(line, here_m - 0.4 * MILE)
            later = point_at(line, here_m + 0.4 * MILE + 1200)
            out["stretch"] = {"at_m": here_m, "bend_deg": round(best[0], 1), "course": round(course, 1)}
            created = (datetime.now(timezone.utc) - timedelta(minutes=5, seconds=10)).isoformat()
            def moose(i, p):
                return {"id": f"test-moose-{i}", "hazard_type": "MOOSE", "latitude": p[1], "longitude": p[0],
                        "created_at": created, "confirmation_count": 2, "heading": None}
            page.evaluate("(rows) => { window.__mockHazards = rows; return window.__WARN_MAP_TEST__.refreshHazards(); }",
                          [moose(1, ahead), moose(2, behind)])
            page.wait_for_timeout(500)

            # The card: open it from the route's hazard list.
            page.locator(".route-hazard-row").first.click()
            page.locator(".hazard-card-popup .hazard-card").wait_for(timeout=5000)
            card = page.locator(".hazard-card-popup .hazard-card")
            out["popup card"] = {
                "title": card.locator(".hazard-card-title").inner_text(),
                "meta": card.locator(".hazard-card-meta").inner_text(),
                "band": card.evaluate("(el) => getComputedStyle(el, '::before').backgroundColor"),
                "slots": card.locator(".hazard-card-action-slot").count(),
                "buttons": card.locator("button").count(),
            }
            check(out["popup card"]["title"] == "Moose" and out["popup card"]["slots"] == 3
                  and out["popup card"]["buttons"] == 0, "popup is a card with a 3-slot placeholder and no buttons")
            check("last seen 5 min ago" in out["popup card"]["meta"] and "2 confirmations" in out["popup card"]["meta"],
                  "card age and confirmations")
            page.screenshot(path=os.path.join(SHOTS, "step11-card-popup.png"))
            page.locator(".maplibregl-popup-close-button").dispatch_event("click")
            page.locator("#clear-route").click()

            # Follow mode, then fixes approaching the moose ahead along the Spur.
            page.locator("#locate").click()
            page.wait_for_timeout(800)
            for back in (500, 300, 0, -100, -200):
                p = point_at(line, here_m - back)
                page.evaluate("(f) => window.__emitFix(f)", {"latitude": p[1], "longitude": p[0], "speed": 20, "heading": course})
                page.wait_for_timeout(300)
            page.wait_for_timeout(500)
            spoken = page.evaluate("() => window.__spoken")
            out["spoken"] = spoken
            metres = hav(here, ahead)
            expected = f"Caution, Moose reported {metres / MILE:.1f} miles away, last seen 5 minutes ago."
            out["expected"] = expected
            check(spoken == [expected], "one spoken alert with the expected text (none for the moose behind)")
            out["alert cards"] = page.locator("#alert-cards .hazard-card").all_inner_texts()
            check(len(out["alert cards"]) == 1 and out["alert cards"][0].startswith("Moose"), "one alert card")
            page.screenshot(path=os.path.join(SHOTS, "step11-alert-card.png"))

            # No repeat within 20 minutes, even after leaving range and coming back into it.
            p = point_at(line, here_m - 1500)
            page.evaluate("(f) => window.__emitFix(f)", {"latitude": p[1], "longitude": p[0], "speed": 20, "heading": course})
            for back in (300, 0):
                p = point_at(line, here_m - back)
                page.evaluate("(f) => window.__emitFix(f)", {"latitude": p[1], "longitude": p[0], "speed": 20, "heading": course})
                page.wait_for_timeout(300)
            check(page.evaluate("() => window.__spoken.length") == 1, "no repeat within 20 minutes")

            # Mute: a third moose further on gets its card but no speech.
            page.locator("#alert-mute").click()
            out["mute"] = [page.locator("#alert-mute").inner_text(),
                           page.locator("#hazard-status").get_attribute("class")]
            check(out["mute"][0] == "Voice muted" and "is-muted" in out["mute"][1], "muted indicator in the strip")
            page.evaluate("(rows) => { window.__mockHazards = rows; return window.__WARN_MAP_TEST__.refreshHazards(); }",
                          [moose(1, ahead), moose(2, behind), moose(3, later)])
            page.wait_for_timeout(500)
            for along in (0.4 * MILE + 600, 0.4 * MILE + 700, 0.4 * MILE + 800):
                p = point_at(line, here_m + along)
                page.evaluate("(f) => window.__emitFix(f)", {"latitude": p[1], "longitude": p[0], "speed": 20, "heading": course})
                page.wait_for_timeout(300)
            out["spoken after mute"] = page.evaluate("() => window.__spoken")
            out["cards after mute"] = page.locator("#alert-cards .hazard-card").count()
            check(len(out["spoken after mute"]) == 1, "mute suppresses speech")
            check(out["cards after mute"] >= 1 and page.locator('#alert-cards [data-hazard-id="test-moose-3"]').count() == 1,
                  "mute still shows the card")
            context.close()

            # The Map with the same moose 0.4 mi ahead: no speech, no card, no strip; the plain popup from before step 26.
            context, page, scripts = open_page("?test=1", "index-alerts")
            page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            page.evaluate("(rows) => { window.__mockHazards = rows; return window.__WARN_MAP_TEST__.refreshHazards(); }",
                          [moose(1, ahead), moose(2, behind)])
            page.wait_for_timeout(500)
            page.locator(".route-hazard-row").first.click()
            page.locator(".maplibregl-popup").wait_for(timeout=5000)
            plain = page.locator(".maplibregl-popup .hazard-popup")
            out["map popup"] = [l for l in plain.inner_text().split("\n") if l] if plain.count() else page.locator(".maplibregl-popup").inner_text()[:80]
            check(page.locator(".hazard-card").count() == 0 and plain.count() == 1 and out["map popup"][0] == "Moose" and "Animals" in out["map popup"][1],
                  "Map: plain popup (title, category, age), not a card")
            page.locator(".maplibregl-popup-close-button").dispatch_event("click")
            page.locator("#clear-route").click()
            page.locator("#locate").click()
            page.wait_for_timeout(800)
            for back in (500, 300, 0, -100, -200):
                p = point_at(line, here_m - back)
                page.evaluate("(f) => window.__emitFix(f)", {"latitude": p[1], "longitude": p[0], "speed": 20, "heading": course})
                page.wait_for_timeout(300)
            page.wait_for_timeout(500)
            out["map spoken"] = page.evaluate("() => window.__spoken")
            out["map cards"] = page.locator(".hazard-card").count()
            out["map strip"] = page.locator("#hazard-status").inner_text()
            check(out["map spoken"] == [] and out["map cards"] == 0, "Map: moose 0.4 mi ahead -> no speech, no card")
            check("Voice" not in out["map strip"] and page.locator("#alert-mute").count() == 0, "Map: no voice strip at any point")
            check(not any(s.endswith(("/report.js", "/cards.js", "/alerts.js")) for s in scripts), "Map: still loads none of the three after following")
            context.close()
            browser.close()
    finally:
        proc.terminate()

    out["console"] = console
    out["external"] = sorted(set(external))
    check(not console["index"] and not console["warn"] and not console.get("index-alerts"), "console clean on both pages")
    check(all(m in ("GET", "OPTIONS") and u.endswith("/rest/v1/public_hazards") for m, u in set(external)),
          "only the Supabase hazards GET")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
