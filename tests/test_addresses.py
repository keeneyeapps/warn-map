
import sys as _sys
def _soft(ok, msg, line):
    if not ok:
        print(f"SOFTFAIL line {line}: {msg!r}"[:400], file=_sys.stderr, flush=True)
import json
import re
import os
import subprocess
import sys
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401
INIT = '\n(() => {\n  window.__shared = [];\n  navigator.share = (data) => { window.__shared.push(data.text); return Promise.resolve(); };\n  const fix = { coords: { latitude: 60.4864, longitude: -151.0583, accuracy: 10, heading: null, speed: null }, timestamp: Date.now() };\n  Object.defineProperty(navigator, "geolocation", {\n    configurable: true,\n    value: {\n      getCurrentPosition(success) { setTimeout(() => success(fix), 10); },\n      watchPosition(success) { setTimeout(() => success(fix), 10); return 1; },\n      clearWatch() {},\n    },\n  });\n})();\n'

def serve(port):
    return start_server(port)

def suggestions_for(page, slot, text):
    page.locator(f'#slot-{slot}').click()
    page.locator('#address-sheet').wait_for(state='visible')
    page.locator('#address-input').fill('')
    page.locator('#address-input').type(text, delay=30)
    page.wait_for_function("() => document.querySelectorAll('.address-suggestion').length > 0 || /No matching/.test(document.querySelector('#address-sheet-status').textContent)", timeout=15000)
    page.wait_for_timeout(250)
    return page.locator('.address-suggestion').all_inner_texts()

def new_context(browser, external, console, name):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    context.add_init_script(INIT)
    context.on('request', lambda req: external.append((req.method, req.url)) if urlparse(req.url).hostname not in ('localhost', '127.0.0.1') else None)
    page = context.new_page()
    page.on('console', lambda m: console.append((name, m.type, m.text, m.location.get('url'))) if m.type in ('error', 'warning') else None)
    page.on('pageerror', lambda e: console.append((name, 'pageerror', str(e), '')))
    return (context, page)

def main():
    out, console, external = ({}, [], [])
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=CHANNEL, headless=True)
        proc = serve(8765)
        try:
            context, page = new_context(browser, external, console, 'online')
            page.goto(base_url(8765) + '/?test=1')
            page.wait_for_function('() => !!window.__WARN_MAP_TEST__', timeout=60000)
            page.wait_for_load_state('networkidle')
            first = suggestions_for(page, 'destination', '35235 kalif')
            out['35235 kalif'] = first
            _soft(first[0] == '35235 Kalifornsky Beach Rd, Kenai', first, 82)
            _soft(page.locator('#address-input').bounding_box()['height'] >= 44, None, 83)
            _soft(all((b['height'] >= 44 for b in [s.bounding_box() for s in page.locator('.address-suggestion').all()])), None, 84)
            page.screenshot(path=os.path.join(SHOTS, "address-entry-phone.png"))
            page.locator('.address-suggestion').first.click()
            _soft(page.locator('#address-sheet').is_hidden(), None, 87)
            slot = page.locator('#slot-destination').get_attribute('aria-label')
            _soft(slot == 'Destination: 35235 Kalifornsky Beach Rd, Kenai', slot, 89)
            page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
            out['route_status'] = page.locator('#route-status').inner_text()
            _soft(page.locator('.route-leg-destination .route-leg-label').inner_text() == 'Destination · 35235 Kalifornsky Beach Rd, Kenai', None, 92)
            page.locator('.route-share-button').click()
            page.wait_for_function('() => window.__shared.length === 1')
            share_first_line = page.evaluate("() => window.__shared[0].split('\\n')[0]")
            out['share_first_line'] = share_first_line
            _soft(share_first_line.endswith('→ 35235 Kalifornsky Beach Rd, Kenai'), share_first_line, 98)
            beach = suggestions_for(page, 'stops', 'beach rd')
            out['beach rd'] = beach
            _soft(any((s.startswith('Kalifornsky Beach Rd, ') and s.endswith('(street)') for s in beach)), beach, 103)
            page.locator('.address-suggestion', has_text='Kalifornsky Beach Rd, Soldotna (street)').click()
            page.wait_for_function("() => document.querySelectorAll('.route-leg-distance').length === 2", timeout=30000)
            _soft(page.locator('#slot-stops').get_attribute('aria-label') == 'Stops: 1', None, 106)
            stop_label = page.locator('.route-leg-stop .route-leg-label').inner_text()
            out['stop_label'] = stop_label
            _soft(stop_label == 'Stop 1 · Kalifornsky Beach Rd, Soldotna', stop_label, 109)
            mains = suggestions_for(page, 'destination', '123 main st kenai')
            out['123 main st kenai'] = mains
            kenai = [i for i, s in enumerate(mains) if re.fullmatch('\\d+ Main St( .+)?, Kenai', s)]
            others = [i for i, s in enumerate(mains) if re.search('\x08Main St\x08', s) and i not in kenai]
            _soft(kenai and kenai[0] == 0 and (not others or max(kenai) < min(others)), mains, 116)
            page.keyboard.press('Escape')
            _soft(page.locator('#address-sheet').is_hidden(), None, 118)
            page.locator('#slot-destination').click()
            page.locator('#address-tap-map').click()
            _soft(page.locator('#pick-banner-text').inner_text() == 'Tap the map to set the destination', None, 123)
            page.locator('#pick-cancel').click()
            context.close()
        finally:
            proc.terminate()
        proc = serve(8767)
        try:
            context, page = new_context(browser, external, console, 'offline')
            page.goto(base_url(8767) + '/?test=1')
            page.wait_for_function('() => !!window.__WARN_MAP_TEST__', timeout=60000)
            page.evaluate('() => navigator.serviceWorker.ready')
            page.wait_for_function('() => !!navigator.serviceWorker.controller', timeout=30000)
            page.wait_for_function('async () => {\n              const cache = await caches.open((await caches.keys()).find((n) => n.startsWith("warn-map-shell-")));\n              return !!(await cache.match(new URL("addresses/kenai-addresses.json", location.href).href));\n            }', timeout=60000)
            page.wait_for_load_state('networkidle')
            blocked = []
            served = []
            context.route('**/addresses/kenai-addresses.json', lambda route: (blocked.append(route.request.url), route.abort()))
            page.on('response', lambda response: served.append((response.url, response.from_service_worker, response.status)) if 'kenai-addresses.json' in response.url else None)
            page.reload()
            page.wait_for_function('() => !!window.__WARN_MAP_TEST__', timeout=60000)
            offline = suggestions_for(page, 'destination', '35235 kalif')
            out['offline 35235 kalif'] = offline
            out['offline_index_responses'] = served
            out['offline_blocked_requests'] = blocked
            _soft(offline and offline[0] == '35235 Kalifornsky Beach Rd, Kenai', offline, 153)
            _soft(served and all((from_sw and status == 200 for _, from_sw, status in served)), served, 154)
            context.close()
        finally:
            proc.terminate()
        browser.close()
    out['console'] = console
    out['external'] = sorted(set(external))
    print(json.dumps(out, indent=1, ensure_ascii=False))
    _soft(not console, console, 163)
    _soft(all((m in ('GET', 'OPTIONS') and 'supabase.co/rest/v1/public_hazards' in u for m, u in external)), external, 164)
if __name__ == '__main__':
    sys.exit(main())
