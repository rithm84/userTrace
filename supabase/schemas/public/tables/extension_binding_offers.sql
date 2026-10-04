CREATE TABLE "public"."extension_binding_offers" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "interview_session_id"          uuid                     NOT NULL,
  "client_user_id"                uuid                     NOT NULL,
  "extension_installation_id"     uuid                     NOT NULL,
  "preview_runtime_allocation_id" uuid                     NOT NULL,
  "protocol_version"              text                     NOT NULL,
  "offer_digest"                  text                     NOT NULL,
  "expires_at"                    timestamp with time zone NOT NULL,
  "consumed_at"                   timestamp with time zone,
  "consumed_by_binding_id"        uuid,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_binding_offers_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "extension_binding_offers_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "extension_binding_offers_consumed_together" CHECK (((consumed_at IS NULL) = (consumed_by_binding_id IS NULL))),
  CONSTRAINT "extension_binding_offers_offer_digest_key" UNIQUE (offer_digest),
  CONSTRAINT "extension_binding_offers_pkey" PRIMARY KEY (id),
  CONSTRAINT "extension_binding_offers_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id),
  CONSTRAINT "extension_binding_offers_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "extension_binding_offers_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id)
);

ALTER TABLE "public"."extension_binding_offers"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX extension_binding_offers_session_idx ON public.extension_binding_offers USING btree (interview_session_id, expires_at DESC);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_binding_offers" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_binding_offers" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_binding_offers" TO "postgres";

REVOKE ALL ON TABLE "public"."extension_binding_offers" FROM "anon", "authenticated";
