-- Server push to the extension's control socket.
--
-- The socket is held by one API process, while bindings, readiness, character
-- state, and visual commands change in any control-plane process: API requests,
-- the worker's voice sideband, and ingestion Jobs. Each of those commits durable
-- state, and these triggers notify the `extension_control` channel with the
-- Interview Session the change belongs to. `NOTIFY` is transactional, so a socket
-- is only ever woken for committed state; it then re-projects that state from
-- the database and routes the durable pending commands. The notification carries
-- no state of its own and a missed one is repaired by the next re-projection.
--
-- Contract: docs/arch-components/browser-extension.md#transport-and-temporary-state

-- A visual command is recorded durably first and routed by the socket that holds
-- the bound installation's connection. `routed_at` is that socket's single-use
-- claim, so two connections can never both deliver one command.
alter table public.visual_command_dispatches
  add column routed_at timestamptz;

-- Every command recorded before this migration was routed in the same call that
-- recorded it, so none of them may be delivered again.
update public.visual_command_dispatches as dispatches
set routed_at = coalesce(commands.dispatched_at, commands.dispatching_at, dispatches.created_at)
from public.commands
where commands.id = dispatches.command_id;

create index visual_command_dispatches_unrouted_idx
  on public.visual_command_dispatches (extension_binding_id, created_at)
  where routed_at is null and outcome is null;

create function utrace.notify_extension_control()
returns trigger
language plpgsql
as $$
begin
  perform pg_notify('extension_control', to_jsonb(new) ->> tg_argv[0]);
  return null;
end;
$$;

create trigger extension_bindings_notify_extension_control
  after insert or update on public.extension_bindings
  for each row execute function utrace.notify_extension_control('interview_session_id');

create trigger session_readiness_notify_extension_control
  after insert or update of readiness_revision on public.session_readiness
  for each row execute function utrace.notify_extension_control('interview_session_id');

create trigger interview_sessions_notify_extension_control
  after update of character_revision on public.interview_sessions
  for each row
  when (old.character_revision is distinct from new.character_revision)
  execute function utrace.notify_extension_control('id');

create trigger visual_command_dispatches_notify_extension_control
  after insert or update of outcome on public.visual_command_dispatches
  for each row execute function utrace.notify_extension_control('interview_session_id');
