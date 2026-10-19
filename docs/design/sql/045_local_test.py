"""Local test of migration 045 on a throwaway embedded Postgres. Never touches the linked project.

    python -m venv venv && venv/Scripts/pip install pgserver "psycopg[binary]"
    venv/Scripts/python docs/design/sql/045_local_test.py [scratch-dir] [--owner warn_owner|superuser]

Starts a fresh PostgreSQL 16 cluster under scratch-dir (deleted at exit), loads 045_local_fixture.sql, then
applies 045_insert_velocity_observe.sql as the chosen owner (default: warn_owner, a non-superuser standing in
for Supabase's postgres) and inserts through the stub upsert_spatial_hazard_v3 as anon. Prints PASS/FAIL per
check, the two query plans, and the lock profile.
"""
import pathlib
import sys
import tempfile
import time

import pgserver
import psycopg

HERE = pathlib.Path(__file__).resolve().parent
MIGRATION = (HERE / "045_insert_velocity_observe.sql").read_text(encoding="utf-8")
ROLLBACK = (HERE / "045_insert_velocity_observe_rollback.sql").read_text(encoding="utf-8")
FIXTURE = (HERE / "045_local_fixture.sql").read_text(encoding="utf-8")

LAT, LON = 60.49, -151.06
RESULTS = []


