# Meter calibration: the app's trip meter against iCabbi

Started 2026-10-19. This page records real trips that were metered by both iCabbi (the cab's meter) and the WARN
Meter page, and what they show. **It holds no GPS data.** Per-fix traces stay on the phone, or in the driver's own
Downloads folder when exported, and are never committed.

## The tariff

| Part | Rule |
|---|---|
| Start | $3.75 |
| Distance | $0.20 per step of 1/14 mile |
| Waiting | $0.50 per whole minute of cumulative waiting over the trip; partial minutes are not charged |

## The trips

App fares for trips 1 to 3 were shown under the app's old billing (before 2026-10-19's cab-meter wait rule); trip 4
was metered under the current rule. The column "now" is what the current app charges for the same app distance and
wait.

| Trip | iCabbi distance | iCabbi wait | iCabbi fare | App distance | App wait | App fare (then) | App fare (now) | Planner estimate |
|---|---|---|---|---|---|---|---|---|
| 1 | 26.07 mi | 0:00 | $76.75 | 26.3 mi | 0:56 | $77.75 | $77.35 | $76.75 |
| 2 | 6.84 mi | 1:12 | $23.45 | 6.9 mi | 1:12 | $23.55 | $23.65 | (none) |
| 3 | 13.96 mi | 2:01 | $43.95 | about 14.3 mi | 2:19 | $44.95 | $44.75 | 13.9 mi |
| 4 | 0.50 mi | 0:51 | $5.15 | 0.6 mi (about 0.55 to 0.61) | 1:07 | $5.85 | $5.85 | (none) |

Distance the app read over iCabbi: trip 1 about +0.23 mi (+0.9%), trip 2 about +0.06 mi (+0.9%), trip 3 about
+0.34 mi (+2.4%), trip 4 about +0.05 to +0.11 mi (+10% to +22%). The planner's road distance sits within 0.06 mi of
iCabbi on trip 3. Trip 4 is short, so a fixed amount of extra distance per trip (at the start or end, or at a stop)
shows up as a large percentage there; the traces will tell.

Trip 4's $0.70 gap is one distance step ($0.20, 8 steps against 7) and one wait minute ($0.50).

### Waiting

| Trip | iCabbi wait | App wait | App extra | Whole minutes, iCabbi / app |
|---|---|---|---|---|
| 1 | 0:00 | 0:56 | +56 s | 0 / 0 |
| 2 | 1:12 | 1:12 | 0 s | 1 / 1 |
| 3 | 2:01 | 2:19 | +18 s | 2 / 2 |
| 4 | 0:51 | 1:07 | +16 s | 0 / 1 |

The app reads more wait than iCabbi on three of four trips. On trip 4 the extra 16 s crossed a whole minute and cost
$0.50. Open question: which part of the extra is slowing down and speeding up around stops (the app counts time below
12 mph, and its three-slow-fix rule credits the first slow intervals in one lump) and which is real standstill that
iCabbi does not count. Answering it needs the trip 3 and 4 traces.

## How iCabbi rounds distance into steps

Steps each iCabbi fare implies, after the start fare and its whole wait minutes:

| Trip | iCabbi distance | Exact steps (x 14) | Steps the fare implies | Round to nearest | Round up |
|---|---|---|---|---|---|
| 1 | 26.07 | 364.98 | 365 | 365, fits | 365, fits |
| 2 | 6.84 | 95.76 | 96 | 96, fits | 96, fits |
| 3 | 13.96 | 195.44 | 196 | 195, **does not fit** ($43.75) | 196, fits |
| 4 | 0.50 | 7.00 | 7 | 7, fits | 7, fits (only if the true distance was at most 0.5000 mi) |

Taken at face value, only rounding up fits all four. But iCabbi shows distance to 0.01 mi, and the answer depends
on whether that display is rounded or truncated (the share of possible true distances, within each trip's display
window, for which the rule gives iCabbi's fare):

| If iCabbi's display is | Nearest fits all four | Round up fits all four |
|---|---|---|
| Rounded to 0.01 (true distance within ±0.005) | 7% | 32% |
| Truncated to 0.01 (true distance up to 0.01 more) | 57% | 0% |

Trip 4 is exactly 7 steps at 0.50 mi, so under round-up any true distance above 0.5000 mi would have been 8 steps:
if iCabbi truncates its display, round-up cannot explain trip 4 at all.

Trip 3 is the deciding case for "nearest": 195.44 steps rounds to 195 unless the true distance was at least
13.9643 mi. Trip 1 is the deciding case for "round up": 26.07 mi is just under 365 steps (26.0714 mi), so a true
distance even slightly above 26.0714 would have been 366 steps.

**Not settled.** Four trips cannot separate the two rules without knowing how iCabbi displays distance. The app keeps
round-to-nearest until either iCabbi's documentation states its rule, or more trips land where the two rules give
different fares (any trip whose exact steps fall between n + 0.0 and n + 0.5 under one display reading and not the other).

## Open hypotheses for the distance over-read

None of these is confirmed. No trip trace had reached the analysis computer when this was written (2026-10-19), so
none has been measured.

1. **Snapping slack.** The app accepts a snapped road path up to 150 m longer than the straight line between two
   fixes. A wrong-edge snap at junctions, ramps or parallel roads can add up to that per fix.
2. **Jitter while stopped or crawling.** Every accepted fix adds its movement, including GPS noise at lights and in
   queues. Trip 3 had more waiting (about two minutes) than the others, which fits its larger over-read.
3. **Distance at the start or end.** Movement or jitter recorded while the cab is still at the pickup or already at the
   drop-off. Trip 4's large percentage over-read fits a fixed per-trip amount.
4. **Accepted low-accuracy fixes.** Fixes up to 50 m accuracy are accepted, and their position error is added as distance.
5. **Scale.** A uniform difference between the app's and iCabbi's distance measures. Unlikely to be the main cause,
   since the over-read varies from 0.9% to over 10%.

What decides between them is the per-fix trace (one exported CSV per trip): metres added while slow versus moving,
the snapped-minus-straight excess, and distance added by fixes that moved less than their own accuracy.
