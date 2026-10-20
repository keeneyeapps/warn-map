# Master UI: hosting, sign-in and build plan (PLAN ONLY, 2026-10-20)

**Status: plan reviewed; the hosting and session decisions below are made (owner, 2026-10-20). Slice 1 is being
built in the private repo `gmessy30/warn-admin`; nothing is deployed and no Cloudflare project exists yet.**
Companion to `master-ui-backend.md` (the design) and WARN's `supabase/migrations/049_admin_gate.sql` (applied
2026-10-20, 27/27 verified). Tags as in that note: **[R]** recorded, **[I]** inferred, **[GAP]** decide.

## Decisions (owner, 2026-10-20)

1. **Hosting: Cloudflare Pages** (option B) from the **private** repository `gmessy30/warn-admin`
   (`https://github.com/gmessy30/warn-admin.git`), its own `*.pages.dev` origin. Not yet connected or deployed.
2. **No custom domain for now.** Adding one later changes only Supabase's Redirect URL list.
3. **Refresh token in `sessionStorage`**, access token in memory, nothing in `localStorage` or cookies (2.2).
4. **The server idle limit stays at 75 minutes** (049 as applied), with the page's **30-minute idle timer** on top
   (2.3). No follow-up migration for it.
5. **The anon key may be committed** in the repo's `config.js`: it is public by design (the same value is in
   `warn-map/js/config.js`); the gate, not the key, is the security. The service-role key is never in any file.

## 0. Where things stand

- [R] 049 is live: every admin RPC needs allow-list membership AND an `aal2` session AND a live session row under the
  12 h / 75 min / newest-session rules; `is_admin()` needs `aal2`, so the admin read policies (`hazard_audit_log`,
  `admin_actions`, `security_events`, cleared rows of `spatial_hazards`) return nothing to a password-only session.
- [R] Both owner accounts are on `public.admins` with two verified TOTP factors each. Two stray `aal2` sessions from
  the enrollment script remain on the first account (harmless; see 2.6).
- [R] Master UI v1 (`WARN/web/admin/`, 722-line `app.js`, 96-line `index.html`, 150-line `style.css`) is not
  deployed. It signs in with supabase-js `signInWithPassword` (`persistSession: true`, so tokens in localStorage),
  has no MFA step, loads supabase-js and MapLibre from jsDelivr, and reads `config.js` (gitignored). Under 049 it can
  only show uncleared public rows.
- [R] Decided (backend note 1.1): the Master UI gets **its own web origin**, so the public PWAs' scripts and storage
  can never reach the admin session, and the page holds only the project URL and the anon key.

## 1. Hosting and origin

**The constraint.** GitHub Pages project sites of one account all live under `https://<account>.github.io/<repo>/`:
one origin, shared `localStorage`, `sessionStorage`, service-worker scope reach and cookies. `keeneyeapps.github.io`
already serves the public PWAs (`/warn-map/`, and `/warn/` for the policy and the planned v1 path), so **a new repo
under keeneyeapps does not satisfy the decision**. A different account's `github.io`, any `*.pages.dev` /
`*.netlify.app` site, or a custom domain is a different origin.

| Option | Origin | Cost | Repo must be public? | Deploy | What to create / sign up for | Supabase change |
|---|---|---|---|---|---|---|
| **A. Pages on the other account** (gmessy30) | `https://gmessy30.github.io/<repo>/` | free with a public repo; [I] a **private** repo needs GitHub Pro (about $4/month) because GitHub Free publishes Pages from public repos only | yes on Free; no on Pro | push to the branch (or an Actions workflow); live within a minute | a repo under gmessy30; Pages enabled in its settings | add the origin to Auth → URL Configuration → Redirect URLs (for reset / invite links only); nothing else |
| **B. Cloudflare Pages** | `https://<project>.pages.dev` | free (no card; [I] unlimited bandwidth, 500 builds/month) | no (private GitHub repo via the Cloudflare GitHub app, or **direct upload** of the folder with no repo link at all) | push to the connected branch, or `wrangler pages deploy ./site`, or drag-and-drop in the dashboard | a Cloudflare account (email), the GitHub authorisation for that one repo; a custom domain can be attached later for free | same as A |
| **C. Netlify** | `https://<site>.netlify.app` | free Starter ([I] 100 GB/month, 300 build minutes; [GAP] whether a card is now required on the free plan) | no | push, CLI, or drag-and-drop | a Netlify account, GitHub authorisation | same as A |
| **D. Custom domain** (on top of A, B or C) | `https://admin.<domain>` | about $10–15/year for the name ([I] Cloudflare Registrar sells at cost); hosting as above | as the host | as the host | register the name, point DNS at the host (one CNAME), HTTPS is automatic on all three | the custom origin in Redirect URLs instead of the host's |

