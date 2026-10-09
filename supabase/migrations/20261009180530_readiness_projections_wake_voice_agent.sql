CREATE TRIGGER telemetry_readiness_projections_notify_voice_sideband
  AFTER INSERT ON public.telemetry_readiness_projections
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_voice_sideband('interview_session_id');
