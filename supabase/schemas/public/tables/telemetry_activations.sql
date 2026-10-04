CREATE TABLE "public"."telemetry_activations" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "revision"                      bigint                   NOT NULL DEFAULT 0,
  "client_user_id"                uuid                     NOT NULL,
  "customer_account_id"           uuid                     NOT NULL,
  "interview_session_id"          uuid                     NOT NULL,
  "preview_environment_id"        uuid                     NOT NULL,
  "preview_runtime_allocation_id" uuid                     NOT NULL,
  "preview_version_id"            uuid,
  "protocol_version"              text                     NOT NULL,
  "source_schema_version"         text                     NOT NULL,
  "policy_version"                text                     NOT NULL,
  "redaction_version"             text                     NOT NULL,
  "annotation_manifest_version"   text,
  "annotation_manifest_digest"    text,
  "semantic_convention_version"   text,
  "limits"                        jsonb                    NOT NULL,
  "document_attachment_id"        uuid,
  "document_nonce"                text,
  "document_origin"               text,
  "sdk_installation_id"           text,
  "attestation_expires_at"        timestamp with time zone,
  "attestation_exchanged_at"      timestamp with time zone,
  "server_stream_id"              uuid,
  "service_instance_id"           uuid,
  "service_key"                   text,
  "preview_entry_token_digest"    text,
  "probe_id"                      uuid,
  "required_hops"                 text[],
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_activations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "telemetry_activations_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id),
  CONSTRAINT "telemetry_activations_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id),
  CONSTRAINT "telemetry_activations_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "telemetry_activations_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_activations_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id),
  CONSTRAINT "telemetry_activations_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id)
);

ALTER TABLE "public"."telemetry_activations"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."telemetry_activations"
  ADD COLUMN "state" public.telemetry_activation_state NOT NULL DEFAULT 'active'::public.telemetry_activation_state;

ALTER TABLE "public"."telemetry_activations"
  ADD COLUMN "kind" public.telemetry_producer_kind NOT NULL;

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_browser_columns" CHECK (((kind <> 'browser'::public.telemetry_producer_kind) OR ((document_attachment_id IS NOT NULL) AND (document_nonce IS
    NOT NULL) AND (document_origin IS NOT NULL) AND (sdk_installation_id IS NOT NULL) AND (attestation_expires_at IS NOT NULL) AND (annotation_manifest_version IS
    NOT NULL) AND (annotation_manifest_digest IS NOT NULL))));

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_preview_environment_fk" FOREIGN KEY (preview_environment_id) REFERENCES public.preview_environments(id) NOT VALID;

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_server_columns" CHECK (((kind <> 'server'::public.telemetry_producer_kind) OR ((server_stream_id IS NOT NULL) AND (service_instance_id IS
    NOT NULL) AND (service_key IS NOT NULL) AND (preview_entry_token_digest IS NOT NULL) AND (semantic_convention_version IS NOT NULL) AND (probe_id IS NOT NULL))));

CREATE UNIQUE INDEX telemetry_activations_document_nonce ON public.telemetry_activations USING btree (client_id, document_nonce)
  WHERE (kind = 'browser'::public.telemetry_producer_kind);

CREATE UNIQUE INDEX telemetry_activations_document ON public.telemetry_activations USING btree (document_attachment_id)
  WHERE (kind = 'browser'::public.telemetry_producer_kind);

CREATE UNIQUE INDEX telemetry_activations_server_stream ON public.telemetry_activations USING btree (server_stream_id)
  WHERE (kind = 'server'::public.telemetry_producer_kind);

CREATE INDEX telemetry_activations_session_idx ON public.telemetry_activations USING btree (interview_session_id, kind, state);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_activations" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_activations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_activations" TO "postgres";

REVOKE ALL ON TABLE "public"."telemetry_activations" FROM "anon", "authenticated";