Common to all four: no build step (plain HTML, CSS, ES modules, as the PWAs), HTTPS included, the anon key in the
page (it is public already in `warn-map/js/config.js`; the gate, not the key, is the security). **No CORS change**
is needed on Supabase: [I] the Auth and PostgREST endpoints accept any browser origin; the Redirect URL list only
governs where email links may land. **Site URL** stays as it is ([GAP] read it in the Dashboard before slice 1).

**Recommendation: B, Cloudflare Pages, from a private repo under gmessy30, no custom domain yet.**
- It is the only free option that keeps the repo private (A needs Pro; C's free terms are uncertain).
- Its own origin (`<project>.pages.dev`) satisfies the decision; Cloudflare's branch previews live on
  `<hash>.<project>.pages.dev`, which is fine (a preview is also a separate origin; disable previews or never add
  them to Redirect URLs).
- Direct upload means a deploy can be a single command from the repo checkout, so "source of truth is this
  directory" (v1 README) survives; later, connecting the repo gives deploy-on-push.
- A custom domain can be added later with no change to the page; only the Redirect URL list changes.
- What changes if the account is ever lost: the sealed-secrets rule in the continuity runbook (backend 8.1) must
  list the Cloudflare login beside the CLI access.

Second choice: A with a **public** repo. The page's content is harmless when public (URL plus anon key, which are
already public), but the design keeps WARN's admin code private, and a public admin repo advertises its shape.

## 2. The sign-in page

All calls go to `https://wpiviguiqqajlxrbvtkx.supabase.co` with the `apikey: <anon>` header; signed-in calls add
`Authorization: Bearer <access token>`. No supabase-js (the no-CDN rule; and a vendored copy would bring
`localStorage` persistence and the auth-lock deadlock v1 hit). About 150 lines of `fetch` against GoTrue's REST.

### 2.1 Password, then the authenticator

```
POST /auth/v1/token?grant_type=password        {"email","password"}
  -> 200 {access_token (JWT, aal="aal1"), refresh_token, expires_in, user{factors:[{id, friendly_name, status}]}}
POST /auth/v1/factors/<factor id>/challenge     {}          (Bearer: the aal1 token)
  -> 200 {id: <challenge id>, expires_at}
POST /auth/v1/factors/<factor id>/verify        {"challenge_id","code"}
  -> 200 {access_token (aal="aal2", same session_id), refresh_token, ...}
```
- The page shows the verified factors by `friendly_name` (phone / backup) and challenges the one chosen.
- The password field is cleared as soon as the request is sent; the password is never kept.
- The JWT's claims (`sub`, `aal`, `session_id`, `exp`) are read by base64url-decoding the middle segment; nothing
  is trusted from them for security (the server decides), they only drive the UI.
- Wrong code: GoTrue returns 4xx; show it, let the user try again; after three, offer the other factor.
- The Dashboard's AAL1 limit ([R] owner: 15 minutes) bounds the time between password and code; the page says so.

### 2.2 Holding the session (backend note 1.2, decided)

- **Access token: memory only** (a module variable). **Refresh token: `sessionStorage`** (dies with the tab; survives
  a reload in the same tab), never `localStorage`. [GAP] If the owner prefers memory-only for both, a reload means
  sign in again; cost is small, choose at slice 1.
- **Refresh on demand**, not on a timer: before any request, if `exp` is within 60 s, or when a request returns
  401, `POST /auth/v1/token?grant_type=refresh_token {"refresh_token"}` and replace both tokens (rotation: a used
  refresh token is dead). [R] JWT expiry is set in the Dashboard; the owner says 15 minutes. With 15-minute tokens
  the page refreshes at most every ~14 minutes while in use.
