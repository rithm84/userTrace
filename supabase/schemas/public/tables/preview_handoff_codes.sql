CREATE TABLE "public"."preview_handoff_codes" (
  "code_digest"          text                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "interview_session_id" uuid                     NOT NULL,
  "preview_route_id"     uuid                     NOT NULL,
  "public_preview_host"  text                     NOT NULL,
  "issued_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "expires_at"           timestamp with time zone NOT NULL,
  "consumed_at"          timestamp with time zone,
  CONSTRAINT "preview_handoff_codes_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "preview_handoff_codes_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "preview_handoff_codes_pkey" PRIMARY KEY (code_digest),
  CONSTRAINT "preview_handoff_codes_preview_route_id_fkey" FOREIGN KEY (preview_route_id) REFERENCES public.preview_routes(id)
);

ALTER TABLE "public"."preview_handoff_codes"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX preview_handoff_codes_session_idx ON public.preview_handoff_codes USING btree (interview_session_id);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_handoff_codes" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_handoff_codes" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_handoff_codes" TO "postgres";

REVOKE ALL ON TABLE "public"."preview_handoff_codes" FROM "anon", "authenticated";
