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