def check(name, ok, info=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {info}" if info else ""))


def one(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()


def scalar(conn, sql, params=None):
    return one(conn, sql, params)[0]


def v3_insert(anon, hazard_type="MOOSE", lat=LAT, lon=LON):
    """One report through the same door the APK uses: anon calling the SECURITY DEFINER RPC."""
    return one(anon, """
        select * from public.upsert_spatial_hazard_v3(
            p_region_code => 'KPB', p_category => 'Wildlife', p_hazard_type => %s,
            p_latitude => %s, p_longitude => %s, p_heading => 90, p_retract_token => 'tok',
            p_position_accuracy_m => 12, p_position_fix_age_ms => 500,
            p_heading_sigma_deg => 5, p_heading_source => 'RECEIVER')""", (hazard_type, lat, lon))


def objects(owner):
    return one(owner, """
        select (select count(*) from pg_trigger where tgrelid = 'public.spatial_hazards'::regclass
                   and tgname = 'trigger_insert_velocity_observe'),
               (select count(*) from pg_indexes where schemaname = 'public' and tablename = 'hazard_audit_log'
                   and indexname in ('hazard_audit_log_inserted_at_idx', 'hazard_audit_log_type_inserted_at_idx')),
               (select count(*) from pg_proc where proname = 'observe_insert_velocity'),
               (select count(*) from information_schema.columns where table_schema = 'public'
                   and table_name = 'hazard_audit_log' and column_name = 'inserted_at'
                   and is_nullable = 'NO' and column_default like 'now()%%')""")


def ping_trigger_def(owner):
    return scalar(owner, """select pg_get_triggerdef(oid) from pg_trigger
                            where tgrelid = 'public.security_events'::regclass and tgname = 'security_events_ping_notify'""")


def push_state_checks(owner):
    return owner.execute("""select conname, pg_get_constraintdef(oid) from pg_constraint
                            where conrelid = 'public.security_events'::regclass and contype = 'c'
                              and pg_get_constraintdef(oid) like '%%push_state%%' order by 1""").fetchall()


def events(owner, kind=None):
    if kind:
        return scalar(owner, "select count(*) from public.security_events where kind = %s", (kind,))
    return scalar(owner, "select count(*) from public.security_events")


def pings(su):
    return scalar(su, "select count(*) from net.http_post_calls")


def reopen_window(owner):
    """Age every event by an hour so the next crossing is not suppressed by the 10-minute window."""
    owner.execute("update public.security_events set occurred_at = occurred_at - interval '1 hour'")


def timed_inserts(anon, n, hazard_type):
    t0 = time.perf_counter()
    for _ in range(n):
        v3_insert(anon, hazard_type)
    return (time.perf_counter() - t0) * 1000 / n


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    owner_mode = "warn_owner"
    if "--owner" in sys.argv:
        owner_mode = sys.argv[sys.argv.index("--owner") + 1]
        args = [a for a in args if a != owner_mode]
    scratch = pathlib.Path(args[0]) if args else pathlib.Path(tempfile.gettempdir())
    pgdata = tempfile.mkdtemp(prefix="warn045-", dir=scratch)
    server = pgserver.get_server(pgdata, cleanup_mode="delete")
    uri = server.get_uri()

    su = psycopg.connect(uri, autocommit=True)
    fixture = FIXTURE
    if owner_mode == "superuser":
        fixture = "\n".join(l for l in FIXTURE.splitlines() if not l.endswith("-- OWNER"))
    su.execute(fixture)

    owner = psycopg.connect(uri, autocommit=True)
    if owner_mode != "superuser":
        owner.execute(f"set role {owner_mode}")
    owner_name, owner_super = one(owner, "select current_user, (select rolsuper from pg_roles where rolname = current_user)")
    print(f"postgres: {scalar(su, 'select version()')}")
    print(f"owner mode: {owner_mode} (current_user={owner_name}, superuser={owner_super})")
    anon = psycopg.connect(uri, autocommit=True)
    anon.execute("set role anon")
    notices = []
    anon.add_notice_handler(lambda d: notices.append(f"{d.severity}: {d.message_primary}"))

    # --- 1. parse + apply twice ------------------------------------------------------------------
    owner.execute(MIGRATION)
    owner.execute(MIGRATION)
    trig, idx, fn, col = objects(owner)
    check("migration applies twice without error; one trigger, two indexes, one function, one NOT NULL column with default now()",
          (trig, idx, fn, col) == (1, 2, 1, 1), f"trigger={trig} indexes={idx} function={fn} column={col}")

    in_window, newest, fast_clock = one(owner, """
        select (select count(*) from public.hazard_audit_log where inserted_at > now() - interval '15 minutes'),
               (select max(inserted_at) <= now() - interval '1 hour' from public.hazard_audit_log),
               (select inserted_at <= now() - interval '1 hour' from public.hazard_audit_log where original_id = 900301)""")
    check("backfill: no pre-045 row lands in a counting window; the fast-clock row is pushed back an hour",
          in_window == 0 and newest and fast_clock, f"in_window={in_window} newest_ok={newest} fast_clock_ok={fast_clock}")

    order = [r[0] for r in owner.execute("""
        select tgname from pg_trigger where tgrelid = 'public.spatial_hazards'::regclass and not tgisinternal
           and (tgtype & 4) = 4 and (tgtype & 2) = 0 order by tgname""").fetchall()]
    check("fires after trigger_hazard_audit (AFTER INSERT triggers in name order)",
          order == ["trigger_hazard_audit", "trigger_insert_velocity_observe"], str(order))

    privs = one(owner, """
        select has_function_privilege('anon', 'public.observe_insert_velocity()', 'execute'),
               has_function_privilege('authenticated', 'public.observe_insert_velocity()', 'execute'),
               (select rolname from pg_roles r join pg_proc p on p.proowner = r.oid where proname = 'observe_insert_velocity'),
               (select rolname from pg_roles r join pg_class c on c.relowner = r.oid where c.oid = 'public.hazard_audit_log'::regclass)""")
    check("anon/authenticated cannot execute the trigger function; it is owned by the table owner",
          privs[0] is False and privs[1] is False and privs[2] == privs[3] == owner_name, str(privs))

    checks = push_state_checks(owner)
    tdef = ping_trigger_def(owner)
    check("push policy: exactly one push_state check, named, accepting log_only; ping trigger has WHEN (new.push_state IS NULL)",
          len(checks) == 1 and checks[0][0] == "security_events_push_state_check_045" and "'log_only'" in checks[0][1]
          and "WHEN ((new.push_state IS NULL))" in tdef, f"checks={checks} trigger={tdef}")
    try:
        owner.execute("insert into public.security_events (kind, push_state) values ('test_bogus', 'bogus')")
        bogus_rejected = False
    except psycopg.errors.CheckViolation:
        bogus_rejected = True
    check("push_state still rejects an unknown value", bogus_rejected)

    # --- (a) ordinary insert ---------------------------------------------------------------------
    hid, inserted, dropped = v3_insert(anon)
    audit = one(owner, """
        select inserted_at between now() - interval '5 seconds' and now(), reported_at = created_at
          from public.hazard_audit_log a join public.spatial_hazards h on h.id = a.original_id
         where a.original_id = %s""", (hid,))
    check("(a) ordinary insert through v3 as anon succeeds; audit row has server inserted_at; no event",
          inserted is True and audit == (True, True) and events(owner) == 0,
          f"id={hid} was_inserted={inserted} audit={audit} events={events(owner)}")

    # --- (c1) the area level: one log-only row, no ping --------------------------------------------
    ids = [hid]
    for _ in range(4):
        r = v3_insert(anon)
        assert r[1] is True
        ids.append(r[0])
    area = one(owner, """select count(*), min(push_state), min((detail->>'hazard_id')::bigint), min(detail->>'levels_crossed')
                         from public.security_events where kind = 'insert_velocity_area_observed'""")
    check("(c1) 5 inserts at one point: exactly ONE insert_velocity_area_observed row, push_state log_only, ZERO pings, no pushed kind",
          area[0] == 1 and area[1] == "log_only" and pings(su) == 0 and events(owner) == 1,
          f"rows={len(ids)} area_rows={area[0]} push_state={area[1]} fired_on_insert_no={ids.index(area[2]) + 1} "
          f"levels={area[3]} pings={pings(su)}")

    # --- (c2) the region level: one pushed row, one ping ------------------------------------------
    for _ in range(27):
        r = v3_insert(anon)
        assert r[1] is True
        ids.append(r[0])
    obs = one(owner, """select count(*), min(push_state), min((detail->>'hazard_id')::bigint), min(detail->>'levels_crossed'),
                               min((detail->>'global_10min')::int), min((detail->>'region_10min')::int)
                        from public.security_events where kind = 'insert_velocity_observed'""")
    check("(c2) 32 inserts at one point: exactly ONE insert_velocity_observed row (region level at the 20th), pending, exactly ONE ping",
          obs[0] == 1 and obs[1] is None and pings(su) == 1 and events(owner, "insert_velocity_area_observed") == 1
          and events(owner) == 2,
          f"rows={len(ids)} observed_rows={obs[0]} push_state={obs[1]} fired_on_insert_no={ids.index(obs[2]) + 1} "
          f"levels={obs[3]} global={obs[4]} region={obs[5]} pings={pings(su)} area_rows={events(owner, 'insert_velocity_area_observed')}")
    claimed = [r[0] for r in owner.execute("select kind from public.claim_security_pushes()").fetchall()]
    check("027's claim_security_pushes() offers only the pushed kind; the log_only row is never a candidate",
          claimed == ["insert_velocity_observed"], str(claimed))
    for _ in range(10):
        v3_insert(anon)
    check("a second crossing inside the window writes no second row of either kind and no second ping",
          events(owner) == 2 and pings(su) == 1, f"events={events(owner)} pings={pings(su)}")

    # --- (b1) the event insert fails: observer_error is logged, insert survives -------------------
    reopen_window(owner)
    owner.execute("alter table public.security_events add constraint test_block check (kind not like 'insert_velocity%%') not valid")
    hid_b1 = v3_insert(anon)[0]
    err = one(owner, """select detail->>'sqlstate', detail->>'message', (detail->>'hazard_id')::bigint = %s
                        from public.security_events where kind = 'observer_error' order by id desc limit 1""", (hid_b1,))
    owner.execute("alter table public.security_events drop constraint test_block")
    check("(b1) forced error in the trigger (event insert blocked by a CHECK): insert still succeeds, observer_error logged",
          err is not None and err[0] == "23514" and "test_block" in err[1] and err[2] is True, f"id={hid_b1} logged={err}")

    # --- (b2) even the error log is unwritable: WARNING, insert survives --------------------------
    reopen_window(owner)
    before = events(owner)
    notices.clear()
    owner.execute("alter table public.security_events add constraint test_block_all check (false) not valid")
    hid_b2 = v3_insert(anon)[0]
    owner.execute("alter table public.security_events drop constraint test_block_all")
    warned = [n for n in notices if "observe_insert_velocity failed" in n]
    check("(b2) log itself unwritable: insert still succeeds, WARNING raised instead, no event rows",
          hid_b2 and len(warned) == 1 and events(owner) == before, f"id={hid_b2} warning={warned}")

    # --- (b3) the counting query itself is broken -------------------------------------------------
    owner.execute("alter table public.hazard_audit_log rename column inserted_at to inserted_at_x")
    hid_b3 = v3_insert(anon)[0]
    owner.execute("alter table public.hazard_audit_log rename column inserted_at_x to inserted_at")
    err3 = one(owner, """select detail->>'sqlstate' from public.security_events
                         where kind = 'observer_error' and (detail->>'hazard_id')::bigint = %s""", (hid_b3,))
    check("(b3) counting column missing: insert succeeds, observer_error 42703 logged",
          err3 is not None and err3[0] == "42703", f"id={hid_b3} sqlstate={err3}")

    # --- locks ------------------------------------------------------------------------------------
    reopen_window(owner)  # so this insert also writes a pushed event: the heaviest path
    anon.execute("begin")
    v3_insert(anon)
    locks = anon.execute("""
        select locktype, coalesce(relation::regclass::text, ''), mode
          from pg_locks where pid = pg_backend_pid() and locktype in ('advisory', 'relation')
         order by 1, 2, 3""").fetchall()
    anon.execute("commit")
    advisory = [l for l in locks if l[0] == "advisory"]
    strong = [l for l in locks if l[2] not in ("AccessShareLock", "RowExclusiveLock")]
    check("no advisory lock and nothing stronger than RowExclusiveLock while the trigger runs",
          not advisory and not strong, "locks held at end of the inserting transaction:\n      " +
          "\n      ".join(f"{t:9} {m:18} {r}" for t, r, m in locks))

    # --- plans ------------------------------------------------------------------------------------
    owner.execute("""
        insert into public.hazard_audit_log (original_id, region_code, category, hazard_type, latitude, longitude, reported_at, inserted_at)
        select 10000 + g, 'KPB', 'Wildlife', (array['MOOSE','BEAR','POTHOLE','ICE'])[1 + (g %% 4)],
               %s + (random() - 0.5) * 0.1, %s + (random() - 0.5) * 0.2, now(), now() - (random() * interval '60 minutes')
          from generate_series(1, 5000) g""", (LAT, LON))
    owner.execute("analyze public.hazard_audit_log")
    total, win10 = one(owner, """select count(*), count(*) filter (where inserted_at > now() - interval '10 minutes')
                                 from public.hazard_audit_log""")
    print(f"\n--- plan: global 10-minute count ({total} rows in the log, {win10} in the window) ---")
    plan_global = "\n".join(r[0] for r in owner.execute("""
        explain (analyze, buffers) select count(*) from public.hazard_audit_log
         where inserted_at > now() - interval '10 minutes'""").fetchall())
    print(plan_global)
    print("\n--- plan: same-type 2 km 15-minute count ---")
    plan_area = "\n".join(r[0] for r in owner.execute("""
        explain (analyze, buffers) select count(*) from public.hazard_audit_log
         where inserted_at > now() - interval '15 minutes'
           and hazard_type is not distinct from 'MOOSE'
           and latitude is not null and longitude is not null
           and 2 * 6371008.8 * asin(sqrt(least(1.0,
                   power(sin(radians(%s - latitude) / 2), 2)
                 + cos(radians(%s)) * cos(radians(latitude))
                 * power(sin(radians(%s - longitude) / 2), 2)))) <= 2000""", (LAT, LAT, LON)).fetchall())
    print(plan_area + "\n")
    check("global count uses hazard_audit_log_inserted_at_idx", "hazard_audit_log_inserted_at_idx" in plan_global)
    check("area count uses one of the two 045 indexes, no seq scan",
          "Seq Scan" not in plan_area and ("inserted_at_idx" in plan_area))

    # --- cost -------------------------------------------------------------------------------------
    with_trigger = timed_inserts(anon, 100, "POTHOLE")

    # --- idempotent again, after everything -------------------------------------------------------
    owner.execute(MIGRATION)
    check("third apply after all of the above: no error, still one trigger, two indexes, one push_state check",
          objects(owner)[:2] == (1, 2) and len(push_state_checks(owner)) == 1)

    # --- rollback twice, inserts still work, re-apply ---------------------------------------------
    owner.execute(ROLLBACK)
    owner.execute(ROLLBACK)
    trig, idx, fn, col = objects(owner)
    tdef_rb = ping_trigger_def(owner)
    hid_rb = v3_insert(anon)[0]
    without_trigger = timed_inserts(anon, 100, "POTHOLE")
    check("rollback twice: trigger, function and indexes gone; inserted_at kept; 027's ping trigger back without WHEN; inserts still work",
          (trig, idx, fn, col) == (0, 0, 0, 1) and hid_rb and "WHEN" not in tdef_rb,
          f"trigger={trig} indexes={idx} function={fn} column={col} id={hid_rb} ping_trigger={tdef_rb}")
    print(f"      cost: mean insert via v3 as anon, 100 each, window suppressed: with trigger {with_trigger:.2f} ms, "
          f"without {without_trigger:.2f} ms")
    owner.execute(MIGRATION)
    check("migration re-applies after rollback", objects(owner) == (1, 2, 1, 1) and "WHEN" in ping_trigger_def(owner))

    kinds = owner.execute("select kind, push_state, count(*) from public.security_events group by 1, 2 order by 1, 2").fetchall()
    print("      security_events by kind and push_state at the end:", kinds)
    print(f"      pings at the end: {pings(su)}")

    print(f"\n{sum(RESULTS)} of {len(RESULTS)} checks passed (owner mode: {owner_mode})")
    for c in (anon, owner, su):
        c.close()
    server.cleanup()
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
