CREATE TABLE "public"."telemetry_evidence_windows" (
  "id"                               uuid                     NOT NULL,
  "client_id"                        uuid                     NOT NULL,
  "interview_session_id"             uuid                     NOT NULL,
  "product_signal_id"                uuid                     NOT NULL,
  "activation_id"                    uuid,
  "purpose"                          text                     NOT NULL,
  "authorized_caller"                text                     NOT NULL,
  "domain_identities"                jsonb                    NOT NULL,
  "query_contract"                   jsonb                    NOT NULL,
  "allowed_source_classes"           text[]                   NOT NULL,
  "starts_at"                        timestamp with time zone NOT NULL,
  "accepted_through"                 timestamp with time zone NOT NULL,
  "browser_accepted_through"         bigint                   NOT NULL,
  "server_accepted_through"          bigint                   NOT NULL,
  "completeness"                     jsonb                    NOT NULL,
  "correlation_revision"             bigint                   NOT NULL,
  "revision"                         bigint                   NOT NULL DEFAULT 0,
  "frozen_at"                        timestamp with time zone,
  "control_plane_evidence_window_id" uuid,
  "created_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                   smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_evidence_windows_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id),
  CONSTRAINT "telemetry_evidence_windows_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "telemetry_evidence_windows_control_plane_evidence_window_i_fkey" FOREIGN KEY (control_plane_evidence_window_id) REFERENCES public.evidence_windows(id),
  CONSTRAINT "telemetry_evidence_windows_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id),
  CONSTRAINT "telemetry_evidence_windows_ordered" CHECK ((accepted_through >= starts_at)),
  CONSTRAINT "telemetry_evidence_windows_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_evidence_windows_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id),
  CONSTRAINT "telemetry_evidence_windows_source_classes_present" CHECK ((array_length(allowed_source_classes, 1) >= 1))
);

ALTER TABLE "public"."telemetry_evidence_windows"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD COLUMN "mode" public.evidence_window_mode NOT NULL;

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD CONSTRAINT "telemetry_evidence_windows_frozen_has_time" CHECK (((mode = 'frozen'::public.evidence_window_mode) = (frozen_at IS NOT NULL)));

CREATE INDEX telemetry_evidence_windows_signal_idx ON public.telemetry_evidence_windows USING btree (product_signal_id, mode, created_at DESC);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_evidence_windows" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_evidence_windows" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_evidence_windows" TO "postgres";

REVOKE ALL ON TABLE "public"."telemetry_evidence_windows" FROM "anon", "authenticated";