- **Every refresh updates `auth.sessions.refreshed_at`**, which is what the gate's 75-minute idle rule reads. So
  the server rule only bites a session nobody refreshes (a closed tab); the page's own idle rule is the one that
  ends a forgotten open tab (next point). [GAP] The note said the server limit becomes 30 minutes if the JWT can be
  15 minutes; 049 shipped 75. Tighten in a later migration or leave as the backstop: decide.
- **12-hour time-box:** when a gated call returns `session_timebox`, the page signs out and asks for a fresh sign-in;
  it may also warn at 11 h 50 from the session's `created_at` (read from the JWT's `iat` of the first token).
- **Newest session wins:** `session_superseded` from the gate means another tab or device signed in; the page says
  so and signs out locally.

### 2.3 Idle sign-out: 30 minutes without input, **global**

```
POST /auth/v1/logout?scope=global              (Bearer: current access token)  -> 204
```
- A single timer, reset by pointer, key and touch events and by visibility returning; at 30 minutes the page calls
  the logout above, wipes memory and `sessionStorage`, and shows the sign-in screen with "Signed out after 30
  minutes idle".
- `scope=global` revokes every refresh token of the account server-side, so a copied refresh token is useless; the
  same call runs on the page's own **Sign out** button.
- **No logout on leaving the page (decision B, owner, 2026-10-20).** A `pagehide` keepalive logout was built
  first; a headless-Edge probe suggested it was never delivered, but real Edge delivered it on F5 and signed the
  owner out mid-task (session row gone at 23:37 UTC, the reloaded page's refresh refused). It was removed. The
  Sign out button and the 30-minute idle timer are the only sign-outs; a reload resumes the session from
  `sessionStorage`; a closed tab leaves a server session nobody can use (its refresh token died with the tab)
  that the gate's 12 h / 75 min rules age out. The page names the server's reason when a renewal is refused, and
  treats "already used" within seconds of loading as a reload during a rotation (sign in again), not a fault.

### 2.4 Sign-out must delete the server-side session: check it

[R] GoTrue's logout takes `scope=global` (all of the user's sessions), `local` (this session) or `others`. [I] On
`global` and `local` it deletes the `auth.sessions` row(s), not only the refresh tokens. The enrollment script
called `logout?scope=local` and two of its sessions are still in `auth.sessions`; its errors were swallowed, so
[GAP] whether the call failed or `local` left the row. **Slice 1's acceptance test settles it:** sign in, sign out,
then read `auth.sessions` by CLI (read-only) and expect the row gone. If `local` leaves rows, the page uses `global`
only (which it does anyway). Until then the two stray rows can be removed from the Dashboard (Authentication →
Users → the account → sign out) or left; older sessions never supersede a new sign-in.

### 2.5 Reading as an admin

```
POST /rest/v1/rpc/is_admin                       -> true at aal2 on the allow-list, false otherwise
GET  /rest/v1/admins?select=user_id,added_at,note -> own row (018's self-read policy, any aal): "on the allow-list"
GET  /rest/v1/spatial_hazards?select=*&order=created_at.desc&limit=2000     (cleared rows included at aal2)
GET  /rest/v1/hazard_audit_log?select=original_id,reported_at&order=reported_at.asc   with Prefer: count=exact
GET  /rest/v1/admin_actions?select=*&order=created_at.desc&limit=200
GET  /rest/v1/security_events?select=id,occurred_at,kind,detail,push_state&order=occurred_at.desc&limit=200
```
The gate's errors arrive as PostgREST 4xx with `message` = `not_admin` | `mfa_required` | `session_unknown` |
`session_timebox` | `session_idle` | `session_superseded`; the page maps each to a sentence and an action (sign in
again; enter a code; close the other tab).

### 2.6 What the page never does

Store a password, put a token in `localStorage` or a cookie, hold the service-role key, load anything from a CDN,
call `require_admin()` itself (it cannot), or decide authorisation from the JWT (every RPC re-checks).

## 3. The first slice: sign-in with MFA, "who am I", read-only views

