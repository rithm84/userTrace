-- A Product Signal may use several Implementation Attempts: the first, then one for
-- each change the originating user asks for while validating a new version, up to
-- the implementation limit its configuration release pins. Each attempt belongs to
-- its own gate decision, and at most one of a case's attempts is in flight at a
-- time; a finished attempt, its candidate ready or not, frees the case for the next.
--
-- Contract: docs/arch-components/implementation.md#attempt-protocol

alter table public.implementation_attempts
  drop constraint implementation_attempts_product_signal_id_key;

alter table public.implementation_attempts
  add constraint implementation_attempts_gate_decision_id_key unique (gate_decision_id);

create unique index implementation_attempts_one_in_flight
  on public.implementation_attempts (product_signal_id)
  where state in (
    'authorized', 'queued', 'acquiring_environment', 'executing', 'checking',
    'publishing_candidate', 'paused', 'infrastructure_blocked'
  );
