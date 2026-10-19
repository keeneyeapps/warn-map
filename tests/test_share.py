
import sys as _sys
def _soft(ok, msg, line):
    if not ok:
        print(f"SOFTFAIL line {line}: {msg!r}"[:400], file=_sys.stderr, flush=True)
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
KENAI = [60.5603, -151.2235]
STERLING = [60.537, -150.765]
SOLDOTNA = [60.4864, -151.0583]
INIT = '\n(() => {\n  const realFetch = window.fetch.bind(window);\n  window.__mockHazards = undefined;\n  window.fetch = (input, init) => {\n    const url = typeof input === "string" ? input : (input.url || String(input));\n    if (window.__mockHazards !== undefined && url.includes("/rest/v1/public_hazards")) {\n      return Promise.resolve(new Response(JSON.stringify(window.__mockHazards), {\n        status: 200, headers: { "Content-Type": "application/json" },\n      }));\n    }\n    return realFetch(input, init);\n  };\n  window.__shared = [];\n  window.__copied = [];\n  navigator.share = (data) => { window.__shared.push(data); return Promise.resolve(); };\n  try {\n    Object.defineProperty(navigator, "clipboard", {\n      configurable: true,\n      value: { writeText: (text) => { window.__copied.push(text); return Promise.resolve(); } },\n    });\n  } catch {}\n})();\n'

def serve(port):
    return start_server(port)

def hav(a, b):
    r = 6371008.8
    la1, la2 = (math.radians(a[1]), math.radians(b[1]))
    dla, dlo = (la2 - la1, math.radians(b[0] - a[0]))
    v = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(v))

def point_at_fraction(line, frac):
    cum = [0]
    for a, b in zip(line, line[1:]):
        cum.append(cum[-1] + hav(a, b))
    target = cum[-1] * frac
    for i in range(1, len(cum)):
        if cum[i] >= target:
            return line[i]
    return line[-1]

def menu(page, lat_lon, button):
    page.evaluate('(p) => window.__WARN_MAP_TEST__.selectPoint(p)', lat_lon)
    page.locator('#route-menu').wait_for(state='visible')
    page.locator(button).click()

def click_share(page, store):
    before = page.evaluate(f'() => window.{store}.length')
    page.locator('.route-share-button').click()
    page.wait_for_function(f'(n) => window.{store}.length > n', arg=before, timeout=10000)
    shared = page.evaluate(f'() => window.{store}.at(-1)')
    return shared['text'] if isinstance(shared, dict) else shared

