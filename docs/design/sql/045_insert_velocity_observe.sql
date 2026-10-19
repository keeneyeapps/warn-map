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
