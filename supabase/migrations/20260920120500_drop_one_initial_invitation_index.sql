-- Drop the partial unique index on one initial invitation per Product Signal.
--
-- The constraint it expressed is real, but it belongs to the outreach controller
-- rather than to the table: the dispatch path locks the Product Signal row and
-- re-reads the case's Outreach Attempts inside that lock, so two concurrent
-- dispatches cannot both create an initial invitation. Leaving the index in
-- place also forbade unrelated lifecycle records that other workstreams write
-- for the same case.
--
-- Contract: docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle

drop index if exists public.outreach_attempts_one_initial_invitation;
