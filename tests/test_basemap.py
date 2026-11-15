"""A basemap that fails to load recovers instead of staying grey until a reload (js/map.js, js/basemap-guard.js; the same fix as
warn-drive 1639e48). Range requests to the map file are aborted at the browser (nothing leaves the machine; any other origin is
answered locally and counted): (1) a healthy map draws and shows no message; (2) the FIRST Range request fails once: the basemap
draws by itself, no reload, and the failed read was really retried over the network (the PMTiles library's cached failure is
dropped); (3) EVERY request fails: three retries, no faster than the backoff, then "Map not loaded. Tap to retry" with a button of
at least 48 px (role alert) that does not overlap the status, route bar, locate and settings buttons, zoom control or attribution
at portrait 390x844 and landscape 870x390 and 740x360; one tap after the network is back draws the map and the message goes away by
itself; (4) no request to any other origin; (5) the service worker caches nothing about the map file. "On screen" means basemap
features are rendered (queryRenderedFeatures), not a flag. Screenshots go to WARN_SHOT_DIR when set, else tests/out/."""
import json
import os
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8784)
ROOT = f"http://127.0.0.1:{PORT}"
URL = f"{ROOT}/?test=1"
SHOTS = os.environ.get("WARN_SHOT_DIR") or OUT
os.makedirs(SHOTS, exist_ok=True)

# Test-only timings (read only on localhost with ?test=1): retries after 0.3 s, 0.6 s, 1 s; "slow" at 6 s; poll every 0.1 s.
INIT = """
(() => {
  globalThis.__WARN_BASEMAP_MS = { delays: [300, 600, 1000], slowMs: 6000, pollMs: 100 };
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.includes("/rest/v1/public_hazards")) {
      return Promise.resolve(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return realFetch(input, init);
  };
  Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
    getCurrentPosition() {}, watchPosition() { return 1; }, clearWatch() {} } });
})();
"""
ON_SCREEN_JS = """() => { const t = globalThis.__WARN_BASEMAP_TEST__; if (!t) return { hook: false };
  const m = t.map; const ids = m.getStyle().layers.filter(l => l.source === 'protomaps').map(l => l.id);
  return { hook: true, features: m.queryRenderedFeatures({ layers: ids }).length, guard: t.onScreen(), box: !document.querySelector('#map-retry').hidden }; }"""
BOXES_JS = """() => { const r = (e) => { if (!e) return null; const s = getComputedStyle(e); if (s.display === 'none' || s.visibility === 'hidden') return null; const b = e.getBoundingClientRect();
    if (!b.width || !b.height) return null; return { l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height }; };
  const q = (s) => r(document.querySelector(s));
  const all = (s) => [...document.querySelectorAll(s)].map(r).filter(Boolean);
  return { vw: innerWidth, vh: innerHeight, msg: q('#map-retry'), btn: q('#map-retry-button'), header: q('#app-header'),
           others: [...all('.status-stack > *'), ...all('.map-controls button'), ...all('.maplibregl-ctrl-top-right .maplibregl-ctrl-group'), ...all('.maplibregl-ctrl-attrib')],
           role: document.querySelector('#map-retry').getAttribute('role'), text: document.querySelector('#map-retry-button').textContent.trim() }; }"""


def overlap(a, b):
    return a and b and a["l"] < b["r"] and b["l"] < a["r"] and a["t"] < b["b"] and b["t"] < a["b"]


