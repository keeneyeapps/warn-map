
import sys as _sys
def _soft(ok, msg, line):
    if not ok:
        print(f"SOFTFAIL line {line}: {msg!r}"[:400], file=_sys.stderr, flush=True)
import json
import math
import re
import sys
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401
BASE = base_url(8765) + '/?test=1'
KENAI = [60.5603, -151.2235]
SOLDOTNA = [60.4864, -151.0583]
RAW_CODE = re.compile('\\b[A-Z]{2,}_[A-Z_]{2,}\\b')
INIT = '\n(() => {\n  const realFetch = window.fetch.bind(window);\n  window.__mockHazards = [];\n  window.fetch = (input, init) => {\n    const url = typeof input === "string" ? input : (input.url || String(input));\n    if (window.__mockHazards !== undefined && url.includes("/rest/v1/public_hazards")) {\n      return Promise.resolve(new Response(JSON.stringify(window.__mockHazards), {\n        status: 200, headers: { "Content-Type": "application/json" },\n      }));\n    }\n    return realFetch(input, init);\n  };\n  window.__shared = [];\n  navigator.share = (data) => { window.__shared.push(data.text); return Promise.resolve(); };\n})();\n'
TABLE = [({'hazard_type': 'DAMAGE_MY_LANE', 'heading': 217.93}, 'Rough road, southbound lane'), ({'hazard_type': 'DAMAGE_MY_LANE', 'heading': 331.17}, 'Rough road, northbound lane'), ({'hazard_type': 'DAMAGE_MY_LANE', 'heading': 80}, 'Rough road, eastbound lane'), ({'hazard_type': 'DAMAGE_ONCOMING', 'heading': 265}, 'Rough road, westbound lane'), ({'hazard_type': 'DAMAGE_MY_LANE', 'heading': None}, 'Rough road, lane unspecified'), ({'hazard_type': 'DAMAGE_ONCOMING'}, 'Rough road, lane unspecified'), ({'hazard_type': 'DAMAGE_BOTH_LANES', 'heading': 10}, 'Rough road, both lanes'), ({'hazard_type': 'DAMAGE_MY_LANE', 'heading': 0, 'display_text': 'Sinkhole'}, 'Sinkhole, northbound lane'), ({'hazard_type': 'DAMAGE_MY_LANE', 'heading': 0, 'display_text': 'Pothole in my lane'}, 'Rough road, northbound lane'), ({'hazard_type': 'CARIBOU', 'specific': 'Caribou'}, 'Caribou'), ({'hazard_type': 'MOOSE', 'display_text': 'MOOSE'}, 'Moose'), ({'hazard_type': 'MOOSE', 'display_text': 'moose with calf'}, 'Moose with calf'), ({'hazard_type': 'DELAY', 'display_text': 'Long queue'}, 'Possible delay'), ({'hazard_type': 'ACCIDENT_BLOCKING'}, 'Accident blocking the road'), ({'hazard_type': 'DOMESTIC_ANIMAL'}, 'Loose animal'), ({'hazard_type': 'ANIMAL_OTHER'}, 'Animal'), ({'hazard_type': 'HYDROPLANE', 'heading': 90}, 'Standing water'), ({'hazard_type': 'PEDESTRIAN_IN_ROAD'}, 'Person in the road'), ({'hazard_type': 'STRUCTURAL_DAMAGE'}, 'Structural damage'), ({'hazard_type': 'MOOSE_CARCASS'}, 'Dead animal'), ({'hazard_type': 'SINKHOLE_REPORT'}, 'Hazard'), ({'hazard_type': 'big_pothole_damage'}, 'Rough road'), ({'hazard_type': None}, 'Hazard')]

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

def main():
    server = start_server(port_for(8765))
    try:
        return _main()
    finally:
        server.terminate()


