# Reporting defences and a write identity for WARN Drive (PROPOSAL, nothing applied)

**Status (2026-10-19): migration 045 APPLIED and verified; everything else design only.** 045 (the
insert-velocity observation; 044 belongs to the report descriptor) was applied to the linked project on
2026-10-19 at 17:26 UTC by CLI and verified read-only (`WARN/supabase/scratch/verify_045.sql`: V1 and V5 all
PASS, both live counts on the new index; V2-V4 are insert paths, covered by the local run in section 5 only).
The observation week runs from that moment. 046, 047 and 048 are not written.
Belongs with the WARN repo's `docs/design/security-migrations-proposed.md`; it lives here because that repo was
read-only for this session. Move it there when accepted.

Every claim is tagged **[R] recorded** (live catalog query, migration file, or source line), **[I] inferred**,
or **[GAP]** (nothing establishes it: decide, don't assume), per `docs/design/README.md` in the WARN repo.

## 0. What is true today (from the investigation of 2026-10-18)

- [R] `upsert_spatial_hazard_v3`, `confirm_spatial_hazard`, `clear_spatial_hazard`, `retract_spatial_hazard`,
  `amend_spatial_hazard`, `set_delay_transit` are SECURITY DEFINER, owned by `postgres`, executable by `anon`.
  None reads `auth.uid()`, a device id, or a tier (live `pg_proc` scan). Android and the fleet tablets call them
  with the anon key as both `apikey` and `Bearer` (`HazardOverlayService.kt:2184-2190`).
- [R] Insert defences: the merge radius only (300 m most types, 20 m road damage, 2 h window; table
  `hazard_merge_radius`, 039) and the `created_at` clamp to `now()+1 h` (019). No count, window, or velocity check
  anywhere in v3's body.
- [R] Confirm: `update … set created_at = now(), confirmation_count = confirmation_count + 1 where id = … and
  cleared_at is null`, returning the new count, or null when the row is gone. No spacing, no bound. The expiry
  it drives IS bounded by `hazard_cap_end` (4x the day term; wildlife 8 h day / 24 h night; `clock` shapes at
  `max_minutes`) from `first_reported_at`, which clients cannot move (037's trigger overwrites it).
- [R] Clear: a global cap of 20 per rolling 10 min across all callers, serialised by advisory lock, logging
  `clear_cap_hit` once per window and `clear_burst` at 10 (024, 027, 041). Wildlife categories refuse.
- [R] Android's behaviour on refusal: a non-2xx from v3 queues the report locally and re-sends on the next
  flush (`saveHazardLocally`, `HazardOverlayService.kt:2306-2310`); Confirm is fire-and-forget (a non-2xx or
  `null` only writes a DiagLog event, `:2020-2026`); a `false` from Clear shows a "could not clear" toast and
  keeps the card (`:3878-3890`).
- [R] Real volume, from `hazard_audit_log` with test rows (`BURST*`, `*TEST*`) excluded: 318 reports over
  53 days (2026-08-15 to 10-18); busiest hour 12, busiest 10 minutes 10, busiest day 30; `hazard_members`
  inserts since 09-30 peak at 5 per hour. Clears: 14 ever, all road damage, busiest 10 minutes 2.
  `security_events` is empty: no cap has ever been hit.
- [R] The published privacy policy (keeneyeapps.github.io/warn/privacy.html, 2026-10-16) says a report carries
  "no identifier of any kind … no account, no user ID, no device ID, and no per-install token attached to a
  report, not even an anonymous one", and that the database "has no column in which your identity could be
  recorded". Any write identity (part 2) is a policy change, not just a schema change.
- [R] Server-side time sources: `hazard_audit_log.reported_at` and `hazard_members.observed_at` are the
  CLIENT's `created_at`; only `hazard_members.received_at` (default `now()`) is server time, and it is written
  inside v3 after the insert. `hazard_audit_log` has no server-time column and no index beyond its key.

## 1. Identity-free caps (protect everyone today, APK untouched)

Modelled on the clear cap: count in a rolling window under an advisory lock, log to `security_events` at most
once per window, refuse past the cap. Honest use is set by the numbers above; every cap below sits at least
5x above the busiest real window ever recorded, and the half-cap warning sits at or above the busiest real
window, so an honest spike is logged before anything is refused.

### 1a. Insert velocity on v3

**Counting source.** Add `inserted_at timestamptz not null default now()` to `hazard_audit_log` (written by
`trigger_hazard_audit` on every insert through any path; server time, client cannot set it) with an index.
Counting `spatial_hazards.created_at` or `hazard_audit_log.reported_at` would count a client-chosen time.

**Caps (enforced in v3, under `pg_advisory_xact_lock(hashtext('warn.insert_cap'))`):**

| Cap | Window | Limit | Warn at | Real max seen | Reasoning |
|---|---|---|---|---|---|
| Global | 10 min | 60 | 30 | 10 | A dozen cabs on a black-ice morning at one report a minute each is 120/10 min in theory, but the busiest real 10 minutes in 53 days is 10. 60 is 6x that; the fleet is small. Raise when the fleet grows. |
| Per `region_code` | 10 min | 40 | 20 | 10 (all KPB) | Only one region exists, so this binds before the global one today. It exists so a flood in one region cannot use up the global budget of another. |
| Same type within 2 km | 15 min | 5 wildlife / other, 10 road damage | 3 / 6 | [GAP] not measured per area; 12/h was the busiest hour anywhere | This is the stringing defence. "A report every 301 m" of MOOSE makes a 6th new row inside 2 km within 15 min; a 21 m pothole string makes an 11th. A real 2 km stretch with 10 distinct potholes in 15 minutes is a bad road, and the 11th tap is refused for 15 minutes, not lost. |

**Past the cap: refuse, don't merge.** `raise exception using message = 'insert_cap_hit', errcode = 'P0001'`.
PostgREST answers 400; Android queues the report and re-sends on the next flush, so an honest report past the
cap is delayed by up to one window, not lost [R, `:2306`]. The alternative, folding the excess into the nearest
live same-type row up to 2 km away, was rejected: 040 keeps the first position, so it would reset `created_at`
("last seen just now") at a point up to 2 km from where the driver saw it, a false freshness claim; and
`was_inserted=false` would deny the driver an Undo for a tap they can't take back any other way.

**Honest failure mode.** A driver whose report lands in a full window gets it filed one flush later. The
flush may re-hit a still-full window for its excess and re-queue; nothing is dropped. [GAP] What the APK shows
the driver for a 400 from v3 (toast wording) is not recorded here; check before enforcing.

**Logging.** `security_events` kinds `insert_burst` (warn level, once per window, counts and region) and
`insert_cap_hit` (once per window, with the refused `hazard_type`, region, and which of the three caps bound).
Never the position of the refused report: `security_events` is admin-readable and a refused report is still
someone's location.

**Order.** Ship it in two steps: an observation trigger first (migration 045 below: counts and logs, refuses
nothing, touches no RPC), a week of real numbers, then the enforcement edit to v3 (048) with the thresholds
confirmed or adjusted. 027 did the same with `clear_burst` before 024's cap was trusted.

**What the observation pushes (decided 2026-10-19).** Two event kinds. `insert_velocity_observed` (global ≥ 30
or region ≥ 20 in 10 min) is born pending and 027 pushes it like `clear_cap_hit`. `insert_velocity_area_observed`
(same type within 2 km ≥ 3 in 15 min, with no global or region crossing) is born `push_state = 'log_only'`: it is
read in the Master UI and never pinged or pushed, because the area level is expected to fire on honest use (one
moose past three cabs) and a push for it would be noise. Each kind is written at most once per rolling 10
minutes. Note that any 20 reports in 10 minutes, at one point or spread over the region, is a region crossing
and does push; "32 inserts at one point" is therefore one area row, one region row, one ping.

### 1b. Confirm

Two problems: a Confirm every second inflates `confirmation_count` and makes a stale hazard read "last seen
just now" to every driver, until the expiry cap (24 h for wildlife at night, 2 years for road damage, whose
cap is 4 x 180 days) finally ends it; and the 2 h merge window keeps reopening.

**Change (`confirm_spatial_hazard`, same signature, same return contract):**
- **Spacing:** a Confirm within 120 s of the row's current `created_at` changes nothing. A card is on screen for
  one encounter; two drivers confirming the same moose inside two minutes is the second one's evidence, but the
  freshness claim is already "just now". [I] Nothing in Android resends a Confirm, so no retry loop forms.
- **Bound:** after 10 effective Confirms the count and `created_at` freeze. The hazard still runs to its expiry
  (reset on the 10th); it just can't be kept alive by tapping. [I] A real hazard with 10 independent
  confirmations over hours is rare here (live distribution: 18 rows at 0, one each at 1, 2, 3). Revisit if
  `confirm_cap_hit` ever fires for a hazard that was real.
- **Return:** the row's current count, unchanged, when a Confirm is suppressed; `null` only when the row is gone,
  exactly as now. Android parses an integer as `confirm_counted` and `null` as `confirm_hazard_gone` [R,
  `:2020-2026`], so neither path changes on the device; a suppressed Confirm is indistinguishable from a counted
  one to the driver, which is right: they did confirm, the server just didn't need it.
- **Side effect kept honest:** `trg_hazard_confirm_member` fires only when `created_at` changes, so a suppressed
  Confirm writes no member row. 031's "one member row per Confirm" becomes "per effective Confirm"; note it there.

**Logging.** `confirm_cap_hit` once per hazard (id, count) when the bound is reached; a global
`confirm_burst` at 30 per 10 min (log only, no refusal; real volume [GAP]: Confirms are not in the audit log,
only `hazard_members kind='confirm'`, 3 rows since 09-30).

### 1c. The global clear cap

20 bogus clears lock out every honest clear for 10 minutes, and the attacker can keep the window full
indefinitely. The cap was sized for a fleet that has cleared 14 rows in its life.

**Change (`clear_spatial_hazard`, same signature and return):**
- **Per-area cap, the one that binds:** at most 5 clears within 5 km of the target in 10 minutes. An attacker
  can lock out clearing in their own 5 km, nowhere else. Real max is 2 per 10 min anywhere.
- **Minimum age:** a row younger than 5 minutes (by `first_reported_at`, server-set) can't be cleared. A fresh
  report has an Undo for the reporter; "clear it before anyone acts" is the griefing path, and a real
  obstruction reported 4 minutes ago is still there. [I] 5 minutes is a guess; the honest cost is a driver who
  passes a just-reported, already-gone obstruction and must wait.
- **Global backstop raised to 40:** it stays as the last line, and with the area cap in front of it, filling it
  needs eight separate 5 km areas at once.

**Honest failure mode.** A driver clearing stale potholes down one road hits the area cap at the 6th within
10 minutes and sees "could not clear"; they can clear it later. The toast already exists [R].

**Logging.** `clear_area_cap_hit` (once per window per 5 km cell, target id), `clear_too_fresh` (once per
window, hazard id and age), the existing `clear_cap_hit`/`clear_burst` unchanged.

## 2. A write identity for Drive and fleet reporting (later, separate)

| | (i) Seeded Supabase auth accounts | (ii) Channel-style per-device credential | (iii) Neither |
|---|---|---|---|
| What it is | Email+password per known driver, seeded by hand (sign-ups are off [R]). Calls carry the user JWT; `auth.uid()` is available server-side. | A random 128-bit secret made once per install, its hash in a `report_credentials` table, sent as a new optional v3 parameter (`v4` wrapper per 015's rule). Revocable per device. | Drive reports exactly as the APK: anon key, no identity. |
| Android change | Sign-in screen, token storage and refresh, offline queue that survives an expired token, per-driver password handling on shared cab tablets. Large. | Generate and store one secret, add one parameter to the v3 call and to the queue schema. Small. | None. |
| Grants | `grant execute` on v3, retract, confirm, clear, amend, set_delay_transit to `authenticated` (v3 and amend are anon-only today [R]). | None: still anon. The RPC checks the credential itself. | None. |
| Lets anon be tightened later | Yes: once every device signs in, revoke the write RPCs from anon. The whole point. | Only by making the credential mandatory, which ends reporting by anyone who just installed the app. Possible for a fleet-only product, not for the public tier. | No. |
| Per-caller caps | Yes, keyed on `auth.uid()`. | Yes, keyed on the credential hash. | No; part 1 only. |
| Privacy policy | Breaks "no accounts, no sign-in" and "no identifier of any kind" outright. Even if `auth.uid()` is never stored, the server can see who filed what. | Breaks the explicit "no per-install token … not even an anonymous one". Honest wording is possible ("a random per-install code, revocable, never linked to a person"), but the sentence was written to rule exactly this out. | Unchanged. |
| Effort | Weeks (Android auth, support burden). | A migration and a day of Android work; a week of soak. | Zero. |

**Recommendation:** (iii) now, with part 1 in place; (ii) later and only if `security_events` shows abuse that
part 1 doesn't contain, with the privacy policy rewritten honestly first; (i) only ever for channel admins,
never for driver reports.

## 3. What must be recorded before any report ships from the web

1. **`passenger-pwa.md` section 2 reversal.** It says "The web page never files a report… This is permanent,
   not a 'not yet'." Record, dated and attributed: WARN Drive is a driver tool, not the passenger viewer; the
   viewer rule stands for WARN Map (`index.html`), which keeps the no-write guarantee; Drive applies the
   Android report gates it can (accuracy ≤ 50 m, fix age, one fix per deliberate tap, Undo within 30 s) and sends
   provenance so the server can judge the position (028).
2. **Privacy-policy re-read in the same change** (migrations README item 1). The policy today describes the
   APK. It must say the web version can file a report, that it sends exactly the fields listed under "Hazard
   reports" plus the fix's accuracy and age, that the browser keeps the undo code in memory only, and that
   WARN's own web pages (not only OSM tile servers) are then contacted with a position, once per tap. The
   "no identifier" paragraph stays true under part 2 option (iii) only.
3. **CLAUDE.md wording, scoped to `warn.html`.** Keep both trunk rules verbatim for `index.html`. Add for
   `warn.html`: "`js/report.js`, and nothing else, may call `upsert_spatial_hazard_v3` and
   `retract_spatial_hazard` with the anon key. It sends one GPS fix per deliberate tap as the hazard's
   position, with the hazard type, course, fix time, accuracy and fix age, and a random undo token; never
   continuous location, never a trip, route or identity."

## 4. Recommended order of work

1. **045 (below): insert-velocity observation.** A column, two indexes, one AFTER INSERT trigger that only
   logs, and 027's push policy taught that a row can be born `log_only`. No RPC changes. Reversible by the
   rollback file beside it. (044 is the report-descriptor migration, `WARN/docs/design/report-descriptor.md`.)
2. One week of `security_events` reading; confirm or adjust the thresholds in 1a.
3. **046: Confirm spacing and bound** (1b). Smallest RPC edit; nothing on Android changes.
4. **047: clear per-area cap, minimum age, backstop 40** (1c).
5. **048: insert cap enforcement in v3** (1a), the only edit to the 14 KB v3 body; takes the advisory lock
   045 deliberately does not; verify with a rolled-back burst as 024/029 did.
6. The three records in part 3, then `js/report.js` on `warn.html` only.
7. Part 2 (ii) only if abuse appears.

## 5. Migration 045, hardened and tested locally (NOT APPLIED; apply only on instruction)

**Numbering (resolved 2026-10-19).** 044 stays with the report-descriptor migration
(`WARN/docs/design/report-descriptor.md` l.29; `WARN/CLAUDE.md` records the same). `WARN/supabase/migrations/`
ends at 043 and nothing in WARN's docs reserves anything past 044, so the observation is **045**, and the later
steps in section 4 shift to 046 (Confirm), 047 (Clear) and 048 (enforcement). The 2026-10-18 and early
2026-10-19 drafts called this migration 044; every file and reference has been renamed.

**Files (2026-10-19).** Canonical copies under `docs/design/sql/` in this repo; the migration and its rollback
are also copied, byte for byte, into `WARN/supabase/migrations/` (uncommitted there). Nothing in this folder is
picked up by any tool. The rollback carries a numeric prefix only so it sorts beside its migration: it is
applied with `db query --file`, never through `db push`, as every migration in that folder is.

| File | What |
|---|---|
| `045_insert_velocity_observe.sql` | The migration, with the standard header and the verify outline. 5.4 below is a copy for review. |
| `045_insert_velocity_observe_rollback.sql` | The rollback (5.5). Idempotent. |
| `045_local_fixture.sql` | The slice of the live schema the local test runs against (never apply it anywhere). |
| `045_local_test.py` | The test. `pip install pgserver "psycopg[binary]"`, then run it with `--owner warn_owner` and `--owner superuser`; about 15 s each. |

**Earlier claim withdrawn.** The 2026-10-18 version of this section reported a 14-check local run on
"pgserver in a scratch venv". No harness, fixture, cluster or venv from that run exists on this machine, and
the SQL it tested was never saved as a file, so that output could not be reproduced. It is replaced by the
runs below, whose method is in the repo and re-runnable.

### 5.0 What changed in the hardening (against the 2026-10-18 draft)

- **Backfill, the real defect found.** The draft added the column as `not null default now()`, which stamps
  every existing audit row (about 700 live) with the apply time. The first report filed within 10 minutes
  of the apply would then have counted 700 in the global window and logged a spurious event. Now the column
  is added without a default, existing rows get `least(reported_at, now() - 1 h)` (their client time, pushed
  back to at least an hour before the apply), and only then are the default and NOT NULL set. [R] Tested:
  after the apply no pre-045 row sits in any window, and a row whose client clock was 30 minutes fast is an
  hour in the past.
- **Push policy (step 27d).** Two kinds instead of one. `insert_velocity_observed` (global ≥ 30 or region
  ≥ 20) is born pending and 027 pushes it. `insert_velocity_area_observed` (area ≥ 3, no global/region
  crossing) is born `push_state = 'log_only'` and is never pinged or pushed. Two small, backward-compatible
  edits to 027 carry this: the `push_state` CHECK gains `'log_only'` (027's inline constraint is replaced by a
  named one, found by definition rather than by name, since the live name was never recorded), and the ping
  trigger gains `WHEN (new.push_state IS NULL)`. `claim_security_pushes()` already ignores rows whose
  `push_state` is not null, so a log-only row could not be pushed even if it were pinged. [R] Tested with
  027's `claim_security_pushes()` verbatim: it offers only the pushed kind.
- **Exception handler keeps the original error.** The draft's inner handler reported the *log* failure's
  sqlstate; the WARNING now names both the original failure and the reason it could not be logged.
- **No lock, and its honest cost, stated in the file header.** Two inserts that cross a level in the same
  instant can both write an event; the rolling check keeps it to about one per window, and 027's one push
  per kind per hour makes a second row free. An exact once-per-window needs an advisory lock or a unique
  index, and either would make one hazard insert wait on another's transaction, which this step forbids.
  048 (enforcement) will take the lock; 045 must not.
- **Ownership stated.** The function is SECURITY DEFINER and must be owned by the owner of
  `hazard_audit_log` and `security_events` (postgres, which the CLI apply gives). Both tables have RLS on
  with no policy for any other role, so a copy owned by anyone else would count zero rows and fail to log,
  silently. [R] The local test runs once with a non-superuser owner and once with the cluster superuser.
- Standard header (why, what, not touched, grants, ownership, privacy re-read, verify outline V1-V6),
  column and function comments, `levels_crossed` and `window_minutes` in the event detail.

### 5.1 Test method

[R] `045_local_fixture.sql` recreates, from the migration files: roles `anon`, `authenticated`,
`service_role` and a non-superuser owner; `spatial_hazards` with every live column (000 + 024 + 028 + 036 +
037 + 041) so v3's INSERT column list is valid; `hazard_audit_log` and `trigger_hazard_audit` exactly as
000 + 022 define them (SECURITY DEFINER, pinned search_path, RLS on, authenticated reads via `is_admin()`);
`security_events` as 024 + 027 define it, with 027's inline `push_state` CHECK, 027's
`security_events_ping_notify` trigger over a stub `net.http_post` that records each call, and 027's
`claim_security_pushes()` verbatim; and a stub `upsert_spatial_hazard_v3` with the live signature that runs
the live insert branch's INSERT statement (041, l.231-254, with 019's `created_at` clamp). 301 audit rows
reported over the past 60 days stand in for the live history, one with a client clock 30 minutes fast.

Omitted on purpose: `trg_spatial_hazard_expiry` (037, BEFORE INSERT, needs the lifetime tables),
`trg_hazard_confirm_member` (031, AFTER UPDATE), the merge branch and `record_hazard_member`. None of them
touches `hazard_audit_log` or `security_events`, and none is an AFTER INSERT trigger, so none can change the
firing order or the counts. [I] The one live difference that matters, that 045's trigger fires inside the
same statement as 031's member insert, costs nothing: both are independent inserts into other tables.

`045_local_test.py` starts a fresh PostgreSQL 16.2 cluster (pgserver, embedded; this machine has no Docker
and no psql), loads the fixture as superuser, applies the migration as the chosen owner and inserts as
`anon` through the RPC. Forced failures: (b1) a CHECK on `security_events` that rejects both velocity kinds;
(b2) a CHECK that rejects every row; (b3) the counting column renamed away. The events table is aged by an
hour between crossings so the window check does not mask them. The 32-insert test is split at 5 and 32:
five inserts at one point cross the area level only (one log-only row, zero pings); the 20th crosses the
region level (one pending row, one ping); the 30th crosses the global level inside the open window and
writes nothing more.

### 5.2 Output, non-superuser owner (2026-10-19)

```
postgres: PostgreSQL 16.2 on x86_64-pc-mingw64, compiled by gcc.exe (x86_64-posix-seh-rev2, Built by MinGW-W64 project) 12.2.0, 64-bit
owner mode: warn_owner (current_user=warn_owner, superuser=False)
PASS migration applies twice without error; one trigger, two indexes, one function, one NOT NULL column with default now(): trigger=1 indexes=2 function=1 column=1
PASS backfill: no pre-045 row lands in a counting window; the fast-clock row is pushed back an hour: in_window=0 newest_ok=True fast_clock_ok=True
PASS fires after trigger_hazard_audit (AFTER INSERT triggers in name order): ['trigger_hazard_audit', 'trigger_insert_velocity_observe']
PASS anon/authenticated cannot execute the trigger function; it is owned by the table owner: (False, False, 'warn_owner', 'warn_owner')
PASS push policy: exactly one push_state check, named, accepting log_only; ping trigger has WHEN (new.push_state IS NULL): checks=[('security_events_push_state_check_045', "CHECK ((push_state = ANY (ARRAY['pushing'::text, 'pushed'::text, 'suppressed'::text, 'failed'::text, 'log_only'::text])))")] trigger=CREATE TRIGGER security_events_ping_notify AFTER INSERT ON public.security_events FOR EACH ROW WHEN ((new.push_state IS NULL)) EXECUTE FUNCTION ping_security_notify()
PASS push_state still rejects an unknown value
PASS (a) ordinary insert through v3 as anon succeeds; audit row has server inserted_at; no event: id=1 was_inserted=True audit=(True, True) events=0
PASS (c1) 5 inserts at one point: exactly ONE insert_velocity_area_observed row, push_state log_only, ZERO pings, no pushed kind: rows=5 area_rows=1 push_state=log_only fired_on_insert_no=3 levels=["area"] pings=0
PASS (c2) 32 inserts at one point: exactly ONE insert_velocity_observed row (region level at the 20th), pending, exactly ONE ping: rows=32 observed_rows=1 push_state=None fired_on_insert_no=20 levels=["region"] global=20 region=20 pings=1 area_rows=1
PASS 027's claim_security_pushes() offers only the pushed kind; the log_only row is never a candidate: ['insert_velocity_observed']
PASS a second crossing inside the window writes no second row of either kind and no second ping: events=2 pings=1
PASS (b1) forced error in the trigger (event insert blocked by a CHECK): insert still succeeds, observer_error logged: id=43 logged=('23514', 'new row for relation "security_events" violates check constraint "test_block"', True)
PASS (b2) log itself unwritable: insert still succeeds, WARNING raised instead, no event rows: id=44 warning=['WARNING: observe_insert_velocity failed (23514 new row for relation "security_events" violates check constraint "test_block_all") and could not log it (23514 new row for relation "security_events" violates check constraint "test_block_all")']
PASS (b3) counting column missing: insert succeeds, observer_error 42703 logged: id=45 sqlstate=('42703',)
PASS no advisory lock and nothing stronger than RowExclusiveLock while the trigger runs: locks held at end of the inserting transaction:
      relation  AccessShareLock    hazard_audit_log
      relation  RowExclusiveLock   hazard_audit_log
      relation  RowExclusiveLock   hazard_audit_log_id_seq
      relation  AccessShareLock    hazard_audit_log_inserted_at_idx
      relation  AccessShareLock    hazard_audit_log_pkey
      relation  AccessShareLock    hazard_audit_log_type_inserted_at_idx
      relation  RowExclusiveLock   net.http_post_calls
      relation  RowExclusiveLock   net.http_post_calls_id_seq
      relation  AccessShareLock    pg_locks
      relation  AccessShareLock    security_events
      relation  RowExclusiveLock   security_events
      relation  RowExclusiveLock   security_events_id_seq
      relation  AccessShareLock    security_events_pkey
      relation  RowExclusiveLock   spatial_hazards
      relation  RowExclusiveLock   spatial_hazards_id_seq

--- plan: global 10-minute count (5347 rows in the log, 862 in the window) ---
Aggregate  (cost=110.35..110.36 rows=1 width=8) (actual time=1.230..1.232 rows=1 loops=1)
  Buffers: shared hit=67
  ->  Bitmap Heap Scan on hazard_audit_log  (cost=23.01..108.18 rows=867 width=0) (actual time=0.628..1.087 rows=862 loops=1)
        Recheck Cond: (inserted_at > (now() - '00:10:00'::interval))
        Heap Blocks: exact=63
        Buffers: shared hit=67
        ->  Bitmap Index Scan on hazard_audit_log_inserted_at_idx  (cost=0.00..22.79 rows=867 width=0) (actual time=0.182..0.182 rows=862 loops=1)
              Index Cond: (inserted_at > (now() - '00:10:00'::interval))
              Buffers: shared hit=4
Planning Time: 0.284 ms
Execution Time: 1.323 ms

--- plan: same-type 2 km 15-minute count ---
Aggregate  (cost=192.90..192.91 rows=1 width=8) (actual time=1.770..1.772 rows=1 loops=1)
  Buffers: shared hit=69
  ->  Bitmap Heap Scan on hazard_audit_log  (cost=33.83..192.59 rows=126 width=0) (actual time=0.291..1.748 rows=80 loops=1)
        Recheck Cond: (inserted_at > (now() - '00:15:00'::interval))
        Filter: ((latitude IS NOT NULL) AND (longitude IS NOT NULL) AND (NOT (hazard_type IS DISTINCT FROM 'MOOSE'::text)) AND (('12742017.6'::double precision * asin(sqrt(LEAST('1'::double precision, (power(sin((radians(('60.49'::double precision - latitude)) / '2'::double precision)), '2'::double precision) + (('0.4925754583282326'::double precision * cos(radians(latitude))) * power(sin((radians(('-151.06'::double precision - longitude)) / '2'::double precision)), '2'::double precision))))))) <= '2000'::double precision))
        Rows Removed by Filter: 1193
        Heap Blocks: exact=63
        Buffers: shared hit=69
        ->  Bitmap Index Scan on hazard_audit_log_inserted_at_idx  (cost=0.00..33.80 rows=1268 width=0) (actual time=0.210..0.211 rows=1273 loops=1)
              Index Cond: (inserted_at > (now() - '00:15:00'::interval))
              Buffers: shared hit=6
Planning:
  Buffers: shared hit=6
Planning Time: 0.593 ms
Execution Time: 1.977 ms

PASS global count uses hazard_audit_log_inserted_at_idx
PASS area count uses one of the two 045 indexes, no seq scan
PASS third apply after all of the above: no error, still one trigger, two indexes, one push_state check
PASS rollback twice: trigger, function and indexes gone; inserted_at kept; 027's ping trigger back without WHEN; inserts still work: trigger=0 indexes=0 function=0 column=1 id=147 ping_trigger=CREATE TRIGGER security_events_ping_notify AFTER INSERT ON public.security_events FOR EACH ROW EXECUTE FUNCTION ping_security_notify()
      cost: mean insert via v3 as anon, 100 each, window suppressed: with trigger 6.23 ms, without 1.44 ms
PASS migration re-applies after rollback
      security_events by kind and push_state at the end: [('insert_velocity_area_observed', 'log_only', 1), ('insert_velocity_observed', 'pushing', 1), ('insert_velocity_observed', None, 1), ('observer_error', None, 2)]
      pings at the end: 4

20 of 20 checks passed (owner mode: warn_owner)
```

### 5.3 Output, superuser owner (2026-10-19; plans and locks identical to 5.2, omitted)

```
postgres: PostgreSQL 16.2 on x86_64-pc-mingw64, compiled by gcc.exe (x86_64-posix-seh-rev2, Built by MinGW-W64 project) 12.2.0, 64-bit
owner mode: superuser (current_user=postgres, superuser=True)
PASS migration applies twice without error; one trigger, two indexes, one function, one NOT NULL column with default now(): trigger=1 indexes=2 function=1 column=1
PASS backfill: no pre-045 row lands in a counting window; the fast-clock row is pushed back an hour: in_window=0 newest_ok=True fast_clock_ok=True
PASS fires after trigger_hazard_audit (AFTER INSERT triggers in name order): ['trigger_hazard_audit', 'trigger_insert_velocity_observe']
PASS anon/authenticated cannot execute the trigger function; it is owned by the table owner: (False, False, 'postgres', 'postgres')
PASS push policy: exactly one push_state check, named, accepting log_only; ping trigger has WHEN (new.push_state IS NULL): checks=[('security_events_push_state_check_045', "CHECK ((push_state = ANY (ARRAY['pushing'::text, 'pushed'::text, 'suppressed'::text, 'failed'::text, 'log_only'::text])))")] trigger=CREATE TRIGGER security_events_ping_notify AFTER INSERT ON public.security_events FOR EACH ROW WHEN ((new.push_state IS NULL)) EXECUTE FUNCTION ping_security_notify()
PASS push_state still rejects an unknown value
PASS (a) ordinary insert through v3 as anon succeeds; audit row has server inserted_at; no event: id=1 was_inserted=True audit=(True, True) events=0
PASS (c1) 5 inserts at one point: exactly ONE insert_velocity_area_observed row, push_state log_only, ZERO pings, no pushed kind: rows=5 area_rows=1 push_state=log_only fired_on_insert_no=3 levels=["area"] pings=0
PASS (c2) 32 inserts at one point: exactly ONE insert_velocity_observed row (region level at the 20th), pending, exactly ONE ping: rows=32 observed_rows=1 push_state=None fired_on_insert_no=20 levels=["region"] global=20 region=20 pings=1 area_rows=1
PASS 027's claim_security_pushes() offers only the pushed kind; the log_only row is never a candidate: ['insert_velocity_observed']
PASS a second crossing inside the window writes no second row of either kind and no second ping: events=2 pings=1
PASS (b1) forced error in the trigger (event insert blocked by a CHECK): insert still succeeds, observer_error logged: id=43 logged=('23514', 'new row for relation "security_events" violates check constraint "test_block"', True)
PASS (b2) log itself unwritable: insert still succeeds, WARNING raised instead, no event rows: id=44 warning=['WARNING: observe_insert_velocity failed (23514 new row for relation "security_events" violates check constraint "test_block_all") and could not log it (23514 new row for relation "security_events" violates check constraint "test_block_all")']
PASS (b3) counting column missing: insert succeeds, observer_error 42703 logged: id=45 sqlstate=('42703',)
PASS no advisory lock and nothing stronger than RowExclusiveLock while the trigger runs: locks held at end of the inserting transaction:
PASS global count uses hazard_audit_log_inserted_at_idx
PASS area count uses one of the two 045 indexes, no seq scan
PASS third apply after all of the above: no error, still one trigger, two indexes, one push_state check
PASS rollback twice: trigger, function and indexes gone; inserted_at kept; 027's ping trigger back without WHEN; inserts still work: trigger=0 indexes=0 function=0 column=1 id=147 ping_trigger=CREATE TRIGGER security_events_ping_notify AFTER INSERT ON public.security_events FOR EACH ROW EXECUTE FUNCTION ping_security_notify()
      cost: mean insert via v3 as anon, 100 each, window suppressed: with trigger 7.84 ms, without 1.39 ms
PASS migration re-applies after rollback
      security_events by kind and push_state at the end: [('insert_velocity_area_observed', 'log_only', 1), ('insert_velocity_observed', 'pushing', 1), ('insert_velocity_observed', None, 1), ('observer_error', None, 2)]
      pings at the end: 4
20 of 20 checks passed (owner mode: superuser)
```

### 5.4 Findings

- **(a), (b), (c) as asked.** An ordinary insert commits and leaves no event. All three forced failures
  (event insert rejected, the whole log rejected, the counting column gone) left the hazard insert
  committed, with `observer_error` 23514 / 42703 logged in the two cases the log would take it and a
  WARNING in the third. Five inserts at one point wrote one `insert_velocity_area_observed` row
  (`log_only`) and sent no ping; 32 wrote one `insert_velocity_observed` row (region level, 20th insert)
  and sent exactly one ping; 10 more wrote and sent nothing.
- **"32 inserts at one point, zero pings" cannot both hold** with the levels in 1a: 20 inserts in 10
  minutes anywhere in the region is the region level, and that is a push by design. The zero-ping
  assertion is therefore made at 5 inserts, the one-ping assertion at 32. If a flood at one point should
  not push until the global level, the region level has to go or rise; that is a 1a decision, not a 045 one.
- **The area level fires first**, on the 3rd insert, long before the global (30) or region (20) level can.
  It is now log-only, so firing on honest use costs a row, not a push. [GAP] Whether three honest same-type
  reports inside 2 km in 15 minutes are common here (one moose walking a road past three cabs does it,
  since positions over 300 m apart are separate rows). The observation week answers it; if the events show
  it is routine, raise the area warn level before 048 turns it into a cap.
- **The composite index was never chosen.** With four types at 25% each the planner preferred the
  `inserted_at` index plus a filter over the ~1,300 rows in the 15-minute window; with the live
  distribution (MOOSE dominant) it will prefer it more. [I] Either plan touches only the window. After the
  observation week, read `pg_stat_user_indexes.idx_scan` for `hazard_audit_log_type_inserted_at_idx`; if
  it is zero, drop it in 046 and save a B-tree write per insert.
- **Cost.** About 6.2 ms per insert with the trigger against 1.3-1.5 ms without, measured with ~900 rows in
  the 10-minute window, about 90x the busiest real window ever recorded (10). [I] With the live volume the
  three counts scan a dozen rows and the overhead is well under a millisecond; the subtransaction the
  exception block opens is a fixed, negligible cost.
- **Locks.** The strongest lock the inserting transaction holds is RowExclusiveLock, an insert's own, on
  `spatial_hazards`, `hazard_audit_log`, `security_events` and pg_net's queue. No advisory lock. Nothing
  here makes one hazard insert wait for another.
- **Idempotent and reversible.** Applied twice, a third time after all the forced failures, rolled back
  twice (027's ping trigger back in its original form, the column and the widened CHECK kept), re-applied,
  each without error and identically under both owners; inserts keep working with the trigger gone.

### 5.5 Final 045 SQL (copy of `docs/design/sql/045_insert_velocity_observe.sql` and of the WARN copy)

```sql
-- Migration 045: insert-velocity OBSERVATION on spatial_hazards (written 2026-10-19, NOT APPLIED; apply only on
-- instruction). 044 is the report-descriptor migration (docs/design/report-descriptor.md).
--
-- WHY. upsert_spatial_hazard_v3 has no count, window or velocity defence: only the merge radius (039) and the
-- created_at clamp (019). Before any cap is enforced (048), a week of real numbers is needed: how often honest use
-- crosses the proposed warning levels. 027 did the same with clear_burst before 024's cap was trusted. Design and
-- local test: warn-map docs/design/reporting-defences-and-write-identity.md, sections 1a and 5.
--
-- WHAT IT DOES. After every insert into spatial_hazards, count recent inserts in hazard_audit_log (global in
-- 10 min, per region in 10 min, same type within 2 km in 15 min) and write at most ONE security_events row per
-- kind per rolling 10 minutes:
--   insert_velocity_observed       global >= 30 or region >= 20. Born pending (push_state null), so 027 pings and
--                                  pushes it like clear_cap_hit.
--   insert_velocity_area_observed  same type within 2 km >= 3 and no global/region crossing. Born 'log_only':
--                                  never pinged, never pushed; read in the Master UI. The area level is the
--                                  stringing signal and is expected to fire on honest use (one moose past three
--                                  cabs); a push for it would be noise.
-- Refuses nothing. Changes no RPC. Takes no lock.
--
-- CANNOT BREAK AN INSERT. The whole body runs inside an exception handler. Any failure is recorded in
-- security_events as kind 'observer_error' (sqlstate and message, never a position) when that insert itself
-- works; if even that fails, it is reduced to RAISE WARNING in the server log. The trigger returns null either
-- way (an AFTER trigger's return value is ignored), so the hazard insert commits regardless.
--
-- NO LOCKS. No advisory lock, no SELECT ... FOR UPDATE, nothing that could make one hazard insert wait on another.
-- The cost of that choice: two inserts that cross a level in the same instant may both write an event. The
-- rolling-window check keeps it to about one per window, not one per insert, and 027's push dedup (one push per
-- kind per hour) means a second row costs nothing. 048 will need an advisory lock for an exact count; this one
-- must not.
--
-- CHEAP. Each count is an index range scan over the last 10-15 minutes of hazard_audit_log (the two B-trees
-- below); plans in the design note. The exception block costs one subtransaction per insert.
--
-- BACKFILL. Rows that exist before this migration get inserted_at = their reported_at (the client's time, the
-- only time they carry), pushed back to at least one hour ago, so none of them can land inside a counting window
-- right after the apply. Rows inserted after this migration get server time via the default.
--
-- PUSH POLICY (027 touched in two places, both backward compatible). push_state gains the value 'log_only' (a row
-- decided at birth: no push), and the ping trigger fires only for pending rows (WHEN new.push_state IS NULL).
-- claim_security_pushes() already ignores rows whose push_state is not null, so a log_only row could not be
-- pushed even if it were pinged. Every row written before this migration has push_state null or a 027 value and
-- behaves exactly as before. The ping function body is not changed.
--
-- NOT TOUCHED: v3 and every other RPC; hazard_audit_log's grants and policies; ping_security_notify()'s body.
-- GRANTS: none for clients. observe_insert_velocity() is revoked from PUBLIC, anon and authenticated (026's
-- default privileges would do it; stated anyway).
--
-- OWNERSHIP. Apply as postgres through the CLI (migrations README item 7). observe_insert_velocity() is SECURITY
-- DEFINER and must be owned by the owner of hazard_audit_log and security_events (postgres), because both tables
-- have RLS enabled with no policy that would let any other role read or write them; a copy of this function
-- owned by anyone else would count zero rows and fail to log, silently.
--
-- PRIVACY RE-READ (migrations README item 1). Adds a server-time column to an existing admin-only audit log: no
-- new fact about a report, no new reader, no change to retention. Stores in security_events only counts, a type,
-- a region code and a row id; never a position. The published policy needs no change.
--
-- IDEMPOTENT. Every statement is IF NOT EXISTS / OR REPLACE / DROP IF EXISTS / a no-op on re-run, so a second run
-- changes nothing. ROLLBACK: 045_insert_velocity_observe_rollback.sql beside this file.
--
-- VERIFY (supabase/scratch/verify_045.sql, to write at apply time; everything inside one transaction that rolls
-- back, so no VERIFY_045 row and no queued push survives):
--   V1  inserted_at and both indexes exist; max(inserted_at) over rows older than the apply <= apply time - 1 h.
--   V2  as anon, one v3 insert of type VERIFY_045 succeeds; its audit row has inserted_at within 5 s of now();
--       no new security_events row.
--   V3  two more at the same point: exactly one insert_velocity_area_observed, push_state = 'log_only',
--       net.http_request_queue unchanged (no ping).
--   V4  17 more at the same point (20 in all, the region level): exactly one insert_velocity_observed, push_state
--       null, exactly one new net.http_request_queue row; nothing else written.
--   V5  anon cannot execute observe_insert_velocity(); the ping trigger's definition carries
--       WHEN (new.push_state IS NULL); the push_state check accepts 'log_only' and rejects anything else.
--   V6  rollback: no VERIFY_045 rows, no new security_events, no new queue rows.
-- The local dry run of all of this is warn-map docs/design/sql/045_local_test.py (PostgreSQL 16.2, run with a
-- superuser owner and with a non-superuser owner); its output is in the design note.
begin;

-- 1. Server time for every audit row. reported_at is the client's created_at and cannot be counted safely.
alter table public.hazard_audit_log
    add column if not exists inserted_at timestamptz;
update public.hazard_audit_log
   set inserted_at = coalesce(least(reported_at, now() - interval '1 hour'), now() - interval '1 hour')
 where inserted_at is null;
alter table public.hazard_audit_log
    alter column inserted_at set default now(),
    alter column inserted_at set not null;
comment on column public.hazard_audit_log.inserted_at is
    'Migration 045: SERVER time the audit row was written (default now(); clients cannot set it). Rows older than 045 carry their reported_at, pushed back to at least an hour before the apply. The velocity counts use this column only.';

create index if not exists hazard_audit_log_inserted_at_idx
    on public.hazard_audit_log (inserted_at);
create index if not exists hazard_audit_log_type_inserted_at_idx
    on public.hazard_audit_log (hazard_type, inserted_at);

-- 2. Push policy: a row may be born decided. Replace 027's inline check (whatever its name) with a named one.
do $$
declare c record;
begin
    for c in select conname from pg_constraint
              where conrelid = 'public.security_events'::regclass and contype = 'c'
                and pg_get_constraintdef(oid) like '%push_state%'
                and conname <> 'security_events_push_state_check_045' loop
        execute format('alter table public.security_events drop constraint %I', c.conname);
    end loop;
end $$;
alter table public.security_events drop constraint if exists security_events_push_state_check_045;
alter table public.security_events add constraint security_events_push_state_check_045
    check (push_state in ('pushing', 'pushed', 'suppressed', 'failed', 'log_only'));
comment on column public.security_events.push_state is
    'NULL = pending; pushing -> pushed | failed; suppressed = another push of this kind went out within the hour (027); log_only = decided at birth, never pinged or pushed, read in the Master UI only (045).';

drop trigger if exists security_events_ping_notify on public.security_events;
create trigger security_events_ping_notify
    after insert on public.security_events
    for each row
    when (new.push_state is null)
    execute function public.ping_security_notify();

-- 3. The observer.
create or replace function public.observe_insert_velocity()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_global int;
    v_region int;
    v_area   int;
    v_push   boolean;
    v_state  text;
    v_msg    text;
begin
    begin
        select count(*) into v_global
          from hazard_audit_log
         where inserted_at > now() - interval '10 minutes';

        select count(*) into v_region
          from hazard_audit_log
         where inserted_at > now() - interval '10 minutes'
           and region_code is not distinct from new.region_code;

        -- Same type within 2 km in 15 minutes: the stringing signal. The index narrows to the type and the
        -- window; the haversine runs only over those rows.
        select count(*) into v_area
          from hazard_audit_log
         where inserted_at > now() - interval '15 minutes'
           and hazard_type is not distinct from new.hazard_type
           and latitude is not null and longitude is not null
           and new.latitude is not null and new.longitude is not null
           and 2 * 6371008.8 * asin(sqrt(least(1.0,
                   power(sin(radians(new.latitude - latitude) / 2), 2)
                 + cos(radians(new.latitude)) * cos(radians(latitude))
                 * power(sin(radians(new.longitude - longitude) / 2), 2)))) <= 2000;

        v_push := v_global >= 30 or v_region >= 20;

        if v_push then
            -- Pushed through 027 (push_state null = pending), once per window.
            if not exists (select 1 from security_events
                            where kind = 'insert_velocity_observed'
                              and occurred_at > now() - interval '10 minutes') then
                insert into security_events (kind, detail)
                values ('insert_velocity_observed', jsonb_build_object(
                    'levels_crossed', (select jsonb_agg(l) from (values ('global'), ('region')) t(l)
                                        where (l = 'global' and v_global >= 30) or (l = 'region' and v_region >= 20)),
                    'global_10min', v_global,
                    'region', new.region_code, 'region_10min', v_region,
                    'hazard_type', new.hazard_type, 'same_type_2km_15min', v_area,
                    'hazard_id', new.id,
                    'window_minutes', 10,
                    'warn_levels', jsonb_build_object('global', 30, 'region', 20, 'area', 3)));
            end if;
        elsif v_area >= 3 then
            -- Log only: born decided, so the ping trigger's WHEN skips it and claim_security_pushes() ignores it.
            if not exists (select 1 from security_events
                            where kind = 'insert_velocity_area_observed'
                              and occurred_at > now() - interval '10 minutes') then
                insert into security_events (kind, detail, push_state)
                values ('insert_velocity_area_observed', jsonb_build_object(
                    'levels_crossed', jsonb_build_array('area'),
                    'global_10min', v_global,
                    'region', new.region_code, 'region_10min', v_region,
                    'hazard_type', new.hazard_type, 'same_type_2km_15min', v_area,
                    'hazard_id', new.id,
                    'window_minutes', 10,
                    'warn_levels', jsonb_build_object('global', 30, 'region', 20, 'area', 3)),
                    'log_only');
            end if;
        end if;
    exception when others then
        -- Observation must never cost a report. Keep the original error, record it if the log will take it,
        -- otherwise warn. Nothing here can raise out of the trigger.
        v_state := sqlstate;
        v_msg   := left(sqlerrm, 200);
        begin
            insert into security_events (kind, detail)
            values ('observer_error', jsonb_build_object(
                'trigger', 'observe_insert_velocity', 'hazard_id', new.id,
                'sqlstate', v_state, 'message', v_msg));
        exception when others then
            raise warning 'observe_insert_velocity failed (% %) and could not log it (% %)',
                v_state, v_msg, sqlstate, sqlerrm;
        end;
    end;
    return null;
end;
$$;
comment on function public.observe_insert_velocity() is
    'Migration 045: AFTER INSERT observer on spatial_hazards. Counts recent inserts in hazard_audit_log and writes at most one insert_velocity_observed (pushed) or insert_velocity_area_observed (log only) row per 10 minutes to security_events. Never raises, never locks, never refuses.';
revoke all on function public.observe_insert_velocity() from public, anon, authenticated;

-- 4. The trigger. Named to fire AFTER trigger_hazard_audit (same event; same-level triggers fire in name order,
-- and 'trigger_h' < 'trigger_i'), so the counts include the row being inserted.
drop trigger if exists trigger_insert_velocity_observe on public.spatial_hazards;
create trigger trigger_insert_velocity_observe
    after insert on public.spatial_hazards
    for each row execute function public.observe_insert_velocity();

commit;
```

### 5.6 Rollback (copy of `docs/design/sql/045_insert_velocity_observe_rollback.sql` and of the WARN copy)

```sql
-- Rollback for 045_insert_velocity_observe.sql (written 2026-10-19). Idempotent: safe to run twice.
-- NOT a migration: apply only to undo 045, with `db query --file`, never through `db push`.
--
-- Removes the observer trigger and function, drops both indexes, and puts 027's ping trigger back in its
-- original form (every insert pings). KEEPS two things, both harmless: the inserted_at column (a timestamp with
-- a default, written by nothing but the default; dropping a column is not reversible, and 048 will want it back)
-- and the widened push_state check (rows with 'log_only' may already exist, and narrowing it would fail on them;
-- nothing writes 'log_only' once the observer is gone). Uncomment the last statement to drop the column anyway.
begin;
drop trigger if exists trigger_insert_velocity_observe on public.spatial_hazards;
drop function if exists public.observe_insert_velocity();
drop index if exists public.hazard_audit_log_type_inserted_at_idx;
drop index if exists public.hazard_audit_log_inserted_at_idx;

drop trigger if exists security_events_ping_notify on public.security_events;
create trigger security_events_ping_notify
    after insert on public.security_events
    for each row execute function public.ping_security_notify();

-- alter table public.hazard_audit_log drop column if exists inserted_at;
commit;
```

### 5.7 Verify script for the live apply (to write when the apply is approved; rolled back)

The outline is in the migration's header (V1-V6): column and indexes, backfill bound, one anon insert of a
`VERIFY_045` type with server `inserted_at` and no event, two more for one log-only area row and no queued
request, seventeen more for one pending region row and exactly one `net.http_request_queue` row, the grant,
trigger and CHECK checks, and a rollback that leaves no `VERIFY_045` rows, no new events and no new queue
rows. The local runs above are the dry run of V2 through V5.
