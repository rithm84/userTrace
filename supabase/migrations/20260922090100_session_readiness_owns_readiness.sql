-- `public.session_readiness` is the only readiness record.
--
-- Readiness was stored twice. `session_readiness` holds the per-component facts
-- the readiness evaluator combines, the typed blockers that explain a refusal,
-- and the monotonic `readiness_revision` a context bundle's freshness check
-- depends on. `interview_sessions.conversational_ready` and `observation_ready`
-- held nothing but the two derived booleans, written by the same evaluator in
-- the same statement, so they were a lossy copy that could only ever drift.
--
-- Mirroring them also bumped `interview_sessions.revision`, which put every
-- readiness heartbeat into the session aggregate's optimistic-concurrency
-- sequence for no gain. Every reader now joins `session_readiness`, and a
-- session with no readiness row yet reads as not ready, exactly as the
-- projection already treated a missing row.
--
-- Contracts: docs/arch-components/interview-and-voice.md#readiness-has-two-boundaries
--            docs/arch-components/telemetry-and-evidence.md#completeness-and-readiness

alter table public.interview_sessions
  drop column conversational_ready,
  drop column observation_ready;
