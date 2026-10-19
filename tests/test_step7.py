"""Route alternatives: Kenai -> Soldotna offers the K-Beach alternative; Soldotna -> Homer offers none."""
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8773)
BASE = f"http://127.0.0.1:{PORT}/?test=1"
KENAI = [60.5603, -151.2235]
SOLDOTNA = [60.4864, -151.0583]
HOMER = [59.6425, -151.5483]


def serve():
    return start_server(PORT)


def plan(page, start, end):
    page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [start, end])
    page.wait_for_function("() => document.querySelector('.route-total')", timeout=30000)
    page.wait_for_function("() => !window.__WARN_MAP_TEST__.alternativesSearching()", timeout=60000)
    page.wait_for_timeout(800)
    options = page.evaluate("() => window.__WARN_MAP_TEST__.routeOptions()")
    return [{"active": o["active"], "mi": round(o["distanceM"] / 1609.344, 1), "min": round(o["timeSeconds"] / 60, 1),
             "roads": o["roads"][:5]} for o in options]


def main():
    out, console, external, failures = {}, [], [], []

    def check(condition, what):
        if not condition:
            failures.append(what)

    proc = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=CHANNEL, headless=True)
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.on("request", lambda req: external.append((req.method, req.url.split("?")[0]))
                       if urlparse(req.url).hostname not in ("localhost", "127.0.0.1")
                       and not req.url.startswith(("data:", "blob:")) else None)
            page = context.new_page()
            page.on("console", lambda m: console.append((m.type, m.text[:200])) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(("pageerror", str(e)[:300])))
            page.goto(BASE)
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
            page.wait_for_load_state("networkidle")
            out["preference"] = page.evaluate("() => document.querySelector('input[name=\"route-preference\"]:checked')?.value")

            options = plan(page, KENAI, SOLDOTNA)
            out["kenai->soldotna options"] = options
            check(len(options) >= 2, "Kenai -> Soldotna: an alternative is offered")
            main_roads = " ".join(options[0]["roads"])
            alt_roads = " ".join(" ".join(o["roads"]) for o in options[1:])
            check("Kenai Spur" in main_roads, "main route on the Kenai Spur")
            check("Kalifornsky" in alt_roads, "an alternative on K-Beach")
            out["labels"] = page.locator(".route-alternative-label").all_inner_texts()
            check(len(out["labels"]) == len(options) - 1, "one label per alternative")
            out["main line rendered"] = page.evaluate("() => window.__WARN_MAP_TEST__.routeLineRendered()")
            out["total before"] = page.locator(".route-total-distance").inner_text()
            page.locator(".route-hazards-toggle").click()  # fold the panel so the map labels are reachable
            page.wait_for_timeout(300)
            page.screenshot(path=os.path.join(SHOTS, "step7-alternatives.png"))

            # Tap the K-Beach alternative's label: it becomes the active route and the panel total follows.
            k_beach = next(i for i, o in enumerate(options) if not o["active"] and "Kalifornsky" in " ".join(o["roads"]))
            label_index = [i for i, o in enumerate(options) if not o["active"]].index(k_beach)
            page.locator(".route-alternative-label").nth(label_index).click()
            page.wait_for_timeout(600)
            after = page.evaluate("() => window.__WARN_MAP_TEST__.routeOptions()")
            out["after tap"] = [{"active": o["active"], "mi": round(o["distanceM"] / 1609.344, 1)} for o in after]
            check(after[k_beach]["active"] and not after[0]["active"], "tapped alternative is active")
            page.locator(".route-hazards-toggle").click()
            page.wait_for_timeout(300)
            out["total after"] = page.locator(".route-total-distance").inner_text()
            check(out["total after"] != out["total before"]
                  and f"{options[k_beach]['mi']:.1f} mi" in out["total after"], "panel total updates")
            out["labels after tap"] = page.locator(".route-alternative-label").all_inner_texts()
            out["share first lines"] = page.evaluate("() => window.__WARN_MAP_TEST__.shareText()").split("\n")[:3]
            check(f"{options[k_beach]['mi']:.1f} mi" in out["share first lines"][1], "share text uses the active route")
            page.locator(".route-hazards-toggle").click()
            page.wait_for_timeout(300)
            page.screenshot(path=os.path.join(SHOTS, "step7-alternative-active.png"))
            page.locator(".route-hazards-toggle").click()

            options = plan(page, SOLDOTNA, HOMER)
            out["soldotna->homer options"] = options
            check(len(options) == 1, "Soldotna -> Homer: no alternative")
            check(page.locator(".route-alternative-label").count() == 0, "Soldotna -> Homer: no labels")
            browser.close()
    finally:
        proc.terminate()

    out["console"] = console
    out["external"] = sorted(set(external))
    check(not console, "console clean")
    check(all(m in ("GET", "OPTIONS") and u.endswith("/rest/v1/public_hazards") for m, u in set(external)),
          "only the Supabase hazards GET")
    out["failures"] = failures
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