**Scope.** One page (`index.html`, `style.css`, `js/auth.js`, `js/api.js`, `js/app.js`, `config.js`), served from
the new origin. No clear, no delete, no map.
1. **Sign-in** as in section 2 (password → factor choice → code), the session rules, idle sign-out, Sign out.
2. **Who am I:** `sub` shortened to 8 characters, `aal`, `session_id` shortened, session started at, the
   `is_admin()` result, and whether `admins` returns the own row. Shows the gate's verdict in words.
3. **Hazards:** v1's list with its filters (category, age, first-report basis), now including cleared rows (marked),
   read with the signed-in token; falls back to the public view when `is_admin()` is false, saying so.
4. **Audit:** `admin_actions` newest first (actor id8, action, hazard id, aal, `override_kind`, reason, snapshot
   expandable) and `security_events` newest first (kind, time, detail), the 045 observation rows included. This is
   the "read in the Master UI" the 045 design promised.
5. **Acceptance:** sign in on each account; `is_admin()` true only after the code; a password-only session sees no
   audit rows and no cleared hazards; after Sign out, `auth.sessions` has no row for the session (CLI, read-only);
   30 minutes idle signs out; a second sign-in in another tab makes the first tab's next gated call say
   superseded. No write of any kind is possible from this slice.

**Reuse from v1** (`WARN/web/admin/app.js`): the category tables and `categoryOf()`, the age buckets and
first-report basis, `loadFirstReports()`'s two silent-failure checks (zero rows while hazards exist; fewer rows than
`count`), the chips / list / counts rendering, `rel()`, `toast()`, the refresh-pause rule (never refresh under a
selection or an open panel), the "couldn't tell" vs "no" handling of `is_admin()`, `style.css`, and the vulnerable-
person mirror `isVulnerable()` (kept for the delete slice).
**Rewrite:** all of auth (supabase-js → the fetch client of section 2; no `onAuthStateChange`, no persisted
session), the data fetches (`sb.from()` → PostgREST URLs with explicit `limit` and `Prefer: count=exact`), the
CDN `<script>` tags (removed; the map comes back in slice 2 with a vendored MapLibre and the public site's
tiles), `config.js` (committed in the private repo, or injected at deploy; the values are public either way), and
the README's deploy section (Cloudflare instead of copying into `keeneyeapps.github.io`).

**Before slice 1 can be deployed:** the decisions in section 5, the Auth settings step from the backend note's
order of work (2): confirm the JWT expiry, add the origin to Redirect URLs, read the Site URL.

## 4. The slices after that, in order

| Slice | What the page gains | Server it needs | Notes |
|---|---|---|---|
| **2. Read-only map** | v1's map back, read-only: a vendored MapLibre (no CDN), the public site's PMTiles basemap from `keeneyeapps.github.io/warn-map` (that origin added to `connect-src` in `_headers`; the Supabase URL stays the only other one), hazard dots coloured by category, click-to-detail, fly-to on a row, and the drag-rectangle area select, which in this slice **only highlights the matching rows**: no action is attached until the delete and clear slices use it | nothing new | the public tiles are public data from an origin the owner runs, so no privacy change; the map must never be the only way to the list (v1's lesson: the fetch does not hang off the map's load event); slices 3 and 4 reuse area select as their bulk selection |
| **3. Hard delete (junk)** | v1's delete flow: selection (rows and area select from slice 2), confirm, the vulnerable-person second confirmation, batches of 500, result rows | nothing new: `admin_delete_hazards` behind 049 | reuse v1's `confirmDelete`, `confirmVulnerable`, `runDelete` almost as they are; the audit view shows the new `aal` / `target_id` columns at once |
| **4. Clear and restore with reason codes** | Clear (soft) with `mistaken` / `resolved` plus free text, per-row `override_kind`; Restore from the audit row while not expired; "Restore all N clears by this account in this window" from an alert | **050** provenance (`cleared_by`, `source`, `public_hazards` serves drivers only) then **051** (`admin_clear_hazards`, `admin_restore_hazards`, `admin_recreate_from_snapshot`, `admin_burst`, `clear_reason`, `app_settings`) | backend 3.5–3.8; the sightings change (`cleared_resolved`) is its own migration after 051 and needs no page change; area select from slice 2 is the bulk selection here too |
| **5. Planted test hazards** | plant a hidden test pin (type, point, heading, text, expiry ≤ 24 h, reason), see it marked `planted_test`, clear it | **050** (source widened, hidden from `public_hazards`, out of merge / Confirm / Clear / caps) and **051** (`admin_plant_test_hazard`) | the page shows planted rows only because it reads `spatial_hazards` at aal2; verify scripts get a planted-row case per consumer |
| **6. Fleet notices** | post / end a "Notice from WARN" on a channel (title, body, optional point, expiry, reason) | the channel-notice migration (order of work 9: `admin_post_channel_notice`, `admin_end_channel_notice`) **and the Android channel half**, which does not exist | backend 2.2; the privacy-policy re-read goes with the app half |
| **7. Org management** | orgs with public label and type, org admins (aal2, separate from `admins`), category authority tiers, the per-org action history, disputes, the org clear-burst alert with bulk restore | **052** | backend 3.2, 3.5, 6; the tier matrix is completed with the first municipality |
| **8. Region records, type lists and quick buttons** | region picker, regional type labels / spoken labels, quick-button slots | **053** (`get_client_config`, the server safety skeleton) and `admin_upsert_region` | backend 2.4, 2.5; the server may only tighten safety flags |
| later | road blocks (point blocks first, the `fleet` schema), whole-road rules after the way-id graph rebuild | order of work 10–11 | backend 5 |

