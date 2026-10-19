"""Wide layout, unchanged phone layout, and route printing.

    test_step9.py measure-phone   -> prints element boxes at 390x844 (run against the old and new builds)
    test_step9.py                 -> wide layout and print checks
"""
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8775)
BASE = f"http://127.0.0.1:{PORT}/?test=1"
KENAI = [60.5603, -151.2235]
SOLDOTNA = [60.4864, -151.0583]
STOP = [60.5333, -151.1180]  # on the Kenai Spur, east of Kenai
BOXES = ["#map", ".status-stack", "#route-bar", "#hazard-status", "#route-hazards-panel", ".map-controls", "#locate",
         "#settings-toggle"]

INIT = """
(() => {
  window.__printed = 0;
  window.print = () => { window.__printed += 1; };
  window.__shared = [];
  navigator.share = (data) => { window.__shared.push(data.text); return Promise.resolve(); };
})();
"""


def serve():
    return start_server(PORT)


def boxes(page, selectors):
    return page.evaluate("""(selectors) => Object.fromEntries(selectors.map((s) => {
      const el = document.querySelector(s);
      if (!el || el.hidden) return [s, null];
      const r = el.getBoundingClientRect();
      return [s, [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]];
    }))""", selectors)


def open_page(browser, viewport, console, external):
    context = browser.new_context(viewport=viewport)
    context.add_init_script(INIT)
    context.on("request", lambda req: external.append((req.method, req.url.split("?")[0]))
               if urlparse(req.url).hostname not in ("localhost", "127.0.0.1")
               and not req.url.startswith(("data:", "blob:")) else None)
    page = context.new_page()
    page.on("console", lambda m: console.append((m.type, m.text[:200])) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: console.append(("pageerror", str(e)[:300])))
    page.goto(BASE)
    page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
    page.wait_for_load_state("networkidle")
    return context, page


def plan_with_stop(page):
    page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [KENAI, SOLDOTNA])
    page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
    line = page.evaluate("() => window.__WARN_MAP_TEST__.routeFeature().geometry.coordinates")
    stop = line[int(len(line) * 0.4)]
    page.evaluate("(p) => window.__WARN_MAP_TEST__.selectPoint(p)", [stop[1], stop[0]])
    page.locator("#add-stop").click()
    page.wait_for_function("() => document.querySelectorAll('.route-leg-distance').length === 2", timeout=30000)
    page.wait_for_function("() => !window.__WARN_MAP_TEST__.alternativesSearching()", timeout=60000)
    page.wait_for_timeout(1200)


def measure_phone():
    console, external = [], []
    proc = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)
            _, page = open_page(browser, {"width": 390, "height": 844}, console, external)
            out = {"empty": boxes(page, BOXES)}
            plan_with_stop(page)
            out["route"] = boxes(page, BOXES)
            page.locator("#settings-toggle").click()
            page.wait_for_timeout(300)
            out["settings open"] = boxes(page, BOXES + ["#settings-panel"])
            out["console"] = console
            browser.close()
    finally:
        proc.terminate()
    print(json.dumps(out))


ANALYSE = """
import json, sys, pypdf
from PIL import Image
page = pypdf.PdfReader(sys.argv[1]).pages[0]
img = page.images[0].image.convert("RGB")
img.save(sys.argv[3])
px = list(img.get_flattened_data()) if hasattr(img, "get_flattened_data") else list(img.getdata())
n = len(px)
white = sum(1 for r, g, b in px if r > 225 and g > 225 and b > 225) / n
light = sum(1 for r, g, b in px if r + g + b > 600) / n
dark = sum(1 for r, g, b in px if r + g + b < 150) / n
route = sum(1 for r, g, b in px if abs(r - 11) < 30 and abs(g - 107) < 30 and abs(b - 79) < 30)
screen = Image.open(sys.argv[2]).convert("L")
sp = list(screen.get_flattened_data()) if hasattr(screen, "get_flattened_data") else list(screen.getdata())
print(json.dumps({"print_size": img.size, "print_white_share": round(white, 3), "print_light_share": round(light, 3),
                  "print_dark_share": round(dark, 4), "print_route_pixels": route,
                  "onscreen_mean": round(sum(sp) / len(sp), 1)}))
"""


def pdf_text(path):
    script = ("import json,sys,pypdf; r=pypdf.PdfReader(sys.argv[1]);"
              "print(json.dumps({'pages':[p.extract_text() for p in r.pages],"
              "'sizes':[[round(float(p.mediabox.width)),round(float(p.mediabox.height))] for p in r.pages]}))")
    return json.loads(subprocess.run([PYTHON, "-c", script, path], capture_output=True, text=True, check=True).stdout)