def share_test(pw, out, console, external):
    proc = serve(8765)
    try:
        browser = pw.chromium.launch(channel=CHANNEL, headless=True)
        context = browser.new_context(viewport={'width': 430, 'height': 900})
        context.add_init_script(INIT)
        # The fare line is a driver-mode feature (step 8): this test runs with driver mode on for the fare check.
        context.add_init_script("localStorage.setItem('warn-map-driver-mode', '1')")
        context.on('request', lambda req: external.append((req.method, req.url)) if urlparse(req.url).hostname not in ('localhost', '127.0.0.1') else None)
        page = context.new_page()
        page.on('console', lambda m: console.append((m.type, m.text, m.location.get('url'))) if m.type in ('error', 'warning') else None)
        page.on('pageerror', lambda e: console.append(('pageerror', str(e), '')))
        page.goto(base_url(8765) + '/?test=1')
        page.wait_for_function('() => !!window.__WARN_MAP_TEST__', timeout=60000)
        page.wait_for_function("() => /updated|unavailable|failed/i.test(document.querySelector('#hazard-summary').textContent)", timeout=30000)
        page.evaluate('() => { window.__mockHazards = []; return window.__WARN_MAP_TEST__.refreshHazards(); }')
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(1000)
        menu(page, KENAI, '#start-here')
        menu(page, STERLING, '#add-stop')
        menu(page, SOLDOTNA, '#route-to-here')
        page.wait_for_function("() => document.querySelectorAll('.route-leg-distance').length === 2 && document.querySelector('.route-share-button')", timeout=30000)
        _soft(page.locator('.route-marker-destination').count() == 1, None, 108)
        _soft(page.locator('.route-marker-start').count() == 1 and page.locator('.route-marker-stop').count() == 1, None, 109)
        line = page.evaluate('() => window.__WARN_MAP_TEST__.routeFeature().geometry.coordinates')
        hz = point_at_fraction(line, 0.43)
        created = (datetime.now(timezone.utc) - timedelta(days=20, minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        page.evaluate('(rows) => { window.__mockHazards = rows; return window.__WARN_MAP_TEST__.refreshHazards(); }', [{'id': 'test-rough', 'hazard_type': 'DAMAGE_BOTH_LANES', 'display_text': 'Rough road', 'latitude': hz[1], 'longitude': hz[0], 'created_at': created, 'expires_at': None, 'confirmation_count': 0}])
        page.get_by_text('Rough road').wait_for(timeout=10000)
        page.wait_for_timeout(800)
        total = re.fullmatch('Total (\\d+\\.\\d mi) · (\\d+ min)', page.locator('.route-total-distance').inner_text())
        hazard_distance = page.locator('.route-hazard-distance').inner_text()
        fare = page.locator('.route-fare').inner_text()
        roads = page.evaluate('() => window.__WARN_MAP_TEST__.routeRoads()')
        text = click_share(page, '__shared')
        lines = text.split('\n')
        out['share_text'] = text
        out['all_named_roads'] = roads
        _soft(lines[0] == 'Kenai → Sterling → Soldotna', lines[0], 132)
        _soft(lines[1] == f'{total.group(1)} · about {total.group(2)}', lines[1], 133)
        road_names = lines[2].split(' → ')
        _soft('Kenai Spur Hwy' in road_names and 'Sterling Hwy' in road_names, lines[2], 135)
        _soft(len(road_names) == len(set(road_names)), lines[2], 136)
        _soft(lines[3] == '1 reported hazard along your route:', lines[3], 137)
        _soft(lines[4] == f'  rough road, both lanes, {hazard_distance} ahead (reported 20 d ago)', lines[4], 138)
        expected_fare = page.evaluate("async (miles) => `Fare $${(await import('./js/driver.js')).fareFor(miles).toFixed(2)}`", float(total.group(1).split()[0]))
        _soft(lines[5] == fare == expected_fare, (lines[5], fare, expected_fare), 139)
        _soft(len(lines) == 6, lines, 140)
        page.screenshot(path=os.path.join(SHOTS, "route-share-destination-flag.png"))
        page.evaluate('() => { window.__mockHazards = []; return window.__WARN_MAP_TEST__.refreshHazards(); }')
        page.get_by_text('No reported hazards along your route').wait_for(timeout=10000)
        page.evaluate("() => localStorage.removeItem('warn-map-driver-mode')")  # driver mode off: no fare line
        text2 = click_share(page, '__shared')
        out['share_text_no_hazards_no_rate'] = text2
        l2 = text2.split('\n')
        _soft(l2[3] == 'No reported hazards along your route' and len(l2) == 4, l2, 152)
        page.evaluate('() => { navigator.share = undefined; }')
        copied = click_share(page, '__copied')
        _soft(copied == text2, None, 157)
        _soft(page.locator('.route-share-button').inner_text() == 'Copied', None, 158)
        out['clipboard_fallback'] = 'Copied'
        page.locator('#clear-route').click()
        _soft(page.locator('.route-marker-destination, .route-marker').count() == 0, None, 162)
        page.evaluate('() => { window.__mockHazards = undefined; return window.__WARN_MAP_TEST__.refreshHazards(); }')
        page.wait_for_function("() => /hazards? · updated/.test(document.querySelector('#hazard-summary').textContent)", timeout=20000)
        browser.close()
    finally:
        proc.terminate()

def offline_test(pw, out):
    proc = serve(8767)
    browser = pw.chromium.launch(channel=CHANNEL, headless=True)
    context = browser.new_context(viewport={'width': 430, 'height': 900})
    page = context.new_page()
    page.goto(base_url(8767) + '/?test=1')
    page.wait_for_function('() => !!window.__WARN_MAP_TEST__', timeout=60000)
    page.evaluate('() => navigator.serviceWorker.ready')
    page.wait_for_function('() => !!navigator.serviceWorker.controller', timeout=30000)
    page.wait_for_load_state('networkidle')
    cached = page.evaluate('async () => {\n      const cache = await caches.open((await caches.keys()).find((n) => n.startsWith("warn-map-shell-")));\n      const keys = (await cache.keys()).map((r) => new URL(r.url).pathname);\n      return ["/js/routing.js", "/js/routing-worker.js", "/js/route-hazards.js", "/graph/kenai-roads.json"]\n        .map((p) => [p, keys.includes(p)]);\n    }')
    out['cache_after_online_load'] = cached
    proc.terminate()
    proc.wait()
    blocked = []

    def gate(route):
        if 'supabase.co/rest/v1/public_hazards' in route.request.url and route.request.method in ('GET', 'OPTIONS'):
            route.continue_()
        else:
            blocked.append(route.request.url)
            route.abort()
    context.route('**/*', gate)
    page.reload()
    page.wait_for_function('() => !!window.__WARN_MAP_TEST__', timeout=60000)
    result = page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d).then((r) => r && r.route ? r.route.totalDistanceM : null, (e) => 'error: ' + e.message)", [SOLDOTNA, KENAI])
    out['offline_route_m'] = result
    out['offline_route_status'] = page.locator('#route-status').inner_text()
    out['offline_blocked_requests'] = len(blocked)
    browser.close()
    _soft(isinstance(result, (int, float)) and result > 0, result, 208)

def main():
    out, console, external = ({}, [], [])
    with sync_playwright() as pw:
        share_test(pw, out, console, external)
        offline_test(pw, out)
    out['console'] = console
    out['external'] = sorted(set(external))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    _soft(not console, console, 219)
    _soft(external and all((m in ('GET', 'OPTIONS') and 'supabase.co/rest/v1/public_hazards' in u for m, u in external)), external, 220)
if __name__ == '__main__':
    sys.exit(main())
