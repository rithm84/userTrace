-- A prepared workspace opens to drafting once its checkout is installed, while its
-- baseline still runs in its own copy of the base; only an attempt reads the
-- baseline, and its claim waits for it. So a claimed workspace, not a ready or
-- drafting one, is what must hold a baseline.
ALTER TABLE "public"."prepared_workspaces"
  DROP CONSTRAINT "prepared_workspaces_check";

ALTER TABLE "public"."prepared_workspaces"
  ADD CONSTRAINT "prepared_workspaces_check"
  CHECK (((state <> 'claimed'::text) OR (baseline_checks IS NOT NULL)));
