CREATE TABLE "public"."visual_command_dispatches" (
  "command_id"                  uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "interview_session_id"        uuid                     NOT NULL,
  "extension_binding_id"        uuid                     NOT NULL,
  "document_attachment_id"      uuid                     NOT NULL,
  "targets"                     jsonb                    NOT NULL,
  "expected_lifecycle_revision" bigint                   NOT NULL,
  "deadline_at"                 timestamp with time zone NOT NULL,
  "policy_decision"             jsonb                    NOT NULL,
  "outcome_reason"              text,
  "observed_targets"            jsonb,
  "completed_at"                timestamp with time zone,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "routed_at"                   timestamp with time zone,
  CONSTRAINT "visual_command_dispatches_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "visual_command_dispatches_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id),
  CONSTRAINT "visual_command_dispatches_extension_binding_id_fkey" FOREIGN KEY (extension_binding_id) REFERENCES public.extension_bindings(id),
  CONSTRAINT "visual_command_dispatches_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "visual_command_dispatches_pkey" PRIMARY KEY (command_id)
);

ALTER TABLE "public"."visual_command_dispatches"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."visual_command_dispatches"
  ADD COLUMN "command_kind" public.visual_command_kind NOT NULL;

ALTER TABLE "public"."visual_command_dispatches"
  ADD COLUMN "outcome" public.visual_command_outcome;

ALTER TABLE "public"."visual_command_dispatches"
  ADD CONSTRAINT "visual_command_dispatches_reason_when_not_applied"
    CHECK (((outcome IS NULL) OR ((outcome = 'applied'::public.visual_command_outcome) = (outcome_reason IS NULL))));

CREATE INDEX visual_command_dispatches_pending_idx ON public.visual_command_dispatches USING btree (interview_session_id)
  WHERE (outcome IS NULL);

CREATE INDEX visual_command_dispatches_session_idx ON public.visual_command_dispatches USING btree (interview_session_id, created_at DESC);

CREATE INDEX visual_command_dispatches_unrouted_idx ON public.visual_command_dispatches USING btree (extension_binding_id, created_at)
  WHERE ((routed_at IS NULL) AND (outcome IS NULL));

CREATE TRIGGER visual_command_dispatches_notify_extension_control
  AFTER INSERT OR UPDATE OF outcome ON public.visual_command_dispatches
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_extension_control('interview_session_id');

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."visual_command_dispatches" TO "service_role";

REVOKE ALL ON TABLE "public"."visual_command_dispatches" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."visual_command_dispatches" TO "postgres";

REVOKE ALL ON TABLE "public"."visual_command_dispatches" FROM "anon", "authenticated";
