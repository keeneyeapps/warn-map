# Browser tests

End-to-end tests for the web pages, driven by Playwright. They load the real pages from a local static server
(rooted at the repo, with Range support for the PMTiles basemap), with geolocation, speech, the share sheet and the
hazard feed mocked, so they need no network and send nothing anywhere.

## Run everything

From the repo root, on Windows:

```
python tests\run_all.py
```

It starts one server, waits until it actually answers, runs each test below, prints `PASS`/`FAIL` per test and a
summary, and exits non-zero if anything failed. A test counts as failed if it exits non-zero or prints a `SOFTFAIL`
line. A full run takes about six minutes.

```
python tests\run_all.py --list              # the test names
python tests\run_all.py start places        # only these (names without the test_ prefix)
python tests\test_start.py                  # one test on its own: starts and stops its own server
python tests\test_step9.py --update-sample  # also rewrite docs\screenshots\print-sample.pdf
```

Output (logs, screenshots, PDFs) goes to `tests\out\`, which is git-ignored. Nothing is written elsewhere in the
repo; the committed print sample is only replaced when you pass `--update-sample`.

## Install

Python 3.10 or newer (developed on 3.12). In a virtual environment:

```
python -m venv .venv-tests
.venv-tests\Scripts\activate
pip install -r tests\requirements.txt
```

The tests drive **Microsoft Edge** (installed with Windows) through Playwright's `msedge` channel, so no browser
download is needed. To use Playwright's bundled Chromium instead, run `playwright install chromium` once and set
`WARN_TEST_CHANNEL=` (empty) before running. To test another checkout of the repo (for example an export of an older
commit), set `WARN_REPO` to its path.

## What each test covers

| Test | Covers |
|---|---|
| `start` | `start.html`: both cards navigate, the Share button and its Copy-link fallback, no manifest link, no driver/tariff/fare/taxi/fleet wording, Open Graph tags, offline; the two manifests' scopes (Drive/Alerts scope must not contain the Map's start_url); the moose backdrop (loads, precached, no horizontal scroll at 360 px, cards still clickable, no visible box, WCAG AA over the art). |
| `places` | Named-place suggestions (hospital, post office, "safeway" nearest first), place names in the route bar and share text, tap-to-call links, offline search. |
| `region` | The region pack: start view, title, nine route distances, address and place suggestions, the service-worker cache contents, offline routing. |
| `addresses` | Address entry: "35235 kalif" first, stops and streets, tap-the-map fallback, offline index. |
| `share` | Route share text, destination flag, the driver-mode fare line, the clipboard fallback, offline routing. |
| `labels` | Plain hazard names (never raw `hazard_type` codes) in the panel, the Map's plain popups and the share text. |
| `step5` | Icon sizes and maskable safe zone, region-boundary ranking ("walmart"), phone overrides, popups folding the route panel, tapping the map near a place. |
| `step6` | The About card (version from `sw.js`, notice, links) and the Fastest / Main roads / Shortest preference. |
| `step7` | Route alternatives: Kenai to Soldotna offers the K-Beach route and swapping it in; Soldotna to Homer offers none. |
| `step8` | Driver mode (hidden by default, `?driver`, 7 taps), the tariff formula and half-up rounding, the trip meter, reload-resume, saved trips. |
| `step9` | Wide layout (380 px column, map fills the rest), the phone layout unchanged, printing to Letter and A4 PDFs with the light-theme map. |
| `step11` | The Map/Alerts split: module rules (static and dynamic), plain Map with no cards or speech, WARN Alerts cards and spoken alerts, mute, repeat suppression, clean console. |
| `trace` | The per-trip GPS trace (driver tools): one row per fix with the reject reasons, exact distance and wait stored next to the rounded figures, trimming to 10 traces and 20,000 rows, Export trace (CSV columns, on tap only), Delete traces, storage-full handling, nothing uploaded. |
| `meter` | WARN Meter (`meter.html`) as its own unlisted page: the Map and Alerts never load driver.js or trace.js and show nothing meter-related even with old driver-mode flags and trip data, seven taps unlock nothing, `meter.html` has the meter, tariff fields, Trips, the fare line and carries over existing data, and works offline from its own cached copy; static module rules. |
| `header` | The header band on the Map, Alerts and meter pages at 360, 390 and 1280 px: variant name, the Android black and yellow as CSS variables and computed colours, at most 44 px tall, unchanged moose image, nothing overlapped, controls clickable, no horizontal scroll, hidden in print, wide layout under the band, clean console. |
| `watch` | WARN Watch (`watch.html`, unlisted): the hazard list and summary match mocked hazards (newest first, colours, ages, confirmations, on-device place descriptions checked against an independent re-implementation), a new hazard is highlighted after a refresh, a failed fetch keeps the data and says stale, a row click flies the map and opens the popup, 30 s polling, wake lock, refresh on return, quiet reload on a new version, no geolocation, none of the Drive/meter modules, nothing links to it, offline, clean console at 1920x1080 and 1366x768. |

`check_icons.py` is a helper for `step5`. `manual/` holds two scripts that are not part of the suite (a live-site
check and a phone-upgrade simulation); see `manual/README.txt`.

## Not here

`test_avoid`, `test_plan` and `test_phone` from the earlier steps are not included: they drive UI that has since
been redesigned and already failed before this suite was moved, so keeping them would make the suite red.
