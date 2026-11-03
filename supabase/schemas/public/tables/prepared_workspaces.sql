CREATE TABLE "public"."prepared_workspaces" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "product_signal_id"         uuid                     NOT NULL,
  "state"                     text                     NOT NULL,
  "sandbox_name"              text                     NOT NULL,
  "pins_digest"               text                     NOT NULL,
  "baseline_checks"           jsonb,
  "failure_detail"            text,
  "implementation_attempt_id" uuid,
  "lease_fencing_token"       bigint,
  "prepared_draft_id"         uuid,
  "generation"                integer                  NOT NULL DEFAULT 1,
  "starting_tree"             text,
  "preview_version_id"        uuid,
  "expires_at"                timestamp with time zone NOT NULL,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "prepared_workspaces_check1" CHECK (((state <> 'claimed'::text) OR ((implementation_attempt_id IS NOT NULL) AND (lease_fencing_token IS NOT NULL)))),
  CONSTRAINT "prepared_workspaces_check" CHECK (((state <> ALL (ARRAY['ready'::text, 'drafting'::text])) OR (baseline_checks IS NOT NULL))),
  CONSTRAINT "prepared_workspaces_check2" CHECK (((state <> 'drafting'::text) OR (prepared_draft_id IS NOT NULL))),
  CONSTRAINT "prepared_workspaces_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "prepared_workspaces_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id),
  CONSTRAINT "prepared_workspaces_pkey" PRIMARY KEY (id),
  CONSTRAINT "prepared_workspaces_state_check" CHECK ((state = ANY (ARRAY['preparing'::text, 'ready'::text, 'drafting'::text, 'failed'::text, 'claimed'::text, 'released'::text]))),
  CONSTRAINT "prepared_workspaces_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "prepared_workspaces_product_signal_id_generation_key" UNIQUE (product_signal_id, generation)
);

CREATE UNIQUE INDEX prepared_workspaces_one_open_per_case ON public.prepared_workspaces USING btree (product_signal_id)
  WHERE (state = ANY (ARRAY['preparing'::text, 'ready'::text, 'drafting'::text]));

CREATE UNIQUE INDEX prepared_workspaces_one_open_per_sandbox ON public.prepared_workspaces USING btree (sandbox_name)
  WHERE (state = ANY (ARRAY['preparing'::text, 'ready'::text, 'drafting'::text]));

ALTER TABLE "public"."prepared_workspaces"
  ENABLE ROW LEVEL SECURITY;

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."prepared_workspaces" TO "service_role";

REVOKE ALL ON TABLE "public"."prepared_workspaces" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."prepared_workspaces" TO "postgres";

REVOKE ALL ON TABLE "public"."prepared_workspaces" FROM "anon", "authenticated";
