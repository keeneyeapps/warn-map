"""WARN Watch (watch.html): the read-only hazard board for a desktop monitor.

Checks: the list and summary match mocked hazards (newest first, colours, ages, confirmations, a place description
that an independent re-implementation of the on-device lookup agrees with); a new hazard appears after a refresh and is
highlighted; a failed fetch keeps the old data and says stale with the last good time; clicking a row flies the map to
the hazard and opens its popup; polling is every 30 s; the kiosk behaviour (wake lock, refresh on return, quiet reload on
a new version); no geolocation request, none of the Drive/meter modules, no app.js/routing.js; nothing links to or
mentions the page; works offline from its own cached copy; clean console at 1920x1080 and 1366x768.
"""
import datetime as dt
import json
import math
import os
import re
import sys
import time
from urllib.parse import urlparse

import numpy as np
from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8782)
ROOT = f"http://127.0.0.1:{PORT}"
URL = f"{ROOT}/watch.html?test=1"
FORBIDDEN_MODULES = ("cards.js", "alerts.js", "driver.js", "trace.js", "report.js", "app.js", "routing.js", "addresses.js")

INIT = """
(() => {
  window.__fetchLog = [];
  window.__mockHazards = [];
  window.__mockFail = false;
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.includes("/rest/v1/public_hazards")) {
      window.__fetchLog.push(Date.now());
      if (window.__mockFail) return Promise.reject(new TypeError("Failed to fetch"));
      return Promise.resolve(new Response(JSON.stringify(window.__mockHazards), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return realFetch(input, init);
  };
  window.__geo = 0;
  Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
    getCurrentPosition() { window.__geo += 1; }, watchPosition() { window.__geo += 1; return 1; }, clearWatch() {} } });
  window.__wake = 0;
  Object.defineProperty(navigator, "wakeLock", { configurable: true, value: {
    request() { window.__wake += 1; return Promise.resolve({ release() { return Promise.resolve(); }, addEventListener() {} }); } } });
  window.__reloads = 0;
  window.__WARN_WATCH_RELOAD = () => { window.__reloads += 1; };
  window.__WARN_WATCH_QUIET_MS = 2000;
})();
"""

# Category colours and labels as hazards.js defines them.
COLOURS = {"Animals": "#FF9800", "Road Damage": "#FDD835", "Obstructions": "#1976D2", "Visibility": "#E0E0E0"}


def read(path):
    return open(os.path.join(REPO, path), encoding="utf-8").read()


# ---- an independent re-implementation of the on-device place lookup, from the same data files ----
class Describer:
    def __init__(self):
        graph = json.load(open(os.path.join(REPO, "graph", "kenai-roads.json"), encoding="utf-8"))
        skip = {i for i, c in enumerate(graph["classes"]) if c in ("service", "service_link", "track", "track_link")}
        rows = []
        for edge in graph["edges"]:
            if not edge[6] or edge[3] in skip:
                continue
            g = edge[7]
            for a, b in zip(g, g[1:]):
                rows.append((a[0], a[1], b[0], b[1], edge[6]))
        self.names = [r[4] for r in rows]
        self.seg = np.array([r[:4] for r in rows], dtype=np.float64)
        data = json.load(open(os.path.join(REPO, "addresses", "kenai-addresses.json"), encoding="utf-8"))
        sums = {}
        for row in data["addresses"]:
            if row[4] >= 0:
                s = sums.setdefault(row[4], [0.0, 0.0, 0])
                s[0] += row[5]; s[1] += row[6]; s[2] += 1
        self.communities = [(data["places"][k], s[0] / s[2], s[1] / s[2]) for k, s in sums.items() if s[2] >= 5]

    def road(self, lon, lat):
        sx = 111195 * math.cos(math.radians(lat))
        ax = (self.seg[:, 0] - lon) * sx; ay = (self.seg[:, 1] - lat) * 111195
        bx = (self.seg[:, 2] - lon) * sx; by = (self.seg[:, 3] - lat) * 111195
        dx, dy = bx - ax, by - ay
        ls = dx * dx + dy * dy
        t = np.where(ls == 0, 0, np.clip(-(ax * dx + ay * dy) / np.where(ls == 0, 1, ls), 0, 1))
        d = np.hypot(ax + t * dx, ay + t * dy)
        i = int(np.argmin(d))
        return self.names[i] if d[i] < 250 else None

    def community(self, lon, lat):
        sx = 111195 * math.cos(math.radians(lat))
        best, bd = None, 15000
        for name, clon, clat in self.communities:
            d = math.hypot((clon - lon) * sx, (clat - lat) * 111195)
            if d < bd:
                best, bd = name, d
        return best

    def describe(self, lon, lat):
        road, community = self.road(lon, lat), self.community(lon, lat)
        if road and community: return f"{road}, near {community}"
        if road: return road
        if community: return f"Near {community}"
        return "Not on a named road"


