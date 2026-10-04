CREATE TABLE "public"."invitations" (
  "id"                      uuid                     NOT NULL,
  "client_id"               uuid                     NOT NULL,
  "outreach_attempt_id"     uuid                     NOT NULL,
  "product_signal_id"       uuid                     NOT NULL,
  "client_user_id"          uuid                     NOT NULL,
  "token_digest"            text                     NOT NULL,
  "bound_provider"          text                     NOT NULL,
  "bound_provider_identity" text                     NOT NULL,
  "expires_at"              timestamp with time zone NOT NULL,
  "claimed_at"              timestamp with time zone,
  "revoked_at"              timestamp with time zone,
  "interview_session_id"    uuid,
  "schema_version"          smallint                 NOT NULL DEFAULT 1,
  "created_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "superseded_by_id"        uuid,
  CONSTRAINT "invitations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "invitations_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "invitations_interview_session_fk" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "invitations_pkey" PRIMARY KEY (id),
  CONSTRAINT "invitations_superseded_by_id_fkey" FOREIGN KEY (superseded_by_id) REFERENCES public.invitations(id),
  CONSTRAINT "invitations_token_digest_key" UNIQUE (token_digest),
  CONSTRAINT "invitations_outreach_attempt_id_fkey" FOREIGN KEY (outreach_attempt_id) REFERENCES public.outreach_attempts(id),
  CONSTRAINT "invitations_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."invitations"
  ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX invitations_one_live_per_signal ON public.invitations USING btree (product_signal_id)
  WHERE ((revoked_at IS NULL) AND (superseded_by_id IS NULL));

CREATE INDEX invitations_signal_idx ON public.invitations USING btree (product_signal_id);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."invitations" TO "service_role";

REVOKE ALL ON TABLE "public"."invitations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."invitations" TO "postgres";

REVOKE ALL ON TABLE "public"."invitations" FROM "anon", "authenticated";