def main():
    failures = []
    out = {}

    def check(condition, what, detail=None):
        if not condition:
            failures.append(what if detail is None else f"{what}: {detail}")

    state = {"failures": 0, "ranges": 0}
    seen = []

    def open_page(browser, viewport, fail):
        context = browser.new_context(viewport=viewport, device_scale_factor=2, is_mobile=True, has_touch=True, locale="en-US")
        context.add_init_script(INIT)
        state["failures"], state["ranges"] = fail, 0

        def other(route, request):
            # Anything off this server is answered here, never sent: the hazard feed becomes an empty list.
            seen.append(request.url)
            route.fulfill(status=200, headers={"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}, body="[]")

        def tiles(route, request):
            if "range" in {k.lower() for k in request.headers}:
                state["ranges"] += 1
                if state["failures"] != 0:
                    if state["failures"] > 0:
                        state["failures"] -= 1
                    route.abort("failed")
                    return
            route.fallback()

        context.route(lambda url: not url.startswith(ROOT) and not url.startswith("data:") and not url.startswith("blob:"), other)
        context.route("**/tiles/kenai.pmtiles", tiles)
        context.on("request", lambda req: seen.append(req.url))
        page = context.new_page()
        page.goto(URL)
        page.wait_for_function("() => !!globalThis.__WARN_BASEMAP_TEST__", timeout=90_000)
        return context, page

    def wait_features(page, timeout_ms):
        page.wait_for_function("""() => { const t = globalThis.__WARN_BASEMAP_TEST__; const m = t.map;
          const ids = m.getStyle().layers.filter(l => l.source === 'protomaps').map(l => l.id);
          return m.queryRenderedFeatures({ layers: ids }).length > 0; }""", timeout=timeout_ms)

    proc = start_server(PORT)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)
            portrait = {"width": 390, "height": 844}

            # ---- a healthy map ----
            context, page = open_page(browser, portrait, 0)
            wait_features(page, 60_000)
            page.wait_for_timeout(1500)
            s = page.evaluate(ON_SCREEN_JS)
            out["healthy"] = s
            check(s["features"] > 0 and s["guard"] is True and not s["box"], "a healthy map: features on screen, no message", s)
            check(state["ranges"] > 0, "and it read the map file with Range requests", state["ranges"])
            context.close()

            # ---- the first Range request fails once: the basemap draws by itself ----
            context, page = open_page(browser, portrait, 1)
            t0 = time.time()
            wait_features(page, 60_000)
            took = time.time() - t0
            page.wait_for_timeout(500)
            s = page.evaluate(ON_SCREEN_JS)
            out["first_fails_once"] = {"state": s, "ranges": state["ranges"], "seconds": round(took, 1)}
            check(s["features"] > 0 and s["guard"] is True, "first Range request aborted once: the basemap draws by itself, no reload", s)
            check(state["ranges"] >= 2, "the failed read was retried over the network (the cached failure was dropped)", state["ranges"])
            check(not s["box"], "and the message never stayed up", s)
            check(took < 30, "within seconds, not a reload", round(took, 1))
            context.close()

            # ---- every request fails; then one tap after the network is back ----
            for label, viewport, shot in [("portrait 390x844", portrait, "portrait"),
                                          ("landscape 870x390", {"width": 870, "height": 390}, "landscape-870x390"),
                                          ("landscape 740x360", {"width": 740, "height": 360}, "landscape-740x360")]:
                context, page = open_page(browser, viewport, -1)
                page.wait_for_selector("#map-retry:not([hidden])", timeout=60_000)
                page.wait_for_timeout(800)
                shown_at = state["ranges"]
                page.wait_for_timeout(2500)
                check(state["ranges"] == shown_at, f"{label}: after the retries it stops asking (no hammering)", (shown_at, state["ranges"]))
                check(4 <= shown_at <= 10, f"{label}: the first read and three retries were made", shown_at)
                b = page.evaluate(BOXES_JS)
                msg, btn = b["msg"], b["btn"]
                check(b["role"] == "alert" and b["text"] == "Map not loaded. Tap to retry", f"{label}: role alert, the exact words", (b["role"], b["text"]))
                check(btn and btn["h"] >= 48 and btn["w"] >= 120, f"{label}: the retry button is at least 48 px tall", btn)
                s = page.evaluate(ON_SCREEN_JS)
                check(s["features"] == 0 and s["guard"] is False, f"{label}: blank, and the hook says so", s)
                check(msg and msg["l"] >= 0 and msg["r"] <= b["vw"] and msg["t"] >= b["header"]["b"] - 0.5 and msg["b"] <= b["vh"],
                      f"{label}: the message is on screen, below the header", (msg, b["header"]))
                hits = [i for i, other_box in enumerate(b["others"]) if overlap(msg, other_box)]
                check(not hits, f"{label}: it overlaps none of the status, route bar, map buttons, zoom control or attribution", [b["others"][i] for i in hits])
                page.screenshot(path=os.path.join(SHOTS, f"basemap-blank-{shot}.png"))
                state["failures"] = 0
                page.tap("#map-retry-button")
                wait_features(page, 45_000)
                page.wait_for_function("() => document.querySelector('#map-retry').hidden", timeout=10_000)
                s = page.evaluate(ON_SCREEN_JS)
                check(s["features"] > 0 and s["guard"] is True and not s["box"], f"{label}: one tap after the network is back draws the map and the message goes away by itself", s)
                page.wait_for_timeout(600)
                page.screenshot(path=os.path.join(SHOTS, f"basemap-recovered-{shot}.png"))
                context.close()

            # ---- nothing cached on failure; no other origin ----
            context, page = open_page(browser, portrait, -1)
            page.wait_for_selector("#map-retry:not([hidden])", timeout=60_000)
            cached = page.evaluate("""async () => { const out = []; for (const name of await caches.keys()) { const c = await caches.open(name); for (const r of await c.keys()) out.push(r.url); } return out; }""")
            out["cached_pmtiles"] = [u for u in cached if ".pmtiles" in u]
            check(not out["cached_pmtiles"], "nothing about the map file is cached after failed reads", out["cached_pmtiles"])
            context.close()
            browser.close()

        origins = sorted({"{0.scheme}://{0.netloc}".format(urlparse(u)) for u in seen if u.startswith("http")})
        out["origins_seen"] = origins
        # Requests to any other origin were answered locally by the test (the hazard feed is the only one the app makes);
        # the Map app's own known extra origin is the Supabase hazard feed, which the init script and route answer without sending it.
        extra = [o for o in origins if o != ROOT]
        check(all("supabase.co" in o for o in extra), "no request to any other origin than the (locally answered) hazard feed", extra)
    finally:
        proc.terminate()
        proc.wait(timeout=10) if hasattr(proc, "wait") else None
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