def iso(seconds_ago):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(rid, hazard_type, lat, lon, minutes_ago, confirmations, **extra):
    return {"id": rid, "region_code": "KPB", "category": "x", "hazard_type": hazard_type, "latitude": lat, "longitude": lon,
            "created_at": iso(minutes_ago * 60 + 30), "first_reported_at": iso(minutes_ago * 60 + 30), "expires_at": None,
            "heading": None, "specific": None, "display_text": None, "confirmation_count": confirmations, **extra}


# id, type, lat, lon, minutes ago, confirmations -> expected title, category
HAZARDS = [
    (101, "FOG", 59.6430, -151.5470, 310, 0, "Fog", "Visibility"),
    (102, "DAMAGE_BOTH_LANES", 60.4864, -151.0583, 95, 1, "Rough road, both lanes", "Road Damage"),
    (103, "BLACK_ICE", 60.5333, -151.1180, 30, 4, "Black ice", "Obstructions"),
    (104, "BEAR", 60.5603, -151.2235, 12, 0, "Bear", "Animals"),
    (105, "MOOSE", 60.4150, -150.2000, 3, 2, "Moose", "Animals"),
]
NEW_HAZARD = (106, "ACCIDENT", 60.9000, -149.6000, 0, 0, "Accident", "Obstructions")


def rows_for(items):
    return [row(i[0], i[1], i[2], i[3], i[4], i[5]) for i in items]