def _main():
    out, console, external = ({}, [], [])
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=CHANNEL, headless=True)
        context = browser.new_context(viewport={'width': 430, 'height': 900})
        context.add_init_script(INIT)
        context.on('request', lambda req: external.append((req.method, req.url)) if urlparse(req.url).hostname not in ('localhost', '127.0.0.1') else None)
        page = context.new_page()
        page.on('console', lambda m: console.append((m.type, m.text, m.location.get('url'))) if m.type in ('error', 'warning') else None)
        page.on('pageerror', lambda e: console.append(('pageerror', str(e), '')))
        page.goto(BASE)
        page.wait_for_function('() => !!window.__WARN_MAP_TEST__', timeout=60000)
        got = page.evaluate('async (rows) => {\n          const { hazardTitle } = await import("./js/hazards.js");\n          return rows.map((row) => hazardTitle(row));\n        }', [row for row, _ in TABLE])
        mismatches = [(row, want, have) for (row, want), have in zip(TABLE, got) if want != have]
        out['table'] = {json.dumps(row): have for (row, _), have in zip(TABLE, got)}
        _soft(not mismatches, mismatches, 99)
        page.evaluate('([s, d]) => window.__WARN_MAP_TEST__.route(s, d)', [KENAI, SOLDOTNA])
        line = page.evaluate('() => window.__WARN_MAP_TEST__.routeFeature().geometry.coordinates')
        rows = [('h-my', 'DAMAGE_MY_LANE', 217.93, None, None, 0.15), ('h-null', 'DAMAGE_MY_LANE', None, None, None, 0.25), ('h-onc', 'DAMAGE_ONCOMING', 90, None, None, 0.35), ('h-both', 'DAMAGE_BOTH_LANES', 10, None, None, 0.45), ('h-car', 'CARIBOU', 166, 'Caribou', None, 0.55), ('h-close', 'ACCIDENT_BLOCKING', None, None, None, 0.65), ('h-delay', 'DELAY', None, None, 'Long queue', 0.75), ('h-dog', 'DOMESTIC_ANIMAL', None, None, None, 0.85)]
        mocked = []
        for hid, code, heading, specific, display, frac in rows:
            lon, lat = point_at_fraction(line, frac)
            mocked.append({'id': hid, 'hazard_type': code, 'heading': heading, 'specific': specific, 'display_text': display, 'latitude': lat, 'longitude': lon, 'created_at': '2026-10-17T20:00:00Z', 'expires_at': None, 'confirmation_count': 0})
        page.evaluate('(r) => { window.__mockHazards = r; return window.__WARN_MAP_TEST__.refreshHazards(); }', mocked)
        page.wait_for_function("() => document.querySelectorAll('.route-hazard-row').length === 8", timeout=10000)
        titles = page.locator('.route-hazard-title').all_inner_texts()
        out['panel_titles'] = titles
        _soft(titles == ['Rough road, southbound lane', 'Rough road, lane unspecified', 'Rough road, eastbound lane', 'Rough road, both lanes', 'Caribou', 'Accident blocking the road', 'Possible delay', 'Loose animal'], titles, 124)
        _soft(page.locator('.route-hazards-panel').get_by_text('Route around').count() == 1, None, 128)
        popups = []
        for index in range(8):
            page.locator('.route-hazard-row').nth(index).click()
            page.locator('.hazard-popup h2').wait_for(timeout=5000)
            text = page.locator('.hazard-popup').inner_text()
            popups.append(chr(10).join((l for l in text.splitlines() if l.strip())))
            page.locator('.maplibregl-popup-close-button').dispatch_event('click')
        out['popups'] = popups
        _soft(not any(('_' in t for t in titles + popups)), (titles, popups), 138)
        _soft(popups[0].startswith('Rough road, southbound lane\nRoad Damage'), popups[0], 139)
        _soft(popups[1].startswith('Rough road, lane unspecified'), popups[1], 140)
        page.locator('.route-share-button').click()
        page.wait_for_function('() => window.__shared.length === 1')
        share = page.evaluate('() => window.__shared[0]')
        out['share_text'] = share
        _soft('  rough road, southbound lane,' in share and '  possible delay,' in share, share, 146)
        visible_text = page.locator('.status-stack').inner_text() + '\n' + '\n'.join(popups) + '\n' + share
        codes = RAW_CODE.findall(visible_text)
        _soft(not codes, codes, 150)
        page.evaluate('() => { window.__mockHazards = undefined; return window.__WARN_MAP_TEST__.refreshHazards(); }')
        live = page.evaluate('async () => {\n          const { SUPABASE_URL, SUPABASE_ANON_KEY } = await import("./js/config.js");\n          const { hazardTitle } = await import("./js/hazards.js");\n          const response = await fetch(`${SUPABASE_URL}/rest/v1/public_hazards?select=*`, {\n            headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}` }, cache: "no-store",\n          });\n          const rows = await response.json();\n          return rows.map((row) => [row.id, row.hazard_type, row.heading, hazardTitle(row)]);\n        }')
        out['live'] = live
        _soft(all((not RAW_CODE.search(title) for *_, title in live)), live, 164)
        browser.close()
    out['console'] = console
    out['external'] = sorted(set(external))
    print(json.dumps(out, indent=1, ensure_ascii=False))
    _soft(not console, console, 170)
    _soft(all((m in ('GET', 'OPTIONS') and 'supabase.co/rest/v1/public_hazards' in u for m, u in external)), external, 171)
if __name__ == '__main__':
    sys.exit(main())