def main():
    out, console, external, failures = {}, [], [], []

    def check(condition, what):
        if not condition:
            failures.append(what)

    proc = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)

            # Wide: a 380 px left column, the map filling the rest; settings as a sheet over the column.
            context, page = open_page(browser, {"width": 1280, "height": 800}, console, external)
            plan_with_stop(page)
            wide = boxes(page, BOXES)
            out["wide"] = wide
            column = wide[".status-stack"]
            check(column[0] == 0 and column[1] == 0 and column[2] == 380 and column[3] == 800, "column 380 x full height")
            check(wide["#map"][0] == 380 and wide["#map"][2] == 1280 - 380 and wide["#map"][3] == 800,
                  "map = viewport minus the column")
            check(wide["#route-hazards-panel"][0] >= 0 and wide["#route-hazards-panel"][0] + wide["#route-hazards-panel"][2] <= 380,
                  "route panel inside the column")
            check(wide[".map-controls"][0] >= 380, "controls on the map")
            out["map canvas"] = page.evaluate("() => { const c = document.querySelector('#map canvas'); return [c.clientWidth, c.clientHeight]; }")
            check(out["map canvas"] == [900, 800], "map canvas resized to the map area")
            page.screenshot(path=os.path.join(SHOTS, "step9-wide.png"))
            page.locator("#settings-toggle").click()
            page.wait_for_timeout(300)
            settings = boxes(page, ["#settings-panel"])["#settings-panel"]
            out["wide settings"] = settings
            check(settings[0] == 0 and settings[2] == 380 and settings[3] == 800, "settings sheet over the column")
            top = page.evaluate("() => document.elementFromPoint(190, 400)?.closest('#settings-panel') !== null")
            check(top, "settings sheet is on top of the column")
            page.screenshot(path=os.path.join(SHOTS, "step9-wide-settings.png"))
            page.locator("#settings-toggle").click()
            context.close()

            # Print: a Kenai -> stop -> Soldotna route on a phone-sized page.
            context, page = open_page(browser, {"width": 390, "height": 844}, console, external)
            plan_with_stop(page)
            out["print buttons"] = page.locator(".route-plan-actions button").all_inner_texts()
            check(out["print buttons"][:2] == ["Share", "Print"], "Print beside Share")
            page.locator(".route-print-button").click()
            page.wait_for_function("() => window.__printed === 1", timeout=20000)
            out["print sheet in DOM"] = page.locator("#print-sheet").count()
            out["print image"] = page.evaluate("() => { const i = document.querySelector('#print-sheet img'); return [i.naturalWidth, i.naturalHeight, i.src.slice(0, 22)]; }")
            check(out["print image"][0] > 0, "map captured")
            check(page.locator("#print-sheet").is_hidden(), "print sheet hidden on screen")
            out["offscreen map removed"] = page.locator(".print-render-map").count() == 0
            check(out["offscreen map removed"], "offscreen print map removed")
            page.locator("#map").screenshot(path=os.path.join(SCRATCH, "onscreen-after-print.png"))
            page.emulate_media(media="print")
            out["print page background"] = page.evaluate(
                "() => [getComputedStyle(document.documentElement).backgroundColor, getComputedStyle(document.body).backgroundColor]")
            check(out["print page background"] == ["rgb(255, 255, 255)", "rgb(255, 255, 255)"], "printed page background white")
            # The committed sample is only rewritten on request (test_step9.py --update-sample), so a plain test run
            # never dirties the working tree.
            letter = (os.path.join(REPO, "docs", "screenshots", "print-sample.pdf") if "--update-sample" in sys.argv
                      else os.path.join(SCRATCH, "print-sample-letter.pdf"))
            a4 = os.path.join(SCRATCH, "print-sample-a4.pdf")
            page.pdf(path=letter, format="Letter", print_background=True)
            page.pdf(path=a4, format="A4", print_background=True)
            page.emulate_media(media="screen")
            context.close()
            browser.close()
    finally:
        proc.terminate()

    for name, path in (("letter", letter), ("a4", a4)):
        text = pdf_text(path)
        out[f"pdf {name}"] = {"pages": len(text["pages"]), "sizes": text["sizes"],
                              "page1": text["pages"][0][:200], "rest": " | ".join(text["pages"][1:])[:1500]}
        page1, rest = text["pages"][0], "\n".join(text["pages"][1:])
        check(len(text["pages"]) == 2, f"{name}: two pages (map, then legs and hazards)")
        check("WARN Map route" in page1, f"{name}: title on page 1")
        check("reported hazard" not in page1, f"{name}: page 1 is the map only")
        check("Stop 1" in rest and "Destination" in rest and rest.count("total") >= 2, f"{name}: two legs")
        check("reported hazard" in rest and "along your route" in rest, f"{name}: hazards heading")
        check("OpenStreetMap" in rest and "Kenai Peninsula Borough" in rest, f"{name}: notices")
        check("Fare" not in page1 + rest, f"{name}: no fare outside driver mode")

    analysis = json.loads(subprocess.run([PYTHON, "-c", ANALYSE, letter, os.path.join(SCRATCH, "onscreen-after-print.png"),
                                          os.path.join(SHOTS, "print-page1-map.png")],
                                         capture_output=True, text=True, check=True).stdout)
    out["image analysis"] = analysis
    check(analysis["print_light_share"] > 0.5, "printed map is light (the light theme's pale land colour)")
    check(analysis["print_route_pixels"] > 2000, "printed route visible")
    check(analysis["print_dark_share"] < 0.05, "printed map has no dark background")
    check(analysis["onscreen_mean"] < 80, "on-screen map still dark after printing")

    out["console"] = console
    out["external"] = sorted(set(external))
    check(not console, "console clean")
    check(all(m in ("GET", "OPTIONS") and u.endswith("/rest/v1/public_hazards") for m, u in set(external)),
          "only the Supabase hazards GET")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["measure-phone"]:
        measure_phone()
    else:
        sys.exit(main())