def main():
    out, console, external, failures = {}, [], [], []
    offline = [False]  # tile fetches fail by design once the network is cut

    def check(condition, what):
        if not condition:
            failures.append(what)

    # ---- static rules ----
    html = read("watch.html")
    check('data-app="watch"' in html and 'data-app-name="WARN Watch"' in html and "<title>WARN Watch</title>" in html
          and 'href="./watch.webmanifest"' in html, "watch.html name, title and manifest link")
    scripts = re.findall(r'<script[^>]+type="module"[^>]+src="\./([^"]+)"', html)
    check(scripts == ["js/header.js", "js/watch.js"], f"watch.html loads only header.js and watch.js ({scripts})")
    check(not re.search(r"<(input|textarea|select|form)\b|id=\"settings|id=\"locate|route-bar|address-", html), "watch.html has no inputs, gear, search or route tools")
    for name in FORBIDDEN_MODULES:
        check(name not in read("js/watch.js") and name not in read("js/place-describe.js"), f"watch.js does not mention {name}")
    check("geolocation" not in read("js/watch.js") + read("js/place-describe.js"), "no geolocation call in the Watch modules")
    for name in ("start.html", "README.md", "index.html", "warn.html", "meter.html", "manifest.webmanifest", "warn.webmanifest", "meter.webmanifest"):
        text = read(name)
        check("watch.html" not in text and "watch.webmanifest" not in text and "WARN Watch" not in text, f"{name} does not mention or link the Watch page")
    precache = read("js/precache-list.js")
    check(all(f'"./{f}"' in precache for f in ("watch.html", "watch.webmanifest", "js/watch.js", "js/place-describe.js")),
          "the Watch page and its modules are precached")
    manifest = json.loads(read("watch.webmanifest"))
    check(manifest["id"] == "/warn-map/watch" and manifest["start_url"] == "./watch.html" and manifest["scope"] == "./watch.html"
          and manifest["name"] == "WARN Watch" and manifest["short_name"] == "WARN Watch", "watch.webmanifest fields")

    describer = Describer()
    expected_place = {h[0]: describer.describe(h[3], h[2]) for h in HAZARDS + [NEW_HAZARD]}
    out["expected places"] = expected_place
    check(len(set(expected_place.values())) >= 4 and any("near" in v for v in expected_place.values()), "the expected places are varied")

    proc = start_server(PORT)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)

            def open_page(size, hazards, path=URL):
                context = browser.new_context(viewport={"width": size[0], "height": size[1]})
                context.add_init_script(INIT)
                context.add_init_script(f"window.__mockHazards = {json.dumps(rows_for(hazards))};")
                context.on("request", lambda req: external.append((req.method, req.url.split("?")[0]))
                           if urlparse(req.url).scheme in ("http", "https") and urlparse(req.url).hostname not in ("localhost", "127.0.0.1") else None)
                page = context.new_page()
                requests = []
                page.on("request", lambda req: requests.append(urlparse(req.url).path))
                page.on("console", lambda m: console.append((size, m.type, m.text[:160])) if m.type in ("error", "warning") and not offline[0] else None)
                page.on("pageerror", lambda e: console.append((size, "pageerror", str(e)[:200])))
                page.goto(path)
                page.wait_for_function("() => !!window.__WARN_WATCH_TEST__", timeout=60000)
                page.wait_for_function(f"() => document.querySelectorAll('#watch-list .watch-row').length === {len(hazards)}", timeout=30000)
                page.wait_for_function("() => !document.body.innerText.includes('Locating…')", timeout=60000)
                page.wait_for_load_state("networkidle")
                return context, page, requests

            def rows_text(page):
                return page.evaluate("""() => [...document.querySelectorAll('#watch-list .watch-row')].map((r) => ({
                  id: r.dataset.id, title: r.querySelector('.watch-title').textContent, age: r.querySelector('.watch-age').textContent,
                  category: r.querySelector('.watch-category-name').textContent, place: r.querySelector('.watch-place').textContent,
                  confirmations: r.querySelector('.watch-confirmations').textContent, band: getComputedStyle(r).borderLeftColor,
                  isNew: r.classList.contains('is-new'), titleSize: parseFloat(getComputedStyle(r.querySelector('.watch-title')).fontSize) }))""")

            def hexrgb(h):
                h = h.lstrip("#")
                return f"rgb({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)})"

            # ---- both screen sizes: layout, list, console ----
            for size in ((1366, 768), (1920, 1080)):
                label = f"{size[0]}x{size[1]}"
                context, page, requests = open_page(size, HAZARDS)
                panel = page.locator("#watch-panel").bounding_box()
                header = page.locator("#app-header").bounding_box()
                map_box = page.locator("#map").bounding_box()
                check(page.locator("#app-header-variant").inner_text() == "Watch", f"{label}: header band says Watch")
                check(header["height"] <= 44 and abs(panel["x"] + panel["width"] - size[0]) < 1 and abs(panel["y"] - header["height"]) < 1
                      and abs(panel["height"] - (size[1] - header["height"])) < 1, f"{label}: the panel fills the right edge under the band")
                check(abs(map_box["width"] - (size[0] - panel["width"])) < 1 and map_box["width"] > size[0] * 0.65,
                      f"{label}: the map fills most of the screen ({map_box['width']:.0f} of {size[0]})")
                check(300 <= panel["width"] <= 560, f"{label}: panel width {panel['width']:.0f}")
                check(page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), f"{label}: no horizontal scroll")
                first_three = page.evaluate("() => [...document.querySelectorAll('.watch-row')].slice(0, 3).every((r) => r.getBoundingClientRect().bottom <= innerHeight)")
                check(first_three, f"{label}: the three newest rows are on screen")
                data = rows_text(page)
                check(min(r["titleSize"] for r in data) >= 22, f"{label}: large type (row titles {data[0]['titleSize']}px)")
                if size == (1920, 1080):
                    page.screenshot(path=os.path.join(SHOTS, "watch-1920.png"))
                else:
                    page.screenshot(path=os.path.join(SHOTS, "watch-1366.png"))
                    context.close()
                    continue

                # ---- the list and summary match the mocked hazards ----
                newest_first = sorted(HAZARDS, key=lambda h: h[4])
                check([r["id"] for r in data] == [str(h[0]) for h in newest_first], "rows are newest first")
                for r, h in zip(data, newest_first):
                    check(r["title"] == h[6] and r["category"] == h[7], f"row {h[0]}: label {r['title']} / {r['category']}")
                    check(r["band"] == hexrgb(COLOURS[h[7]]), f"row {h[0]}: category colour {r['band']}")
                    check(r["confirmations"] == f"{h[5]} {'confirmation' if h[5] == 1 else 'confirmations'}", f"row {h[0]}: {r['confirmations']}")
                    minutes = h[4]
                    age = "Just now" if minutes < 1 else (f"{minutes} min ago" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min ago")
                    check(r["age"] == age, f"row {h[0]}: age {r['age']} (expected {age})")
                    check(r["place"] == expected_place[h[0]], f"row {h[0]}: place {r['place']!r} (expected {expected_place[h[0]]!r})")
                check(not any(r["isNew"] for r in data), "the first load highlights nothing")
                check(page.locator("#watch-count-number").inner_text() == "5" and page.locator("#watch-count-label").inner_text() == "active hazards",
                      "summary: 5 active hazards")
                chips = page.evaluate("() => [...document.querySelectorAll('.watch-category')].map((c) => c.textContent.replace(/\\s+/g, ' ').trim())")
                check(chips == ["Animals 2", "Obstructions 1", "Road Damage 1", "Visibility 1"], f"summary: counts by category {chips}")
                check(re.fullmatch(r"Updated \d\d:\d\d:\d\d", page.locator("#watch-updated").inner_text()) is not None, "an Updated hh:mm:ss line")
                check(page.locator("#watch-stale").is_hidden(), "not stale after a good fetch")
                shown = page.evaluate("""() => { const m = window.__WARN_WATCH_TEST__.map; const b = m.getBounds();
                  const f = m.querySourceFeatures('live-hazards'); return { n: new Set(f.map((x) => x.properties.id)).size,
                    inView: [...new Set(f.map((x) => x.id))].length }; }""")
                check(shown["n"] == 5, f"the map layer holds all 5 active hazards ({shown})")
                in_view = page.evaluate("""() => { const m = window.__WARN_WATCH_TEST__.map; const b = m.getBounds();
                  return m.querySourceFeatures('live-hazards').every((x) => b.contains(x.geometry.coordinates)); }""")
                check(in_view, "every active hazard is inside the initial map view")
                mentions = page.evaluate("() => /\\b(driver|vehicle|taxi|fare|tariff|trip|cab)\\b/i.test(document.body.innerText)")
                check(not mentions, "nothing about a driver or vehicle is shown")

                # ---- kiosk bits that need no waiting: wake lock, geolocation, modules ----
                check(page.evaluate("() => window.__wake") >= 1, "a wake lock is requested")
                check(page.evaluate("() => window.__geo") == 0, "no geolocation request is made")
                loaded = sorted({p.split("/")[-1] for p in requests if p.endswith(".js")})
                out["modules loaded"] = loaded
                check(not any(name in loaded for name in FORBIDDEN_MODULES), f"none of the Drive/meter/app modules load ({loaded})")

                # ---- a new hazard appears after a refresh, highlighted; the old rows are not ----
                page.evaluate("(r) => { window.__mockHazards = r; return window.__WARN_WATCH_TEST__.refresh(); }",
                              rows_for(HAZARDS + [NEW_HAZARD]))
                page.wait_for_function("() => document.querySelectorAll('#watch-list .watch-row').length === 6", timeout=15000)
                page.wait_for_function("() => !document.body.innerText.includes('Locating…')")
                data2 = rows_text(page)
                check(data2[0]["id"] == "106" and data2[0]["title"] == "Accident" and data2[0]["age"] == "Just now", "the new hazard is first, 'Just now'")
                check(data2[0]["isNew"] and not any(r["isNew"] for r in data2[1:]), "only the new hazard is highlighted")
                check(data2[0]["place"] == expected_place[106], f"new row place {data2[0]['place']!r}")
                check(page.locator("#watch-count-number").inner_text() == "6", "summary updates to 6")
                chips = page.evaluate("() => [...document.querySelectorAll('.watch-category')].map((c) => c.textContent.replace(/\\s+/g, ' ').trim())")
                check(chips == ["Animals 2", "Obstructions 2", "Road Damage 1", "Visibility 1"], f"summary after the new hazard {chips}")
                shown = page.evaluate("() => new Set(window.__WARN_WATCH_TEST__.map.querySourceFeatures('live-hazards').map((x) => x.properties.id)).size")
                check(shown == 6, "the map shows the new hazard")
                good_text = page.locator("#watch-updated").inner_text()

                # ---- a failed fetch keeps the old data and says stale ----
                page.wait_for_timeout(1100)
                page.evaluate("() => { window.__mockFail = true; return window.__WARN_WATCH_TEST__.refresh(); }")
                page.wait_for_function("() => !document.querySelector('#watch-stale').hidden", timeout=10000)
                check(page.locator("#watch-list .watch-row").count() == 6 and page.locator("#watch-count-number").inner_text() == "6",
                      "after a failed fetch the old list is still shown")
                stale = page.locator("#watch-stale").inner_text()
                last_time = good_text.replace("Updated ", "")
                check("Stale" in stale and last_time in stale, f"stale notice carries the last good time ({stale!r})")
                check(page.locator("#watch-updated").inner_text() == good_text, "the Updated time stays at the last good fetch")
                check(page.locator("#watch-panel").evaluate("(el) => el.classList.contains('is-stale')"), "the panel is marked stale")
                page.screenshot(path=os.path.join(SHOTS, "watch-stale.png"))
                page.wait_for_timeout(1100)
                page.evaluate("() => { window.__mockFail = false; return window.__WARN_WATCH_TEST__.refresh(); }")
                page.wait_for_function("() => document.querySelector('#watch-stale').hidden", timeout=10000)
                check(page.locator("#watch-updated").inner_text() != good_text, "recovery moves the Updated time on and clears the stale notice")

                # ---- clicking a row focuses the map and opens its popup ----
                page.locator('.watch-row[data-id="103"]').click()
                page.wait_for_function("() => !window.__WARN_WATCH_TEST__.map.isMoving()", timeout=15000)
                view = page.evaluate("() => { const m = window.__WARN_WATCH_TEST__.map; const c = m.getCenter(); return { lng: c.lng, lat: c.lat, zoom: m.getZoom() }; }")
                check(abs(view["lng"] - -151.1180) < 0.002 and abs(view["lat"] - 60.5333) < 0.002 and view["zoom"] >= 12.4, f"clicking a row flies the map to it ({view})")
                page.locator(".maplibregl-popup .hazard-popup h2").wait_for(timeout=5000)
                check(page.locator(".maplibregl-popup .hazard-popup h2").inner_text() == "Black ice" and page.locator(".maplibregl-popup").count() == 1,
                      "its popup opens")
                check(page.locator('.watch-row[data-id="103"].is-selected').count() == 1, "the row is marked selected")
                page.locator('.watch-row[data-id="105"]').click()
                page.wait_for_function("() => !window.__WARN_WATCH_TEST__.map.isMoving()", timeout=15000)
                check(page.locator(".maplibregl-popup").count() == 1 and page.locator(".maplibregl-popup .hazard-popup h2").inner_text() == "Moose",
                      "a second click replaces the popup")
                page.screenshot(path=os.path.join(SHOTS, "watch-selected.png"))

                # ---- refresh on returning to the tab, and the wake lock again ----
                before = page.evaluate("() => [window.__fetchLog.length, window.__wake]")
                page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
                  document.dispatchEvent(new Event('visibilitychange')); }""")
                page.wait_for_timeout(300)
                page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
                  document.dispatchEvent(new Event('visibilitychange')); }""")
                page.wait_for_function("(b) => window.__fetchLog.length > b[0] && window.__wake > b[1]", arg=before, timeout=5000)
                check(True, "returning to the tab refreshes at once and asks for the wake lock again")
                check(page.evaluate("() => window.__geo") == 0, "still no geolocation request")

                # ---- a new service-worker version: reload only when quiet, no prompt ----
                page.evaluate("() => navigator.serviceWorker.ready")
                page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=30000)
                page.wait_for_timeout(700)
                body_before = page.evaluate("() => document.body.children.length")
                page.mouse.click(size[0] - 100, 700)  # an interaction just before the update lands
                page.evaluate("() => navigator.serviceWorker.dispatchEvent(new Event('controllerchange'))")
                page.wait_for_timeout(1200)
                check(page.evaluate("() => window.__reloads") == 0, "no reload while the page was just touched")
                page.wait_for_function("() => window.__reloads === 1", timeout=8000)
                check(page.evaluate("() => document.body.children.length") == body_before and page.locator("dialog, [role=dialog], .update-prompt").count() == 0,
                      "no update prompt appears")
                page.wait_for_timeout(1500)
                check(page.evaluate("() => window.__reloads") == 1, "one reload for one update")

                # ---- offline from its own cached copy ----
                cached = page.evaluate("""async () => { const cache = await caches.open((await caches.keys()).find((n) => n.startsWith('warn-map-shell-')));
                  return (await cache.keys()).map((r) => new URL(r.url).pathname).filter((p) => /watch|place-describe/.test(p)); }""")
                out["cached watch files"] = cached
                check(all(any(p.endswith(f) for p in cached) for f in ("/watch.html", "/watch.webmanifest", "/js/watch.js", "/js/place-describe.js")),
                      "the Watch files are in the shell cache")
                offline[0] = True
                context.set_offline(True)
                page.reload()
                page.wait_for_function("() => !!window.__WARN_WATCH_TEST__", timeout=60000)
                check(page.title().startswith("WARN Watch") and page.locator("#watch-panel").count() == 1, "offline: /watch.html is served as the Watch page")
                context.close()

            # ---- polling every 30 s (a fresh page, no manual refreshes) ----
            context, page, _ = open_page((1366, 768), HAZARDS)
            page.wait_for_function("() => window.__fetchLog.length >= 2", timeout=45000)
            gaps = page.evaluate("() => window.__fetchLog.slice(1).map((t, i) => (t - window.__fetchLog[i]) / 1000)")
            out["poll gaps s"] = gaps
            check(len(gaps) >= 1 and 28 <= gaps[0] <= 33, f"polls every 30 s ({gaps})")
            context.close()
            browser.close()
    finally:
        proc.terminate()

    out["console"] = console
    out["external"] = sorted(set(external))
    check(not console, "no console errors at either size")
    check(all(m in ("GET", "OPTIONS") and u.endswith("/rest/v1/public_hazards") for m, u in set(external)) or not external,
          "nothing leaves the device but the hazards GET (mocked here)")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    for failure in failures:
        print("SOFTFAIL", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
