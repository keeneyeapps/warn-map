"""The header band (index, warn, meter): black and yellow (the Android caution-stripe values), moose, WARN, variant name.

Checks per page and size: the right variant name, no taller than 44 px at 360 px wide, the Android colours as CSS
variables and as computed colours, AA contrast, the unchanged moose image, nothing fixed to the top overlaps it, the map
sits below it, controls stay clickable, no horizontal scroll, nothing in the print layout, the wide layout spans the
top with the left column below it, and a clean console. start.html has no band.
"""
import json
import os
import re
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8781)
ROOT = f"http://127.0.0.1:{PORT}"
PAGES = (("index.html", "Map"), ("warn.html", "Alerts"), ("meter.html", "Meter"))
YELLOW, BLACK = "rgb(253, 216, 53)", "rgb(0, 0, 0)"  # #FDD835 and #000000, WARN CautionStripe.kt


def read(path):
    return open(os.path.join(REPO, path), encoding="utf-8").read()


def luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def main():
    out, console, failures = {}, [], []

    def check(condition, what):
        if not condition:
            failures.append(what)

    css = read("css/app.css")
    check("--brand-yellow: #fdd835" in css and "--brand-black: #000" in css, "the Android yellow and black are CSS variables")
    check("env(safe-area-inset-top" in css.split(".app-header-moose")[0], "the band respects the top safe-area inset")
    check("app-header" not in read("start.html"), "start.html has no header band")
    ratio = (luminance((253, 216, 53)) + 0.05) / (luminance((0, 0, 0)) + 0.05)
    out["contrast yellow on black"] = round(ratio, 2)
    check(ratio >= 4.5, "AA contrast, yellow text on black")

    proc = start_server(PORT)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)
            for size in ((360, 740), (390, 844), (1280, 800)):
                width, height = size
                context = browser.new_context(viewport={"width": width, "height": height})
                for name, variant in PAGES:
                    label = f"{name} {width}px"
                    page = context.new_page()
                    page.on("console", lambda m, l=label: console.append((l, m.type, m.text[:160])) if m.type in ("error", "warning") else None)
                    page.on("pageerror", lambda e, l=label: console.append((l, "pageerror", str(e)[:200])))
                    page.goto(f"{ROOT}/{name}?test=1")
                    page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
                    page.wait_for_load_state("networkidle")
                    band = page.locator("#app-header")
                    check(band.count() == 1, f"{label}: the band exists")
                    box = band.bounding_box()
                    text = page.locator("#app-header").inner_text().split()
                    check(text == ["WARN", variant], f"{label}: reads WARN {variant} (got {text})")
                    check(box["x"] == 0 and box["y"] == 0 and abs(box["width"] - width) < 0.5, f"{label}: spans the top")
                    check(box["height"] <= 44, f"{label}: no taller than 44 px ({box['height']})")
                    style = page.evaluate("""() => {
                      const s = getComputedStyle(document.querySelector('#app-header'));
                      const r = getComputedStyle(document.documentElement);
                      return { bg: s.backgroundColor, color: s.color, yellow: r.getPropertyValue('--brand-yellow').trim(),
                               black: r.getPropertyValue('--brand-black').trim(),
                               warn: getComputedStyle(document.querySelector('.app-header-warn')).fontWeight,
                               warnColor: getComputedStyle(document.querySelector('.app-header-warn')).color,
                               variantColor: getComputedStyle(document.querySelector('.app-header-variant')).color };
                    }""")
                    check(style["bg"] == BLACK and style["color"] == YELLOW and style["warnColor"] == YELLOW
                          and style["variantColor"] == YELLOW, f"{label}: black band, yellow text")
                    check(style["yellow"] == "#fdd835" and style["black"] == "#000", f"{label}: the variables hold the Android values")
                    check(int(style["warn"]) >= 700, f"{label}: WARN is bold")
                    moose = page.evaluate("""() => { const i = document.querySelector('.app-header-moose');
                      return { ok: i.complete && i.naturalWidth === 720 && i.naturalHeight === 540, src: i.getAttribute('src'),
                               h: i.getBoundingClientRect().height, left: i.getBoundingClientRect().left,
                               filter: getComputedStyle(i).filter }; }""")
                    check(moose["ok"] and moose["src"] == "./icons/home-moose.webp" and moose["filter"] == "none",
                          f"{label}: the unchanged full-colour moose image, loaded")
                    check(moose["h"] <= box["height"] and moose["left"] < 20, f"{label}: moose at the left, inside the band")

                    # Nothing fixed to the top overlaps the band; the map starts right below it.
                    bottom = box["y"] + box["height"]
                    for selector in ("#hazard-status", "#route-bar", ".status-stack"):
                        b = page.locator(selector).bounding_box()
                        check(b is not None and b["y"] >= bottom - 0.5, f"{label}: {selector} starts below the band")
                    map_box = page.locator("#map").bounding_box()
                    check(abs(map_box["y"] - bottom) < 0.5 and abs(map_box["height"] - (height - bottom)) < 1,
                          f"{label}: the map fills the space below the band")
                    if width >= 900:
                        check(abs(map_box["x"] - 380) < 0.5, f"{label}: the left column keeps its 380 px")
                        stack = page.locator(".status-stack").bounding_box()
                        check(abs(stack["y"] - bottom) < 0.5 and stack["width"] == 380, f"{label}: the left column sits under the band")

                    # Controls stay clickable: nothing (the band included) sits on top of their centre.
                    for selector in ("#slot-start", "#slot-destination", "#settings-toggle", "#locate"):
                        covered = page.evaluate("""(sel) => { const el = document.querySelector(sel); const r = el.getBoundingClientRect();
                          const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                          return !(top === el || el.contains(top)); }""", selector)
                        check(not covered, f"{label}: {selector} is not covered")
                    centre = page.evaluate("""() => { const t = document.elementFromPoint(innerWidth / 2 + (innerWidth >= 900 ? 190 : 0), innerHeight / 2);
                      return !!t && !t.closest('#app-header'); }""")
                    check(centre, f"{label}: a tap in the middle of the map reaches the map, not the band")
                    page.locator("#settings-toggle").click()
                    panel = page.locator("#settings-panel")
                    check(panel.is_visible(), f"{label}: the gear opens settings")
                    pb = panel.bounding_box()
                    check(pb["y"] >= bottom - 0.5 and pb["y"] + pb["height"] <= height + 0.5, f"{label}: the settings panel fits below the band")
                    page.screenshot(path=os.path.join(SHOTS, f"header-{name.split('.')[0]}-{width}.png"))
                    page.locator("#settings-toggle").click()
                    check(page.evaluate("() => document.documentElement.scrollWidth <= innerWidth && document.body.scrollWidth <= innerWidth"),
                          f"{label}: no horizontal scroll")
                    page.emulate_media(media="print")
                    check(page.evaluate("() => getComputedStyle(document.querySelector('#app-header')).display") == "none",
                          f"{label}: the band is hidden when printing")
                    page.close()
                context.close()
            browser.close()
    finally:
        proc.terminate()

    out["console"] = console
    check(not console, "no console errors or warnings")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    for failure in failures:
        print("SOFTFAIL", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
