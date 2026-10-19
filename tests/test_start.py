"""start.html: loads clean, cards navigate, share fallback, no manifest, no banned words, offline."""
import json, os, re, subprocess, sys, time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8777)
ROOT = f"http://127.0.0.1:{PORT}/"
BANNED = re.compile(r"driver|tariff|fare|taxi|fleet", re.I)


def serve():
    return start_server(PORT)


def main():
    out, console, failures = {}, [], []
    offline_from = 0

    def check(ok, what):
        if not ok:
            failures.append(what)

    source = open(os.path.join(REPO, "start.html"), encoding="utf-8").read()
    out["banned words in source"] = BANNED.findall(source)
    check(not out["banned words in source"], "no driver/tariff/fare/taxi/fleet in the source")
    check("manifest" not in source.lower(), "no manifest in source")

    proc = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.on("console", lambda m: console.append((m.type, m.text[:200])) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(("pageerror", str(e)[:200])))
            page.goto(ROOT + "start.html")
            page.wait_for_load_state("networkidle")
            out["title"] = page.title()
            out["manifest links"] = page.locator("link[rel=manifest]").count()
            check(out["manifest links"] == 0, "no manifest link")
            out["text banned"] = BANNED.findall(page.content())
            check(not out["text banned"], "no banned words in rendered page")
            boxes = page.evaluate("() => [...document.querySelectorAll('.card, .share')].map((e) => Math.round(e.getBoundingClientRect().height))")
            out["tap target heights"] = boxes
            check(all(h >= 56 for h in boxes), "large tap targets")
            out["og"] = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('meta[property^=og], meta[name^=twitter]')].map((m) => [m.getAttribute('property') || m.getAttribute('name'), m.content]))")
            check(out["og"]["og:url"] == "https://keeneyeapps.github.io/warn-map/start.html", "og:url absolute")
            check(out["og"]["og:image"].startswith("https://keeneyeapps.github.io/warn-map/icons/"), "og:image absolute")
            out["footer has why"] = "Why there's a map" in page.locator("footer").inner_text()
            check(out["footer has why"], "footer why text")
            page.screenshot(path=os.path.join(SHOTS, "start-page.png"))

            # share: with navigator.share undefined -> Copy link -> "Link copied"
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin=ROOT.rstrip("/"))
            page.evaluate("() => { Object.defineProperty(navigator, 'share', { value: undefined, configurable: true }); }")
            page.reload()
            page.evaluate("() => { Object.defineProperty(navigator, 'share', { value: undefined, configurable: true }); }")
            page2 = context.new_page()
            page2.add_init_script("Object.defineProperty(navigator, 'share', { value: undefined, configurable: true });")
            page2.on("console", lambda m: console.append((m.type, m.text[:200])) if m.type in ("error", "warning") else None)
            page2.goto(ROOT + "start.html")
            out["fallback label"] = page2.locator("#share").inner_text()
            check(out["fallback label"] == "Copy link", "Copy link label without navigator.share")
            page2.locator("#share").click()
            page2.wait_for_function("() => document.querySelector('#share').textContent === 'Link copied'", timeout=5000)
            out["clipboard"] = page2.evaluate("() => navigator.clipboard.readText()")
            check(out["clipboard"] == "https://keeneyeapps.github.io/warn-map/start.html", "copied the absolute share URL")
            page2.close()

            # share with navigator.share present: called with title, text, url
            page3 = context.new_page()
            page3.add_init_script("window.__shareArgs = null; navigator.share = (d) => { window.__shareArgs = d; return Promise.resolve(); };")
            page3.goto(ROOT + "start.html")
            page3.locator("#share").click()
            out["share args"] = page3.evaluate("() => window.__shareArgs")
            check(out["share args"] and out["share args"]["url"].endswith("/warn-map/start.html"), "navigator.share called with the URL")
            page3.close()

            # cards navigate
            page.goto(ROOT + "start.html")
            page.locator("#open-map").click()
            page.wait_for_load_state("domcontentloaded")
            out["map card -> "] = urlparse(page.url).path
            check(out["map card -> "] == "/", "WARN Map card opens ./")
            page.goto(ROOT + "start.html")
            page.locator("#open-drive").click()
            page.wait_for_load_state("domcontentloaded")
            out["drive card -> "] = urlparse(page.url).path
            check(out["drive card -> "] == "/warn.html", "WARN Drive card opens ./warn.html")

            # offline: after one visit through the app (which registers the worker), start.html is served from its cached copy
            page.goto(ROOT)
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__ || true")
            page.evaluate("() => navigator.serviceWorker.ready")
            page.wait_for_function("async () => { const n = (await caches.keys()).find((k) => k.startsWith('warn-map-shell-')); return n && (await (await caches.open(n)).keys()).some((r) => r.url.endsWith('/start.html')); }", timeout=60000)
            offline_from = len(console)  # tiles are network-only by design: offline tile fetches fail and may log
            context.set_offline(True)
            page.goto(ROOT + "start.html")
            out["offline title"] = page.title()
            check(out["offline title"] == "WARN" and page.locator(".card").count() == 2, "start.html works offline")
            # Both apps still load offline from the same service worker, which controls both pages.
            offline_pages = {}
            for path in ("", "warn.html"):
                page.goto(ROOT + path)
                page.wait_for_function("() => document.querySelector('#route-bar .route-slot')", timeout=30000)
                offline_pages[path or "index.html"] = page.evaluate(
                    "() => [document.title, !!navigator.serviceWorker.controller, document.querySelectorAll('#route-bar .route-slot').length]")
            out["offline pages"] = offline_pages
            check(all(v[1] and v[2] == 3 for v in offline_pages.values()), "index.html and warn.html load offline under the service worker")
            context.set_offline(False)
            browser.close()

    finally:
        proc.terminate()

    # ---- Moose backdrop (icons/home-moose.webp): loads, precached, no horizontal scroll, cards clickable, AA, no box ----
    precache = open(os.path.join(REPO, "js", "precache-list.js"), encoding="utf-8").read()
    check("./icons/home-moose.webp" in precache, "backdrop art is in the precache list")
    art = {}
    proc = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)
            for width, height in ((360, 640), (390, 844), (1280, 800)):
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                page.on("console", lambda m: art.setdefault("console", []).append(m.text[:120]) if m.type in ("error", "warning") else None)
                page.goto(ROOT + "start.html")
                page.wait_for_load_state("networkidle")
                scroll = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth]")
                art[f"scroll {width}"] = scroll
                check(scroll[0] <= scroll[1] and scroll[2] <= scroll[1], f"no horizontal scroll at {width} px")
                hits = page.evaluate("() => ['open-map', 'open-drive'].map((id) => { const r = document.getElementById(id).getBoundingClientRect(); const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return e && e.closest('a') && e.closest('a').id; })")
                art[f"card hit-test {width}"] = hits
                check(hits == ["open-map", "open-drive"], f"cards receive taps at {width} px")
                check(page.evaluate("() => getComputedStyle(document.querySelector('.backdrop')).pointerEvents") == "none", "backdrop has pointer-events: none")
                page.screenshot(path=os.path.join(SHOTS, "start-moose-" + str(width) + ".png"))
                if width == 360:
                    page.locator("#open-map").click()
                    page.wait_for_load_state("domcontentloaded")
                    check(urlparse(page.url).path == "/", "360 px: WARN Map card still navigates")
                    page.goto(ROOT + "start.html")
                    page.locator("#open-drive").click()
                    page.wait_for_load_state("domcontentloaded")
                    check(urlparse(page.url).path == "/warn.html", "360 px: WARN Alerts card still navigates")
                if width == 1280:
                    from PIL import Image
                    page.screenshot(path=os.path.join(SHOTS, "start-moose-wide.png"))
                    shot = Image.open(os.path.join(SHOTS, "start-moose-wide.png")).convert("RGB")
                    top = round(0.58 * (height - 720))
                    left = (width - 960) // 2
                    corner = shot.getpixel((left + 2, top + 2))
                    far = shot.getpixel((4, 4))
                    art["corner pixel"], art["page pixel"] = corner, far
                    check(all(abs(a - b) <= 1 for a, b in zip(corner, far)) and far == (23, 27, 29), "art blends into the page: no visible rectangle")
                    # Only the strips beside the 560 px content column (x 360-920), so text pixels cannot count.
                    strips = list(range(left, 350, 4)) + list(range(930, left + 960, 4))
                    lightest = max(sum(shot.getpixel((x, y))) for x in strips for y in range(max(top, 0), min(top + 720, height), 4))
                    art["lightest art pixel sum beside the column"] = lightest
                    check(lightest > sum(far) + 12, "the head is visible (lighter than the page) beside the content")
                context.close()
            # The art file loads online and, after the worker installs, offline from its cache.
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(ROOT)
            page.evaluate("() => navigator.serviceWorker.ready")
            page.wait_for_function("async () => { const n = (await caches.keys()).find((k) => k.startsWith('warn-map-shell-')); return n && (await (await caches.open(n)).keys()).some((r) => r.url.endsWith('/icons/home-moose.webp')); }", timeout=60000)
            art["precached"] = True
            page.goto(ROOT + "start.html")
            art["online status"] = page.evaluate("() => fetch('icons/home-moose.webp').then((r) => [r.status, r.headers.get('content-type')])")
            check(art["online status"][0] == 200, "artwork file returns 200")
            context.set_offline(True)
            art["offline status"] = page.evaluate("() => fetch('icons/home-moose.webp').then((r) => r.status)")
            check(art["offline status"] == 200, "artwork file served offline from the cache")
            context.close()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(ROOT + "start.html")
            colours = page.evaluate("() => ({ opacity: Number(getComputedStyle(document.querySelector('.backdrop')).opacity), h1: getComputedStyle(document.querySelector('h1')).color, lead: getComputedStyle(document.querySelector('.lead')).color, footer: getComputedStyle(document.querySelector('footer p')).color })")
            art["text colours"] = colours
            context.close()
            browser.close()
        from PIL import Image
        import numpy as np
        img = np.asarray(Image.open(os.path.join(REPO, "icons", "home-moose.webp")).convert("RGB")).astype(float)
        pagec = np.array([23, 27, 29], float)

        def lum(c):
            c = np.asarray(c, float) / 255
            c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
            return c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722

        eff = pagec + colours["opacity"] * (np.maximum(img, pagec) - pagec)
        worst = float(lum(eff).max())

        def parse(text):
            return [int(v) for v in re.findall(r"\d+", text)[:3]]

        ratios = {k: round((float(lum(np.array(parse(colours[k])))) + 0.05) / (worst + 0.05), 2) for k in ("h1", "lead", "footer")}
        art["worst-case contrast over the art"] = ratios
        check(ratios["lead"] >= 4.5 and ratios["footer"] >= 4.5 and ratios["h1"] >= 3.0, "WCAG AA for every text colour over the brightest pixel of the art")
        check(not art.get("console"), "backdrop pages: console clean")
    finally:
        proc.terminate()
    out["backdrop"] = art

    # Manifest scopes: the Drive scope must not be able to contain the Map's start_url (or vice versa the cards
    # cannot be link-captured into the wrong installed app). Resolved the way a browser does, against the page URL.
    from urllib.parse import urljoin
    base = "https://keeneyeapps.github.io/warn-map/start.html"
    manifests = {name: json.load(open(os.path.join(REPO, name), encoding="utf-8")) for name in ("manifest.webmanifest", "warn.webmanifest")}
    resolved = {name: {"id": m.get("id"), "start_url": urljoin(base, m["start_url"]), "scope": urljoin(base, m["scope"])}
                for name, m in manifests.items()}
    out["manifests"] = resolved
    drive_scope = resolved["warn.webmanifest"]["scope"]
    map_start = resolved["manifest.webmanifest"]["start_url"]
    check(not map_start.startswith(drive_scope), "Drive's scope cannot contain the Map's start_url")
    check(resolved["warn.webmanifest"]["start_url"].startswith(drive_scope), "Drive's start_url is inside its own scope")
    check(resolved["manifest.webmanifest"]["start_url"].startswith(resolved["manifest.webmanifest"]["scope"]), "Map's start_url is inside its own scope")
    check(resolved["warn.webmanifest"]["id"] == "/warn-map/warn" and resolved["warn.webmanifest"]["start_url"].endswith("/warn-map/warn.html"), "Drive id and start_url unchanged")
    check(len({resolved[n]["id"] for n in resolved}) == 2, "the two apps have distinct ids")
    online = console[:offline_from]
    offline_unexpected = [c for c in console[offline_from:]
                          if not any(k in c[1] for k in ("ERR_INTERNET_DISCONNECTED", "ERR_FAILED", "Failed to fetch"))]
    out["console online"] = online
    out["console offline unexpected"] = offline_unexpected
    out["console offline expected tile failures"] = len(console) - offline_from - len(offline_unexpected)
    check(not online, "console clean while online")
    check(not offline_unexpected, "offline: only the expected tile fetch failures")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
