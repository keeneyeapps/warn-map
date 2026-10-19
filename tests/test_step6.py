"""About card and route preference (Shortest / Fastest / Main roads)."""
import json
import re
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from harness import CHANNEL, OUT, PYTHON, REPO, SCRATCH, SHOTS, base_url, port_for, start_server  # noqa: F401

PORT = port_for(8772)
BASE = f"http://127.0.0.1:{PORT}/?test=1"
PLACES = {
    "soldotna": [60.4864, -151.0583], "kenai": [60.5603, -151.2235], "homer": [59.6425, -151.5483],
    "sterling": [60.537, -150.765], "kasilof": [60.3375, -151.2747], "nikiski": [60.69, -151.29],
    "k-beach": [60.4975, -151.2050], "funny river": [60.4890, -150.8590],
}
PAIRS = [("soldotna", "kenai"), ("kenai", "homer"), ("nikiski", "kasilof"), ("k-beach", "sterling"),
         ("kenai", "funny river")]
NOTICE = ("Required Notice: Copyright 2026 Keen Eye Apps (https://keeneyeapps.github.io). "
          "Licensed under PolyForm Noncommercial 1.0.0.")


def serve():
    return start_server(PORT)


def routes(page):
    out = {}
    for start, end in PAIRS:
        out[f"{start}->{end}"] = page.evaluate(
            """([s, d]) => window.__WARN_MAP_TEST__.routeWithBlockedPoints(s, d, []).then((r) => r.route
                 ? { mi: Math.round(r.route.totalDistanceM / 160.9344) / 10, min: Math.round(r.route.estimatedTimeSeconds / 6) / 10 }
                 : { reason: r.reason })""", [PLACES[start], PLACES[end]])
    return out


def choose(page, value):
    if page.locator("#settings-panel").is_hidden():
        page.locator("#settings-toggle").click()
    page.locator(f'input[name="route-preference"][value="{value}"]').check()
    page.wait_for_timeout(300)


def main():
    out, console, external, failures = {}, [], [], []

    def check(condition, what):
        if not condition:
            failures.append(what)

    licence = open(os.path.join(REPO, "LICENSE"), encoding="utf-8").read()
    check(licence.startswith("# PolyForm Noncommercial License 1.0.0"), "LICENSE starts with PolyForm NC 1.0.0")
    check("> Required Notice: Copyright 2026 Keen Eye Apps (https://keeneyeapps.github.io).\n> Licensed under PolyForm Noncommercial 1.0.0." in licence, "LICENSE notice")
    check(NOTICE in open(os.path.join(REPO, "README.md"), encoding="utf-8").read(), "README notice")

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
            page.wait_for_function("async () => (await caches.keys()).some((n) => n.startsWith('warn-map-shell-'))",
                                   timeout=60000)

            page.locator("#settings-toggle").click()
            page.wait_for_timeout(500)
            about = page.locator(".about-card")
            out["about"] = [line for line in about.inner_text().split("\n") if line]
            out["about links"] = page.evaluate("() => [...document.querySelectorAll('.about-links a')].map((a) => [a.textContent, a.href, a.target])")
            expected_version = re.search(r"warn-map-shell-v(\d+)", open(os.path.join(REPO, "sw.js"), encoding="utf-8").read()).group(1)
            check(page.locator("#app-version").inner_text() == expected_version, f"About version {expected_version}")
            check(NOTICE in about.inner_text(), "About notice")
            check([l[1] for l in out["about links"]] == ["https://keeneyeapps.github.io/warn/privacy.html",
                                                        "https://keeneyeapps.github.io/warn/licence.html"], "About links")
            out["default preference"] = page.evaluate("() => document.querySelector('input[name=\"route-preference\"]:checked')?.value")
            check(out["default preference"] == "fastest", "Fastest by default")
            page.screenshot(path=os.path.join(SHOTS, "step6-settings-about.png"))

            results = {}
            for value in ("shortest", "fastest", "main"):
                choose(page, value)
                results[value] = routes(page)
            out["routes"] = results
            for pair in results["fastest"]:
                s, f, m = results["shortest"][pair], results["fastest"][pair], results["main"][pair]
                check(s["mi"] <= f["mi"] + 0.05 and s["mi"] <= m["mi"] + 0.05, f"{pair}: shortest has least distance")
                check(f["min"] <= s["min"] + 0.05 and f["min"] <= m["min"] + 0.05, f"{pair}: fastest has least time")

            # The panel names the preference in use; changing it re-routes.
            choose(page, "fastest")
            page.evaluate("([s, d]) => window.__WARN_MAP_TEST__.route(s, d)", [PLACES["k-beach"], PLACES["sterling"]])
            page.wait_for_function("() => document.querySelector('.route-preference-in-use')", timeout=30000)
            out["panel fastest"] = page.locator(".route-total").inner_text().replace("\n", " | ")
            check(page.locator(".route-preference-in-use").inner_text() == "Fastest route", "panel shows Fastest")
            choose(page, "shortest")
            page.wait_for_function("() => document.querySelector('.route-preference-in-use')?.textContent === 'Shortest route'",
                                   timeout=30000)
            out["panel shortest"] = page.locator(".route-total").inner_text().replace("\n", " | ")

            page.reload()
            page.wait_for_function("() => !!window.__WARN_MAP_TEST__", timeout=60000)
            out["after reload"] = page.evaluate("() => document.querySelector('input[name=\"route-preference\"]:checked')?.value")
            check(out["after reload"] == "shortest", "preference persists")
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
