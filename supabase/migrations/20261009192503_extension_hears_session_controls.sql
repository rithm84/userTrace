DROP TRIGGER "interview_sessions_notify_extension_control" ON "public"."interview_sessions";

CREATE TRIGGER interview_sessions_notify_extension_control
  AFTER UPDATE OF character_revision, muted, paused, state ON public.interview_sessions
  FOR EACH ROW
  WHEN
    (((old.character_revision IS DISTINCT FROM new.character_revision) OR (old.muted IS DISTINCT FROM new.muted) OR (old.paused IS DISTINCT FROM new.paused) OR (old.state IS
    DISTINCT FROM new.state)))
  EXECUTE FUNCTION utrace.notify_extension_control('id');
