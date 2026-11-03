ALTER TABLE "public"."prepared_workspaces"
  DROP CONSTRAINT "prepared_workspaces_product_signal_id_key";

ALTER TABLE "public"."prepared_workspaces"
  DROP CONSTRAINT "prepared_workspaces_sandbox_name_key";

ALTER TABLE "public"."prepared_drafts"
  ADD COLUMN "kind" text NOT NULL DEFAULT 'proposal'::text;

ALTER TABLE "public"."prepared_drafts"
  ADD COLUMN "agent_cost_cents" integer NOT NULL DEFAULT 0;

ALTER TABLE "public"."prepared_drafts"
  ADD COLUMN "gate_proposal" jsonb;

ALTER TABLE "public"."prepared_drafts"
  ADD COLUMN "gate_inputs_digest" text;

ALTER TABLE "public"."prepared_drafts"
  ADD COLUMN "preview_version_id" uuid;

ALTER TABLE "public"."prepared_drafts"
  ADD COLUMN "source_digest" text;

ALTER TABLE "public"."prepared_drafts"
  ADD COLUMN "dependencies" jsonb;

ALTER TABLE "public"."prepared_drafts"
  ADD COLUMN "artifacts" jsonb;

ALTER TABLE "public"."prepared_workspaces"
  ADD COLUMN "generation" integer NOT NULL DEFAULT 1;

ALTER TABLE "public"."prepared_workspaces"
  ADD COLUMN "starting_tree" text;

ALTER TABLE "public"."prepared_workspaces"
  ADD COLUMN "preview_version_id" uuid;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "staged_schema" jsonb;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "prepared_route" jsonb;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "prepared_readiness" jsonb;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "prepared_at" timestamp WITH time zone;

ALTER TABLE "public"."prepared_drafts"
  ADD CONSTRAINT "prepared_drafts_kind_check" CHECK ((kind = ANY (ARRAY['intake'::text, 'proposal'::text])));

ALTER TABLE "public"."prepared_workspaces"
  ADD CONSTRAINT "prepared_workspaces_product_signal_id_generation_key" UNIQUE (product_signal_id, generation);

CREATE UNIQUE INDEX prepared_workspaces_one_open_per_case ON public.prepared_workspaces USING btree (product_signal_id)
  WHERE (state = ANY (ARRAY['preparing'::text, 'ready'::text, 'drafting'::text]));

CREATE UNIQUE INDEX prepared_workspaces_one_open_per_sandbox ON public.prepared_workspaces USING btree (sandbox_name)
  WHERE (state = ANY (ARRAY['preparing'::text, 'ready'::text, 'drafting'::text]));
