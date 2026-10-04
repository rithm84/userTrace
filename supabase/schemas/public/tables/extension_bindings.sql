CREATE TABLE "public"."extension_bindings" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "interview_session_id"          uuid                     NOT NULL,
  "extension_installation_id"     uuid                     NOT NULL,
  "preview_runtime_allocation_id" uuid                     NOT NULL,
  "document_attachment_id"        uuid                     NOT NULL,
  "binding_revision"              bigint                   NOT NULL DEFAULT 0,
  "document_origin"               text                     NOT NULL,
  "tab_id"                        integer                  NOT NULL,
  "document_nonce"                text                     NOT NULL,
  "sdk_protocol_version"          text                     NOT NULL,
  "page_proof_digest"             text                     NOT NULL,
  "offer_token_id"                uuid,
  "reason"                        text,
  "bound_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "revoked_at"                    timestamp with time zone,
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "extension_bindings_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "extension_bindings_pkey" PRIMARY KEY (id),
  CONSTRAINT "extension_bindings_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id),
  CONSTRAINT "extension_bindings_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "extension_bindings_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id)
);

ALTER TABLE "public"."extension_bindings"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."extension_bindings"
  ADD COLUMN "status" public.extension_binding_status NOT NULL;

ALTER TABLE "public"."extension_bindings"
  ADD CONSTRAINT "extension_bindings_reason_when_not_bound" CHECK (((status = 'bound'::public.extension_binding_status) = (reason IS NULL)));

CREATE UNIQUE INDEX extension_bindings_offer_consumed_once ON public.extension_bindings USING btree (offer_token_id)
  WHERE (offer_token_id IS NOT NULL);

CREATE UNIQUE INDEX extension_bindings_one_current ON public.extension_bindings USING btree (interview_session_id)
  WHERE (status = 'bound'::public.extension_binding_status);

CREATE INDEX extension_bindings_session_idx ON public.extension_bindings USING btree (interview_session_id, status);

CREATE TRIGGER extension_bindings_notify_extension_control
  AFTER INSERT OR UPDATE ON public.extension_bindings
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_extension_control('interview_session_id');

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_bindings" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_bindings" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_bindings" TO "postgres";

REVOKE ALL ON TABLE "public"."extension_bindings" FROM "anon", "authenticated";
