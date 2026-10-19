"""Region-pack regression: start view, title, routes, address results, and offline behaviour for Kenai."""
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8768)
BASE = f"http://127.0.0.1:{PORT}/?test=1"

PLACES = {
    "soldotna": [60.4864, -151.0583],
    "kenai": [60.5603, -151.2235],
    "homer": [59.6425, -151.5483],
    "sterling": [60.537, -150.765],
    "kasilof": [60.3375, -151.2747],
    "seward": [60.1042, -149.4422],
    "anchorage": [61.2181, -149.9003],
    "nikiski": [60.69, -151.29],
    "fairbanks": [64.8378, -147.7164],
}
ROUTES = [
    ("soldotna", "kenai"), ("kenai", "homer"), ("soldotna", "sterling"), ("kasilof", "kenai"),
    ("soldotna", "seward"), ("homer", "anchorage"), ("nikiski", "kasilof"), ("seward", "homer"),
    ("soldotna", "fairbanks"),
]
QUERIES = [
    "35235 kalif", "beach rd", "123 main st kenai", "homer spit", "sterling hwy", "1663 a homer spit",
    "kenai spur hwy, soldotna", "44 bridge", "funny river", "xyzzy",
]
UI_QUERIES = ["35235 kalif", "beach rd", "123 main st kenai"]


def serve():
    return start_server(PORT)


def new_page(browser, console, external, name):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.on("request", lambda req: external.append((req.method, req.url.split("?")[0]))
               if urlparse(req.url).hostname not in ("localhost", "127.0.0.1")
               and not req.url.startswith(("data:", "blob:")) else None)
    page = context.new_page()
    page.on("console", lambda m: console.append((name, m.type, m.text)) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: console.append((name, "pageerror", str(e))))
    return context, page


def load(page):
    page.goto(BASE)
    page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
    page.wait_for_load_state("networkidle")


def view(page):
    return {
        "title": page.title(),
        "map_label": page.locator("#map").get_attribute("aria-label"),
        "center": [round(v, 6) for v in page.evaluate("() => window.__WARN_MAP_TEST__.mapCenter()")],
        "attribution": page.locator(".maplibregl-ctrl-attrib-inner").inner_text(),
    }


def routes(page):
    result = {}
    for start, end in ROUTES:
        result[f"{start}->{end}"] = page.evaluate(
            """([s, d]) => window.__WARN_MAP_TEST__.routeWithBlockedPoints(s, d, []).then((r) => r.route
                 ? { distanceM: Math.round(r.route.totalDistanceM * 10) / 10,
                     timeS: Math.round(r.route.estimatedTimeSeconds),
                     points: r.route.coordinates ? r.route.coordinates.length : null }
                 : { reason: r.reason || null }, (e) => ({ error: e.message }))""",
            [PLACES[start], PLACES[end]])
    return result


def address_results(page, queries):
    return page.evaluate("""async (queries) => {
      const { searchAddresses } = await import("./js/addresses.js");
      const out = {};
      for (const q of queries) {
        const rows = await searchAddresses(q);
        out[q] = rows.map((r) => r.label ?? r.text ?? JSON.stringify(r));
      }
      return out;
    }""", queries)


def ui_suggestions(page, text):
    page.locator("#slot-destination").click()
    page.locator("#address-sheet").wait_for(state="visible")
    page.locator("#address-input").fill("")
    page.locator("#address-input").type(text, delay=20)
    page.wait_for_function(
        "() => document.querySelectorAll('.address-suggestion').length > 0"
        " || /No matching/.test(document.querySelector('#address-sheet-status').textContent)", timeout=15000)
    page.wait_for_timeout(250)
    rows = page.locator(".address-suggestion").all_inner_texts()
    page.keyboard.press("Escape")
    return rows


def main():
    out, console, external = {}, [], []
    proc = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)

            context, page = new_page(browser, console, external, "online")
            load(page)
            out["view"] = view(page)
            out["routes"] = routes(page)
            out["addresses"] = address_results(page, QUERIES)
            out["ui_suggestions"] = {q: ui_suggestions(page, q) for q in UI_QUERIES}
            page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [PLACES["soldotna"], PLACES["kenai"]])
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            out["route_status"] = page.locator("#route-status").inner_text()
            out["share_first_lines"] = page.evaluate("() => window.__WARN_MAP_TEST__.shareText()").split("\n")[:2]
            out["route_roads"] = page.evaluate("() => window.__WARN_MAP_TEST__.routeRoads()")

            # Offline: after one online load, everything but tiles and hazards comes from the cache.
            page.evaluate("() => navigator.serviceWorker.ready")
            page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=30000)
            page.wait_for_function("""async () => {
              const name = (await caches.keys()).find((n) => n.startsWith("warn-map-shell-"));
              if (!name) return false;
              const keys = (await (await caches.open(name)).keys()).map((r) => new URL(r.url).pathname);
              return keys.some((p) => p.endsWith("-roads.json")) && keys.some((p) => p.endsWith("-addresses.json"));
            }""", timeout=60000)
            out["cache"] = page.evaluate("""async () => {
              const names = (await caches.keys()).filter((n) => n.startsWith("warn-map-shell-"));
              const keys = (await (await caches.open(names[0])).keys()).map((r) => new URL(r.url).pathname);
              return { names, count: keys.length,
                       data: keys.filter((p) => !p.startsWith("/vendor/") && !/\\.(js|css|png|webmanifest)$/.test(p)).sort() };
            }""")
            out["sw_script"] = page.evaluate("() => navigator.serviceWorker.controller.scriptURL.replace(location.origin, '')")
            context.set_offline(True)
            page.reload()
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
            page.wait_for_timeout(1500)
            out["offline_view"] = view(page)
            out["offline_routes"] = {k: v for k, v in routes(page).items() if k in ("soldotna->kenai", "kenai->homer")}
            out["offline_addresses"] = address_results(page, ["35235 kalif", "123 main st kenai"])
            out["offline_ui"] = ui_suggestions(page, "35235 kalif")
            context.close()
            browser.close()
    finally:
        proc.terminate()

    out["console"] = sorted({c for c in console if c[0] == "online"}) + sorted({c for c in console if c[0] != "online"})
    out["external"] = sorted(set(external))
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
