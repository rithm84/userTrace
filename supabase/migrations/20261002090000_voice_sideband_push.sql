-- Server push to the voice sideband.
--
-- The worker that observes a GPT-Live session acts on state other processes
-- commit: the participant's queued mute, the preview route and readiness, the
-- implementation progress that makes the new version ready, and the activity the
-- participant performs in their preview. These triggers notify the
-- `voice_sideband` channel with the Interview Session the change belongs to, and
-- the sideband re-reads that session's state when woken instead of polling it.
-- `NOTIFY` is transactional, so only committed state wakes it; the notification
-- carries no state of its own, and the sideband re-reads everything whenever its
-- listener (re)connects, so a notification missed while disconnected is repaired.
--
-- Contract: docs/arch-components/interview-and-voice.md

create function utrace.notify_voice_sideband()
returns trigger
language plpgsql
as $$
begin
  perform pg_notify('voice_sideband', to_jsonb(new) ->> tg_argv[0]);
  return null;
end;
$$;

create trigger interview_sessions_notify_voice_sideband
  after update of implementation_progress on public.interview_sessions
  for each row
  when (old.implementation_progress is distinct from new.implementation_progress)
  execute function utrace.notify_voice_sideband('id');

create trigger session_readiness_notify_voice_sideband
  after insert or update of readiness_revision on public.session_readiness
  for each row execute function utrace.notify_voice_sideband('interview_session_id');

create trigger preview_routes_notify_voice_sideband
  after insert or update of state, readiness_state on public.preview_routes
  for each row execute function utrace.notify_voice_sideband('interview_session_id');

create trigger commands_notify_voice_sideband
  after insert on public.commands
  for each row
  when (new.command_type = 'voice.set_input_muted')
  execute function utrace.notify_voice_sideband('target_id');

-- Observations arrive in export batches, so one notification per session per
-- statement; Postgres also folds identical payloads within one transaction.
create function utrace.notify_voice_sideband_observations()
returns trigger
language plpgsql
as $$
begin
  perform pg_notify('voice_sideband', sessions.interview_session_id::text)
  from (
    select distinct activations.interview_session_id
    from inserted
    join public.telemetry_activations as activations
      on activations.id = inserted.activation_id
    where activations.interview_session_id is not null
  ) as sessions;
  return null;
end;
$$;

create trigger browser_observations_notify_voice_sideband
  after insert on public.browser_observations
  referencing new table as inserted
  for each statement execute function utrace.notify_voice_sideband_observations();

-- A voice delegation is routed for the change's progress as well as for a
-- gesture, so its conversation bundle and judgement carry their own purpose.
alter type public.context_purpose add value if not exists 'delegation_routing';
alter type public.attention_purpose add value if not exists 'delegation_routing';
