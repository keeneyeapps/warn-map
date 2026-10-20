# Master UI backend: admin login, admin actions, clear and confirm authority, fleet restrictions, tenancy (DESIGN ONLY)

**Status (2026-10-19): design only, revised the same day with the owner's decisions (section 11).** No migration here is
written as a file or applied. The SQL in section 10 is for review. Belongs with the WARN repo's `docs/design/`
(beside `zones.md`, `channel-layer.md`, `security-migrations-proposed.md`).

Every claim is tagged **[R] recorded** (migration file, source line or live read), **[I] inferred**, or **[GAP]**
(nothing establishes it: decide, don't assume), per the WARN repo's `docs/design/README.md`.

## 0. What already exists (read 2026-10-19)

- [R] **Master UI v1** lives in `WARN/web/admin/`: a phone page that reads hazards with the anon key and deletes through
  `admin_delete_hazards(bigint[], boolean)` (016, revised 018). The record says it is **not deployed**. It signs in with
  supabase-js `signInWithPassword`, `persistSession: true` (`app.js` l.38-39, 628), and loads supabase-js and
  MapLibre from jsDelivr (`index.html` l.17, 91-92).
- [R] **`public.admins`** (016 l.33): `user_id` (references `auth.users`, cascade on delete), `email`, `added_at`,
  `note`, `region_codes text[]` (an unused tenancy hook).
- [R] **`public.is_admin()`** (016 l.97): allow-list membership only, no MFA check. It also gates the admin read
  policies on `hazard_audit_log`, `admin_actions` and `security_events` (018, 024 l.76, 84).
- [R] **`admin_delete_hazards`** (018 l.123): checks `auth.uid()` is not null and present in `admins`, with no MFA
  check. It has a vulnerable-person second confirmation, a blast-radius cap, and a per-row snapshot in `admin_actions`.
- [R] **`admin_actions`** (016 l.62): `batch_id`, `actor_user_id`, `actor_email`, `action`, `hazard_id`,
  `snapshot jsonb not null`, `created_at`. Clients may only read it (030).
- [R] **`spatial_hazards.source`** (036): `check (source = 'driver')`. 042 has since removed anon's direct inserts, so
  widening it no longer lets anyone insert a trusted row.
- [R] **`public_hazards`** (043) is the public read surface and does not serve `source`.
- [R] **Driver Clear** (`clear_spatial_hazard`, 024/027) refuses wildlife and pedestrian rows outright. It has a global
  cap of 20 clears per 10 minutes under an advisory lock.
- [R] **The reporter's undo** (`retract_spatial_hazard`, 015/024) needs the report's random token (only its hash is
  stored).
  - It is issued only for wildlife quick-button reports.
  - Server-side age limit: 60 s (the client offers 30 s).
  - It is refused once anyone has confirmed or merged into the report (gate 3).
  - It is the only "this report is mine" proof that exists, and it carries no identity.
- [R] **Channels** (020): per-subscription device credentials (a random secret, only its hash stored), `open` and
  `approved` subscription modes. Built server-side; no channel exists yet.
- [R] **The baseline `tenants` table** has 0 rows and is in the orphan set awaiting a drop. Do not build on it.
- [R] **The road graph** (`graph/kenai-roads.json`) has no OSM way ids on its edges. **The routing worker** already
  accepts blocked points and penalised edges per route (`js/routing-worker.js` l.438, 494).

## 1. Login

### 1.1 Where the Master UI lives (decided)

**Its own web origin: a separate repo and site**, not a page under keeneyeapps.github.io/warn-map.
- The admin session (held in `sessionStorage` and memory) is then out of reach of every script on the public pages,
  and the public pages can never load an admin module by mistake.
- The page holds **only the project URL and the anon key**. Admin power comes from the user's own session plus the
  checks inside each RPC. The `service_role` key never appears in any page or repo.
- The no-CDN rule carries over. Either vendor a pinned supabase-js build, or call the GoTrue REST endpoints directly
  with `fetch`: password sign-in, MFA challenge and verify, refresh, logout. [I] The direct calls are about 150
  lines with nothing to keep patched.
- Supabase Auth must list the new origin as an allowed redirect URL for the invite and password-reset links. [I]

### 1.2 Holding the session (designed for the Supabase free plan)

**What the free plan gives** [R, Supabase docs, "User sessions"]: Supabase's own session controls are **Pro plan and
above**. Those are time-boxed sessions, the inactivity timeout, and single session per user. On the free plan:
- refresh tokens never expire on their own, but each can be used only once (rotation);
- access tokens (JWTs) are short-lived, typically 5 minutes to 1 hour, with 1 hour the default; the docs advise not
  going below 5 minutes;
- [GAP] whether the JWT expiry can be changed on a free project is not stated. Check it in the Dashboard (Project
  Settings, JWT). The design below works at the default 1 hour.

**So WARN enforces admin session limits itself, in the admin gate, from the session's own record.**
- [R] Supabase writes every session to `auth.sessions`, and every JWT carries its `session_id`.
- `require_admin()` (section 10) looks the session up and refuses unless all of these hold:
  - the session is **under 12 hours old**: a time-box. An admin signs in afresh each working day;
  - it was **refreshed within the last 75 minutes**: an inactivity limit of one JWT lifetime plus 15 minutes, so an
    open but unused page stops working within about an hour and a quarter;
  - it is **the account's newest session**: single session. Signing in on a second device makes the first one's admin
    actions fail.
- These apply to admin RPCs only. They don't affect drivers (who have no session) or anything public.
- [I] The column names (`created_at`, `refreshed_at` or `updated_at`) are checked against the live catalog before the
  migration is written.

**In the page:**
- **Where the tokens live:** the access token in memory, the refresh token in `sessionStorage`. They die with the tab,
  and closing the tab signs out.
- **Idle sign-out:** after 30 minutes without input, the page calls Auth's logout with **global scope**. That revokes
  every refresh token of the account server-side, so a copied refresh token is useless afterwards. It also runs on the
  page's own "Sign out".
- **Server-side recheck:** every admin RPC rechecks the session in the database, so a time-boxed, idle, superseded or
  revoked session fails there, not only in the page.

### 1.3 MFA: mandatory now (decided)

- **What it means:** every admin RPC requires the JWT's `aal` claim to be `aal2`, issued only after a verified TOTP
  challenge. A password alone signs in but cannot act.
- **Existing functions:** this applies to the existing `admin_delete_hazards` from 049 onward. It also applies to
  admin **reads**, because `is_admin()` gets the same check. A password-only session therefore cannot read
  `hazard_audit_log`, which holds report positions.
- **No recovery codes:** [I] Supabase Auth MFA does not issue recovery codes; its guidance is to enroll a second
  factor. So **enroll two TOTP factors per account** (for example a phone authenticator and a second device).
- **Before applying 049:** both accounts must be enrolled. Otherwise deletes fail closed for the account without MFA.

### 1.4 Two admin accounts (decided)

Both belong to the owner, on two different email addresses. Both have MFA and both are on the allow-list.
[I] This note does not record the addresses or their providers: the repo is public, and the allow-list lives in the
database.

**Adding the second account while sign-ups stay disabled.** Disabled sign-ups only block self-service sign-up.
Creating a user from the admin side still works.
1. Supabase Dashboard, Authentication, Users, **Add user**. Use **Create new user** (email plus a password, with
   auto-confirm) or **Send invitation** (an invite link to the address). [I] Equivalently, the Auth admin API from a
   local shell holding the service-role key in an environment variable, never in a file.
2. Read the new user's id (the Dashboard, or `select id, email from auth.users` by CLI).
3. Add it to the allow-list by CLI, never by an RPC:
   `insert into public.admins (user_id, email, note) values ('<uuid>', '<address>', 'second admin account, owner, <date>');`
4. Sign in to the Master UI with it, enroll two TOTP factors, and check that an admin action succeeds (aal2) and
   fails with the password alone.
5. Record the date and that both accounts are the owner's in WARN's `CLAUDE.md` (not the addresses).

**What it protects against:** a lost TOTP device on one account no longer locks the owner out of the Master UI. It does
**not** protect against the owner being unavailable. Both accounts are one person.

### 1.5 How every admin RPC checks the caller

Every admin RPC is SECURITY DEFINER, owned by `postgres`, with `set search_path = public, pg_temp`, and starts with
`v_actor := public.require_admin();`. That raises `42501` unless `auth.uid()` is on `public.admins` **and** the session
is `aal2`.
- Identity comes only from `auth.uid()`, which PostgREST sets from the verified JWT.
- There is never a `p_is_admin`, `p_user_id` or `p_role` parameter.
- No client role can write `admins`. Adding or removing an admin is always a CLI step.

### 1.6 Protections and compromise

- **Audit:** every admin action writes an `admin_actions` row in the same transaction. It records who (uid and email),
  when, the RPC, the `aal`, the target, on whose behalf (section 3.5), the reason (required), and the before and after.
- **Notification:** every admin action and sign-in pushes through 027's content-free push (a `security_events` kind
  `admin_action`, at most once per kind per hour).
- **Guardrail:** an admin burst limit of about 50 writes per 10 minutes per actor, logged as `admin_burst` (section 9).

**If an account is compromised:**
1. Remove it from `admins` by CLI. Every admin RPC then fails at once.
2. Sign out all its sessions, and reset its password and both TOTP factors.
3. Read `admin_actions` for the window and undo from the before snapshots. Admin clears are soft and restorable;
   deletes keep 016's snapshot.
