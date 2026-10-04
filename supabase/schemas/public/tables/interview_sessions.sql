CREATE TABLE "public"."interview_sessions" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "product_signal_id"             uuid                     NOT NULL,
  "client_user_id"                uuid                     NOT NULL,
  "revision"                      bigint                   NOT NULL DEFAULT 0,
  "machine_version"               smallint                 NOT NULL DEFAULT 1,
  "character_revision"            bigint                   NOT NULL DEFAULT 0,
  "muted"                         boolean                  NOT NULL DEFAULT false,
  "paused"                        boolean                  NOT NULL DEFAULT false,
  "preview_runtime_allocation_id" uuid,
  "closure_reason"                text,
  "closed_at"                     timestamp with time zone,
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "current_preview_route_id"      uuid,
  CONSTRAINT "interview_sessions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "interview_sessions_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "interview_sessions_pkey" PRIMARY KEY (id),
  CONSTRAINT "interview_sessions_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."interview_sessions"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."interview_sessions"
  ADD COLUMN "character_state" public.agent_character_state NOT NULL;

ALTER TABLE "public"."interview_sessions"
  ADD COLUMN "implementation_progress" public.implementation_progress NOT NULL;

ALTER TABLE "public"."interview_sessions"
  ADD COLUMN "phase" public.interview_phase NOT NULL;

ALTER TABLE "public"."interview_sessions"
  ADD COLUMN "state" public.interview_session_state NOT NULL;

CREATE UNIQUE INDEX interview_sessions_one_open ON public.interview_sessions USING btree (product_signal_id)
  WHERE (state <> 'closed'::public.interview_session_state);

CREATE INDEX interview_sessions_participant_idx ON public.interview_sessions USING btree (client_user_id);

CREATE INDEX interview_sessions_signal_idx ON public.interview_sessions USING btree (product_signal_id, state);

CREATE TRIGGER interview_sessions_notify_extension_control
  AFTER UPDATE OF character_revision ON public.interview_sessions
  FOR EACH ROW
  WHEN ((old.character_revision IS DISTINCT FROM new.character_revision))
  EXECUTE FUNCTION utrace.notify_extension_control('id');

CREATE TRIGGER interview_sessions_notify_voice_sideband
  AFTER UPDATE OF implementation_progress ON public.interview_sessions
  FOR EACH ROW
  WHEN ((old.implementation_progress IS DISTINCT FROM new.implementation_progress))
  EXECUTE FUNCTION utrace.notify_voice_sideband('id');

CREATE POLICY "interview_sessions_authorized_select" ON "public"."interview_sessions"
  FOR SELECT
  TO "authenticated"
  USING ((utrace.is_client_member(client_id) OR utrace.is_participant(client_user_id)));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."interview_sessions" TO "service_role";

REVOKE ALL ON TABLE "public"."interview_sessions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."interview_sessions" TO "authenticated";

REVOKE ALL ON TABLE "public"."interview_sessions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."interview_sessions" TO "postgres";

REVOKE ALL ON TABLE "public"."interview_sessions" FROM "anon";
