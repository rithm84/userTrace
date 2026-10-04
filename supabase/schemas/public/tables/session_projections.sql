CREATE TABLE "public"."session_projections" (
  "session_id"        uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "client_user_id"    uuid                     NOT NULL,
  "product_signal_id" uuid                     NOT NULL,
  "revision"          bigint                   NOT NULL,
  "projection"        jsonb                    NOT NULL,
  "updated_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "session_projections_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "session_projections_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "session_projections_pkey" PRIMARY KEY (session_id),
  CONSTRAINT "session_projections_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "session_projections_session_id_fkey" FOREIGN KEY (session_id) REFERENCES public.interview_sessions(id)
);

ALTER TABLE "public"."session_projections"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX session_projections_participant_idx ON public.session_projections USING btree (client_user_id);

CREATE POLICY "session_projections_authorized_select" ON "public"."session_projections"
  FOR SELECT
  TO "authenticated"
  USING ((utrace.is_client_member(client_id) OR utrace.is_participant(client_user_id)));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."session_projections" TO "service_role";

REVOKE ALL ON TABLE "public"."session_projections" FROM "authenticated";

GRANT SELECT ON TABLE "public"."session_projections" TO "authenticated";

REVOKE ALL ON TABLE "public"."session_projections" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."session_projections" TO "postgres";

REVOKE ALL ON TABLE "public"."session_projections" FROM "anon";