Each slice ships with its migration's verify script and a page-side acceptance list, and nothing in a slice is
reachable from the page before its migration is applied (the RPC simply does not exist).

## 5. Risks, and what to decide or create before slice 1

**Decide**
1. **Hosting:** B (Cloudflare Pages, private repo under gmessy30) as recommended, or A public, or C.
2. **Repo name** and that it is private, under gmessy30 beside WARN; whether `config.js` is committed there.
3. **Custom domain now or later** (later is free of consequence: only the Redirect URL list changes).
4. **Refresh token in `sessionStorage` (note 1.2) or memory only** (2.2).
5. **Server idle limit:** keep 75 minutes as the backstop, or tighten to 30 in a later migration (2.2).
6. **The stray sessions:** remove in the Dashboard now, or let slice 1's sign-out test cover them (2.4).

**Create**
7. The hosting account (Cloudflare or Netlify) and the GitHub authorisation for the one repo; record the login in
   the sealed set the continuity runbook (backend 8.1) will point to.
8. The repo, from v1's files, with the rewrite of section 3; the README's deploy section rewritten.
9. Auth settings in the Dashboard: JWT expiry confirmed ([GAP] whether the free plan accepted 15 minutes), the new
   origin in Redirect URLs, Site URL read and recorded in WARN's `CLAUDE.md`.

**Risks**
- **Account loss = admin loss:** both admin accounts, the Cloudflare login and the CLI access are all one person's.
  Mitigated only by the continuity runbook (backend 8.1), still unwritten; it should be written before slice 3 gives
  the page write power, not after.
- **A lost or desynchronised authenticator** on one account: use the other; the Dashboard can unenroll factors. The
  page must never offer a "skip MFA" path.
- **The old v1 page**, if it were ever published at `keeneyeapps.github.io/warn/admin/`, would look broken under 049
  (no deletes, no audit). It is not deployed; keep it that way and mark its README "superseded" when the new repo
  exists.
- **PostgREST row caps:** v1 learned that `max_rows` silently truncates; the new fetches keep `Prefer: count=exact`
  and page explicitly. The audit log is 353 rows and grows per report.
- **Report positions on screen:** `hazard_audit_log` and the hazards list show where people were. That is the
  admin's job and gated by MFA; the page must not cache them (no service worker, no `localStorage`, `Cache-Control:
  no-store` from the host for `index.html` if the host allows headers; Cloudflare Pages does via `_headers`).
- **The 15-minute AAL1 limit** means a slow authenticator setup fails the challenge; the page tells the user to start
  over, nothing worse.
- **Branch previews** on Cloudflare are separate origins that would also hold the anon key; harmless, but never add
  them to Redirect URLs.
- **The free-plan JWT expiry** may not be shortenable; the design works at 1 hour with the 75-minute server rule.