4. The other account carries on.

## 2. Admin actions

Common to all:
- SECURITY DEFINER, `revoke all … from public, anon`, `grant execute … to authenticated`.
- First line `require_admin()`.
- One audit row per target, and a required non-empty reason.

### 2.1 Planted hazards: tests only (decided)

```sql
admin_plant_test_hazard(
  p_hazard_type text,                        -- must exist in hazard_type_lifetimes
  p_latitude double precision, p_longitude double precision,
  p_heading double precision default null,
  p_display_text text default null,
  p_expires_at timestamptz,                  -- required, at most now() + 24 h
  p_reason text
) returns bigint
```

- **Provenance:** `source = 'planted_test'` and `planted_by` (the admin's uid). 036's check widens to
  `('driver', 'planted_test')` and nothing else.
- **Hidden from the public view:** `public_hazards` gains `where source = 'driver'`. Planted rows are therefore
  visible only to the Master UI and verify scripts, never to a driver, the Map, Alerts, Watch or Meter.
  [I] Hazards are never region-filtered, so a test pin anywhere would otherwise be a test pin everywhere.
- **Out of driver mechanics:**
  - v3's merge skips non-driver rows.
  - Confirm, driver Clear and retract refuse them.
  - The caps never count them (section 7).
  - The purge removes them at their own `expires_at`.
- **Who may clear them:** the admin who planted them, and the Master UI (section 3).

### 2.2 Fleet notices: through the channel, not the hazard database (decided)

The hazard database holds **real driver reports only** (plus hidden test rows). A notice to a fleet is a
`channel_items` row (020), shown on the device as **"Notice from WARN"**, and never phrased as "reported".

```sql
admin_post_channel_notice(p_channel_id uuid, p_title text, p_body text, p_point double precision[2] default null,
                          p_expires_at timestamptz, p_reason text) returns bigint
admin_end_channel_notice(p_item_id bigint, p_reason text) returns boolean
```

- [R] `get_channel_items()` already delivers items to subscribed devices with one fetch. [GAP] The app half is not
  built. The "Notice from WARN" label and its spoken wording belong there, and need the privacy policy re-read when
  the app half ships (a new kind of content reaching drivers).

### 2.3 Clears and restores: see section 3

`admin_clear_hazards` and `admin_restore_hazards` are the Master UI's override door. `admin_delete_hazards` (016/018)
stays as the hard delete for junk, now behind MFA (049).

### 2.4 Region records

```sql
admin_upsert_region(p_id text, p_name text, p_region_code text, p_bbox double precision[4], p_reason text) returns text
```

A small `regions` table mirroring each `regions/<id>/region.json`. The repo's region pack stays the authority for the
PWA. The table exists for org service areas (section 6) and the Master UI's region picker. It never filters hazards.

### 2.5 The type lists and quick-button config (decided: skeleton in the app, specifics on the server)

**The split** (decided):
- **The app holds the skeleton:** every built-in type code, its category, and its **safety flags** (`is_wildlife`,
  `is_moving`, whether a driver may clear it), plus default labels in the app's translations. The app works fully with
  no server list at all.
- **The server holds the regional specifics:** which types are offered in a region, regional labels and spoken
  labels (a local name for a species), regional extra species (mapped onto a skeleton category), sort order, and the
  quick buttons.

**The note's earlier shape matches, with one correction.** The server catalog no longer holds the authoritative
safety flags for built-in codes, because the app does.

| Table | Purpose | Read by |
|---|---|---|
| `hazard_type_regional` | per region and code: enabled, label, spoken label, sort; for a **regional extra** code, also its skeleton category | `get_client_config(p_region, p_known_version)` (anon); `gemini-proxy` (service role) |
| `quick_buttons` | region, slot, hazard type, label, enabled, `org_id` (null = everyone) | `get_client_config()` |
| `config_version` | one version counter, bumped by every edit | `get_client_config()` |

- **Writers:** `admin_upsert_regional_type(…, p_reason)` and `admin_set_quick_buttons(p_region, p_buttons jsonb,
  p_reason)`, audited. Org admins may later edit their own org's quick buttons.
- **Safety rules:**
  - The server may **tighten** but never loosen a built-in code's safety flags. A region may say "treat X as moving",
    never "treat Moose as clearable".
  - A regional extra code takes its category's flags.
  - A code the app does not know, and the server has not described, counts as **moving and wildlife** (refuse the
    clear) and is logged, per the migrations README's rule 3.
  - Server-side (Clear, the moving rule), the same flags come from a server copy of the skeleton, shipped with the
    migration that introduces it and versioned with the app. They are checked by a coverage test against the app's
    `HazardTaxonomy`.

## 3. Who may confirm and clear: the authority model (decided rules, worked out here)

### 3.1 Actors

| Actor | How they are identified | Through |
|---|---|---|
| **Driver** (any app user) | nobody: anonymous by design | driver Confirm, driver Clear, retract (token) |
| **The device that just filed a report** | the report's retract token (proves "this device filed it seconds ago", no identity) | retract (section 4.1) |
| **Customer admin** (incl. a fleet admin) | a signed-in org user with aal2, on `org_admins`; named in every audit row | the org's own UI (its own origin, like the Master UI) |
| **Authorised fleet member** | a channel subscription approved as `role = 'authority'` by its org admin | the app, with the subscription credential |
| **WARN admin (Master UI)** | signed in with aal2, on `admins` | the Master UI |

**Public hazards have no owner.** A driver report is anonymous: nothing records who filed it, which fleet it came from,
or who confirmed it, and no rule below grants anyone a right *because they reported or confirmed* a hazard. The
only owned rows are admin-planted test rows (`planted_by`), and those are hidden from the public view. The retract
token is not ownership either: it proves only that this device created this row in the last seconds, and it dies with
the window.

### 3.2 Organisation types and category tiers (skeleton; completed with the first municipality)

- **Org type:** each org has exactly one `type`.
- **Category tiers:** `category_authority(category, org_type, may_confirm, may_clear)` lists, per hazard category,
  which org types may authority-confirm and which may clear under a hold.
- **Defaults are deny:** a pair not in the table may do neither. A type-level exception (for example a utility and
  downed power lines) is a row in `type_authority(hazard_type, org_type, may_confirm, may_clear)`, which overrides its
  category's row for that one type.

**Organisation types (skeleton):**

| Type | Who | Authority role |
|---|---|---|
| `municipal_roads` | city or borough public works, road maintenance | roads |
| `state_roads` | Alaska DOT&PF maintenance district | roads (state routes) |
| `police` | police department, state troopers | incidents, scenes, people in the road |
| `fire_ems` | fire, EMS | incidents, scenes |
| `parks_wildlife` | wildlife and parks agencies | animals |
| `utility` | electric and other utilities | their own infrastructure only (type-level rows) |
| `fleet` | cab and transport companies | none: fleet admins clear ordinary hazards, and moving ones inside the window (4.2), but never confirm or hold |
| `enterprise` | other business customers | none by default |

**Category table (skeleton defaults, to be completed with a municipality):**

| Category | May confirm (places a hold) | May clear under a hold | Notes |
|---|---|---|---|
| Road Damage | municipal_roads, state_roads | municipal_roads, state_roads | police may not clear potholes |
| Obstructions | municipal_roads, state_roads, police | municipal_roads, state_roads, police | debris, ice, drifts, stalled vehicles |
| Visibility | municipal_roads, state_roads | municipal_roads, state_roads | fog, smoke, whiteout |
| Closures | police, fire_ems, municipal_roads, state_roads | the same | a scene, a closure for works; `utility` per type (downed power line) |
| Animals (moving) | parks_wildlife, police | none early: the moving rule (3.3) | a hold is corroboration only |
| Pedestrian (moving) | police | none early: the moving rule | |
| Delay | police, municipal_roads, state_roads | the same | |

[GAP] To complete with the first municipality: whether `state_roads` exists in the area, whether an incident or crime
scene becomes a separate type (police only, so parks may not clear one), and each department's exact list.

### 3.3 The model

| Hazard kind | Recognised by | Who may confirm | Who may clear early | Everyone else |
|---|---|---|---|---|
| **Ordinary** (not moving, no authority hold) | `source = 'driver'`, catalog `is_moving = false`, no hold | any driver (046 spacing and bound); an org whose type has `may_confirm` for the category, which places an authority hold (row 4) | the filing device by retract (seconds); any driver (driver Clear); any customer admin (org clear); WARN admin. All but WARN admin are subject to the caps (section 7) | not applicable |
| **Moving** (animals incl. moose, pedestrians) | catalog `is_moving = true` (missing code = moving) | any driver (Confirm runs the type's own countdown rules, 037); an authority confirm is recorded as corroboration only | the filing device by retract (seconds); **any fleet admin **any fleet admin within the moving-clear window** (30 min after the report, section 4.2); WARN admin. Otherwise nobody: it expires on its own countdown | driver Clear is refused, as today |
| **Admin-planted** (tests; hidden) | `source = 'planted_test'` | nobody | the admin who planted it, and the Master UI | never visible to them |
| **Under an authority hold** | `held_by_org_type` set | an org of an authorised type for the category (refreshes the hold); drivers may still Confirm (corroboration, freshness), which never changes the hold | any org whose type has `may_clear` for the held category; WARN admin | **recorded as a dispute** (3.4), not cleared |
| **Any hazard** | not applicable | not applicable | **WARN admin, always** (3.5) | not applicable |

Precedence when a clear arrives:
1. A WARN admin (Master UI) is always allowed and audited.
2. If the row is planted, only its planter (and the Master UI) may clear it.
3. If it is moving, only the filing device's retract (seconds) or a fleet admin inside the moving-clear window may
   clear it.
4. If it is under an authority hold, an org of a type authorised to clear that category may clear it; anyone else
   creates a dispute.
5. Otherwise it is ordinary: driver Clear or customer-admin clear, under the caps.

- **An authority hold is a permission rule, not ownership.** It restricts clearing of that hazard to the **types** of
  organisation authorised for its category. Any police service may clear an accident scene a police service
  confirmed, and no org gains a right over the hazard itself. It records which type placed the hold and the audit log
  records which org, for accountability. It never makes the hazard that org's.
- **A hold and the moving rule together:** [GAP] for a moving hazard under a hold, the moving rule wins in this
  table, so an authority confirm on a moose does not let that authority clear it early. The owner's rules don't settle which
  wins; the strict reading is shown, and it is in section 11.
- **Where the hold lives:** in new `spatial_hazards` columns: `held_by_org_type text`, `held_at`, `held_category` (the
  category at hold time, so a later re-type cannot move the hold into another authority's tier), `hold_note`, and
  `held_display_label`. The org that placed it, and the signed-in person, are in the audit log, not on the row.

**The confirming authority is named, like a posted notice (decided).** A hazard under an authority hold shows
"Confirmed by <department>". Driver reports never carry a name.
- **What is stored:**
  - `orgs.public_display_label text`, the department's public name (for example "Kenai Public Works"), up to 40
    characters of plain text. It is set by a WARN admin in the Master UI at onboarding, or changed at the org's
    request. It is **not** free text an org types per hazard, so a compromised org account cannot put arbitrary words
    in front of every driver.
  - `spatial_hazards.held_display_label text`, a copy of that label taken when the hold is placed. It is a snapshot,
    so a later rename does not rewrite what drivers were shown. It is set **only** by `org_confirm_hazard`, and
    cleared when the hold ends or the hazard is cleared.
  - Every other row, which means every driver report that no authority has confirmed, has it null.
- **What is served:** `public_hazards` gains one column, `confirmed_by`. It is null for every driver report and the
  label for held hazards. The app shows "Confirmed by …" on the card and may speak it. [I] Shipped builds ignore the
  unknown key.
- **Privacy consequence:**
  - A department's name becomes public data on the hazards it confirmed. That is intended: it is the department's
    public act, like a sign it posts.
  - No person is named. The individual who confirmed it is only in the admin-readable audit log.
  - Nothing about reporters changes: the label is never set by a driver report, and nothing links a report to a
    reporter.
  - [I] The published privacy policy's "no identifier" promise is about the people who report, so it still holds, but
    the policy should say that hazards confirmed by an authority carry that authority's public name. This joins the
    policy re-read in section 11.

### 3.4 A hazard under an authority hold and an ordinary clear: a dispute, not silence

When a driver, or a customer admin of a type not authorised for the category, tries to clear a hazard under a hold:
- **Nothing is cleared, but nothing is lost.** The attempt is recorded in `hazard_disputes(hazard_id, window_start,
  driver_count, org_ids uuid[], last_at)`. This is **one row per hazard per 10-minute window**, with a counter for
  anonymous drivers and the ids of disputing orgs. There are no device identities, so a flood of taps is one row with
  a count, not a table of rows.
- **The authority sees it:** disputes appear in the UI of every org authorised to clear that category in the area
  ("3 drivers say this is gone, last 14:05"). A push goes out at a threshold (for example 3 disputes in a window). An
  authorised org then re-confirms or clears.
- **The caller is told:**
  - A new `clear_hazard_v2` returns `'disputed'`, and the app says "Sent to the responsible authority for review".
  - Old builds call `clear_spatial_hazard`, which keeps its boolean. They get `false` and their existing "could not
    clear" toast [R, Android `:3878-3890`]. The dispute is still recorded server-side.
- **Disputes are not clears:** they never count against the driver Clear caps (047) and never move any row's
  freshness. They have their own small budget per hazard (one row per window) so they cannot be used to flood
  anything.

### 3.5 The Master UI override: any hazard, any time, including where another organisation's rule applies (decided)

- **Scope:** the Master UI can clear **any** hazard: one under an authority hold, a planted one, a moving one, and one
  where another organisation's rule or hold applies. Problems in someone else's system can then be fixed without
  going to the database directly.
- **Not a new privilege:** [R] `postgres` (the CLI) can already do this, and 016 already lets an admin hard-delete any
  row, wildlife included. This makes that power usable from a page, with a record, instead of by hand.
- **Every override is audited** in `admin_actions`:
  - who (uid, email, aal);
  - which hazard (id, and the full before row);
  - **on whose behalf**: `on_behalf_of_org`, the org that placed the hold or whose request led to the clear, or null
    for WARN's own;
  - which rule was overridden (`override_kind`: `held`, `moving`, `planted`, `other_org`, `none`);
  - and the reason (required).
- **The affected organisation sees it:** the clear is stamped on the row as `cleared_by = 'warn_admin'`. An org that
  placed a hold, or asked for the clear, sees **"Cleared by WARN admin"** in its own action history, with the time and
  the reason. [I] The reason is written for that org to read, so it is shown as entered; no WARN-internal note field is
  shown to orgs.
- **Restorable:** `admin_restore_hazards` undoes an admin clear while the row has not expired, also audited, and
  shown to the org.

### 3.6 Clear functions

```sql
-- The one door for every driver clear from new builds. Precedence as in 3.3; old builds keep clear_spatial_hazard.
-- Takes no identity of any kind: drivers stay anonymous.
clear_hazard_v2(p_hazard_id bigint)
  returns text   -- 'cleared' | 'disputed' | 'refused_moving' | 'refused_planted' | 'cap' | 'not_found'
org_clear_hazards(p_ids bigint[], p_reason text)                 -- customer admin (aal2, org_admins); moving hazards
  returns table(hazard_id bigint, outcome text)                  -- only inside the moving-clear window (4.2)
org_confirm_hazard(p_hazard_id bigint, p_note text)              -- customer admin or authority credential; places a hold
  returns text   -- 'held' | 'refreshed' | 'not_authorised' | 'not_found'
admin_clear_hazards(p_ids bigint[], p_reason text, p_confirm_vulnerable boolean default false)
  returns table(hazard_id bigint, outcome text, override_kind text)
admin_restore_hazards(p_ids bigint[], p_reason text) returns table(hazard_id bigint, outcome text)
```

All of them write `cleared_at` (soft) and `cleared_by` (`driver`, `org`, `warn_admin`). Which org cleared is recorded
in the audit log, not on the public row. A retract by the filing device is not a clear: it deletes the row, as today.

### 3.7 Every clear records who, when and why (decided)

- **Who:** `cleared_by` on the row (`driver`, `org`, `warn_admin`). For org and WARN admin clears, the signed-in
  account and its org are in the audit row. Drivers stay anonymous.
- **When:** `cleared_at`.
- **Why:** `clear_reason` on the row and in the audit row, at minimum one of:
  - `mistaken`: **not there, or never was**;
  - `resolved`: **was there, and has moved on or been dealt with**.
- **How each actor gives it:**
  - **Fleet admins and the Master UI must choose** one of the two (required; no default), plus their free-text reason.
  - **A driver's Clear** sends `resolved`. A driver clearing a hazard they drove past is saying "gone now", and the
    voice words that trigger it are "gone", "resolved", "clear" [R, `HazardOverlayService.kt` l.1703]. A future build
    may add "not there" as a separate word or button for `mistaken`.
  - **Old builds** send nothing: stored as null, meaning unknown.
  - **A filing device's Undo** is not a clear: the row is withdrawn (4.5), which already means "mistaken".

**How the moose longitudinal study uses it.** Today the sightings archive (032) records a wildlife row **only at
natural expiry**, and "a cleared row is never a sighting" [R, 032 l.12-18, 181-182]. Early clearing of moving hazards
(4.2, 3.5) adds a third ending, and the reason code tells the two kinds apart:
- `resolved` (moved on): **a real sighting that ended early.** It gets a sightings row with a new provenance,
  `cleared_resolved`. Its duration runs from the first report to the clear, rounded to 15 min; the position, the
  coarsened time and the other fields follow 032 exactly.
- `mistaken`: **no sightings row**, the same as an Undo or an admin delete today.
- Unknown (old builds): a sightings row flagged `excluded_reason = 'clear_reason_unknown'`. It is kept, because
  filtering later is possible and recovering is not, but excluded by default.
- **Analysis:** the risk surface counts `provenance in ('expiry', 'cleared_resolved')` with `excluded_reason is null`.
  Duration analyses treat `cleared_resolved` separately, because a human ended it, not the countdown.
- This changes 032's "a cleared row is never a sighting" rule. It lands in its own migration, with 032's privacy
  coarsening unchanged (no hazard id, no device, time to year, month and local hour).

### 3.8 Restore (decided: one click; automatic only by rule, and loop-safe)

**One-click restore from the audit record (decided).** Every clear's audit row holds the full before snapshot. The
Master UI shows it with **Restore**:
- **For a soft clear** (every clear in this design): `admin_restore_hazards(p_ids, p_reason)` clears `cleared_at`,
  `cleared_by` and `clear_reason`, as long as the hazard is still within its own `expires_at`. Past that, it refuses
  with "expired", because restoring a hazard whose countdown has ended would resurrect it.
- **For a hard delete** (016's `admin_delete_hazards`, junk only): `admin_recreate_from_snapshot(p_action_id,
  p_reason)` re-inserts the snapshot as a new row with its original type, position, `first_reported_at` and expiry,
  again only if not expired.
- Both are audited, and an org whose clear was undone sees "Restored by WARN admin" in its history (3.5).

**Automatic restore: the options.**

| Option | What happens | Fits the "alert, never block" decision? | Loop risk |
|---|---|---|---|
| **1. Alert plus a one-click bulk restore** | the per-org clear alert (7) carries **"Restore all N clears by this account in this window"**: one click, one reason, every row audited | yes | none: a person decides |
| 2. Automatic restore on a burst rule | when one account crosses the alert threshold, its clears in the window are restored automatically | yes, if the account is not stopped | real: the account clears again, the rule restores again |
| 3. Quarantine | an account over the threshold has its further clears held as "pending" until reviewed | **no**: it blocks | none |

**Recommendation: option 1 now.** It is a person's decision with a one-click path, and it has no loop.

If option 2 is ever adopted, these rules keep it loop-safe:
- each hazard is auto-restored **at most once**, ever; a second clear of it stands until a person acts;
- auto-restore never reverses a **WARN admin** clear, and never reverses another restore;
- it never restores past `expires_at`;
- it acts only on clears made **inside** the burst window that triggered it, once per trigger;
- every auto-restore is audited as `restored_by = 'auto_rule'` with the rule and threshold, and raises its own alert.

## 4. Fleet identity and clearing a wrong report (decided 2026-10-19)

### 4.0 Fleet identity is a recipient function, never a broadcast function

- **What a fleet id does:** it decides **who receives** a private message or notice (channel items, section 2.2;
  later perhaps a more modifiable fleet PWA). It is never attached to a public hazard.
- **What follows:** a hazard filed by a fleet driver is the same as any other driver's report. There is one overlay,
  not two, and nothing in the public data says which fleet, if any, a report came from.
- **Fleet-private hazards and notices** go through the channel (or that later PWA), **never into the public hazard
  data**.

### 4.1 Seconds: the filing device's retract

A driver who files a report by mistake takes it back within seconds with the existing per-report token
(`retract_spatial_hazard`, 015/024). Today that covers only Moose quick-button taps (4.3); 4.5 extends it to every
report, with correction at the press position.

### 4.2 Minutes: a fleet admin clears a moving hazard inside the window

- **Who and when:** after the retract window, **any fleet admin** may clear a **moving** hazard (`is_moving`) within
  **30 minutes of its report** (decided), through `org_clear_hazards`.
- **The window is a server-side setting:** `app_settings` (key `moving_clear_window_minutes`, value 30), read by
  `org_clear_hazards` on every call. It changes without an app release: `admin_set_setting(p_key, p_value, p_reason)`,
  Master UI only, audited, bounded to 0 to 120 minutes (0 turns fleet early-clearing off). Apps never read it; the
  server enforces it.
- **How they find it:** by location and type, typically because a driver phoned in a mistake.
- **Hazards are not tagged by fleet**, so any fleet's admin could clear any moving hazard inside that window, including
  one another fleet's driver filed. **That is accepted** because the window is short, the fleet admin is a named
  signed-in account (aal2) in the audit row of every clear, and those clears are **subject to the caps** (section 7).
  After the window, a moving hazard runs its own countdown; only the Master UI can end it early.
- **Not ownership:** this is a time-limited permission granted to a role (fleet admin), not a right over "their"
  reports. No fleet has reports.

### 4.3 Undo today (findings, read 2026-10-19; no code changed)

| Hazard type | Undo in the Android app | Undo in the PWA | Window | Would the RPC accept a retract? |
|---|---|---|---|---|
| **Moose, from the quick-access button** | **yes**. The token is issued for quick-button reports in the Animals category (`HazardOverlayService.kt` l.1006, 2075); the Undo button is drawn on the card (`HazardStackAdapter.kt` l.311-312); the Moose button is the only Animals quick button (`QuickAccessButtonsView.kt` l.152) | no | 30 s in the app (`RETRACTION_WINDOW_MS`, l.369); 60 s on the server | **yes**: token present; not cleared; `created_at` unchanged (nobody confirmed or merged); under 60 s |
| Moose re-labelled by the species row (Bear, Caribou and so on) after a quick-button tap | **yes**: the Undo follows the grant, not the type (`HazardOverlayService.kt` l.3641-3665) | no | as above | yes, under the same gates |
| Rough road, from the quick-access button (`DAMAGE_MY_LANE`) | **no**: not Animals, so no token (l.908, 928, 1006) | no | none | **no**: no stored hash |
| DELAY | **no Undo button**. A token is issued (l.2075) but only for the reporter's transit-time update (`armDelayTransit`, l.3391) | no | none in the app | **yes**: recorded in the code as a known consequence (l.2073-2074). The token would let the server retract a delay within 60 s if nobody confirmed or merged |
| Every other type, by any path (voice, finger menu, other buttons) | **no**: those reports send no token (l.1003-1005) | no | none | **no**: no stored hash |
| Any report filed from the offline queue | **no**: an offline report never has a token (l.2509-2522) | no | none | **no** |
| Any report that merged into an existing hazard | **no**: nothing of the driver's own to undo (l.3643-3647) | no | none | **no**: the merge never writes a hash |

- **PWA:** none of this repo's pages files a report at all. `js/report.js` is an empty placeholder for the future WARN
  Drive page, so the PWA has no undo for any type.
- **The RPC:** `retract_spatial_hazard` itself is type-agnostic. It checks only the token hash, that the row is not
  cleared, that `created_at` is unchanged, and the 60-second age. Then it **deletes the row**.

**Every type now gets an undo (decided): see 4.5.**

### 4.4 Why an org id on reports was not chosen

Tagging fleet reports with their org would let a fleet clear its own drivers' reports without the open window. But it
turns fleet identity into a broadcast function:
- it attaches a company to public hazards;
- it lets WARN learn which company's vehicle was where;
- it creates two kinds of hazard where there should be one overlay;
- and it would require rewriting the privacy policy's "no identifier of any kind".

The open, short, audited and capped window in 4.2 gets the practical benefit without any of that.

### 4.5 Undo and correction on every report path (decided 2026-10-19)

**Decision:** every hazard type gets an undo, in the Android app and the future Drive PWA. A wrong report is corrected
at the position where the driver first pressed, not where the car is when they correct it.

#### 4.5.1 Two phases and one rule for all report paths

**The rule.** The position is captured at the **first press**: the mic (PTT) button, the menu button, or a quick
button. For Rough Road with the bump meter running, the bump meter's impact point replaces it. The position is held
**on the device only**, and is used for the report and for any correction. The server receives exactly what it
receives today: one position per filed report, and that is the press position. A retract sends no position.

A report moves between two phases:

| Phase | What it is | Timers | Ends when |
|---|---|---|---|
| **Held position** (nothing on the server) | after the first press and before a category is chosen; again after Undo | idle 20 s, then 2 beeps and a pulse, then 10 s; any action restarts it. Ceiling: **5 min from the first press** (4.5.5) | a category is chosen (it moves to "filed"); the menu is dismissed back to the three buttons; the idle timer runs out; or the ceiling is reached. In the last three cases the position is discarded and nothing is filed |
| **Filed, Undo available** | the report is on the server | **Undo shown for 90 s** after filing (card, then chip, 4.5.3). Server window 120 s from **receipt** (4.5.2) | 90 s pass; the driver taps X (hides the card or chip and ends Undo; the report stays); or the driver taps Undo (back to "held position") |

- **After Undo** (decided): the withdrawn report is gone from the server, and the driver is back in the held-position
  phase with the held position and the longer timers. The idle timer starts fresh; the ceiling still counts from the
  first press.

What happens on each path:

| Path | First press | If the result is wrong |
|---|---|---|
| **Fast button** (Moose, Rough Road) | the button | **Undo retracts.** The report is gone and the held position is discarded. A fast button has no menu to return to. |
| **Menu** | the menu button | **Undo** retracts the report and reopens the menu at the last level the driver was at: the held-position phase. Choosing again files the corrected report at the held position. Leaving the menu files nothing. |
| **Voice** (misheard) | the mic button | The receipt card shows **Undo** where it shows Clear (then "CONFIRM?") today. Undo retracts the wrong report at once (a misheard type must stop warning others) and opens the menu: the held-position phase. The driver chooses, or presses PTT and says it again. |
| **Voice, no hazard detected** (too loud, not understood, nothing matched) | the mic button | **The press is not dropped.** "No hazard detected" is shown, and the menu opens: the held-position phase. The driver chooses, or presses PTT to try again, as many times as needed. **Nothing has been filed, so there is no server window.** |

#### 4.5.2 The server window (decided: 120 s, with `withdrawn_at`)

A menu correction "a mile down the road" takes about 90 s at 40 mph (1,609 m). The retract RPC refuses anything over 60
s. The voice path has the same problem: a misheard report is already filed when its card appears.

| Option | Server change | Merge rules (300 m; 20 m road damage; 2 h) | Caps | Privacy |
|---|---|---|---|---|
| **A. A longer retract window**, then a normal new report at the held position (**chosen**) | the window in `retract_spatial_hazard`, and marking the withdrawn report in the audit log | **unchanged.** The corrected report goes through v3 like any report: it merges with a same-type hazard within its radius and 2 h, or inserts | the withdrawn report stops counting (4.5.4) | none new. The token is per report, random and hash-only; a retract sends no position; the corrected report sends the press position, as any report does |
| B. Generalise `amend_spatial_hazard` to change any type in place | a rewrite: today it is species-only and has no time window (035) | **bypassed:** the amended row never re-runs the merge, so it can duplicate an existing hazard; a category change also changes clearability and lifetime mid-life | counts once | none new |
| C. Retract within today's 60 s plus a new report | none | unchanged | counts twice today | none new |

**Decided (owner, 2026-10-19):**
- the window is **120 s**;
- `hazard_audit_log.withdrawn_at` marks retracted reports;
- a correction counts once and a plain undo counts zero;
- the corrected report keeps the original press time as its `created_at`.

**The window is measured from when the server received the report, not from `created_at`.** This is needed to make
the decisions fit together:
- The corrected report keeps the press time as `created_at`, and a held position may now be up to 5 minutes old.
- A window measured from `created_at` would therefore already be over for a correction filed 3 minutes after the press,
  and that report could never be undone.
- Measured from receipt, every filed report, original or corrected, gets its full 120 s.
- The receipt time already exists: `hazard_audit_log.inserted_at`, server time, written for every insert since 045.

**Gate 3 is unchanged.** If anyone confirms or merges into the report within the window, Undo is refused. The app says
"Others have confirmed it" and offers Clear; moving types can't be cleared by a driver, which is correct because
someone else saw it too.

**Chaining.** Each correction is a new report with its own 120 s, so a driver could in principle undo and re-file
repeatedly.
- **The device bounds it:** the held position's 5-minute ceiling stops it.
- **The server bounds what matters:** every live report is counted while it is live. Withdrawn ones count zero, but at
  any moment the caps still see every hazard that is actually on the map. A chain keeps at most one hazard live.

SQL (unapplied; written into a numbered file only at build time, from the live definitions):

```sql
-- Correction window: retract up to 120 s after the server received the report; withdrawn reports stop counting.
begin;

alter table public.hazard_audit_log add column if not exists withdrawn_at timestamptz;
comment on column public.hazard_audit_log.withdrawn_at is
  'Set when the reporter retracted the row within its window (a mistaken or corrected report). Withdrawn rows are excluded from the insert-velocity counts (045) and the insert cap (048).';

-- retract_spatial_hazard: 024's body with exactly two changes.
--   gate 4, measured from server receipt (045's inserted_at), not from the client-chosen created_at:
--     if not exists (select 1 from public.hazard_audit_log
--                     where original_id = p_hazard_id and inserted_at >= now() - interval '120 seconds') then
--       return false;
--     end if;
--   before the delete:
--     update public.hazard_audit_log set withdrawn_at = now()
--      where original_id = p_hazard_id and withdrawn_at is null;
-- Same signature and grants (anon may execute). Gates 1-3 (token, not cleared, created_at unchanged) stay as they are.

-- observe_insert_velocity (045): its three counts gain  and withdrawn_at is null.

commit;
```

**Client window versus server window.** Undo is shown for 90 s after filing; the server allows 120 s from receipt. That
is the same slack-over-client pattern as today's 30 s and 60 s, absorbing a slow network and clock skew.

#### 4.5.3 The Undo card, the chip, and X (decided)

- **0 to about 20 s after filing:** the reporter's receipt card shows **Undo** in Clear's slot, in the same place and
  footprint as today.
- **About 20 to 90 s:** the card shrinks to a small **Undo chip**, which stays until the 90 s are up. The chip is a
  real target sized like the card buttons (76 dp tall, as the card's Undo is today), not a small icon.
- **X on the card and on the chip** hides the card or chip only.
  - The report stays on the map, and **Undo for that report ends**: the token is dropped from memory and the held
    position is discarded.
  - A driver who wants the report gone presses **Undo**, not X.
  - [I] X must look different from Undo (an outline glyph in the corner, not a filled button), so that "hide" and
    "withdraw" are never confused.
- **The change at 90 s** is visible in advance: a thin bar along the chip's bottom edge drains over the remaining time,
  and in the last 5 s the label counts down "UNDO 5" to "1". Then the chip disappears.
  - If the driver later approaches that hazard like anyone else, its card shows **Clear**, the normal two-tap
    "CLEAR" then "CONFIRM?" [R, `HazardStackAdapter.kt` l.258-282].
  - Any button that changes from Undo to Clear in place ignores taps for 1 s after the change, so a late tap aimed at
    Undo does nothing.
- **Clear on a report this device filed, while the server window is still open** (90 to 120 s, unconfirmed, token
  still held, chip not dismissed with X): Clear calls **retract**, not Clear.
  - **So it is excluded from 047 by construction.** It is a withdrawal of the driver's own unconfirmed report, not a
    clear.
  - After 120 s, or once anyone has confirmed it, it is an ordinary Clear and counts. Moving types are then refused, as
    for any driver.

**Stacking rule (decided).** Newly encountered hazards stack **below**, and never displace a freshly placed card or chip
that still has an Undo button. [R] Today the reporter's own wildlife card is kept on screen as a receipt for as long as
Undo shows (`HazardStackAdapter.kt` l.304-308); this extends that to every type and to the chip, and fixes the order: the
Undo card or chip stays on top and new alerts go beneath it.

#### 4.5.4 Caps: a correction counts once, a plain undo counts zero (decided)

| Cap | Plain undo | Undo plus corrected report |
|---|---|---|
| **045** observation | the audit row is marked withdrawn and excluded: counts 0 | the withdrawn original counts 0, the corrected report counts 1: **net 1** |
| **046** Confirm spacing and bound | not involved | not involved (gate 3 means the original had no Confirm) |
| **047** Clear caps | a retract is not a clear: not counted | not counted |
| **048** insert cap | the withdrawn row is excluded from the count, so it frees its slot | the corrected report is checked against counts that already exclude the original, so it is not refused when the original was accepted; it counts once |

No linking parameter is needed: excluding withdrawn audit rows from every count makes undo plus re-report net out to one.

#### 4.5.5 How long a held position lives (decided)

**The idle timer** applies only in the held-position phase (nothing on the server):
- The menu has been open for **20 s with no action**. Any action restarts it: opening a submenu, choosing back,
  pressing PTT, or speech being heard. So the countdown only runs while nothing is happening.
- **At 20 s:** two DTMF-style dual-tone beeps and a gentle visual pulse (below). The menu stays open **another 10 s**.
- **At 30 s idle:** with still no action, the menu closes, the screen returns to the three original buttons, and the
  held position is discarded. Nothing is filed.
- **Why 10 s rather than 20:** the beeps last under a second, and a driver who hears them acts within a few seconds or
  not at all. At 40 mph the whole idle period is then 30 s, or **536 m (0.33 mi)**: 358 m for the first 20 s and 179 m
  for the grace. With 20 s of grace it would be 715 m.

**The ceiling: 5 minutes from the first press** (decided). It replaces both the earlier 120 s proposal and the approved
60 s first-touch expiry (CLAUDE.md, Core design principles, 2026-10-04 [R, `HazardOverlayService.kt` l.497-518]).
- **Why it is needed:** any action restarts the idle timer, so a driver who keeps interacting could hold a position
  indefinitely.
- **When it is reached:** the app says "Report position expired". That is today's toast and spoken message for the 60 s
  limit, reused. The menu closes, the screen returns to the three buttons, the held position is discarded, and nothing
  is filed.
- **It never withdraws a report that is already filed.** A filed report's own Undo runs on its 90 s and 120 s windows.
- **A 5-minute-old position is still the right place.** It is where the first press happened, which is where the driver
  saw the hazard. The ceiling is not about accuracy. It bounds two things:
  - how long a position is kept on the device;
  - how long a press can still plausibly stand for what the driver saw.
- At 40 mph the cab is 8,047 m (5 mi) further on by then. That does not move the report; it shows why reporting it
  after that long is unlikely to be what the driver meant.

**The two-beep warning: DTMF digit "D", 941 Hz plus 1633 Hz.**
- **What it is:** two bursts of 150 ms each, 100 ms apart.
- **Why this pair:** it is the widest low-group and high-group separation in the DTMF table. The 941 Hz tone carries
  for a driver with high-frequency hearing loss; the 1633 Hz tone sits above most road and engine rumble. "D" is not
  on a phone keypad, and it is unlike any spoken alert.
- **No clash with existing sounds:** [R] the app's only other non-speech sound, `playChimeSound()` on PTT, loads
  `alert_chime` from `res/raw`. That folder does not exist, so it is silent today (`HazardOverlayService.kt`
  l.1245-1259, 1537). [GAP] If a chime file is added later, it must not use 941 or 1633 Hz.
- **How it plays:**
  - through the spoken alerts' audio path (`USAGE_ASSISTANCE_NAVIGATION_GUIDANCE`, as `HazardAudioSequencer` uses
    [R, l.69-78]), so the driver's WARN voice volume governs it;
  - with transient audio focus and ducking, so music dips for under a second;
  - skipped when focus is refused (for example during a call), leaving the visual warning only.
- **When WARN's spoken alerts are muted** (decided): the beeps still play. Mute silences hazard announcements; the
  beeps are feedback on an interaction the driver started.
- **When a spoken alert is playing when the beeps are due** (decided): the beeps wait until the speech finishes, and
  the idle timer is extended by the length of the speech, so a driver never loses held-position time to an
  announcement. [I] Implemented as "the idle timer does not run while WARN is speaking". `HazardAudioSequencer`
  already tracks each utterance's start and end (`UtteranceProgressListener` [R, `HazardAudioSequencer.kt`]); the same
  pause applies to the 10 s grace if speech starts during it. The 5-minute ceiling is not extended: it bounds how long
  a position is kept, not the driver's time.
- **How it is generated:** as a short PCM buffer with those attributes. [I] `ToneGenerator.TONE_DTMF_D` takes a legacy
  stream type and would not follow that volume.

**The visual warning, at the same moment.**
- **The pulse:** the menu's background opacity eases from 100% to 80% and back over 1.2 s, twice. There is no flash and
  no colour burst.
- **At night:** the app themes by the system day and night mode (`Theme_MaterialComponents_DayNight`
  [R, `FingerMenuWindow.kt` l.35]) and has no separate brightness setting, so in night mode the pulse amplitude halves
  (100% to 90%).
- **Reduced motion:** under Android's "Remove animations" (`ValueAnimator.areAnimatorsEnabled()` false), a static 2 dp
  caution-yellow (#FDD835) outline replaces the pulse for the grace period. [R] There is no reduced-motion handling
  today, so this is new.
- **The grace period's remaining time** shows as small text ("Closing in 10") that changes once a second.

#### 4.5.6 Driving safety: the correction flow

- **Two taps at most:** Undo (on the card or chip, where Clear is), then the right item in the menu, which reopens
  where the driver was. The alternative is Undo and then speak, with PTT in the menu.
- **Voice only:** while the card or chip is up, saying "undo" or "wrong" does the same as tapping Undo. [I] The command
  parser already matches "delete", "remove" and "clear" (`HazardOverlayService.kt` l.1703); "undo" and "wrong" are
  added there.
- **No dialogs, no typing, no confirmation for Undo,** and the press position is kept for the driver.
- **The limits:**
  - Undo is shown for 90 s after a report is filed: a card for about 20 s, then the chip.
  - In the held-position phase the menu closes after 30 s with no action.
  - Nothing outlives 5 minutes from the first press.
  - After that the report stands as filed, or stays withdrawn, and the driver uses Clear like anyone else.

#### 4.5.7 What the code already does (for a realistic estimate)

| Piece | Today [R] | Work |
|---|---|---|
| Capture at the first press (mic, menu, quick Moose, quick Rough Road), with provenance | `captureReportOriginLocation`, `HazardOverlayService.kt` l.619-637; called from `FloatingPttView.kt` l.729 and `QuickAccessButtonsView.kt` l.151 | keep the capture after a report is filed, through Undo and back into the held-position phase; today it is cleared when consumed |
| The bump meter's retained impact position for Rough Road | `reportRoughRoadQuick`, l.906-930, with `JoltWindow` | none: it already replaces the held position with the jolt's |
| The 60 s first-touch expiry, with a toast and spoken message | l.497-518 | replace it with the idle timer plus the 5-minute ceiling; reuse the message |
| The Undo slot (in Clear's place) and its 30 s grant | `HazardStackAdapter.kt` l.311-312; `armRetractionGrant` l.3641-3665 | widen to every type; 90 s; the chip, X, countdown and drain bar |
| The reporter's card kept on screen as a receipt | `HazardStackAdapter.kt` l.304-308, wildlife only | extend to every type and the chip; the stacking order (new alerts below) |
| Retract token generation | l.2075, quick-button Animals and DELAY only | generate it for every report |
| Menu re-entry at the last level | none | new: remember the menu path; reopen there |
| Voice receipt card's Clear and "CONFIRM?" | `HazardStackAdapter.kt` l.258-282 | Undo in that slot on the reporter's own card |
| "No hazard detected" | toast only, press dropped (l.1617-1618) | keep the held position and open the menu |
| Idle timer, DTMF tones, pulse, reduced-motion outline | none | new |
| Server: 120 s window from receipt, `withdrawn_at`, 045 exclusion | none | one small migration and its verify |

[I] Rough size: Android about 5 to 7 days, including the menu re-entry, the two phases' timers, the card-to-chip states
and the stacking order, plus a field session. Server about half a day including the verify. The Drive PWA has no
reporting yet, so its undo is built with its report flow, using the same rules.

### 4.6 Privacy-policy wording for the undo code (DRAFT, for the owner's review before any publication)

Written for drivers reading the published policy; to replace or extend the paragraph that today promises "no
per-install token … not even an anonymous one".

> **Undoing a report.** When you file a hazard report, your phone makes a new random code for that one report. It is
> sent with the report, and it lets you, and only you, withdraw or correct that report for a short time afterwards.
> Your phone keeps the code only in its memory and forgets it when the undo time ends, at most a few minutes later.
> The server never stores the code itself. It stores a scrambled form (a one-way hash) that can check the code but
> cannot be turned back into it, and that is deleted with the report.
>
> The code identifies no person, no phone and no app installation. Every report gets a different code, so codes cannot
> be used to tell which reports came from the same phone. A report carries no identifier of you or your phone; the
> undo code is the only code attached to it, and only for this purpose.

[I] Every statement checked against this design:
- **new and random per report:** 256 random bits [R, `HazardOverlayService.kt` l.2071-2077];
- **memory only:** the plaintext "never touches disk" [R, same];
- **hash only:** sha256, stored on the hazard row [R, 015; v3];
- **deleted with the report:** the hash lives only on the hazard row and goes when the row is purged; the audit log
  never copies it;
- **the undo time:** at most 120 s on the server and 90 s in the app (4.5).

The authority-name paragraph (3.3) is drafted separately, when the first hold ships.

## 5. Fleet restrictions: point blocks and whole-road rules

**How closures are avoided today** [R, this repo]:
- In the PWA, a CLOSURES hazard on a route gets an "avoid" toggle (`js/routing.js` l.1011-1051).
- An avoided closure becomes a **blocked point**: its position and a 40 m radius (l.1130).
- The routing worker blocks every graph edge that passes within that radius of the point (`blockEdgesForPoints`,
  `js/routing-worker.js` l.438-472) and routes around it.
- No way id is involved: the point and radius find the edges on the current graph, whatever build it is.

**So a permanent block can use the same mechanism as a temporary closure.** It is a closure at a location that never
expires.
- **Shape:** `fleet.road_blocks(id, org_id, point, radius_m, kind, valid_from, valid_to, note, created_by,
  created_at)`, with `kind in ('closed', 'never')`. `valid_to` is required for `closed` and null for `never`.
- **Radius:** small, about 15 to 25 m, placed mid-segment away from junctions. A radius catches **every** edge within
  it, so a point near a crossing would block the crossing road too.
- **Preview:** the Master UI (and later an org's own UI) previews exactly which edges a point blocks, using the
  worker's existing `blockedPointsAffected`, before saving.
- **No graph rebuild needed.** It works on today's graph, and it survives future rebuilds because it stores a place,
  not an edge index.
- **Write:** WARN admins, and later an org's admins for their own org. **Read:** only that fleet's devices and pages,
  with their channel credentials (`get_fleet_road_blocks(p_credentials text[])`). The public pages never call it.
- **In the fleet UI:** blocks are added to every route request as blocked points, with no toggle. They are policy, not
  a suggestion.

**What the way-id rebuild is still needed for: whole-road rules only.** Rules about a **road as a whole**, where
"is the trip's origin or destination on this road?" must be answered:
- **Escape Route** (`endpoint_only`): a road allowed only if the trip starts or ends on it;
- **No-through** (`no_through`): usable only to reach or leave a point on it;
- **A ban on an entire road** too long to cover with a few points.

Those need the road's identity along its whole length. A point radius cannot express "the start snap lies on this
road", and road names are not unique (two "Main Street"s). That needs `tools/build_graph.py` to carry the OSM way id on
every edge, and a `fleet.road_rules(id, org_id, way_ids bigint[], kind, …)` table, applied in the worker as a new
`wayRules` argument (impassable unless the start or end snap is on one of those ways).

**Where they live, for both:** the `fleet` schema in the same project. No foreign key to hazards, nothing in
`public_hazards`, and no hazard function reads it.

## 6. Tenancy

- **`orgs`** (not the orphan `tenants`): `id`, `name`, `type` (section 3.2), `region_ids` (its service area), and
  `created_at`.
- **`org_admins`:** `user_id` and `org_id`, with aal2 required. Kept apart from `admins`, so an org admin can never
  pass `require_admin()`.
- **Per-org reads:** an org sees its own action history (holds it placed, clears it made, and "Cleared by WARN admin"
  where the Master UI overrode one of its actions), disputes on hazards its type is authorised for, its own road
  overrides, notices and quick buttons. That is all through RPCs filtered on the caller's org, never table grants. It
  never sees which fleet filed a hazard, because nothing records that.
- **Customer admins may clear public hazards** (decided), under the model in section 3, always named in the audit log
  and subject to the caps:
  - ordinary ones anywhere;
  - moving ones only inside the moving-clear window (fleet admins, 4.2);
  - ones under an authority hold only if their type is authorised to clear that category;
  - planted ones never.
- **Never tenant-scoped:** the public hazards themselves. Every driver sees every hazard, and no hazard carries a
  fleet or org. Tenancy scopes administration and private messaging, never visibility (the Architecture rule in
  `tenancy-and-categories.md`). `public_hazards`, driver Confirm, driver Clear, the caps and the audit log stay global.

## 7. Interaction with the reporting caps (045 to 048)

WARN admin (Master UI) actions are not blocked by the caps and do not count against them. **Customer-admin clears,
fleet admins' moving-hazard clears included, are subject to the caps** (decided): the admin is identified in the audit
log, and the cap still bounds what one account can do.

| Cap | What changes |
|---|---|
| **045** insert-velocity observation (applied) | `hazard_audit_log` gains `source` and `withdrawn_at`. The observer counts `source = 'driver'` and not withdrawn only, and returns early for planted rows. A test plant can then never trip the area level, and a corrected report counts once (4.5.4). |
| **046** Confirm spacing and bound | Applies to **driver** Confirm, including on hazards under an authority hold (drivers may still corroborate). `org_confirm_hazard` is outside the spacing and the bound: it is a different act (an investigation), audited, with its own per-org budget. A driver Confirm on a held hazard refreshes freshness as usual but never alters the hold. |
| **047** Clear per-area cap, minimum age, backstop | Counts `cleared_by in ('driver', 'org')`: driver clears **and** customer-admin clears share the per-area cap and the global backstop. WARN admin clears are not counted, so an admin clearing 20 false reports does not lock drivers out. A filing device's retract is a delete, not a clear, and is not counted (it is bounded by its 60 s and the token). **Disputes are not clears** and never count; they are one row per hazard per window (3.4). The minimum age applies to driver clears; an org clear of a moving hazard is bounded by the moving-clear window instead. **Per-org clear alert (decided):** alert only, never a block. When one org's clears exceed **10 within 30 minutes**, the Master UI shows an alert (`security_events` kind `org_clear_burst`, once per org per window, pushed through 027). [I] Real volume: 14 clears ever, at most 2 in any 10 minutes, all from drivers. So 10 by one org in 30 minutes is far above honest use and well below what the shared cap (5 per 5 km per 10 min; global backstop) would allow it to do across several areas. The shared caps remain the backstop. |
| **048** insert cap in v3 | (1) **The cap applies only to v3's insert branch, after the merge lookup.** A driver report that merges into an existing hazard (a held one included) adds no row and is never refused: refusing corroboration would be wrong. (2) Planted rows never go through v3, so they are never refused. With the 045 change they are never counted either. Withdrawn reports (retracted or corrected) are excluded from the count, so a correction is never refused when its original was accepted (4.5.4). (3) **The authority hold is not a database lock.** 048 takes a Postgres advisory lock (`warn.insert_cap`) to count exactly. The hold is row state (`held_by_org_type`). Org and admin functions never take `warn.insert_cap`, so a burst of driver reports cannot block them. Org clears take `warn.clear_cap` like driver clears, because they share 047's count. |

**Order:** `source` on the audit log and `cleared_by` on Clear must land **before or with** 047 and 048, so those are
written against the right counts.

## 8. Risks

1. **Both admin accounts are one person.** Two accounts cover a lost device, not the owner's absence. The continuity
   runbook (8.1) is the answer and is required.
2. **Authority holds can be abused by a compromised org account** (holding real hazards so drivers cannot clear them).
   Mitigation: disputes surface to WARN as well as to authorised orgs, at a higher threshold; the Master UI override;
   org admins need aal2.
3. **Any fleet admin can clear any moving hazard inside the window** (4.2), not only ones their own drivers filed.
   Accepted: short window, named in the audit log, capped. Watch the audit log for a fleet admin clearing far from its
   service area.
4. **Planted rows leaking into driver mechanics** if one consumer misses the `source` check. Every consumer change
   gets a planted-row case in its verify script.
5. **Editable type lists changing safety behaviour** (2.5). Fail closed on a missed lookup.
6. **Road overrides depend on a graph format change** (section 5).

### 8.1 Continuity runbook (required; to be written as its own step)

A continuity runbook is **required** before the Master UI or any customer depends on WARN. It is an instruction manual
for a successor or a trusted helper: how to reach the admin accounts, apply or roll back a migration with the CLI,
revoke a compromised account (1.6), restore cleared hazards (3.8), and keep the live site and the database running.

- **Secrets are kept separately, never in this public repo, and never in the runbook's text.** They are in sealed
  written form: the account passwords, the **second MFA authenticator for each admin account** (or its setup secret),
  and the CLI access.
- **Two copies, in different places.**
- The runbook says where the sealed copies are, not what is in them.
- Writing it is its own step, scheduled before the first customer onboarding. It is not written in this note.

## 9. Order of work

1. **049: the admin gate, MFA everywhere admins act, and the audit shape** (section 10). Before applying: enroll two
   TOTP factors on both accounts, and add the second account to `admins` (1.4).
2. Auth settings: shorter JWT expiry, the Master UI's origin as an allowed redirect, and the admin-action push kind.
3. **050: provenance.** Widen 036 to `('driver', 'planted_test')`, `public_hazards` serves `source = 'driver'` only,
   v3 merge skips non-driver rows, `hazard_audit_log.source`, the 045 observer counts drivers only, and `cleared_by` on
   Clear. All before 047 and 048.
4. **051:** `admin_plant_test_hazard`, `admin_clear_hazards`, `admin_restore_hazards`,
   `admin_recreate_from_snapshot`, `admin_burst`, `clear_reason` on the row and in the audit, and `app_settings` with
   `admin_set_setting`.
5. The Master UI on its own origin.
6. **052:** orgs (with `public_display_label`), `org_admins`, `category_authority`, the hold columns (with
   `held_display_label`) and `public_hazards.confirmed_by`, `hazard_disputes`, `org_confirm_hazard`, `org_clear_hazards`
   (with the 30-minute moving-clear window read from `app_settings`), the `org_clear_burst` alert with its bulk-restore
   action, `clear_hazard_v2`, and the tier skeleton (`category_authority`, `type_authority`), completed with the first
   municipality.
7. **053:** regional types, quick buttons and `get_client_config`, plus the server copy of the app's safety skeleton,
   with Clear and the moving rule failing closed.
7a. **The sightings change:** `cleared_resolved` sightings and the `clear_reason_unknown` flag (3.7), in its own
   migration after 051.
8. **Correction window** (4.5.2, decided): `retract_spatial_hazard` to 120 s from server receipt,
   `hazard_audit_log.withdrawn_at`, and the 045 exclusion. Then the Android build for undo on every type (4.5.7).
9. Channel notices (`admin_post_channel_notice`) together with the app's channel half.
10. **Point blocks first:** the `fleet` schema, `fleet.road_blocks`, `get_fleet_road_blocks`, and the fleet UI passing
    them as blocked points. No graph rebuild.
11. **Whole-road rules later:** way ids in `tools/build_graph.py`, then `fleet.road_rules` and the worker's `wayRules`
    (Escape Route, no-through, whole-road bans).
12. **The continuity runbook** (8.1), its own step, before the first customer onboarding.

## 10. 049 (PROPOSED, NOT WRITTEN AS A FILE, NOT APPLIED)

What changed from the first draft: MFA is now mandatory on the existing delete function and on admin reads (the owner's
decision 3), and the audit row gains "on whose behalf" and "which rule was overridden" (decision 6).

```sql
-- 049_admin_gate_mfa.sql: one admin gate (allow-list AND MFA), applied now to the one admin action that exists and to
-- admin reads, and a general audit shape. No new admin power.
-- BEFORE APPLYING: both admin accounts are on public.admins and each has two verified TOTP factors. Otherwise that
-- account's deletes and admin reads fail closed (which is the intended failure, but not a surprise to have on a bad day).
begin;

-- 1. The gate every admin RPC calls.
create or replace function public.require_admin(p_need_mfa boolean default true)
returns uuid
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  v_uid     uuid := auth.uid();
  v_started timestamptz;
  v_active  timestamptz;
begin
  if v_uid is null or not exists (select 1 from admins where user_id = v_uid) then
    raise exception using errcode = '42501', message = 'not_admin';
  end if;
  if p_need_mfa and coalesce(auth.jwt() ->> 'aal', '') <> 'aal2' then
    raise exception using errcode = '42501', message = 'mfa_required';
  end if;
  -- Free plan: Supabase's own time-box, inactivity timeout and single-session settings are Pro-only (1.2), so the
  -- admin gate enforces them from the session record. Column names are checked against the live catalog first.
  select s.created_at, coalesce(s.refreshed_at, s.updated_at, s.created_at)
    into v_started, v_active
    from auth.sessions s
   where s.id = (auth.jwt() ->> 'session_id')::uuid and s.user_id = v_uid;
  if not found then
    raise exception using errcode = '42501', message = 'session_unknown';
  end if;
  if v_started < now() - interval '12 hours' then
    raise exception using errcode = '42501', message = 'session_timebox';      -- sign in again each working day
  end if;
  if v_active < now() - interval '75 minutes' then
    raise exception using errcode = '42501', message = 'session_idle';          -- one JWT lifetime (1 h) + 15 min
  end if;
  if exists (select 1 from auth.sessions s2 where s2.user_id = v_uid and s2.created_at > v_started) then
    raise exception using errcode = '42501', message = 'session_superseded';    -- single session per admin
  end if;
  return v_uid;
end;
$$;
-- Called only from inside other SECURITY DEFINER functions (they run as postgres); no client calls it directly.
revoke all on function public.require_admin(boolean) from public, anon, authenticated;

-- 2. Admin reads need MFA too. is_admin() gates the read policies on hazard_audit_log, admin_actions and
--    security_events (018, 024). Same signature and grants (016); only the body adds the aal2 condition.
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (select 1 from public.admins a where a.user_id = auth.uid())
     and coalesce(auth.jwt() ->> 'aal', '') = 'aal2';
$$;

-- 3. The audit shape for every admin action from here on. Additive and nullable: 016/018's rows are untouched.
alter table public.admin_actions
  add column if not exists target_table     text,   -- 'spatial_hazards', 'hazard_type_regional', 'fleet.road_blocks', ...
  add column if not exists target_id        text,
  add column if not exists reason           text,   -- required (non-empty) by every admin RPC from 049 on
  add column if not exists aal              text,   -- the session's assurance level at the time
  add column if not exists on_behalf_of_org uuid,   -- the org that placed a hold or asked for the clear; null = WARN's own
  add column if not exists override_kind    text    -- 'held' | 'moving' | 'planted' | 'other_org' | 'none'
    check (override_kind is null or override_kind in ('held', 'moving', 'planted', 'other_org', 'none'));

-- 4. admin_delete_hazards: 018's body (to be checked equal to the live function before the file is written) with ONLY
--    the identity block replaced, and the audit insert recording the aal. Signature, return shape, the vulnerable-
--    person gate, the blast-radius cap and the snapshot are unchanged.
--
--    018, removed:
--      if v_uid is null then
--        raise exception 'admin_delete_hazards: not authenticated';
--      end if;
--      select a.email into v_email from public.admins a where a.user_id = v_uid;
--      if not found then
--        raise exception 'admin_delete_hazards: caller is not an admin';
--      end if;
--
--    049, in its place:
--      v_uid := public.require_admin();          -- allow-list AND aal2, or 42501
--      select a.email into v_email from public.admins a where a.user_id = v_uid;
--
--    and in its insert into admin_actions: add  aal = auth.jwt() ->> 'aal', target_table = 'spatial_hazards',
--    target_id = <hazard id>, override_kind = 'none'.
--    (The full CREATE OR REPLACE is written into the file from the live definition, not retyped from this note.)

commit;
```

**Verify outline (rolled back):**
- V1: `require_admin()` raises `not_admin` for anon and for a signed-in non-admin.
- V2: it raises `mfa_required` for an admin at `aal1` and returns the uid at `aal2`. Simulate with
  `set local request.jwt.claims` and `role authenticated`.
- V3: no client can execute it directly.
- V4: `is_admin()` is false at `aal1` and true at `aal2`. An `aal1` admin reads 0 rows of `hazard_audit_log`; an
  `aal2` admin reads them.
- V5: `admin_delete_hazards` at `aal1` raises `mfa_required` and deletes nothing. At `aal2` it deletes a planted
  `VERIFY_049` row, and its audit row carries `aal = 'aal2'`.
- V6: the vulnerable-person gate and the blast-radius cap behave exactly as 018's verify recorded.
- V7: the new columns exist and are nullable, and `override_kind` rejects an unknown value.
- V8: with a simulated session, the gate raises `session_timebox` for a session started 13 h ago, `session_idle` for
  one last refreshed 2 h ago, and `session_superseded` when a newer session exists for the same user. It passes for a
  fresh, recently refreshed, newest session.

**What 049 deliberately does not contain:**
- the authority hold columns, orgs and dispute tables (052);
- the widening of `source` (050).

049 stays a pure gate and audit change that makes the existing power safer, with nothing new to misuse.

## 11. Decisions

### Decided (owner, 2026-10-19)

1. **Fleet notices** go through the channel as "Notice from WARN". The hazard database holds real driver reports only.
   Planted rows exist for tests only, hidden from the public view. (2.1, 2.2)
2. **The Master UI has its own web origin**: a separate repo and site. (1.1)
3. **MFA is mandatory now**, including on the existing `admin_delete_hazards` (and, as worked out here, on admin
   reads through `is_admin()`). (1.3, 10)
4. **Two admin accounts**, both the owner's, both with MFA, both on the allow-list. They are added through the Dashboard or
   the Auth admin API, since sign-ups stay disabled. (1.4)
5. **Customer admins may clear public hazards**, under the authority model, always audited and subject to the caps:
   - ordinary hazards by any driver or customer admin;
   - moving hazards expire on their own countdown, with early clear by the filing device's retract (seconds), any
     fleet admin within 30 minutes of the report (a server setting), or the Master UI;
   - planted test hazards by their planter and the Master UI;
   - hazards under an authority hold by orgs of a type authorised for the category, plus the Master UI;
   - org types and category tiers decide who may confirm or clear what. (3, 6)
6. **The Master UI can clear any hazard at any time**, including held, planted and moving ones and ones where
   another organisation's rule applies. This is the existing database power made usable, not a new privilege. Each
   clear is audited with who, which hazard, on whose behalf, which rule and why, and the affected org sees "Cleared by
   WARN admin". (3.5)
7. **Fleet identity is a recipient function, never a broadcast function.** A fleet id decides who receives a private
   message or notice and is never attached to a public hazard: one overlay, not two. Fleet-private hazards and
   notices go through the channel (or later a fleet PWA), never into the public hazard data. (4.0)
8. **Clearing a wrong report:** the filing device retracts within seconds with the existing token. After that, any
   fleet admin may clear a moving hazard within 30 minutes of its report (a server setting), found by location and type. Hazards are
   not tagged by fleet, so any fleet's admin could do it; accepted because the window is short and every clear is
   audited and capped. The org-id tag was not chosen (4.4). (4.1, 4.2)

9. **Undo on every type** (Android and the future Drive PWA), with correction at the first-press position:
   - fast buttons retract;
   - menu and voice reports retract and return to the held-position phase;
   - "no hazard detected" keeps the press and opens the menu;
   - the position stays on the device. (4.5)
10. **The server correction window:** 120 s, with `withdrawn_at`. A correction counts once, a plain undo counts zero,
    and the corrected report keeps the original press time. The window is measured from server receipt, so a
    correction filed late in the held phase still gets its full 120 s. (4.5.2, 4.5.4)
11. **Undo visible for 90 s after filing:** a card, then after about 20 s a small Undo chip. X on either hides it and
    ends Undo; the report stays. (4.5.3)
12. **Stacking:** new hazards stack below and never displace a card or chip that still has Undo. (4.5.3)
13. **The held-position phase:** idle 20 s, then two DTMF beeps (D: 941 plus 1633 Hz) and a pulse, then 10 s; any
    action restarts it. A **5-minute ceiling** from the first press replaces the 120 s proposal and the 60 s first-touch
    expiry. When it is reached, "Report position expired", back to three buttons, position discarded, nothing filed.
    (4.5.5)
14. **After Undo** the withdrawn report is gone from the server, and the driver is back in the held-position phase with
    the longer timers. (4.5.1)

15. **Warning beeps play even when WARN's voice is muted.** If a spoken alert is playing when they are due, they wait
    for it to finish, and the idle timer is extended by the length of the speech. (4.5.5)
16. **The per-organisation clear budget is an alert only:** more than 10 clears by one org in 30 minutes raises an
    alert in the Master UI. It never blocks; the shared caps remain the backstop. (7)
17. **The category tier matrix is deferred** to first customer onboarding. (3.2)
18. **Authority holds show the authority's public name** ("Confirmed by <department>"), from a label WARN sets per org,
    copied onto the held row. Driver reports never carry a name. (3.3)

19. **The moving-hazard early-clear window for fleet admins is 30 minutes,** held as a server setting
    (`app_settings`) that changes without an app release. (4.2)
20. **Every clear records who, when and a reason code:** `mistaken` (not there) or `resolved` (moved on). The moose
    study counts `resolved` clears as real sightings that ended early, and excludes `mistaken` ones. (3.7)
21. **Restore** is one click from the audit record. Automatic restore is by rule only: option 1 (alert plus one-click
    bulk restore) now, with loop-safety rules recorded for any later automatic option. (3.8)
22. **Tier matrix:** a skeleton of org types and a category table with deny-by-default rows, completed with the first
    municipality. (3.2)
23. **Type lists:** the app holds the skeleton (codes, categories, safety flags); the server holds the regional
    specifics and may only tighten safety flags. (2.5)
24. **Road overrides:** permanent and temporary blocks at a location use the existing point-and-radius mechanism, with
    no graph rebuild. The way-id rebuild is needed only for whole-road rules (Escape Route, no-through, whole-road
    bans). (5)
25. **Admin sessions on the free plan:** the admin gate enforces a 12-hour time-box, a 75-minute inactivity limit and
    single session per account from `auth.sessions`; the page signs out globally after 30 minutes idle. (1.2)
26. **A continuity runbook is required,** written as its own step, with secrets sealed separately (two copies,
    different places, including each account's second MFA authenticator). (8.1)

### Still open

**Decide now:**
1. **The privacy-policy wording for the undo code:** review the draft in 4.6 before the Android undo build ships.

**Check now, takes the default if unchecked:**
2. **JWT expiry on the free plan:** whether the Dashboard lets it be shortened. The default is 1 hour with the 75-minute
   inactivity limit; if it can be shortened to 15 minutes, the inactivity limit becomes 30 minutes. (1.2)

**At first customer onboarding** (scheduled):
3. **Complete the tier matrix** with the municipality. (3.2)
4. **A hold and the moving rule together:** may an org of the confirming type clear a moving hazard early? Default no.
   (3.3)
5. **The authority-name paragraph** of the privacy policy, drafted when the first hold ships. (3.3)
6. **The continuity runbook,** written before onboarding. (8.1)

**Defer to the build:**
7. **Whole-road rules:** approve the way-id graph rebuild when Escape Route or no-through is first needed. (5)
