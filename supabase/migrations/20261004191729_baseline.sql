SET local check_function_bodies = off;

CREATE SCHEMA "utrace";

CREATE EXTENSION "pg_cron";

CREATE EXTENSION "pgmq";

CREATE SEQUENCE "utrace"."fencing_token_seq" AS bigint INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1 NO CYCLE;

CREATE TABLE "public"."acceptance_criteria_agreements" (
  "id"                           uuid                     NOT NULL,
  "client_id"                    uuid                     NOT NULL,
  "product_signal_id"            uuid                     NOT NULL,
  "interview_session_id"         uuid                     NOT NULL,
  "acceptance_criteria_revision" bigint                   NOT NULL,
  "agreed_scope"                 jsonb                    NOT NULL,
  "criterion_ids"                uuid[]                   NOT NULL,
  "supporting_evidence_ids"      uuid[]                   NOT NULL,
  "context_bundle_id"            uuid                     NOT NULL,
  "recorded_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"               smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "acceptance_criteria_agreement_product_signal_id_acceptance__key" UNIQUE (product_signal_id, acceptance_criteria_revision),
  CONSTRAINT "acceptance_criteria_agreements_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."acceptance_criteria_agreements"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."acceptance_criteria_agreements" FROM "anon", "authenticated";

CREATE TABLE "public"."artifacts" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "artifact_type"             text                     NOT NULL,
  "media_type"                text                     NOT NULL,
  "digest_algorithm"          text                     NOT NULL DEFAULT 'sha256'::text,
  "digest"                    text                     NOT NULL,
  "byte_size"                 bigint                   NOT NULL,
  "retention_class"           text                     NOT NULL,
  "storage_bucket"            text                     NOT NULL,
  "storage_path"              text                     NOT NULL,
  "producer"                  text                     NOT NULL,
  "source_revision"           text,
  "implementation_attempt_id" uuid,
  "environment_lease_id"      uuid,
  "product_signal_id"         uuid,
  "legal_hold"                boolean                  NOT NULL DEFAULT false,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "artifacts_byte_size_nonnegative" CHECK ((byte_size >= 0)),
  CONSTRAINT "artifacts_client_type_digest_key" UNIQUE (client_id, artifact_type, digest),
  CONSTRAINT "artifacts_digest_is_sha256" CHECK ((digest ~ '^[0-9a-f]{64}$'::text)),
  CONSTRAINT "artifacts_pkey" PRIMARY KEY (id),
  CONSTRAINT "artifacts_retention_class_known"
    CHECK ((retention_class = ANY (ARRAY['participant_personal'::text, 'client_evidence'::text, 'operational'::text, 'audit_minimized'::text])))
);

ALTER TABLE "public"."artifacts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."artifacts" FROM "anon", "authenticated";

CREATE TABLE "public"."audit_records" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "subject_id"                      uuid                     NOT NULL,
  "action"                          text                     NOT NULL,
  "outcome"                         text                     NOT NULL,
  "reason_code"                     text,
  "actor_ref"                       text,
  "lifecycle_revision"              bigint,
  "pinned_configuration_release_id" uuid,
  "detail"                          jsonb                    NOT NULL,
  "recorded_at"                     timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "audit_records_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."audit_records"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."audit_records" FROM "anon", "authenticated";

CREATE TABLE "public"."browser_observations" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "activation_id"            uuid                     NOT NULL,
  "browser_stream_id"        uuid                     NOT NULL,
  "ingestion_receipt_id"     uuid                     NOT NULL,
  "observation_id"           uuid                     NOT NULL,
  "observation_type"         text                     NOT NULL,
  "runtime_id"               uuid                     NOT NULL,
  "document_attachment_id"   uuid                     NOT NULL,
  "frame_id"                 text                     NOT NULL,
  "source_sequence"          bigint                   NOT NULL,
  "export_sequence"          bigint                   NOT NULL,
  "occurred_at"              timestamp with time zone NOT NULL,
  "monotonic_offset"         double precision         NOT NULL,
  "route_revision"           bigint                   NOT NULL,
  "visibility_state"         text                     NOT NULL,
  "target_id"                text,
  "target_revision"          bigint,
  "trace_id"                 text,
  "span_id"                  text,
  "browser_request_id"       text,
  "payload"                  jsonb                    NOT NULL,
  "policy_version"           text                     NOT NULL,
  "redaction_version"        text                     NOT NULL,
  "source_schema_version"    text                     NOT NULL,
  "canonical_schema_version" text                     NOT NULL,
  "normalization_run_id"     uuid                     NOT NULL,
  "content_sha256"           text                     NOT NULL,
  "received_at"              timestamp with time zone NOT NULL,
  "accepted_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "browser_observations_pkey" PRIMARY KEY (id),
  CONSTRAINT "browser_observations_span_id_format" CHECK (((span_id IS NULL) OR (span_id ~ '^[0-9a-f]{16}$'::text))),
  CONSTRAINT "browser_observations_trace_id_format" CHECK (((trace_id IS NULL) OR (trace_id ~ '^[0-9a-f]{32}$'::text)))
);

ALTER TABLE "public"."browser_observations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."browser_observations" FROM "anon", "authenticated";

CREATE TABLE "public"."browser_stream_gaps" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "browser_stream_id"     uuid                     NOT NULL,
  "ingestion_receipt_id"  uuid                     NOT NULL,
  "gap_runtime_id"        uuid                     NOT NULL,
  "first_source_sequence" bigint                   NOT NULL,
  "last_source_sequence"  bigint                   NOT NULL,
  "record_count"          integer                  NOT NULL,
  "observation_class"     text                     NOT NULL,
  "reason"                text                     NOT NULL,
  "baseline_follows"      boolean                  NOT NULL,
  "resolved"              boolean                  NOT NULL DEFAULT false,
  "recorded_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "browser_stream_gaps_pkey" PRIMARY KEY (id),
  CONSTRAINT "browser_stream_gaps_range_ordered" CHECK ((last_source_sequence >= first_source_sequence))
);

ALTER TABLE "public"."browser_stream_gaps"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."browser_stream_gaps" FROM "anon", "authenticated";

CREATE TABLE "public"."browser_streams" (
  "id"                               uuid                     NOT NULL,
  "client_id"                        uuid                     NOT NULL,
  "activation_id"                    uuid                     NOT NULL,
  "runtime_id"                       uuid                     NOT NULL,
  "document_attachment_id"           uuid                     NOT NULL,
  "revision"                         bigint                   NOT NULL DEFAULT 0,
  "accepted_through_export_sequence" bigint                   NOT NULL DEFAULT 0,
  "highest_received_export_sequence" bigint                   NOT NULL DEFAULT 0,
  "terminal_export_sequence"         bigint,
  "end_reason"                       text,
  "opened_at"                        timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                   smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "browser_streams_pkey" PRIMARY KEY (id),
  CONSTRAINT "browser_streams_watermarks_ordered" CHECK ((accepted_through_export_sequence <= highest_received_export_sequence))
);

ALTER TABLE "public"."browser_streams"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."browser_streams" FROM "anon", "authenticated";

CREATE TABLE "public"."case_file_amendments" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "case_file_id"   uuid                     NOT NULL,
  "ordinal"        integer                  NOT NULL,
  "reason"         text                     NOT NULL,
  "body"           jsonb                    NOT NULL,
  "actor_ref"      text,
  "recorded_at"    timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "case_file_amendments_case_file_id_ordinal_key" UNIQUE (case_file_id, ordinal),
  CONSTRAINT "case_file_amendments_ordinal_positive" CHECK ((ordinal >= 1)),
  CONSTRAINT "case_file_amendments_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."case_file_amendments"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."case_file_amendments" FROM "anon";

CREATE TABLE "public"."case_files" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "product_signal_id"         uuid                     NOT NULL,
  "revision"                  bigint                   NOT NULL DEFAULT 0,
  "machine_version"           smallint                 NOT NULL DEFAULT 1,
  "core"                      jsonb,
  "core_digest"               text,
  "delivery_id"               uuid,
  "sealed_at"                 timestamp with time zone,
  "published_at"              timestamp with time zone,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "title"                     text,
  "context_revision"          bigint,
  "narrative_source"          text,
  "authenticated_url"         text,
  "blocker_reason"            text,
  "core_canonical"            text,
  "validation_attempt_id"     uuid,
  "implementation_attempt_id" uuid,
  "preview_version_id"        uuid,
  "gate_decision_id"          uuid,
  "evidence_window_id"        uuid,
  "originating_user_ref"      text,
  "customer_account_ref"      text,
  CONSTRAINT "case_files_narrative_source_values" CHECK (((narrative_source IS NULL) OR (narrative_source = ANY (ARRAY['model_drafted'::text, 'evidence_derived'::text])))),
  CONSTRAINT "case_files_pkey" PRIMARY KEY (id),
  CONSTRAINT "case_files_product_signal_id_key" UNIQUE (product_signal_id),
  CONSTRAINT "case_files_sealed_bytes" CHECK (((core_digest IS NULL) = (core_canonical IS NULL)))
);

ALTER TABLE "public"."case_files"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."case_files" FROM "anon";

CREATE TABLE "public"."client_members" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "auth_user_id"   uuid                     NOT NULL,
  "revision"       bigint                   NOT NULL DEFAULT 0,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "client_members_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."client_members"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."client_members" FROM "anon";

CREATE TABLE "public"."client_users" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "utrace_user_id" uuid                     NOT NULL,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "client_users_client_id_utrace_user_id_key" UNIQUE (client_id, utrace_user_id),
  CONSTRAINT "client_users_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."client_users"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."client_users" FROM "anon", "authenticated";

CREATE TABLE "public"."clients" (
  "id"             uuid                     NOT NULL,
  "name"           text                     NOT NULL,
  "product_name"   text                     NOT NULL,
  "revision"       bigint                   NOT NULL DEFAULT 0,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "clients_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."clients"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."clients" FROM "anon";

CREATE TABLE "public"."code_investigation_findings" (
  "id"                uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "product_signal_id" uuid                     NOT NULL,
  "investigation_id"  uuid                     NOT NULL,
  "repository_ref"    text                     NOT NULL,
  "base_revision"     text                     NOT NULL,
  "path"              text                     NOT NULL,
  "first_line"        integer                  NOT NULL,
  "last_line"         integer                  NOT NULL,
  "excerpt_digest"    text                     NOT NULL,
  "note"              text                     NOT NULL,
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  "created_at"        timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "code_investigation_findings_line_range" CHECK (((first_line >= 1) AND (last_line >= first_line))),
  CONSTRAINT "code_investigation_findings_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."code_investigation_findings"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."code_investigation_findings" FROM "anon", "authenticated";

CREATE TABLE "public"."commands" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "command_type"          text                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "target_id"             uuid                     NOT NULL,
  "payload"               jsonb                    NOT NULL,
  "declared_revisions"    jsonb                    NOT NULL,
  "cancellation_scope_id" uuid,
  "provider_ref"          text,
  "queued_at"             timestamp with time zone,
  "dispatching_at"        timestamp with time zone,
  "dispatched_at"         timestamp with time zone,
  "acknowledged_at"       timestamp with time zone,
  "reconciling_at"        timestamp with time zone,
  "terminal_at"           timestamp with time zone,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "commands_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."commands"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."commands" FROM "anon", "authenticated";

CREATE TABLE "public"."configuration_releases" (
  "id"                         uuid                     NOT NULL,
  "client_id"                  uuid                     NOT NULL,
  "release_number"             bigint                   NOT NULL,
  "policy"                     jsonb                    NOT NULL,
  "digest"                     text                     NOT NULL,
  "authorized_by_auth_user_id" uuid                     NOT NULL,
  "schema_version"             smallint                 NOT NULL DEFAULT 1,
  "created_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "configuration_releases_client_id_release_number_key" UNIQUE (client_id, release_number),
  CONSTRAINT "configuration_releases_digest_is_sha256" CHECK ((digest ~ '^sha256:[0-9a-f]{64}$'::text)),
  CONSTRAINT "configuration_releases_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."configuration_releases"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."configuration_releases" FROM "anon";

CREATE TABLE "public"."context_bundle_invalidations" (
  "context_bundle_id"     uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "reason"                text                     NOT NULL,
  "caused_by_evidence_id" uuid,
  "invalidated_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "context_bundle_invalidations_pkey" PRIMARY KEY (context_bundle_id)
);

ALTER TABLE "public"."context_bundle_invalidations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."context_bundle_invalidations" FROM "anon", "authenticated";

CREATE TABLE "public"."context_bundle_items" (
  "id"                uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "context_bundle_id" uuid                     NOT NULL,
  "ordinal"           integer                  NOT NULL,
  "source_kind"       text                     NOT NULL,
  "source_id"         text                     NOT NULL,
  "source_revision"   text                     NOT NULL,
  "included"          boolean                  NOT NULL,
  "omission_reason"   text,
  "weight"            integer                  NOT NULL,
  "body"              jsonb                    NOT NULL,
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  "created_at"        timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "context_bundle_items_context_bundle_id_ordinal_key" UNIQUE (context_bundle_id, ordinal),
  CONSTRAINT "context_bundle_items_omission_reason" CHECK ((included = (omission_reason IS NULL))),
  CONSTRAINT "context_bundle_items_pkey" PRIMARY KEY (id),
  CONSTRAINT "context_bundle_items_weight_positive" CHECK ((weight > 0))
);

ALTER TABLE "public"."context_bundle_items"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."context_bundle_items" FROM "anon", "authenticated";

CREATE TABLE "public"."context_bundles" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "interview_session_id" uuid,
  "context_revision"     bigint                   NOT NULL,
  "cutoff_at"            timestamp with time zone NOT NULL,
  "budget_items"         integer                  NOT NULL,
  "included_items"       integer                  NOT NULL,
  "omitted_items"        integer                  NOT NULL,
  "dependencies"         jsonb                    NOT NULL,
  "digest"               text                     NOT NULL,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "context_bundles_budget_positive" CHECK ((budget_items > 0)),
  CONSTRAINT "context_bundles_counts_nonnegative" CHECK (((included_items >= 0) AND (omitted_items >= 0))),
  CONSTRAINT "context_bundles_pkey" PRIMARY KEY (id),
  CONSTRAINT "context_bundles_within_budget" CHECK ((included_items <= budget_items))
);

ALTER TABLE "public"."context_bundles"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."context_bundles" FROM "anon", "authenticated";

CREATE TABLE "public"."correlation_projections" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "activation_id"            uuid                     NOT NULL,
  "server_activation_id"     uuid,
  "interview_session_id"     uuid                     NOT NULL,
  "trace_id"                 text                     NOT NULL,
  "revision"                 bigint                   NOT NULL DEFAULT 1,
  "browser_stream_id"        uuid,
  "browser_request_id"       text,
  "required_components"      jsonb                    NOT NULL,
  "observed_components"      jsonb                    NOT NULL,
  "gaps"                     jsonb                    NOT NULL,
  "conflicts"                jsonb                    NOT NULL,
  "first_watermark_at"       timestamp with time zone,
  "last_watermark_at"        timestamp with time zone,
  "browser_accepted_through" bigint                   NOT NULL DEFAULT 0,
  "server_accepted_through"  bigint                   NOT NULL DEFAULT 0,
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "correlation_projections_pkey" PRIMARY KEY (id),
  CONSTRAINT "correlation_projections_trace_id_format" CHECK ((trace_id ~ '^[0-9a-f]{32}$'::text))
);

ALTER TABLE "public"."correlation_projections"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."correlation_projections" FROM "anon", "authenticated";

CREATE TABLE "public"."customer_accounts" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "external_account_key" text                     NOT NULL,
  "name"                 text                     NOT NULL,
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "customer_accounts_client_id_external_account_key_key" UNIQUE (client_id, external_account_key),
  CONSTRAINT "customer_accounts_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."customer_accounts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."customer_accounts" FROM "anon";

CREATE TABLE "public"."customer_memberships" (
  "id"                  uuid                     NOT NULL,
  "client_id"           uuid                     NOT NULL,
  "customer_account_id" uuid                     NOT NULL,
  "client_user_id"      uuid                     NOT NULL,
  "schema_version"      smallint                 NOT NULL DEFAULT 1,
  "created_at"          timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "customer_memberships_client_id_customer_account_id_client_u_key" UNIQUE (client_id, customer_account_id, client_user_id),
  CONSTRAINT "customer_memberships_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."customer_memberships"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."customer_memberships" FROM "anon", "authenticated";

CREATE TABLE "public"."deliveries" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "case_file_id"          uuid                     NOT NULL,
  "validation_attempt_id" uuid                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "repository_ref"        text                     NOT NULL,
  "branch_ref"            text,
  "head_commit"           text                     NOT NULL,
  "base_revision"         text                     NOT NULL,
  "pull_request_ref"      text,
  "idempotency_marker"    text                     NOT NULL,
  "blocker_reason"        text,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "command_id"            uuid,
  "base_branch"           text,
  "case_file_url"         text,
  "pull_request_number"   integer,
  "pull_request_node_id"  text,
  "verified_head_sha"     text,
  "preflight"             jsonb,
  "reconciliation"        jsonb,
  CONSTRAINT "deliveries_idempotency_marker_key" UNIQUE (idempotency_marker),
  CONSTRAINT "deliveries_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."deliveries"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."deliveries" FROM "anon";

CREATE TABLE "public"."dependency_holds" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "product_signal_id"               uuid                     NOT NULL,
  "interview_session_id"            uuid,
  "reason"                          text                     NOT NULL,
  "raised_by_evidence_id"           uuid                     NOT NULL,
  "affected_evidence_id"            uuid,
  "semantic_judgement_id"           uuid,
  "blocks_new_implementation_steps" boolean                  NOT NULL,
  "released_at"                     timestamp with time zone,
  "release_reason"                  text,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "dependency_holds_pkey" PRIMARY KEY (id),
  CONSTRAINT "dependency_holds_release_reason" CHECK (((released_at IS NULL) = (release_reason IS NULL)))
);

ALTER TABLE "public"."dependency_holds"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."dependency_holds" FROM "anon", "authenticated";

CREATE TABLE "public"."environment_leases" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "implementation_attempt_id" uuid                     NOT NULL,
  "revision"                  bigint                   NOT NULL DEFAULT 0,
  "machine_version"           smallint                 NOT NULL DEFAULT 1,
  "fencing_token"             bigint                   NOT NULL,
  "workspace_ref"             text,
  "expires_at"                timestamp with time zone NOT NULL,
  "heartbeat_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "released_at"               timestamp with time zone,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "environment_leases_implementation_attempt_id_fencing_token_key" UNIQUE (implementation_attempt_id, fencing_token),
  CONSTRAINT "environment_leases_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."environment_leases"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."environment_leases" FROM "anon", "authenticated";

CREATE TABLE "public"."events" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "stream_id"      uuid                     NOT NULL,
  "sequence"       bigint                   NOT NULL,
  "event_type"     text                     NOT NULL,
  "payload"        jsonb                    NOT NULL,
  "actor_ref"      text,
  "causation_id"   uuid,
  "correlation_id" uuid,
  "occurred_at"    timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "events_pkey" PRIMARY KEY (id),
  CONSTRAINT "events_sequence_positive" CHECK ((sequence > 0))
);

ALTER TABLE "public"."events"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."events" FROM "anon", "authenticated";

CREATE TABLE "public"."evidence_records" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "interview_session_id" uuid,
  "source_ref"           text                     NOT NULL,
  "context_revision"     bigint                   NOT NULL,
  "artifact_ref"         jsonb,
  "body"                 jsonb                    NOT NULL,
  "supersedes_id"        uuid,
  "recorded_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "evidence_records_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."evidence_records"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."evidence_records" FROM "anon", "authenticated";

CREATE TABLE "public"."evidence_windows" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "interview_session_id" uuid,
  "window_kind"          text                     NOT NULL,
  "starts_at"            timestamp with time zone NOT NULL,
  "ends_at"              timestamp with time zone NOT NULL,
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "evidence_windows_ordered" CHECK ((ends_at >= starts_at)),
  CONSTRAINT "evidence_windows_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."evidence_windows"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."evidence_windows" FROM "anon", "authenticated";

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
  CONSTRAINT "extension_binding_offers_consumed_together" CHECK (((consumed_at IS NULL) = (consumed_by_binding_id IS NULL))),
  CONSTRAINT "extension_binding_offers_offer_digest_key" UNIQUE (offer_digest),
  CONSTRAINT "extension_binding_offers_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."extension_binding_offers"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."extension_binding_offers" FROM "anon", "authenticated";

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
  CONSTRAINT "extension_bindings_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."extension_bindings"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."extension_bindings" FROM "anon", "authenticated";

CREATE TABLE "public"."extension_control_frames" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid,
  "extension_installation_id" uuid                     NOT NULL,
  "interview_session_id"      uuid,
  "sequence"                  bigint                   NOT NULL,
  "kind"                      text                     NOT NULL,
  "payload"                   jsonb                    NOT NULL,
  "sent_at"                   timestamp with time zone NOT NULL DEFAULT now(),
  "acknowledged_at"           timestamp with time zone,
  "superseded_at"             timestamp with time zone,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_control_frames_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."extension_control_frames"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."extension_control_frames" FROM "anon", "authenticated";

CREATE TABLE "public"."extension_control_streams" (
  "extension_installation_id" uuid                     NOT NULL,
  "last_sequence"             bigint                   NOT NULL DEFAULT 0,
  "acknowledged_sequence"     bigint                   NOT NULL DEFAULT 0,
  "connected_at"              timestamp with time zone,
  "disconnected_at"           timestamp with time zone,
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_control_streams_ordered" CHECK ((acknowledged_sequence <= last_sequence)),
  CONSTRAINT "extension_control_streams_pkey" PRIMARY KEY (extension_installation_id)
);

ALTER TABLE "public"."extension_control_streams"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."extension_control_streams" FROM "anon", "authenticated";

CREATE TABLE "public"."extension_installations" (
  "id"                          uuid                     NOT NULL,
  "utrace_user_id"              uuid                     NOT NULL,
  "device_public_key_spki_b64u" text                     NOT NULL,
  "extension_version"           text                     NOT NULL,
  "supported_protocol_versions" text[]                   NOT NULL,
  "negotiated_protocol_version" text                     NOT NULL,
  "revision"                    bigint                   NOT NULL DEFAULT 0,
  "registered_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "revoked_at"                  timestamp with time zone,
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "extension_installations_device_public_key_spki_b64u_key" UNIQUE (device_public_key_spki_b64u),
  CONSTRAINT "extension_installations_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."extension_installations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."extension_installations" FROM "anon";

CREATE TABLE "public"."extension_link_codes" (
  "id"                          uuid                     NOT NULL,
  "code_digest"                 text                     NOT NULL,
  "utrace_user_id"              uuid                     NOT NULL,
  "redirect_uri"                text                     NOT NULL,
  "extension_version"           text                     NOT NULL,
  "protocol_version"            text                     NOT NULL,
  "expires_at"                  timestamp with time zone NOT NULL,
  "consumed_at"                 timestamp with time zone,
  "consumed_by_installation_id" uuid,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_link_codes_code_digest_key" UNIQUE (code_digest),
  CONSTRAINT "extension_link_codes_consumed_together" CHECK (((consumed_at IS NULL) = (consumed_by_installation_id IS NULL))),
  CONSTRAINT "extension_link_codes_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."extension_link_codes"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."extension_link_codes" FROM "anon", "authenticated";

CREATE TABLE "public"."extension_possession_proofs" (
  "jti"                         uuid                     NOT NULL,
  "extension_installation_id"   uuid,
  "device_public_key_spki_b64u" text                     NOT NULL,
  "purpose"                     text                     NOT NULL,
  "audience"                    text                     NOT NULL,
  "issued_at"                   timestamp with time zone NOT NULL,
  "accepted_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_possession_proofs_pkey" PRIMARY KEY (jti)
);

ALTER TABLE "public"."extension_possession_proofs"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."extension_possession_proofs" FROM "anon", "authenticated";

CREATE TABLE "public"."extension_preferences" (
  "utrace_user_id"          uuid                     NOT NULL,
  "preferences_revision"    bigint                   NOT NULL DEFAULT 0,
  "visual_guidance_enabled" boolean                  NOT NULL DEFAULT true,
  "agent_cursor_enabled"    boolean                  NOT NULL DEFAULT true,
  "updated_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"          smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_preferences_pkey" PRIMARY KEY (utrace_user_id)
);

ALTER TABLE "public"."extension_preferences"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."extension_preferences" FROM "anon";

CREATE TABLE "public"."flow_classification_records" (
  "id"                      uuid                     NOT NULL,
  "client_id"               uuid                     NOT NULL,
  "product_signal_id"       uuid                     NOT NULL,
  "interview_session_id"    uuid,
  "classification_revision" bigint                   NOT NULL,
  "retained"                boolean                  NOT NULL,
  "rationale"               text                     NOT NULL,
  "supporting_evidence_ids" uuid[]                   NOT NULL,
  "context_bundle_id"       uuid                     NOT NULL,
  "semantic_judgement_id"   uuid,
  "recorded_at"             timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"          smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "flow_classification_records_pkey" PRIMARY KEY (id),
  CONSTRAINT "flow_classification_records_product_signal_id_classificatio_key" UNIQUE (product_signal_id, classification_revision)
);

ALTER TABLE "public"."flow_classification_records"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."flow_classification_records" FROM "anon", "authenticated";

CREATE TABLE "public"."gate_decisions" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "product_signal_id"               uuid                     NOT NULL,
  "blocker_reason"                  text,
  "classification_revision"         bigint                   NOT NULL,
  "acceptance_criteria_revision"    bigint                   NOT NULL,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "evidence_window_id"              uuid                     NOT NULL,
  "approved_scope"                  jsonb                    NOT NULL,
  "required_checks"                 jsonb                    NOT NULL,
  "budgets"                         jsonb                    NOT NULL,
  "cited_evidence"                  jsonb                    NOT NULL,
  "decided_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "gate_decisions_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."gate_decisions"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."gate_decisions" FROM "anon";

CREATE TABLE "public"."implementation_attempts" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "gate_decision_id"      uuid                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "repository_ref"        text                     NOT NULL,
  "base_revision"         text                     NOT NULL,
  "consumed_budgets"      jsonb                    NOT NULL,
  "checkpoint"            jsonb,
  "outcome_reason"        text,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "progress"              text                     NOT NULL DEFAULT 'not_started'::text,
  "workspace_ref"         text,
  "workspace_snapshot_id" text,
  CONSTRAINT "implementation_attempts_gate_decision_id_key" UNIQUE (gate_decision_id),
  CONSTRAINT "implementation_attempts_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."implementation_attempts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."implementation_attempts" FROM "anon";

CREATE TABLE "public"."implementation_check_runs" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "implementation_attempt_id" uuid                     NOT NULL,
  "environment_lease_id"      uuid                     NOT NULL,
  "phase"                     text                     NOT NULL,
  "check_name"                text                     NOT NULL,
  "command"                   text                     NOT NULL,
  "exit_code"                 integer                  NOT NULL,
  "duration_seconds"          double precision         NOT NULL,
  "log_artifact_id"           uuid,
  "cycle_index"               integer                  NOT NULL,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "implementation_check_runs_phase_known" CHECK ((phase = ANY (ARRAY['baseline'::text, 'candidate'::text]))),
  CONSTRAINT "implementation_check_runs_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."implementation_check_runs"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."implementation_check_runs" FROM "anon";

CREATE TABLE "public"."implementation_scope_checks" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "implementation_attempt_id" uuid                     NOT NULL,
  "cycle_index"               integer                  NOT NULL,
  "passed"                    boolean                  NOT NULL,
  "scope_digest"              text                     NOT NULL,
  "changed_paths"             text[]                   NOT NULL,
  "violations"                jsonb                    NOT NULL,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "implementation_scope_checks_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."implementation_scope_checks"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."implementation_scope_checks" FROM "anon";

CREATE TABLE "public"."inbound_direct_messages" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid,
  "provider"                 text                     NOT NULL,
  "provider_message_id"      text                     NOT NULL,
  "sender_provider_identity" text                     NOT NULL,
  "content"                  text                     NOT NULL,
  "route_kind"               text                     NOT NULL,
  "outreach_attempt_id"      uuid,
  "interview_session_id"     uuid,
  "occurred_at"              timestamp with time zone NOT NULL,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "inbound_direct_messages_pkey" PRIMARY KEY (id),
  CONSTRAINT "inbound_direct_messages_provider_provider_message_id_key" UNIQUE (PROVIDER, provider_message_id)
);

ALTER TABLE "public"."inbound_direct_messages"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."inbound_direct_messages" FROM "anon", "authenticated";

CREATE TABLE "public"."inbox" (
  "message_id"     uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "source"         text                     NOT NULL,
  "message_kind"   text                     NOT NULL,
  "command_id"     uuid,
  "payload"        jsonb                    NOT NULL,
  "received_at"    timestamp with time zone NOT NULL DEFAULT now(),
  "processed_at"   timestamp with time zone,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "inbox_pkey" PRIMARY KEY (message_id)
);

ALTER TABLE "public"."inbox"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."inbox" FROM "anon", "authenticated";

CREATE TABLE "public"."ingestion_failures" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "activation_id"         uuid,
  "ingestion_receipt_id"  uuid,
  "source_stream_id"      uuid,
  "code"                  text                     NOT NULL,
  "retry_classification"  text                     NOT NULL,
  "readiness_consequence" text                     NOT NULL,
  "affected_watermark"    bigint,
  "affected_trace_id"     text,
  "detail"                jsonb                    NOT NULL,
  "occurred_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "ingestion_failures_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."ingestion_failures"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."ingestion_failures" FROM "anon", "authenticated";

CREATE TABLE "public"."ingestion_receipt_stages" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "ingestion_receipt_id" uuid                     NOT NULL,
  "normalization_run_id" uuid,
  "entered_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "ingestion_receipt_stages_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."ingestion_receipt_stages"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."ingestion_receipt_stages" FROM "anon", "authenticated";

CREATE TABLE "public"."ingestion_receipts" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "activation_id"            uuid                     NOT NULL,
  "telemetry_credential_id"  uuid                     NOT NULL,
  "source_stream_id"         uuid                     NOT NULL,
  "batch_id"                 uuid                     NOT NULL,
  "batch_sequence"           bigint,
  "first_export_sequence"    bigint,
  "last_export_sequence"     bigint,
  "received_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "byte_count"               bigint                   NOT NULL,
  "record_count"             integer                  NOT NULL,
  "content_sha256"           text                     NOT NULL,
  "source_schema_version"    text                     NOT NULL,
  "canonical_schema_version" text                     NOT NULL,
  "policy_version"           text                     NOT NULL,
  "failure_code"             text,
  "failure_reason"           text,
  "retry_classification"     text,
  "raw_artifact_id"          uuid,
  "revision"                 bigint                   NOT NULL DEFAULT 0,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "ingestion_receipts_counts_nonnegative" CHECK (((byte_count >= 0) AND (record_count >= 0))),
  CONSTRAINT "ingestion_receipts_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."ingestion_receipts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."ingestion_receipts" FROM "anon", "authenticated";

CREATE TABLE "public"."intake_author_attributions" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "provider"                 text                     NOT NULL,
  "author_provider_identity" text                     NOT NULL,
  "client_user_id"           uuid                     NOT NULL,
  "customer_account_id"      uuid                     NOT NULL,
  "reason"                   text                     NOT NULL,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "intake_author_attributions_client_id_provider_author_provid_key" UNIQUE (client_id, PROVIDER, author_provider_identity),
  CONSTRAINT "intake_author_attributions_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."intake_author_attributions"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."intake_author_attributions" FROM "anon", "authenticated";

CREATE TABLE "public"."intake_batches" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "provider"                 text                     NOT NULL,
  "channel_ref"              text                     NOT NULL,
  "author_provider_identity" text                     NOT NULL,
  "seal_job_id"              uuid,
  "intake_item_id"           uuid,
  "message_count"            integer                  NOT NULL DEFAULT 0,
  "opened_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "seal_not_before"          timestamp with time zone NOT NULL,
  "seal_deadline_at"         timestamp with time zone NOT NULL,
  "sealed_at"                timestamp with time zone,
  "revision"                 bigint                   NOT NULL DEFAULT 0,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "intake_batches_pkey" PRIMARY KEY (id),
  CONSTRAINT "intake_batches_seal_window_ordered" CHECK ((seal_deadline_at >= seal_not_before))
);

ALTER TABLE "public"."intake_batches"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."intake_batches" FROM "anon", "authenticated";

CREATE TABLE "public"."intake_items" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "revision"                 bigint                   NOT NULL DEFAULT 0,
  "machine_version"          smallint                 NOT NULL DEFAULT 1,
  "source_provider"          text                     NOT NULL,
  "source_channel_ref"       text                     NOT NULL,
  "source_batch_digest"      text                     NOT NULL,
  "author_provider_identity" text                     NOT NULL,
  "client_user_id"           uuid,
  "customer_account_id"      uuid,
  "payload"                  jsonb                    NOT NULL,
  "product_signal_id"        uuid,
  "blocked_reason"           text,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "intake_items_client_id_source_batch_digest_key" UNIQUE (client_id, source_batch_digest),
  CONSTRAINT "intake_items_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."intake_items"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."intake_items" FROM "anon", "authenticated";

CREATE TABLE "public"."intake_messages" (
  "id"                           uuid                     NOT NULL,
  "client_id"                    uuid                     NOT NULL,
  "intake_batch_id"              uuid                     NOT NULL,
  "provider"                     text                     NOT NULL,
  "channel_ref"                  text                     NOT NULL,
  "provider_message_id"          text                     NOT NULL,
  "author_provider_identity"     text                     NOT NULL,
  "author_is_bot"                boolean                  NOT NULL,
  "ordinal"                      integer                  NOT NULL,
  "content"                      text                     NOT NULL,
  "reply_to_provider_message_id" text,
  "thread_ref"                   text,
  "occurred_at"                  timestamp with time zone NOT NULL,
  "edited_at"                    timestamp with time zone,
  "schema_version"               smallint                 NOT NULL DEFAULT 1,
  "created_at"                   timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "intake_messages_client_id_provider_provider_message_id_key" UNIQUE (client_id, PROVIDER, provider_message_id),
  CONSTRAINT "intake_messages_ordinal_positive" CHECK ((ordinal > 0)),
  CONSTRAINT "intake_messages_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."intake_messages"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."intake_messages" FROM "anon", "authenticated";

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
  CONSTRAINT "interview_sessions_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."interview_sessions"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."interview_sessions" FROM "anon";

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
  CONSTRAINT "invitations_pkey" PRIMARY KEY (id),
  CONSTRAINT "invitations_token_digest_key" UNIQUE (token_digest)
);

ALTER TABLE "public"."invitations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."invitations" FROM "anon", "authenticated";

CREATE TABLE "public"."job_runs" (
  "id"               uuid                     NOT NULL,
  "client_id"        uuid                     NOT NULL,
  "job_id"           uuid                     NOT NULL,
  "revision"         bigint                   NOT NULL DEFAULT 0,
  "machine_version"  smallint                 NOT NULL DEFAULT 1,
  "fencing_token"    bigint                   NOT NULL,
  "worker_id"        text                     NOT NULL,
  "lease_expires_at" timestamp with time zone NOT NULL,
  "heartbeat_at"     timestamp with time zone NOT NULL DEFAULT now(),
  "started_at"       timestamp with time zone NOT NULL DEFAULT now(),
  "finished_at"      timestamp with time zone,
  "failure_category" text,
  "schema_version"   smallint                 NOT NULL DEFAULT 1,
  "created_at"       timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"       timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "job_runs_job_id_fencing_token_key" UNIQUE (job_id, fencing_token),
  CONSTRAINT "job_runs_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."job_runs"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."job_runs" FROM "anon", "authenticated";

CREATE TABLE "public"."jobs" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "job_type"              text                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "input"                 jsonb                    NOT NULL,
  "deadline_at"           timestamp with time zone NOT NULL,
  "timeout_seconds"       integer                  NOT NULL,
  "max_runs"              integer                  NOT NULL,
  "run_count"             integer                  NOT NULL DEFAULT 0,
  "resource_budget"       jsonb                    NOT NULL,
  "retryable_categories"  text[]                   NOT NULL,
  "backoff_base_seconds"  integer                  NOT NULL,
  "backoff_max_seconds"   integer                  NOT NULL,
  "concurrency_key"       text,
  "cancellation_scope_id" uuid,
  "not_before"            timestamp with time zone NOT NULL DEFAULT now(),
  "last_failure_category" text,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "jobs_backoff_ordered" CHECK (((backoff_base_seconds > 0) AND (backoff_max_seconds >= backoff_base_seconds))),
  CONSTRAINT "jobs_max_runs_positive" CHECK ((max_runs > 0)),
  CONSTRAINT "jobs_pkey" PRIMARY KEY (id),
  CONSTRAINT "jobs_run_count_within_max" CHECK (((run_count >= 0) AND (run_count <= max_runs))),
  CONSTRAINT "jobs_timeout_positive" CHECK ((timeout_seconds > 0))
);

ALTER TABLE "public"."jobs"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."jobs" FROM "anon", "authenticated";

CREATE TABLE "public"."offer_budgets" (
  "id"                         uuid                     NOT NULL,
  "client_id"                  uuid                     NOT NULL,
  "currency"                   text                     NOT NULL,
  "sequence_number"            bigint                   NOT NULL,
  "authorized_minor_units"     bigint                   NOT NULL,
  "authorized_by_auth_user_id" uuid                     NOT NULL,
  "schema_version"             smallint                 NOT NULL DEFAULT 1,
  "created_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_budgets_authorized_nonnegative" CHECK ((authorized_minor_units >= 0)),
  CONSTRAINT "offer_budgets_client_id_currency_sequence_number_key" UNIQUE (client_id, currency, sequence_number),
  CONSTRAINT "offer_budgets_currency_is_iso" CHECK ((currency = upper(currency))),
  CONSTRAINT "offer_budgets_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."offer_budgets"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."offer_budgets" FROM "anon";

CREATE TABLE "public"."offer_entitlements" (
  "id"                     uuid                     NOT NULL,
  "client_id"              uuid                     NOT NULL,
  "outreach_offer_id"      uuid                     NOT NULL,
  "product_signal_id"      uuid                     NOT NULL,
  "customer_account_id"    uuid                     NOT NULL,
  "currency"               text                     NOT NULL,
  "amount_minor_units"     bigint                   NOT NULL,
  "participation_evidence" jsonb                    NOT NULL,
  "stop_reason"            text                     NOT NULL,
  "earned_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"         smallint                 NOT NULL DEFAULT 1,
  "created_at"             timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_entitlements_outreach_offer_id_key" UNIQUE (outreach_offer_id),
  CONSTRAINT "offer_entitlements_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."offer_entitlements"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."offer_entitlements" FROM "anon";

CREATE TABLE "public"."offer_invoice_applications" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "offer_issuance_operation_id" uuid                     NOT NULL,
  "billing_customer_ref"        text                     NOT NULL,
  "invoice_ref"                 text                     NOT NULL,
  "currency"                    text                     NOT NULL,
  "invoice_total_minor_units"   bigint                   NOT NULL,
  "applied_minor_units"         bigint                   NOT NULL,
  "amount_due_minor_units"      bigint                   NOT NULL,
  "invoice_status"              text                     NOT NULL,
  "reconciled_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_invoice_applications_applied_nonnegative" CHECK ((applied_minor_units >= 0)),
  CONSTRAINT "offer_invoice_applications_offer_issuance_operation_id_invo_key" UNIQUE (offer_issuance_operation_id, invoice_ref),
  CONSTRAINT "offer_invoice_applications_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."offer_invoice_applications"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."offer_invoice_applications" FROM "anon";

CREATE TABLE "public"."offer_issuance_operations" (
  "id"                         uuid                     NOT NULL,
  "client_id"                  uuid                     NOT NULL,
  "offer_entitlement_id"       uuid                     NOT NULL,
  "command_id"                 uuid                     NOT NULL,
  "revision"                   bigint                   NOT NULL DEFAULT 0,
  "machine_version"            smallint                 NOT NULL DEFAULT 1,
  "currency"                   text                     NOT NULL,
  "amount_minor_units"         bigint                   NOT NULL,
  "provider_transaction_ref"   text,
  "blocked_reason"             text,
  "reconciled_at"              timestamp with time zone,
  "schema_version"             smallint                 NOT NULL DEFAULT 1,
  "created_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "idempotency_key"            text                     NOT NULL,
  "billing_account_ref"        text                     NOT NULL,
  "billing_customer_ref"       text                     NOT NULL,
  "verified_at"                timestamp with time zone,
  "ending_balance_minor_units" bigint,
  "dispatch_count"             integer                  NOT NULL DEFAULT 0,
  CONSTRAINT "offer_issuance_operations_offer_entitlement_id_key" UNIQUE (offer_entitlement_id),
  CONSTRAINT "offer_issuance_operations_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."offer_issuance_operations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."offer_issuance_operations" FROM "anon";

CREATE TABLE "public"."offer_ledger_entries" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "currency"                    text                     NOT NULL,
  "delta_minor_units"           bigint                   NOT NULL,
  "outreach_offer_id"           uuid,
  "offer_entitlement_id"        uuid,
  "offer_issuance_operation_id" uuid,
  "causal_evidence"             jsonb                    NOT NULL,
  "recorded_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_ledger_entries_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."offer_ledger_entries"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."offer_ledger_entries" FROM "anon", "authenticated";

CREATE TABLE "public"."offer_reservations" (
  "id"                 uuid                     NOT NULL,
  "client_id"          uuid                     NOT NULL,
  "outreach_offer_id"  uuid                     NOT NULL,
  "currency"           text                     NOT NULL,
  "amount_minor_units" bigint                   NOT NULL,
  "released_at"        timestamp with time zone,
  "converted_at"       timestamp with time zone,
  "schema_version"     smallint                 NOT NULL DEFAULT 1,
  "created_at"         timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"         timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "offer_reservations_outreach_offer_id_key" UNIQUE (outreach_offer_id),
  CONSTRAINT "offer_reservations_pkey" PRIMARY KEY (id),
  CONSTRAINT "offer_reservations_single_disposition" CHECK (((released_at IS NULL) OR (converted_at IS NULL)))
);

ALTER TABLE "public"."offer_reservations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."offer_reservations" FROM "anon";

CREATE TABLE "public"."outbox" (
  "id"             uuid                     NOT NULL,
  "client_id"      uuid                     NOT NULL,
  "job_id"         uuid,
  "command_id"     uuid,
  "available_at"   timestamp with time zone NOT NULL DEFAULT now(),
  "enqueued_at"    timestamp with time zone,
  "dispatched_at"  timestamp with time zone,
  "attempts"       integer                  NOT NULL DEFAULT 0,
  "last_error"     text,
  "schema_version" smallint                 NOT NULL DEFAULT 1,
  "created_at"     timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"     timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "outbox_exactly_one_intent" CHECK ((num_nonnulls(job_id, command_id) = 1)),
  CONSTRAINT "outbox_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."outbox"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."outbox" FROM "anon", "authenticated";

CREATE TABLE "public"."outreach_attempts" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "product_signal_id"     uuid                     NOT NULL,
  "client_user_id"        uuid                     NOT NULL,
  "revision"              bigint                   NOT NULL DEFAULT 0,
  "machine_version"       smallint                 NOT NULL DEFAULT 1,
  "outreach_offer_id"     uuid,
  "delivery_provider_ref" text,
  "scheduled_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "dispatched_at"         timestamp with time zone,
  "responded_at"          timestamp with time zone,
  "expires_at"            timestamp with time zone NOT NULL,
  "reason"                text,
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  "created_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"            timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "outreach_attempts_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."outreach_attempts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."outreach_attempts" FROM "anon";

CREATE TABLE "public"."outreach_deliveries" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "outreach_attempt_id"         uuid                     NOT NULL,
  "command_id"                  uuid                     NOT NULL,
  "provider"                    text                     NOT NULL,
  "recipient_provider_identity" text                     NOT NULL,
  "provider_message_ref"        text,
  "failure_category"            text,
  "detail"                      jsonb                    NOT NULL,
  "delivered_at"                timestamp with time zone,
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  "created_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "outreach_deliveries_command_id_key" UNIQUE (command_id),
  CONSTRAINT "outreach_deliveries_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."outreach_deliveries"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."outreach_deliveries" FROM "anon", "authenticated";

CREATE TABLE "public"."outreach_offers" (
  "id"                       uuid                     NOT NULL,
  "client_id"                uuid                     NOT NULL,
  "product_signal_id"        uuid                     NOT NULL,
  "customer_account_id"      uuid                     NOT NULL,
  "revision"                 bigint                   NOT NULL DEFAULT 0,
  "machine_version"          smallint                 NOT NULL DEFAULT 1,
  "currency"                 text                     NOT NULL,
  "amount_minor_units"       bigint                   NOT NULL,
  "billing_account_ref"      text                     NOT NULL,
  "billing_customer_ref"     text                     NOT NULL,
  "billing_mapping_revision" bigint                   NOT NULL,
  "completion_terms"         jsonb                    NOT NULL,
  "schema_version"           smallint                 NOT NULL DEFAULT 1,
  "created_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"               timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "outreach_offers_amount_positive" CHECK ((amount_minor_units > 0)),
  CONSTRAINT "outreach_offers_pkey" PRIMARY KEY (id),
  CONSTRAINT "outreach_offers_product_signal_id_key" UNIQUE (product_signal_id)
);

ALTER TABLE "public"."outreach_offers"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."outreach_offers" FROM "anon";

CREATE TABLE "public"."participant_preflight_records" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "interview_session_id"          uuid                     NOT NULL,
  "client_user_id"                uuid                     NOT NULL,
  "browser_name"                  text                     NOT NULL,
  "browser_major_version"         integer                  NOT NULL,
  "browser_supported"             boolean                  NOT NULL,
  "extension_installed"           boolean                  NOT NULL,
  "extension_installation_id"     uuid,
  "account_linked"                boolean                  NOT NULL,
  "host_permission_granted"       boolean                  NOT NULL,
  "microphone_permission_granted" boolean                  NOT NULL,
  "control_plane_reachable"       boolean                  NOT NULL,
  "reported_protocol_version"     text                     NOT NULL,
  "protocol_supported"            boolean                  NOT NULL,
  "passed"                        boolean                  NOT NULL,
  "failed_checks"                 text[]                   NOT NULL,
  "recorded_at"                   timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "participant_preflight_failed_checks_match" CHECK ((passed = (cardinality(failed_checks) = 0))),
  CONSTRAINT "participant_preflight_records_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."participant_preflight_records"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."participant_preflight_records" FROM "anon", "authenticated";

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
  "expires_at"                timestamp with time zone NOT NULL,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "prepared_workspaces_check1" CHECK (((state <> 'claimed'::text) OR ((implementation_attempt_id IS NOT NULL) AND (lease_fencing_token IS NOT NULL)))),
  CONSTRAINT "prepared_workspaces_check" CHECK (((state <> 'ready'::text) OR (baseline_checks IS NOT NULL))),
  CONSTRAINT "prepared_workspaces_pkey" PRIMARY KEY (id),
  CONSTRAINT "prepared_workspaces_product_signal_id_key" UNIQUE (product_signal_id),
  CONSTRAINT "prepared_workspaces_sandbox_name_key" UNIQUE (sandbox_name),
  CONSTRAINT "prepared_workspaces_state_check" CHECK ((state = ANY (ARRAY['preparing'::text, 'ready'::text, 'failed'::text, 'claimed'::text, 'released'::text])))
);

ALTER TABLE "public"."prepared_workspaces"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."prepared_workspaces" FROM "anon", "authenticated";

CREATE TABLE "public"."preview_environments" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "preview_runtime_template_id"     uuid                     NOT NULL,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "preview_environments_client_id_preview_runtime_template_id_key" UNIQUE (client_id, preview_runtime_template_id),
  CONSTRAINT "preview_environments_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."preview_environments"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."preview_environments" FROM "anon", "authenticated";

CREATE TABLE "public"."preview_handoff_codes" (
  "code_digest"          text                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "interview_session_id" uuid                     NOT NULL,
  "preview_route_id"     uuid                     NOT NULL,
  "public_preview_host"  text                     NOT NULL,
  "issued_at"            timestamp with time zone NOT NULL DEFAULT now(),
  "expires_at"           timestamp with time zone NOT NULL,
  "consumed_at"          timestamp with time zone,
  CONSTRAINT "preview_handoff_codes_pkey" PRIMARY KEY (code_digest)
);

ALTER TABLE "public"."preview_handoff_codes"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."preview_handoff_codes" FROM "anon", "authenticated";

CREATE TABLE "public"."preview_readiness_results" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "preview_runtime_allocation_id" uuid                     NOT NULL,
  "preview_route_id"              uuid,
  "preview_version_id"            uuid,
  "route_revision"                integer,
  "probe"                         text                     NOT NULL,
  "detail"                        jsonb                    NOT NULL,
  "observed_at"                   timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "preview_readiness_results_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."preview_readiness_results"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."preview_readiness_results" FROM "anon", "authenticated";

CREATE TABLE "public"."preview_routes" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "product_signal_id"             uuid                     NOT NULL,
  "interview_session_id"          uuid                     NOT NULL,
  "preview_runtime_allocation_id" uuid                     NOT NULL,
  "preview_version_id"            uuid,
  "public_preview_host"           text                     NOT NULL,
  "route_revision"                integer                  NOT NULL,
  "upstream_port_url"             text                     NOT NULL,
  "application_port"              integer                  NOT NULL,
  "route_context_signing_secret"  text                     NOT NULL,
  "expires_at"                    timestamp with time zone NOT NULL,
  "committed_at"                  timestamp with time zone NOT NULL DEFAULT now(),
  "superseded_at"                 timestamp with time zone,
  "revoked_at"                    timestamp with time zone,
  "revocation_reason"             text,
  "revision"                      bigint                   NOT NULL DEFAULT 0,
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "preview_routes_pkey" PRIMARY KEY (id),
  CONSTRAINT "preview_routes_port_range" CHECK (((application_port >= 1) AND (application_port <= 65535))),
  CONSTRAINT "preview_routes_public_preview_host_route_revision_key" UNIQUE (public_preview_host, route_revision),
  CONSTRAINT "preview_routes_revision_positive" CHECK ((route_revision >= 1))
);

ALTER TABLE "public"."preview_routes"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."preview_routes" FROM "anon", "authenticated";

CREATE TABLE "public"."preview_runtime_allocations" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "product_signal_id"               uuid,
  "revision"                        bigint                   NOT NULL DEFAULT 0,
  "machine_version"                 smallint                 NOT NULL DEFAULT 1,
  "interview_session_id"            uuid,
  "preview_version_id"              uuid,
  "seed_manifest_digest"            text,
  "runtime_ref"                     text,
  "origin_host"                     text,
  "released_at"                     timestamp with time zone,
  "terminated_at"                   timestamp with time zone,
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "preview_runtime_template_id"     uuid,
  "pinned_configuration_release_id" uuid,
  "image_digest"                    text,
  "recipe_digest"                   text,
  "fixture_clock"                   timestamp with time zone,
  "authenticated_identity"          text,
  "sdk_versions"                    jsonb,
  "application_port"                integer,
  "route_revision"                  integer,
  "handoff_secret"                  text,
  "claimed_at"                      timestamp with time zone,
  "evidence"                        jsonb,
  "fencing_token"                   bigint                   NOT NULL,
  "route_context_signing_secret"    text,
  "preview_entry_token"             text,
  "runtime_expires_at"              timestamp with time zone,
  CONSTRAINT "preview_runtime_allocations_origin_host_key" UNIQUE (origin_host),
  CONSTRAINT "preview_runtime_allocations_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."preview_runtime_allocations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."preview_runtime_allocations" FROM "anon", "authenticated";

CREATE TABLE "public"."preview_runtime_templates" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "template_id"                     text                     NOT NULL,
  "template_revision"               integer                  NOT NULL,
  "snapshot_id"                     text                     NOT NULL,
  "image_digest"                    text                     NOT NULL,
  "recipe_digest"                   text                     NOT NULL,
  "seed_manifest_digest"            text                     NOT NULL,
  "seed_manifest"                   jsonb                    NOT NULL,
  "fixture_clock"                   timestamp with time zone NOT NULL,
  "application_port"                integer                  NOT NULL,
  "authenticated_identity"          text                     NOT NULL,
  "sdk_versions"                    jsonb                    NOT NULL,
  "warm_pool_target"                integer                  NOT NULL,
  "retired_at"                      timestamp with time zone,
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "preview_runtime_templates_client_id_template_id_template_re_key" UNIQUE (client_id, template_id, template_revision),
  CONSTRAINT "preview_runtime_templates_pkey" PRIMARY KEY (id),
  CONSTRAINT "preview_runtime_templates_pool_target_nonnegative" CHECK ((warm_pool_target >= 0)),
  CONSTRAINT "preview_runtime_templates_port_range" CHECK (((application_port >= 1) AND (application_port <= 65535)))
);

ALTER TABLE "public"."preview_runtime_templates"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."preview_runtime_templates" FROM "anon", "authenticated";

CREATE TABLE "public"."preview_versions" (
  "id"                               uuid                     NOT NULL,
  "client_id"                        uuid                     NOT NULL,
  "product_signal_id"                uuid                     NOT NULL,
  "implementation_attempt_id"        uuid                     NOT NULL,
  "revision"                         bigint                   NOT NULL DEFAULT 0,
  "machine_version"                  smallint                 NOT NULL DEFAULT 1,
  "source_commit"                    text                     NOT NULL,
  "source_digest"                    text                     NOT NULL,
  "build_digest"                     text                     NOT NULL,
  "base_revision"                    text                     NOT NULL,
  "seed_manifest_digest"             text,
  "ready_at"                         timestamp with time zone,
  "schema_version"                   smallint                 NOT NULL DEFAULT 1,
  "created_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "source_manifest"                  jsonb,
  "patch_digest"                     text,
  "patch_text"                       text,
  "build_recipe"                     jsonb,
  "migrations"                       jsonb,
  "artifact_recorded_at"             timestamp with time zone,
  "readiness_recorded_at"            timestamp with time zone,
  "failure_reason"                   text,
  "artifact_manifest_digest"         text,
  "check_summary"                    jsonb,
  "superseded_by_preview_version_id" uuid,
  CONSTRAINT "preview_versions_implementation_attempt_id_key" UNIQUE (implementation_attempt_id),
  CONSTRAINT "preview_versions_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."preview_versions"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."preview_versions" FROM "anon";

CREATE TABLE "public"."product_overview_versions" (
  "id"              uuid                     NOT NULL,
  "client_id"       uuid                     NOT NULL,
  "version_number"  bigint                   NOT NULL,
  "revision"        bigint                   NOT NULL DEFAULT 0,
  "machine_version" smallint                 NOT NULL DEFAULT 1,
  "content_digest"  text                     NOT NULL,
  "overview"        jsonb                    NOT NULL,
  "sources"         jsonb                    NOT NULL,
  "activated_at"    timestamp with time zone,
  "superseded_at"   timestamp with time zone,
  "schema_version"  smallint                 NOT NULL DEFAULT 1,
  "created_at"      timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "product_overview_versions_client_id_content_digest_key" UNIQUE (client_id, content_digest),
  CONSTRAINT "product_overview_versions_client_id_version_number_key" UNIQUE (client_id, version_number),
  CONSTRAINT "product_overview_versions_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."product_overview_versions"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."product_overview_versions" FROM "anon";

CREATE TABLE "public"."product_signal_concerns" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "concern_revision"     bigint                   NOT NULL,
  "pain_point"           text                     NOT NULL,
  "validation_criterion" text                     NOT NULL,
  "user_wording"         jsonb                    NOT NULL,
  "unknowns"             jsonb                    NOT NULL,
  "established_at"       timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "product_signal_concerns_pkey" PRIMARY KEY (id),
  CONSTRAINT "product_signal_concerns_product_signal_id_concern_revision_key" UNIQUE (product_signal_id, concern_revision)
);

ALTER TABLE "public"."product_signal_concerns"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."product_signal_concerns" FROM "anon";

CREATE TABLE "public"."product_signals" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "revision"                        bigint                   NOT NULL DEFAULT 0,
  "machine_version"                 smallint                 NOT NULL DEFAULT 1,
  "intake_item_id"                  uuid                     NOT NULL,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "originating_client_user_id"      uuid                     NOT NULL,
  "originating_customer_account_id" uuid                     NOT NULL,
  "classification_revision"         bigint                   NOT NULL DEFAULT 0,
  "concern_revision"                bigint                   NOT NULL DEFAULT 0,
  "acceptance_criteria_revision"    bigint                   NOT NULL DEFAULT 0,
  "cancellation_scope_id"           uuid                     NOT NULL,
  "return_deadline_at"              timestamp with time zone,
  "closure_reason"                  text,
  "closure_detail"                  jsonb,
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "product_signals_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."product_signals"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."product_signals" FROM "anon";

CREATE TABLE "public"."provider_refs" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "provider"             text                     NOT NULL,
  "installation_ref"     text                     NOT NULL,
  "resource_type"        text                     NOT NULL,
  "provider_resource_id" text                     NOT NULL,
  "owner_id"             uuid                     NOT NULL,
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "provider_refs_client_id_provider_installation_ref_resource__key" UNIQUE (client_id, PROVIDER, installation_ref, resource_type, provider_resource_id),
  CONSTRAINT "provider_refs_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."provider_refs"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."provider_refs" FROM "anon", "authenticated";

CREATE TABLE "public"."results" (
  "id"                 uuid                     NOT NULL,
  "client_id"          uuid                     NOT NULL,
  "command_id"         uuid                     NOT NULL,
  "technical_category" text,
  "payload"            jsonb                    NOT NULL,
  "received_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"     smallint                 NOT NULL DEFAULT 1,
  "created_at"         timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "results_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."results"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."results" FROM "anon", "authenticated";

CREATE TABLE "public"."routing_decisions" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "intake_item_id"                  uuid                     NOT NULL,
  "attempt_index"                   integer                  NOT NULL,
  "blocked_reason"                  text,
  "product_signal_id"               uuid,
  "source_report_id"                uuid,
  "pinned_configuration_release_id" uuid                     NOT NULL,
  "product_overview_version_id"     uuid                     NOT NULL,
  "decision_model_id"               text                     NOT NULL,
  "questions"                       jsonb                    NOT NULL,
  "answers"                         jsonb                    NOT NULL,
  "acceptance"                      jsonb                    NOT NULL,
  "assistance_reason"               text,
  "assisted_interpretation"         text,
  "decided_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  "created_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "routing_decisions_attempt_index_positive" CHECK ((attempt_index > 0)),
  CONSTRAINT "routing_decisions_intake_item_id_attempt_index_key" UNIQUE (intake_item_id, attempt_index),
  CONSTRAINT "routing_decisions_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."routing_decisions"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."routing_decisions" FROM "anon", "authenticated";

CREATE TABLE "public"."semantic_judgements" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "product_signal_id"    uuid                     NOT NULL,
  "interview_session_id" uuid,
  "context_bundle_id"    uuid                     NOT NULL,
  "model_id"             text                     NOT NULL,
  "question_ids"         text[]                   NOT NULL,
  "answers"              jsonb                    NOT NULL,
  "acceptance"           jsonb                    NOT NULL,
  "accepted"             boolean                  NOT NULL,
  "rejections"           text[]                   NOT NULL,
  "created_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "semantic_judgements_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."semantic_judgements"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."semantic_judgements" FROM "anon", "authenticated";

CREATE TABLE "public"."server_control_commands" (
  "id"                           uuid                     NOT NULL,
  "client_id"                    uuid                     NOT NULL,
  "activation_id"                uuid                     NOT NULL,
  "server_stream_id"             uuid                     NOT NULL,
  "reason"                       text                     NOT NULL,
  "issued_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "delivered_at"                 timestamp with time zone,
  "acknowledged_at"              timestamp with time zone,
  "acknowledged_lifecycle_state" text,
  "schema_version"               smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "server_control_commands_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."server_control_commands"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."server_control_commands" FROM "anon", "authenticated";

CREATE TABLE "public"."server_health_reports" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "activation_id"               uuid                     NOT NULL,
  "server_stream_id"            uuid                     NOT NULL,
  "reported_at"                 timestamp with time zone NOT NULL,
  "received_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "lifecycle_state"             text                     NOT NULL,
  "sdk_version"                 text                     NOT NULL,
  "contract_major"              integer                  NOT NULL,
  "node_version"                text                     NOT NULL,
  "service_key"                 text                     NOT NULL,
  "service_instance_id"         uuid                     NOT NULL,
  "manifest_version"            integer,
  "policy_version"              text,
  "utrace_schema_version"       text,
  "semantic_convention_version" text,
  "credential_expires_at"       timestamp with time zone,
  "capabilities"                jsonb                    NOT NULL,
  "adapters"                    jsonb                    NOT NULL,
  "buffer"                      jsonb                    NOT NULL,
  "probe_result"                jsonb                    NOT NULL,
  "failures"                    jsonb                    NOT NULL,
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "server_health_reports_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."server_health_reports"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."server_health_reports" FROM "anon", "authenticated";

CREATE TABLE "public"."server_spans" (
  "id"                          uuid                     NOT NULL,
  "client_id"                   uuid                     NOT NULL,
  "activation_id"               uuid                     NOT NULL,
  "server_stream_id"            uuid                     NOT NULL,
  "ingestion_receipt_id"        uuid                     NOT NULL,
  "trace_id"                    text                     NOT NULL,
  "span_id"                     text                     NOT NULL,
  "parent_span_id"              text,
  "span_name"                   text                     NOT NULL,
  "span_kind"                   text                     NOT NULL,
  "start_time_unix_nano"        bigint                   NOT NULL,
  "end_time_unix_nano"          bigint                   NOT NULL,
  "status_code"                 text                     NOT NULL,
  "scope_name"                  text                     NOT NULL,
  "scope_version"               text,
  "service_name"                text                     NOT NULL,
  "service_namespace"           text                     NOT NULL,
  "service_version"             text                     NOT NULL,
  "deployment_environment_name" text                     NOT NULL,
  "attributes"                  jsonb                    NOT NULL,
  "links"                       jsonb                    NOT NULL,
  "span_events"                 jsonb                    NOT NULL,
  "registered_operation"        text,
  "http_route"                  text,
  "http_status_code"            integer,
  "source_schema_version"       text                     NOT NULL,
  "canonical_schema_version"    text                     NOT NULL,
  "semantic_convention_version" text                     NOT NULL,
  "policy_version"              text                     NOT NULL,
  "normalization_run_id"        uuid                     NOT NULL,
  "content_sha256"              text                     NOT NULL,
  "received_at"                 timestamp with time zone NOT NULL,
  "accepted_at"                 timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"              smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "server_spans_interval_ordered" CHECK ((end_time_unix_nano >= start_time_unix_nano)),
  CONSTRAINT "server_spans_parent_span_id_format" CHECK (((parent_span_id IS NULL) OR (parent_span_id ~ '^[0-9a-f]{16}$'::text))),
  CONSTRAINT "server_spans_pkey" PRIMARY KEY (id),
  CONSTRAINT "server_spans_span_id_format" CHECK ((span_id ~ '^[0-9a-f]{16}$'::text)),
  CONSTRAINT "server_spans_trace_id_format" CHECK ((trace_id ~ '^[0-9a-f]{32}$'::text))
);

ALTER TABLE "public"."server_spans"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."server_spans" FROM "anon", "authenticated";

CREATE TABLE "public"."server_streams" (
  "id"                              uuid                     NOT NULL,
  "client_id"                       uuid                     NOT NULL,
  "activation_id"                   uuid                     NOT NULL,
  "service_instance_id"             uuid                     NOT NULL,
  "service_key"                     text                     NOT NULL,
  "revision"                        bigint                   NOT NULL DEFAULT 0,
  "accepted_through_batch_sequence" bigint                   NOT NULL DEFAULT 0,
  "highest_received_batch_sequence" bigint                   NOT NULL DEFAULT 0,
  "clean"                           boolean,
  "opened_at"                       timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                  smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "server_streams_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."server_streams"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."server_streams" FROM "anon", "authenticated";

CREATE TABLE "public"."session_projections" (
  "session_id"        uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "client_user_id"    uuid                     NOT NULL,
  "product_signal_id" uuid                     NOT NULL,
  "revision"          bigint                   NOT NULL,
  "projection"        jsonb                    NOT NULL,
  "updated_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "session_projections_pkey" PRIMARY KEY (session_id)
);

ALTER TABLE "public"."session_projections"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."session_projections" FROM "anon";

CREATE TABLE "public"."session_readiness" (
  "interview_session_id"      uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "readiness_revision"        bigint                   NOT NULL DEFAULT 0,
  "participant_authenticated" boolean                  NOT NULL DEFAULT false,
  "voice_transport_ready"     boolean                  NOT NULL DEFAULT false,
  "sideband_attached"         boolean                  NOT NULL DEFAULT false,
  "backend_context_current"   boolean                  NOT NULL DEFAULT false,
  "preview_allocation_ready"  boolean                  NOT NULL DEFAULT false,
  "browser_sdk_ready"         boolean                  NOT NULL DEFAULT false,
  "server_sdk_ready"          boolean                  NOT NULL DEFAULT false,
  "trace_correlation_ready"   boolean                  NOT NULL DEFAULT false,
  "conversational_ready"      boolean                  NOT NULL DEFAULT false,
  "observation_ready"         boolean                  NOT NULL DEFAULT false,
  "blockers"                  text[]                   NOT NULL DEFAULT '{}'::text[],
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "session_readiness_pkey" PRIMARY KEY (interview_session_id)
);

ALTER TABLE "public"."session_readiness"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."session_readiness" FROM "anon", "authenticated";

CREATE TABLE "public"."source_reports" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "product_signal_id"             uuid                     NOT NULL,
  "intake_item_id"                uuid                     NOT NULL,
  "reporting_client_user_id"      uuid                     NOT NULL,
  "reporting_customer_account_id" uuid                     NOT NULL,
  "source_excerpt"                text                     NOT NULL,
  "occurred_at"                   timestamp with time zone NOT NULL,
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "source_reports_pkey" PRIMARY KEY (id),
  CONSTRAINT "source_reports_product_signal_id_key" UNIQUE (product_signal_id)
);

ALTER TABLE "public"."source_reports"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."source_reports" FROM "anon";

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
  CONSTRAINT "telemetry_activations_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."telemetry_activations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."telemetry_activations" FROM "anon", "authenticated";

CREATE TABLE "public"."telemetry_artifacts" (
  "id"              uuid                     NOT NULL,
  "client_id"       uuid                     NOT NULL,
  "artifact_class"  text                     NOT NULL,
  "media_type"      text                     NOT NULL,
  "schema_type"     text                     NOT NULL,
  "byte_size"       bigint                   NOT NULL,
  "content_sha256"  text                     NOT NULL,
  "chunk_manifest"  jsonb,
  "retention_class" text                     NOT NULL,
  "created_at"      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"  smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_artifacts_digest_format" CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
  CONSTRAINT "telemetry_artifacts_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_artifacts_size_positive" CHECK ((byte_size > 0))
);

ALTER TABLE "public"."telemetry_artifacts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."telemetry_artifacts" FROM "anon", "authenticated";

CREATE TABLE "public"."telemetry_credential_revocations" (
  "id"                      uuid                     NOT NULL,
  "client_id"               uuid                     NOT NULL,
  "telemetry_credential_id" uuid                     NOT NULL,
  "reason"                  text                     NOT NULL,
  "revoked_at"              timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"          smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_credential_revocations_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."telemetry_credential_revocations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."telemetry_credential_revocations" FROM "anon", "authenticated";

CREATE TABLE "public"."telemetry_credentials" (
  "id"                uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "activation_id"     uuid                     NOT NULL,
  "revision"          bigint                   NOT NULL DEFAULT 0,
  "token_digest"      text                     NOT NULL,
  "issued_at"         timestamp with time zone NOT NULL DEFAULT now(),
  "expires_at"        timestamp with time zone NOT NULL,
  "revoked_at"        timestamp with time zone,
  "revocation_reason" text,
  "created_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"        timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_credentials_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_credentials_token_digest_key" UNIQUE (token_digest)
);

ALTER TABLE "public"."telemetry_credentials"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."telemetry_credentials" FROM "anon", "authenticated";

CREATE TABLE "public"."telemetry_evidence_items" (
  "id"                    uuid                     NOT NULL,
  "client_id"             uuid                     NOT NULL,
  "evidence_window_id"    uuid                     NOT NULL,
  "source_record_id"      uuid                     NOT NULL,
  "source_observation_id" uuid,
  "trace_id"              text,
  "span_id"               text,
  "target_id"             text,
  "artifact_id"           uuid,
  "claim_ref"             text                     NOT NULL,
  "supports"              boolean                  NOT NULL,
  "policy_version"        text                     NOT NULL,
  "source_schema_version" text                     NOT NULL,
  "correlation_revision"  bigint                   NOT NULL,
  "watermarks"            jsonb                    NOT NULL,
  "promoted_at"           timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"        smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_evidence_items_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."telemetry_evidence_items"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."telemetry_evidence_items" FROM "anon", "authenticated";

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
  CONSTRAINT "telemetry_evidence_windows_ordered" CHECK ((accepted_through >= starts_at)),
  CONSTRAINT "telemetry_evidence_windows_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_evidence_windows_source_classes_present" CHECK ((array_length(allowed_source_classes, 1) >= 1))
);

ALTER TABLE "public"."telemetry_evidence_windows"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."telemetry_evidence_windows" FROM "anon", "authenticated";

CREATE TABLE "public"."telemetry_identity_conflicts" (
  "id"                               uuid                     NOT NULL,
  "client_id"                        uuid                     NOT NULL,
  "activation_id"                    uuid                     NOT NULL,
  "source_stream_id"                 uuid                     NOT NULL,
  "identity_kind"                    text                     NOT NULL,
  "identity_value"                   text                     NOT NULL,
  "first_content_sha256"             text                     NOT NULL,
  "conflicting_content_sha256"       text                     NOT NULL,
  "first_ingestion_receipt_id"       uuid                     NOT NULL,
  "conflicting_ingestion_receipt_id" uuid,
  "detected_at"                      timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"                   smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_identity_conflicts_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."telemetry_identity_conflicts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."telemetry_identity_conflicts" FROM "anon", "authenticated";

CREATE TABLE "public"."telemetry_readiness_projections" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "activation_id"             uuid                     NOT NULL,
  "interview_session_id"      uuid                     NOT NULL,
  "revision"                  bigint                   NOT NULL,
  "ready"                     boolean                  NOT NULL,
  "contributing_resource_ids" jsonb                    NOT NULL,
  "accepted_watermarks"       jsonb                    NOT NULL,
  "probe_id"                  uuid,
  "failures"                  jsonb                    NOT NULL,
  "checked_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "expires_at"                timestamp with time zone NOT NULL,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "telemetry_readiness_projections_pkey" PRIMARY KEY (id),
  CONSTRAINT "telemetry_readiness_revision_positive" CHECK ((revision >= 1))
);

ALTER TABLE "public"."telemetry_readiness_projections"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."telemetry_readiness_projections" FROM "anon", "authenticated";

CREATE TABLE "public"."users" (
  "id"                  uuid                     NOT NULL,
  "auth_user_id"        uuid,
  "display_name"        text,
  "do_not_contact"      boolean                  NOT NULL DEFAULT false,
  "participation_count" integer                  NOT NULL DEFAULT 0,
  "revision"            bigint                   NOT NULL DEFAULT 0,
  "schema_version"      smallint                 NOT NULL DEFAULT 1,
  "created_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"          timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "users_auth_user_id_key" UNIQUE (auth_user_id),
  CONSTRAINT "users_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."users"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."users" FROM "anon";

CREATE TABLE "public"."validation_attempts" (
  "id"                            uuid                     NOT NULL,
  "client_id"                     uuid                     NOT NULL,
  "product_signal_id"             uuid                     NOT NULL,
  "implementation_attempt_id"     uuid                     NOT NULL,
  "preview_version_id"            uuid                     NOT NULL,
  "preview_runtime_allocation_id" uuid                     NOT NULL,
  "interview_session_id"          uuid                     NOT NULL,
  "client_user_id"                uuid                     NOT NULL,
  "revision"                      bigint                   NOT NULL DEFAULT 0,
  "machine_version"               smallint                 NOT NULL DEFAULT 1,
  "dataset_manifest_digest"       text                     NOT NULL,
  "acceptance_criteria_revision"  bigint                   NOT NULL,
  "completed_at"                  timestamp with time zone,
  "schema_version"                smallint                 NOT NULL DEFAULT 1,
  "created_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                    timestamp with time zone NOT NULL DEFAULT now(),
  "authenticated_identity_ref"    text                     NOT NULL,
  "workflow_ref"                  text                     NOT NULL,
  "evidence_window_id"            uuid,
  "interrupted_at"                timestamp with time zone,
  "frozen_result"                 jsonb,
  "frozen_result_digest"          text,
  CONSTRAINT "validation_attempts_frozen_digest_with_result" CHECK (((frozen_result IS NULL) = (frozen_result_digest IS NULL))),
  CONSTRAINT "validation_attempts_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."validation_attempts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."validation_attempts" FROM "anon";

CREATE TABLE "public"."validation_criteria" (
  "id"                uuid                     NOT NULL,
  "client_id"         uuid                     NOT NULL,
  "product_signal_id" uuid                     NOT NULL,
  "criteria_revision" bigint                   NOT NULL,
  "ordinal"           integer                  NOT NULL,
  "statement"         text                     NOT NULL,
  "schema_version"    smallint                 NOT NULL DEFAULT 1,
  "created_at"        timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "validation_criteria_pkey" PRIMARY KEY (id),
  CONSTRAINT "validation_criteria_product_signal_id_criteria_revision_ord_key" UNIQUE (product_signal_id, criteria_revision, ordinal)
);

ALTER TABLE "public"."validation_criteria"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."validation_criteria" FROM "anon";

CREATE TABLE "public"."validation_outcomes" (
  "id"                                uuid                     NOT NULL,
  "client_id"                         uuid                     NOT NULL,
  "validation_attempt_id"             uuid                     NOT NULL,
  "validation_criterion_id"           uuid                     NOT NULL,
  "user_confirmed"                    boolean                  NOT NULL,
  "evidence_record_ids"               uuid[]                   NOT NULL,
  "note"                              text,
  "schema_version"                    smallint                 NOT NULL DEFAULT 1,
  "created_at"                        timestamp with time zone NOT NULL DEFAULT now(),
  "confirmation_evidence_record_id"   uuid,
  "contradicting_evidence_record_ids" uuid[]                   NOT NULL,
  "superseded_claim_record_ids"       uuid[]                   NOT NULL,
  "reason"                            text                     NOT NULL,
  CONSTRAINT "validation_outcomes_pkey" PRIMARY KEY (id),
  CONSTRAINT "validation_outcomes_validation_attempt_id_validation_criter_key" UNIQUE (validation_attempt_id, validation_criterion_id)
);

ALTER TABLE "public"."validation_outcomes"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."validation_outcomes" FROM "anon";

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
  CONSTRAINT "visual_command_dispatches_pkey" PRIMARY KEY (command_id)
);

ALTER TABLE "public"."visual_command_dispatches"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."visual_command_dispatches" FROM "anon", "authenticated";

CREATE TABLE "public"."voice_connections" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "interview_session_id"      uuid                     NOT NULL,
  "revision"                  bigint                   NOT NULL DEFAULT 0,
  "machine_version"           smallint                 NOT NULL DEFAULT 1,
  "voice_run_id"              uuid                     NOT NULL,
  "participant_connection_id" uuid                     NOT NULL,
  "provider_session_ref"      text,
  "superseded_by_id"          uuid,
  "closed_at"                 timestamp with time zone,
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  "created_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "voice_connections_pkey" PRIMARY KEY (id),
  CONSTRAINT "voice_connections_voice_run_id_key" UNIQUE (voice_run_id)
);

ALTER TABLE "public"."voice_connections"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."voice_connections" FROM "anon", "authenticated";

CREATE TABLE "public"."voice_delegations" (
  "id"                     uuid                     NOT NULL,
  "client_id"              uuid                     NOT NULL,
  "interview_session_id"   uuid                     NOT NULL,
  "voice_connection_id"    uuid                     NOT NULL,
  "provider_delegation_id" text                     NOT NULL,
  "delegation_target"      text                     NOT NULL,
  "context_bundle_id"      uuid,
  "finding"                text,
  "refusal_reason"         text,
  "offset_ms"              integer,
  "created_at"             timestamp with time zone NOT NULL DEFAULT now(),
  "settled_at"             timestamp with time zone,
  "schema_version"         smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "voice_delegations_pkey" PRIMARY KEY (id),
  CONSTRAINT "voice_delegations_voice_connection_id_provider_delegation_i_key" UNIQUE (voice_connection_id, provider_delegation_id)
);

ALTER TABLE "public"."voice_delegations"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."voice_delegations" FROM "anon", "authenticated";

CREATE TABLE "public"."voice_playback_segments" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "interview_session_id" uuid                     NOT NULL,
  "voice_connection_id"  uuid                     NOT NULL,
  "start_ms"             integer                  NOT NULL,
  "end_ms"               integer                  NOT NULL,
  "audio_byte_length"    integer                  NOT NULL,
  "recorded_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "voice_playback_segments_pkey" PRIMARY KEY (id),
  CONSTRAINT "voice_playback_segments_span_ordered" CHECK ((end_ms >= start_ms)),
  CONSTRAINT "voice_playback_segments_voice_connection_id_start_ms_end_ms_key" UNIQUE (voice_connection_id, start_ms, end_ms)
);

ALTER TABLE "public"."voice_playback_segments"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."voice_playback_segments" FROM "anon", "authenticated";

CREATE TABLE "public"."voice_transcript_fragments" (
  "id"                   uuid                     NOT NULL,
  "client_id"            uuid                     NOT NULL,
  "interview_session_id" uuid                     NOT NULL,
  "voice_connection_id"  uuid                     NOT NULL,
  "voice_run_id"         uuid                     NOT NULL,
  "provider_session_ref" text                     NOT NULL,
  "provider_event_type"  text                     NOT NULL,
  "start_ms"             integer                  NOT NULL,
  "end_ms"               integer                  NOT NULL,
  "fragment_text"        text                     NOT NULL,
  "provenance"           jsonb                    NOT NULL,
  "recorded_at"          timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"       smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "voice_transcript_fragments_pkey" PRIMARY KEY (id),
  CONSTRAINT "voice_transcript_fragments_span_ordered" CHECK ((end_ms >= start_ms)),
  CONSTRAINT "voice_transcript_fragments_voice_connection_id_provider_eve_key" UNIQUE (voice_connection_id, provider_event_type, start_ms, end_ms)
);

ALTER TABLE "public"."voice_transcript_fragments"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."voice_transcript_fragments" FROM "anon", "authenticated";

CREATE TYPE "public"."actor_kind" AS ENUM (
  'customer_participant',
  'client_member',
  'utrace_agent_role',
  'provider_identity',
  'service',
  'system'
);

ALTER TABLE "public"."audit_records"
  ADD COLUMN "actor_kind" public.actor_kind NOT NULL;

ALTER TABLE "public"."case_file_amendments"
  ADD COLUMN "actor_kind" public.actor_kind NOT NULL;

ALTER TABLE "public"."events"
  ADD COLUMN "actor_kind" public.actor_kind NOT NULL;

CREATE TYPE "public"."agent_character_state" AS ENUM (
  'idle',
  'listening',
  'thinking',
  'speaking',
  'acknowledgement',
  'concern',
  'paused',
  'reconnecting',
  'completion'
);

ALTER TABLE "public"."interview_sessions"
  ADD COLUMN "character_state" public.agent_character_state NOT NULL;

CREATE TYPE "public"."aggregate_kind" AS ENUM (
  'intake_item',
  'product_signal',
  'outreach_attempt',
  'invitation',
  'interview_session',
  'voice_connection',
  'preview_runtime_allocation',
  'environment_lease',
  'implementation_attempt',
  'preview_version',
  'validation_attempt',
  'case_file',
  'delivery',
  'outreach_offer',
  'job',
  'job_run',
  'command',
  'client',
  'client_user',
  'customer_account'
);

ALTER TABLE "public"."audit_records"
  ADD COLUMN "subject_kind" public.aggregate_kind NOT NULL;

ALTER TABLE "public"."commands"
  ADD COLUMN "target_kind" public.aggregate_kind NOT NULL;

ALTER TABLE "public"."events"
  ADD COLUMN "stream_kind" public.aggregate_kind NOT NULL;

ALTER TABLE "public"."provider_refs"
  ADD COLUMN "owner_kind" public.aggregate_kind NOT NULL;

CREATE TYPE "public"."attention_purpose" AS ENUM (
  'evidence_coverage',
  'claim_relationship',
  'attention_routing',
  'visual_target',
  'flow_classification',
  'delegation_routing',
  'speech_gesture'
);

ALTER TABLE "public"."semantic_judgements"
  ADD COLUMN "purpose" public.attention_purpose NOT NULL;

CREATE TYPE "public"."case_file_stage" AS ENUM (
  'draft',
  'verified',
  'delivery_resolved',
  'ready',
  'published'
);

ALTER TABLE "public"."case_files"
  ADD COLUMN "stage" public.case_file_stage NOT NULL;

CREATE TYPE "public"."case_file_variant" AS ENUM (
  'validated_implementation',
  'no_implementation',
  'blocked'
);

ALTER TABLE "public"."case_files"
  ADD COLUMN "variant" public.case_file_variant NOT NULL;

CREATE TYPE "public"."client_member_role" AS ENUM (
  'owner',
  'admin'
);

ALTER TABLE "public"."client_members"
  ADD COLUMN "role" public.client_member_role NOT NULL;

CREATE TYPE "public"."command_disposition" AS ENUM (
  'succeeded',
  'rejected',
  'failed',
  'canceled',
  'expired'
);

ALTER TABLE "public"."outreach_deliveries"
  ADD COLUMN "disposition" public.command_disposition NOT NULL;

ALTER TABLE "public"."results"
  ADD COLUMN "disposition" public.command_disposition;

CREATE TYPE "public"."command_state" AS ENUM (
  'created',
  'queued',
  'dispatching',
  'dispatched',
  'acknowledged',
  'reconciling',
  'terminal'
);

ALTER TABLE "public"."commands"
  ADD COLUMN "state" public.command_state NOT NULL;

CREATE TYPE "public"."concern_origin" AS ENUM (
  'routing_inference',
  'interview_established'
);

ALTER TABLE "public"."product_signal_concerns"
  ADD COLUMN "origin" public.concern_origin NOT NULL;

CREATE TYPE "public"."context_purpose" AS ENUM (
  'voice_delegation',
  'jev_attention',
  'flow_classification',
  'acceptance_criteria',
  'visual_target',
  'delegation_routing',
  'speech_gesture'
);

ALTER TABLE "public"."context_bundles"
  ADD COLUMN "purpose" public.context_purpose NOT NULL;

CREATE TYPE "public"."context_section" AS ENUM (
  'recent_conversation',
  'agreed_scope',
  'acceptance_criteria',
  'open_questions',
  'contradictions',
  'selected_observations',
  'implementation_status',
  'lifecycle_and_policy',
  'prior_commands_and_results',
  'product_knowledge'
);

ALTER TABLE "public"."context_bundle_items"
  ADD COLUMN "section" public.context_section NOT NULL;

CREATE TYPE "public"."correlation_status" AS ENUM (
  'pending',
  'complete',
  'partial',
  'ambiguous',
  'invalid',
  'expired'
);

ALTER TABLE "public"."correlation_projections"
  ADD COLUMN "status" public.correlation_status NOT NULL;

CREATE TYPE "public"."criterion_observability" AS ENUM (
  'observed',
  'subjective'
);

ALTER TABLE "public"."validation_criteria"
  ADD COLUMN "observability" public.criterion_observability NOT NULL;

ALTER TABLE "public"."validation_outcomes"
  ADD COLUMN "observability" public.criterion_observability NOT NULL;

CREATE TYPE "public"."criterion_result" AS ENUM (
  'met',
  'unmet',
  'unconfirmed',
  'contradicted'
);

ALTER TABLE "public"."validation_outcomes"
  ADD COLUMN "result" public.criterion_result NOT NULL;

CREATE TYPE "public"."delivery_state" AS ENUM (
  'reserved',
  'preflighting',
  'creating',
  'reconciling',
  'delivered',
  'blocked'
);

ALTER TABLE "public"."deliveries"
  ADD COLUMN "state" public.delivery_state NOT NULL;

CREATE TYPE "public"."dependency_hold_kind" AS ENUM (
  'contradiction_flag',
  'accepted_correction'
);

ALTER TABLE "public"."dependency_holds"
  ADD COLUMN "hold_kind" public.dependency_hold_kind NOT NULL;

CREATE TYPE "public"."effect_state" AS ENUM (
  'absent',
  'partial',
  'completed',
  'indeterminate'
);

ALTER TABLE "public"."outreach_deliveries"
  ADD COLUMN "effect_state" public.effect_state NOT NULL;

ALTER TABLE "public"."results"
  ADD COLUMN "effect_state" public.effect_state;

CREATE TYPE "public"."environment_lease_state" AS ENUM (
  'active',
  'fenced',
  'released',
  'expired'
);

ALTER TABLE "public"."environment_leases"
  ADD COLUMN "state" public.environment_lease_state NOT NULL;

CREATE TYPE "public"."evidence_claim_state" AS ENUM (
  'proposed',
  'confirmed',
  'contradicted',
  'withdrawn'
);

ALTER TABLE "public"."evidence_records"
  ADD COLUMN "claim_state" public.evidence_claim_state NOT NULL;

CREATE TYPE "public"."evidence_class" AS ENUM (
  'intake_source',
  'transcript_fragment',
  'browser_observation',
  'server_span',
  'implementation_artifact',
  'check_result',
  'validation_observation'
);

ALTER TABLE "public"."evidence_records"
  ADD COLUMN "evidence_class" public.evidence_class NOT NULL;

CREATE TYPE "public"."evidence_window_mode" AS ENUM (
  'rolling',
  'frozen'
);

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD COLUMN "mode" public.evidence_window_mode NOT NULL;

CREATE TYPE "public"."extension_account_link_state" AS ENUM (
  'linked',
  'revoked'
);

ALTER TABLE "public"."extension_installations"
  ADD COLUMN "account_link_state" public.extension_account_link_state NOT NULL;

CREATE TYPE "public"."extension_binding_status" AS ENUM (
  'bound',
  'revoked',
  'rejected'
);

ALTER TABLE "public"."extension_bindings"
  ADD COLUMN "status" public.extension_binding_status NOT NULL;

CREATE TYPE "public"."extension_readiness_state" AS ENUM (
  'ready',
  'recovering',
  'withdrawn'
);

ALTER TABLE "public"."session_readiness"
  ADD COLUMN "extension_readiness" public.extension_readiness_state NOT NULL DEFAULT 'withdrawn'::public.extension_readiness_state;

CREATE TYPE "public"."flow_type" AS ENUM (
  'bugfix_flow',
  'feature_request_flow'
);

ALTER TABLE "public"."flow_classification_records"
  ADD COLUMN "flow_type" public.flow_type NOT NULL;

ALTER TABLE "public"."flow_classification_records"
  ADD COLUMN "initial_flow_type" public.flow_type NOT NULL;

ALTER TABLE "public"."product_signals"
  ADD COLUMN "initial_flow_type" public.flow_type NOT NULL;

ALTER TABLE "public"."product_signals"
  ADD COLUMN "established_flow_type" public.flow_type;

CREATE TYPE "public"."gate_decision_kind" AS ENUM (
  'approved_live',
  'no_implementation'
);

ALTER TABLE "public"."gate_decisions"
  ADD COLUMN "decision" public.gate_decision_kind NOT NULL;

CREATE TYPE "public"."implementation_attempt_state" AS ENUM (
  'authorized',
  'queued',
  'acquiring_environment',
  'executing',
  'checking',
  'publishing_candidate',
  'candidate_ready',
  'scope_change_required',
  'checks_failed',
  'unsafe',
  'infrastructure_blocked',
  'paused',
  'canceled',
  'budget_exhausted'
);

ALTER TABLE "public"."implementation_attempts"
  ADD COLUMN "state" public.implementation_attempt_state NOT NULL;

CREATE TYPE "public"."implementation_progress" AS ENUM (
  'not_started',
  'reproduction_captured',
  'making_changes',
  'preparing_preview',
  'ready_for_validation'
);

ALTER TABLE "public"."interview_sessions"
  ADD COLUMN "implementation_progress" public.implementation_progress NOT NULL;

CREATE TYPE "public"."ingestion_outcome" AS ENUM (
  'accepted',
  'rejected',
  'quarantined'
);

ALTER TABLE "public"."ingestion_receipts"
  ADD COLUMN "outcome" public.ingestion_outcome NOT NULL;

CREATE TYPE "public"."ingestion_stage" AS ENUM (
  'received',
  'authenticated',
  'source_validated',
  'normalized',
  'indexed',
  'correlated',
  'available'
);

ALTER TABLE "public"."ingestion_failures"
  ADD COLUMN "stage" public.ingestion_stage NOT NULL;

ALTER TABLE "public"."ingestion_receipt_stages"
  ADD COLUMN "stage" public.ingestion_stage NOT NULL;

ALTER TABLE "public"."ingestion_receipts"
  ADD COLUMN "stage" public.ingestion_stage NOT NULL;

CREATE TYPE "public"."intake_batch_state" AS ENUM (
  'open',
  'sealed',
  'discarded'
);

ALTER TABLE "public"."intake_batches"
  ADD COLUMN "state" public.intake_batch_state NOT NULL;

CREATE TYPE "public"."intake_item_state" AS ENUM (
  'received',
  'routing',
  'routed',
  'routing_blocked',
  'canceled'
);

ALTER TABLE "public"."intake_items"
  ADD COLUMN "state" public.intake_item_state NOT NULL;

CREATE TYPE "public"."interview_phase" AS ENUM (
  'participant_setup',
  'greeting',
  'discovery',
  'implementation',
  'validation',
  'closing'
);

ALTER TABLE "public"."interview_sessions"
  ADD COLUMN "phase" public.interview_phase NOT NULL;

CREATE TYPE "public"."interview_session_state" AS ENUM (
  'invited',
  'claimed',
  'preparing',
  'live',
  'closed'
);

ALTER TABLE "public"."interview_sessions"
  ADD COLUMN "state" public.interview_session_state NOT NULL;

CREATE TYPE "public"."job_run_state" AS ENUM (
  'leased',
  'succeeded',
  'failed',
  'abandoned',
  'timed_out'
);

ALTER TABLE "public"."job_runs"
  ADD COLUMN "state" public.job_run_state NOT NULL;

CREATE TYPE "public"."job_state" AS ENUM (
  'pending',
  'ready',
  'leased',
  'succeeded',
  'failed',
  'canceled',
  'expired'
);

ALTER TABLE "public"."jobs"
  ADD COLUMN "state" public.job_state NOT NULL;

CREATE TYPE "public"."membership_status" AS ENUM (
  'active',
  'revoked'
);

ALTER TABLE "public"."client_members"
  ADD COLUMN "status" public.membership_status NOT NULL;

CREATE TYPE "public"."offer_issuance_state" AS ENUM (
  'pending',
  'dispatched',
  'issued',
  'blocked'
);

ALTER TABLE "public"."offer_issuance_operations"
  ADD COLUMN "state" public.offer_issuance_state NOT NULL;

CREATE TYPE "public"."offer_ledger_entry_kind" AS ENUM (
  'budget_authorized',
  'reservation_created',
  'reservation_released',
  'credit_earned',
  'credit_issued',
  'issuance_blocked'
);

ALTER TABLE "public"."offer_ledger_entries"
  ADD COLUMN "entry_kind" public.offer_ledger_entry_kind NOT NULL;

CREATE TYPE "public"."offer_state" AS ENUM (
  'reserved',
  'earned',
  'released'
);

ALTER TABLE "public"."outreach_offers"
  ADD COLUMN "state" public.offer_state NOT NULL;

CREATE TYPE "public"."outbox_state" AS ENUM (
  'pending',
  'enqueued',
  'dispatched',
  'failed'
);

ALTER TABLE "public"."outbox"
  ADD COLUMN "state" public.outbox_state NOT NULL;

CREATE TYPE "public"."outreach_attempt_state" AS ENUM (
  'scheduled',
  'dispatching',
  'awaiting_response',
  'claimed',
  'declined',
  'expired',
  'unreached',
  'revoked'
);

ALTER TABLE "public"."outreach_attempts"
  ADD COLUMN "state" public.outreach_attempt_state NOT NULL;

CREATE TYPE "public"."outreach_purpose" AS ENUM (
  'initial_invitation',
  'return_reminder'
);

ALTER TABLE "public"."outreach_attempts"
  ADD COLUMN "purpose" public.outreach_purpose NOT NULL;

CREATE TYPE "public"."playback_delivery" AS ENUM (
  'generated',
  'unknown'
);

ALTER TABLE "public"."voice_playback_segments"
  ADD COLUMN "delivery" public.playback_delivery NOT NULL;

CREATE TYPE "public"."preview_allocation_kind" AS ENUM (
  'base',
  'candidate'
);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "allocation_kind" public.preview_allocation_kind NOT NULL;

CREATE TYPE "public"."preview_pool_role" AS ENUM (
  'warm_pool',
  'session_base',
  'candidate'
);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "pool_role" public.preview_pool_role NOT NULL;

CREATE TYPE "public"."preview_readiness_outcome" AS ENUM (
  'passed',
  'failed',
  'blocked',
  'not_applicable'
);

ALTER TABLE "public"."preview_readiness_results"
  ADD COLUMN "outcome" public.preview_readiness_outcome NOT NULL;

CREATE TYPE "public"."preview_route_readiness" AS ENUM (
  'ready',
  'reconnecting'
);

ALTER TABLE "public"."preview_routes"
  ADD COLUMN "readiness_state" public.preview_route_readiness NOT NULL;

CREATE TYPE "public"."preview_route_state" AS ENUM (
  'active',
  'superseded',
  'revoked'
);

ALTER TABLE "public"."preview_routes"
  ADD COLUMN "state" public.preview_route_state NOT NULL;

CREATE TYPE "public"."preview_runtime_allocation_state" AS ENUM (
  'pending',
  'seeding',
  'ready',
  'bound',
  'released',
  'terminated'
);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "state" public.preview_runtime_allocation_state NOT NULL;

CREATE TYPE "public"."preview_version_state" AS ENUM (
  'building',
  'ready',
  'retired',
  'failed'
);

ALTER TABLE "public"."preview_versions"
  ADD COLUMN "state" public.preview_version_state NOT NULL;

CREATE TYPE "public"."product_overview_state" AS ENUM (
  'active',
  'superseded'
);

ALTER TABLE "public"."product_overview_versions"
  ADD COLUMN "state" public.product_overview_state NOT NULL;

CREATE TYPE "public"."product_signal_state" AS ENUM (
  'awaiting_interview',
  'interviewing',
  'gate_pending',
  'implementing',
  'awaiting_validation',
  'validating',
  'closed_validated',
  'closed_no_implementation',
  'closed_blocked',
  'canceled'
);

ALTER TABLE "public"."product_signals"
  ADD COLUMN "state" public.product_signal_state NOT NULL;

CREATE TYPE "public"."result_kind" AS ENUM (
  'accepted',
  'progress',
  'terminal'
);

ALTER TABLE "public"."results"
  ADD COLUMN "kind" public.result_kind NOT NULL;

CREATE TYPE "public"."retry_advice" AS ENUM (
  'never',
  'after_delay',
  'after_condition_change'
);

ALTER TABLE "public"."job_runs"
  ADD COLUMN "retry_advice" public.retry_advice;

ALTER TABLE "public"."jobs"
  ADD COLUMN "last_retry_advice" public.retry_advice;

ALTER TABLE "public"."results"
  ADD COLUMN "retry_advice" public.retry_advice;

CREATE TYPE "public"."routing_outcome" AS ENUM (
  'close_as_noise',
  'create_product_signal'
);

ALTER TABLE "public"."intake_items"
  ADD COLUMN "routing_outcome" public.routing_outcome;

ALTER TABLE "public"."routing_decisions"
  ADD COLUMN "outcome" public.routing_outcome;

CREATE TYPE "public"."server_control_command_kind" AS ENUM (
  'pause',
  'resume',
  'terminate'
);

ALTER TABLE "public"."server_control_commands"
  ADD COLUMN "kind" public.server_control_command_kind NOT NULL;

CREATE TYPE "public"."telemetry_activation_state" AS ENUM (
  'active',
  'paused',
  'superseded',
  'revoked',
  'expired'
);

ALTER TABLE "public"."telemetry_activations"
  ADD COLUMN "state" public.telemetry_activation_state NOT NULL DEFAULT 'active'::public.telemetry_activation_state;

CREATE TYPE "public"."telemetry_credential_state" AS ENUM (
  'active',
  'revoked',
  'expired'
);

ALTER TABLE "public"."telemetry_credentials"
  ADD COLUMN "state" public.telemetry_credential_state NOT NULL DEFAULT 'active'::public.telemetry_credential_state;

CREATE TYPE "public"."telemetry_producer_kind" AS ENUM (
  'browser',
  'server'
);

ALTER TABLE "public"."ingestion_failures"
  ADD COLUMN "producer_kind" public.telemetry_producer_kind NOT NULL;

ALTER TABLE "public"."ingestion_receipts"
  ADD COLUMN "producer_kind" public.telemetry_producer_kind NOT NULL;

ALTER TABLE "public"."telemetry_activations"
  ADD COLUMN "kind" public.telemetry_producer_kind NOT NULL;

ALTER TABLE "public"."telemetry_credentials"
  ADD COLUMN "kind" public.telemetry_producer_kind NOT NULL;

ALTER TABLE "public"."telemetry_identity_conflicts"
  ADD COLUMN "producer_kind" public.telemetry_producer_kind NOT NULL;

CREATE TYPE "public"."telemetry_readiness_kind" AS ENUM (
  'browser',
  'server',
  'correlation'
);

ALTER TABLE "public"."telemetry_readiness_projections"
  ADD COLUMN "kind" public.telemetry_readiness_kind NOT NULL;

CREATE TYPE "public"."telemetry_source_class" AS ENUM (
  'browser_observation',
  'server_span'
);

ALTER TABLE "public"."telemetry_evidence_items"
  ADD COLUMN "source_class" public.telemetry_source_class NOT NULL;

CREATE TYPE "public"."telemetry_stream_state" AS ENUM (
  'open',
  'paused',
  'ended',
  'failed'
);

ALTER TABLE "public"."browser_streams"
  ADD COLUMN "state" public.telemetry_stream_state NOT NULL DEFAULT 'open'::public.telemetry_stream_state;

ALTER TABLE "public"."server_streams"
  ADD COLUMN "state" public.telemetry_stream_state NOT NULL DEFAULT 'open'::public.telemetry_stream_state;

CREATE TYPE "public"."transcript_speaker" AS ENUM (
  'participant',
  'agent'
);

ALTER TABLE "public"."voice_transcript_fragments"
  ADD COLUMN "speaker" public.transcript_speaker NOT NULL;

CREATE TYPE "public"."validation_attempt_state" AS ENUM (
  'pending',
  'in_progress',
  'interrupted',
  'completed',
  'abandoned'
);

ALTER TABLE "public"."validation_attempts"
  ADD COLUMN "state" public.validation_attempt_state NOT NULL;

CREATE TYPE "public"."validation_outcome" AS ENUM (
  'validated',
  'partially_validated',
  'not_validated',
  'unclear'
);

ALTER TABLE "public"."validation_attempts"
  ADD COLUMN "outcome" public.validation_outcome;

CREATE TYPE "public"."visual_command_kind" AS ENUM (
  'scene',
  'scroll_into_view',
  'clear_scene'
);

ALTER TABLE "public"."visual_command_dispatches"
  ADD COLUMN "command_kind" public.visual_command_kind NOT NULL;

CREATE TYPE "public"."visual_command_outcome" AS ENUM (
  'applied',
  'rejected',
  'failed',
  'stale_target',
  'canceled',
  'timed_out'
);

ALTER TABLE "public"."visual_command_dispatches"
  ADD COLUMN "outcome" public.visual_command_outcome;

CREATE TYPE "public"."voice_connection_state" AS ENUM (
  'connecting',
  'active',
  'reconnecting',
  'closed'
);

ALTER TABLE "public"."voice_connections"
  ADD COLUMN "state" public.voice_connection_state NOT NULL;

CREATE TYPE "public"."voice_delegation_state" AS ENUM (
  'received',
  'assembling',
  'running',
  'answered',
  'refused',
  'failed'
);

ALTER TABLE "public"."voice_delegations"
  ADD COLUMN "state" public.voice_delegation_state NOT NULL;

CREATE OR REPLACE FUNCTION utrace.acquire_preview_allocation (
  p_allocation_id uuid
)
  RETURNS bigint
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_token bigint;
begin
  select utrace.next_fencing_token() into v_token;
  update public.preview_runtime_allocations
  set fencing_token = v_token,
      updated_at = now()
  where id = p_allocation_id;
  if not found then
    raise exception 'preview runtime allocation % does not exist', p_allocation_id
      using errcode = 'no_data_found';
  end if;
  return v_token;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.enqueue_ingestion_job (
  p_client_id       uuid,
  p_job_type        text,
  p_input           jsonb,
  p_concurrency_key text
)
  RETURNS uuid
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_job_id uuid;
begin
  if exists (
    select 1
    from public.jobs
    where client_id = p_client_id
      and concurrency_key = p_concurrency_key
      and state in ('pending', 'ready', 'leased')
  ) then
    return null;
  end if;

  v_job_id := utrace.uuid7();
  insert into public.jobs (
    id, client_id, job_type, state, input, deadline_at, timeout_seconds, max_runs,
    resource_budget, retryable_categories, backoff_base_seconds,
    backoff_max_seconds, concurrency_key, not_before
  )
  values (
    v_job_id, p_client_id, p_job_type, 'ready', p_input,
    now() + interval '1 hour', 60, 5, '{"kind": "ingestion"}'::jsonb,
    array['provider_unavailable', 'timeout', 'internal'], 1, 30,
    p_concurrency_key, now()
  );
  insert into public.outbox (id, client_id, state, job_id, available_at)
  values (v_job_id, p_client_id, 'pending', v_job_id, now());
  return v_job_id;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.enqueue_ready_outbox (
  p_limit integer
)
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row public.outbox%rowtype;
  v_count integer := 0;
begin
  for v_row in
    select *
    from public.outbox
    where state = 'pending'
      and available_at <= now()
    order by available_at, created_at
    limit p_limit
    for update skip locked
  loop
    perform pgmq.send(
      'utrace_outbox',
      jsonb_build_object(
        'outbox_id', v_row.id,
        'client_id', v_row.client_id,
        'job_id', v_row.job_id,
        'command_id', v_row.command_id
      )
    );
    update public.outbox
    set state = 'enqueued',
        enqueued_at = now(),
        updated_at = now()
    where id = v_row.id;
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.expire_overdue_jobs()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_count integer;
begin
  with overdue as (
    update public.jobs
    set state = 'expired',
        last_retry_advice = 'never',
        revision = revision + 1,
        updated_at = now()
    where state in ('pending', 'ready')
      and deadline_at <= now()
    returning 1
  )
  select count(*) into v_count from overdue;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.is_client_member (
  p_client_id uuid
)
  RETURNS boolean
  LANGUAGE sql
  STABLE
  SECURITY DEFINER
  SET search_path TO ''
  AS $function$
  select exists (
    select 1
    from public.client_members cm
    where cm.client_id = p_client_id
      and cm.auth_user_id = (select auth.uid())
      and cm.status = 'active'
  );
$function$;

CREATE OR REPLACE FUNCTION utrace.is_client_owner (
  p_client_id uuid
)
  RETURNS boolean
  LANGUAGE sql
  STABLE
  SECURITY DEFINER
  SET search_path TO ''
  AS $function$
  select exists (
    select 1
    from public.client_members cm
    where cm.client_id = p_client_id
      and cm.auth_user_id = (select auth.uid())
      and cm.status = 'active'
      and cm.role = 'owner'
  );
$function$;

CREATE OR REPLACE FUNCTION utrace.is_participant (
  p_client_user_id uuid
)
  RETURNS boolean
  LANGUAGE sql
  STABLE
  SECURITY DEFINER
  SET search_path TO ''
  AS $function$
  select exists (
    select 1
    from public.client_users cu
    join public.users u on u.id = cu.utrace_user_id
    where cu.id = p_client_user_id
      and u.auth_user_id = (select auth.uid())
  );
$function$;

CREATE OR REPLACE FUNCTION utrace.next_fencing_token()
  RETURNS bigint
  LANGUAGE sql
  SET search_path TO ''
  AS $function$
  select nextval('utrace.fencing_token_seq');
$function$;

CREATE OR REPLACE FUNCTION utrace.notify_extension_control()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
begin
  perform pg_notify('extension_control', to_jsonb(new) ->> tg_argv[0]);
  return null;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.notify_voice_sideband()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
begin
  perform pg_notify('voice_sideband', to_jsonb(new) ->> tg_argv[0]);
  return null;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.notify_voice_sideband_observations()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
begin
  perform pg_notify('voice_sideband', sessions.interview_session_id::text)
  from (
    select distinct activations.interview_session_id
    from inserted
    join public.telemetry_activations as activations
      on activations.id = inserted.activation_id
    where activations.interview_session_id is not null
  ) as sessions;
  return null;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.preview_warm_pool_shortfall()
  RETURNS TABLE (
    preview_runtime_template_id uuid,
    client_id                   uuid,
    shortfall                   integer
  )
  LANGUAGE sql
  STABLE
  SET search_path TO ''
  AS $function$
  select templates.id,
         templates.client_id,
         (templates.warm_pool_target - coalesce(pool.live, 0))::integer
  from public.preview_runtime_templates as templates
  left join lateral (
    select count(*) as live
    from public.preview_runtime_allocations as allocations
    where allocations.preview_runtime_template_id = templates.id
      and allocations.pool_role = 'warm_pool'
      and allocations.state in ('pending', 'seeding', 'ready')
      and utrace.preview_warm_runtime_fresh(allocations.runtime_expires_at)
  ) as pool on true
  where templates.retired_at is null
    and templates.warm_pool_target > coalesce(pool.live, 0);
$function$;

CREATE OR REPLACE FUNCTION utrace.preview_warm_runtime_fresh (
  p_runtime_expires_at timestamp with time zone
)
  RETURNS boolean
  LANGUAGE sql
  STABLE
  SET search_path TO ''
  AS $function$
  select p_runtime_expires_at is not null
     and p_runtime_expires_at > now() + interval '20 minutes';
$function$;

CREATE OR REPLACE FUNCTION utrace.reclaim_expired_job_leases()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_run public.job_runs%rowtype;
  v_job public.jobs%rowtype;
  v_backoff integer;
  v_count integer := 0;
begin
  for v_run in
    select *
    from public.job_runs
    where state = 'leased'
      and lease_expires_at < now()
    order by lease_expires_at
    for update skip locked
  loop
    update public.job_runs
    set state = 'abandoned',
        finished_at = now(),
        failure_category = 'lease_expired',
        retry_advice = 'after_delay',
        revision = revision + 1,
        updated_at = now()
    where id = v_run.id;

    select * into v_job from public.jobs where id = v_run.job_id for update;

    v_backoff := least(
      v_job.backoff_max_seconds,
      v_job.backoff_base_seconds * power(2, greatest(v_job.run_count - 1, 0))::integer
    );

    if v_job.run_count >= v_job.max_runs or v_job.deadline_at <= now() then
      update public.jobs
      set state = 'failed',
          last_failure_category = 'lease_expired',
          last_retry_advice = 'never',
          revision = revision + 1,
          updated_at = now()
      where id = v_job.id;
    else
      update public.jobs
      set state = 'ready',
          not_before = now() + make_interval(secs => v_backoff),
          last_failure_category = 'lease_expired',
          last_retry_advice = 'after_delay',
          revision = revision + 1,
          updated_at = now()
      where id = v_job.id;
    end if;

    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.reject_evidence_item_rewrite()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
begin
  if tg_op = 'DELETE' then
    raise exception 'public.telemetry_evidence_items is append-only; DELETE is rejected'
      using errcode = 'restrict_violation';
  end if;
  if old.artifact_id is not null and new.artifact_id is distinct from old.artifact_id then
    raise exception 'a promoted evidence item already names its immutable artifact'
      using errcode = 'restrict_violation';
  end if;
  if (to_jsonb(new) - 'artifact_id') is distinct from (to_jsonb(old) - 'artifact_id') then
    raise exception
      'public.telemetry_evidence_items only accepts the artifact_id materialization write'
      using errcode = 'restrict_violation';
  end if;
  return new;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.reject_mutation()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
begin
  raise exception
    'table %.% is append-only; % is rejected',
    tg_table_schema, tg_table_name, tg_op
    using errcode = 'restrict_violation';
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.requeue_stale_outbox (
  p_stale_after interval
)
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_count integer;
begin
  with stale as (
    update public.outbox
    set state = 'pending',
        enqueued_at = null,
        attempts = attempts + 1,
        available_at = now(),
        updated_at = now()
    where state = 'enqueued'
      and enqueued_at < now() - p_stale_after
    returning 1
  )
  select count(*) into v_count from stale;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.rescan_stale_telemetry_readiness (
  p_limit integer
)
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row record;
  v_count integer := 0;
begin
  for v_row in
    select activations.id, activations.client_id, activations.interview_session_id
    from public.telemetry_activations as activations
    join public.interview_sessions as sessions
      on sessions.id = activations.interview_session_id
    where activations.state = 'active'
      and sessions.state <> 'closed'
      and not exists (
        select 1
        from public.telemetry_readiness_projections as fresh
        where fresh.activation_id = activations.id
          and fresh.expires_at > now()
      )
    order by activations.created_at
    limit p_limit
  loop
    if utrace.enqueue_ingestion_job(
      v_row.client_id,
      'ingestion.evaluate_readiness',
      jsonb_build_object('activation_id', v_row.id),
      'ingestion.evaluate_readiness:' || v_row.interview_session_id::text
    ) is not null then
      v_count := v_count + 1;
    end if;
  end loop;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.scan_prepared_workspace_releases()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row record;
  v_job_id uuid;
  v_count integer := 0;
begin
  for v_row in
    select workspaces.id, workspaces.client_id
    from public.prepared_workspaces as workspaces
    join public.product_signals as signals on signals.id = workspaces.product_signal_id
    where workspaces.state in ('preparing', 'ready', 'failed')
      and (
        workspaces.expires_at < now()
        or signals.state in (
          'closed_validated', 'closed_no_implementation', 'closed_blocked', 'canceled'
        )
      )
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'implementation.release_prepared_workspace'
        and concurrency_key = 'prepared_workspace_release:' || v_row.id::text
        and state in ('pending', 'ready', 'leased')
    ) then
      continue;
    end if;

    v_job_id := gen_random_uuid();
    insert into public.jobs (
      id, client_id, job_type, state, input, deadline_at, timeout_seconds, max_runs,
      resource_budget, retryable_categories, backoff_base_seconds, backoff_max_seconds,
      concurrency_key
    )
    values (
      v_job_id,
      v_row.client_id,
      'implementation.release_prepared_workspace',
      'ready',
      jsonb_build_object('prepared_workspace_id', v_row.id),
      now() + interval '1 hour',
      300,
      3,
      jsonb_build_object('workspaces', 1),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      30,
      300,
      'prepared_workspace_release:' || v_row.id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.scan_preview_teardowns()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row record;
  v_job_id uuid;
  v_count integer := 0;
begin
  for v_row in
    select allocations.id, allocations.client_id, 'case_ended' as reason
    from public.preview_runtime_allocations as allocations
    join public.product_signals as signals on signals.id = allocations.product_signal_id
    where allocations.state <> 'terminated'
      and signals.state in (
        'closed_validated', 'closed_no_implementation', 'closed_blocked', 'canceled'
      )
    union all
    select allocations.id, allocations.client_id, 'template_retired' as reason
    from public.preview_runtime_allocations as allocations
    join public.preview_runtime_templates as templates
      on templates.id = allocations.preview_runtime_template_id
    where allocations.pool_role = 'warm_pool'
      and allocations.interview_session_id is null
      and allocations.state <> 'terminated'
      and templates.retired_at is not null
    union all
    select allocations.id, allocations.client_id, 'warm_runtime_expiring' as reason
    from public.preview_runtime_allocations as allocations
    where allocations.pool_role = 'warm_pool'
      and allocations.interview_session_id is null
      and allocations.state <> 'terminated'
      and not utrace.preview_warm_runtime_fresh(allocations.runtime_expires_at)
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'preview.tear_down_allocation'
        and concurrency_key = 'preview_teardown:' || v_row.id::text
        and state in ('pending', 'ready', 'leased')
    ) then
      continue;
    end if;

    v_job_id := gen_random_uuid();
    insert into public.jobs (
      id, client_id, job_type, state, input, deadline_at, timeout_seconds, max_runs,
      resource_budget, retryable_categories, backoff_base_seconds, backoff_max_seconds,
      concurrency_key
    )
    values (
      v_job_id,
      v_row.client_id,
      'preview.tear_down_allocation',
      'ready',
      jsonb_build_object('preview_runtime_allocation_id', v_row.id, 'reason', v_row.reason),
      now() + interval '1 hour',
      900,
      3,
      jsonb_build_object('runtimes', 1),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      30,
      300,
      'preview_teardown:' || v_row.id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.scan_preview_warm_pools()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row record;
  v_job_id uuid;
  v_count integer := 0;
begin
  for v_row in select * from utrace.preview_warm_pool_shortfall()
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'preview.maintain_warm_pool'
        and client_id = v_row.client_id
        and concurrency_key = 'preview_warm_pool:' || v_row.preview_runtime_template_id::text
        and state in ('pending', 'ready', 'leased')
    ) then
      continue;
    end if;

    v_job_id := gen_random_uuid();
    insert into public.jobs (
      id, client_id, job_type, state, input, deadline_at, timeout_seconds, max_runs,
      resource_budget, retryable_categories, backoff_base_seconds, backoff_max_seconds,
      concurrency_key
    )
    values (
      v_job_id,
      v_row.client_id,
      'preview.maintain_warm_pool',
      'ready',
      jsonb_build_object(
        'preview_runtime_template_id', v_row.preview_runtime_template_id,
        'shortfall', v_row.shortfall
      ),
      now() + interval '1 hour',
      1800,
      3,
      jsonb_build_object('runtimes', v_row.shortfall),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      30,
      300,
      'preview_warm_pool:' || v_row.preview_runtime_template_id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.uuid7()
  RETURNS uuid
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_millis bigint := (extract(epoch from clock_timestamp()) * 1000)::bigint;
  v_bytes bytea := pg_catalog.uuid_send(pg_catalog.gen_random_uuid());
begin
  v_bytes := pg_catalog.set_byte(v_bytes, 0, ((v_millis >> 40) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 1, ((v_millis >> 32) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 2, ((v_millis >> 24) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 3, ((v_millis >> 16) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 4, ((v_millis >> 8) & 255)::integer);
  v_bytes := pg_catalog.set_byte(v_bytes, 5, (v_millis & 255)::integer);
  v_bytes := pg_catalog.set_byte(
    v_bytes, 6, ((pg_catalog.get_byte(v_bytes, 6) & 15) | 112)
  );
  v_bytes := pg_catalog.set_byte(
    v_bytes, 8, ((pg_catalog.get_byte(v_bytes, 8) & 63) | 128)
  );
  return pg_catalog.encode(v_bytes, 'hex')::uuid;
end;
$function$;

ALTER TABLE "public"."browser_observations"
  ADD CONSTRAINT "browser_observations_browser_stream_id_fkey" FOREIGN KEY (browser_stream_id) REFERENCES public.browser_streams(id);

ALTER TABLE "public"."browser_stream_gaps"
  ADD CONSTRAINT "browser_stream_gaps_browser_stream_id_fkey" FOREIGN KEY (browser_stream_id) REFERENCES public.browser_streams(id);

ALTER TABLE "public"."case_file_amendments"
  ADD CONSTRAINT "case_file_amendments_case_file_id_fkey" FOREIGN KEY (case_file_id) REFERENCES public.case_files(id);

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_sealed_core" CHECK (((stage = ANY (ARRAY['ready'::public.case_file_stage, 'published'::public.case_file_stage])) = (core_digest IS NOT NULL)));

ALTER TABLE "public"."client_members"
  ADD CONSTRAINT "client_members_auth_user_id_fkey" FOREIGN KEY (auth_user_id) REFERENCES auth.users(id);

ALTER TABLE "public"."acceptance_criteria_agreements"
  ADD CONSTRAINT "acceptance_criteria_agreements_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."artifacts"
  ADD CONSTRAINT "artifacts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."audit_records"
  ADD CONSTRAINT "audit_records_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."browser_observations"
  ADD CONSTRAINT "browser_observations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."browser_stream_gaps"
  ADD CONSTRAINT "browser_stream_gaps_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."browser_streams"
  ADD CONSTRAINT "browser_streams_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."case_file_amendments"
  ADD CONSTRAINT "case_file_amendments_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."client_members"
  ADD CONSTRAINT "client_members_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."client_users"
  ADD CONSTRAINT "client_users_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."code_investigation_findings"
  ADD CONSTRAINT "code_investigation_findings_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."commands"
  ADD CONSTRAINT "commands_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."configuration_releases"
  ADD CONSTRAINT "configuration_releases_authorized_by_auth_user_id_fkey" FOREIGN KEY (authorized_by_auth_user_id) REFERENCES auth.users(id);

ALTER TABLE "public"."configuration_releases"
  ADD CONSTRAINT "configuration_releases_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."audit_records"
  ADD CONSTRAINT "audit_records_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id);

ALTER TABLE "public"."context_bundle_invalidations"
  ADD CONSTRAINT "context_bundle_invalidations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."context_bundle_items"
  ADD CONSTRAINT "context_bundle_items_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."context_bundles"
  ADD CONSTRAINT "context_bundles_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."acceptance_criteria_agreements"
  ADD CONSTRAINT "acceptance_criteria_agreements_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id);

ALTER TABLE "public"."context_bundle_invalidations"
  ADD CONSTRAINT "context_bundle_invalidations_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id);

ALTER TABLE "public"."context_bundle_items"
  ADD CONSTRAINT "context_bundle_items_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id);

ALTER TABLE "public"."context_bundles"
  ADD CONSTRAINT "context_bundles_product_signal_id_purpose_context_revision_key" UNIQUE (product_signal_id, purpose, context_revision);

ALTER TABLE "public"."correlation_projections"
  ADD CONSTRAINT "correlation_projections_browser_stream_id_fkey" FOREIGN KEY (browser_stream_id) REFERENCES public.browser_streams(id);

ALTER TABLE "public"."correlation_projections"
  ADD CONSTRAINT "correlation_projections_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."customer_accounts"
  ADD CONSTRAINT "customer_accounts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."customer_memberships"
  ADD CONSTRAINT "customer_memberships_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."customer_memberships"
  ADD CONSTRAINT "customer_memberships_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."customer_memberships"
  ADD CONSTRAINT "customer_memberships_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id);

ALTER TABLE "public"."deliveries"
  ADD CONSTRAINT "deliveries_blocked_carries_reason" CHECK (((state <> 'blocked'::public.delivery_state) OR (blocker_reason IS NOT NULL)));

ALTER TABLE "public"."deliveries"
  ADD CONSTRAINT "deliveries_case_file_id_fkey" FOREIGN KEY (case_file_id) REFERENCES public.case_files(id);

ALTER TABLE "public"."deliveries"
  ADD CONSTRAINT "deliveries_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."deliveries"
  ADD CONSTRAINT "deliveries_delivered_identities" CHECK (((state <> 'delivered'::public.delivery_state) OR ((pull_request_ref IS NOT NULL) AND (pull_request_number IS
    NOT NULL) AND (verified_head_sha IS NOT NULL) AND (branch_ref IS NOT NULL))));

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_delivery_fk" FOREIGN KEY (delivery_id) REFERENCES public.deliveries(id);

ALTER TABLE "public"."dependency_holds"
  ADD CONSTRAINT "dependency_holds_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."environment_leases"
  ADD CONSTRAINT "environment_leases_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."artifacts"
  ADD CONSTRAINT "artifacts_environment_lease_id_fkey" FOREIGN KEY (environment_lease_id) REFERENCES public.environment_leases(id);

ALTER TABLE "public"."events"
  ADD CONSTRAINT "events_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."events"
  ADD CONSTRAINT "events_stream_kind_stream_id_sequence_key" UNIQUE (stream_kind, stream_id, SEQUENCE);

ALTER TABLE "public"."evidence_records"
  ADD CONSTRAINT "evidence_records_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."context_bundle_invalidations"
  ADD CONSTRAINT "context_bundle_invalidations_caused_by_evidence_id_fkey" FOREIGN KEY (caused_by_evidence_id) REFERENCES public.evidence_records(id);

ALTER TABLE "public"."dependency_holds"
  ADD CONSTRAINT "dependency_holds_affected_evidence_id_fkey" FOREIGN KEY (affected_evidence_id) REFERENCES public.evidence_records(id);

ALTER TABLE "public"."dependency_holds"
  ADD CONSTRAINT "dependency_holds_raised_by_evidence_id_fkey" FOREIGN KEY (raised_by_evidence_id) REFERENCES public.evidence_records(id);

ALTER TABLE "public"."evidence_records"
  ADD CONSTRAINT "evidence_records_supersedes_id_fkey" FOREIGN KEY (supersedes_id) REFERENCES public.evidence_records(id);

ALTER TABLE "public"."evidence_windows"
  ADD CONSTRAINT "evidence_windows_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_evidence_window_id_fkey" FOREIGN KEY (evidence_window_id) REFERENCES public.evidence_windows(id);

ALTER TABLE "public"."extension_binding_offers"
  ADD CONSTRAINT "extension_binding_offers_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."extension_binding_offers"
  ADD CONSTRAINT "extension_binding_offers_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."extension_bindings"
  ADD CONSTRAINT "extension_bindings_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."extension_bindings"
  ADD CONSTRAINT "extension_bindings_offer_fk" FOREIGN KEY (offer_token_id) REFERENCES public.extension_binding_offers(id);

ALTER TABLE "public"."extension_binding_offers"
  ADD CONSTRAINT "extension_binding_offers_consumed_by_binding_id_fkey" FOREIGN KEY (consumed_by_binding_id) REFERENCES public.extension_bindings(id);

ALTER TABLE "public"."extension_bindings"
  ADD CONSTRAINT "extension_bindings_reason_when_not_bound" CHECK (((status = 'bound'::public.extension_binding_status) = (reason IS NULL)));

ALTER TABLE "public"."extension_control_frames"
  ADD CONSTRAINT "extension_control_frames_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."extension_binding_offers"
  ADD CONSTRAINT "extension_binding_offers_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id);

ALTER TABLE "public"."extension_bindings"
  ADD CONSTRAINT "extension_bindings_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id);

ALTER TABLE "public"."extension_control_frames"
  ADD CONSTRAINT "extension_control_frames_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id);

ALTER TABLE "public"."extension_control_streams"
  ADD CONSTRAINT "extension_control_streams_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id);

ALTER TABLE "public"."extension_installations"
  ADD CONSTRAINT "extension_installations_revoked_state" CHECK (((account_link_state = 'revoked'::public.extension_account_link_state) = (revoked_at IS NOT NULL)));

ALTER TABLE "public"."extension_link_codes"
  ADD CONSTRAINT "extension_link_codes_consumed_by_installation_id_fkey" FOREIGN KEY (consumed_by_installation_id) REFERENCES public.extension_installations(id);

ALTER TABLE "public"."extension_possession_proofs"
  ADD CONSTRAINT "extension_possession_proofs_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id);

ALTER TABLE "public"."flow_classification_records"
  ADD CONSTRAINT "flow_classification_records_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."flow_classification_records"
  ADD CONSTRAINT "flow_classification_records_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id);

ALTER TABLE "public"."flow_classification_records"
  ADD CONSTRAINT "flow_classification_retained_matches" CHECK ((retained = (flow_type = initial_flow_type)));

ALTER TABLE "public"."gate_decisions"
  ADD CONSTRAINT "gate_decisions_blocker_only_when_denied" CHECK (((decision = 'no_implementation'::public.gate_decision_kind) = (blocker_reason IS NOT NULL)));

ALTER TABLE "public"."gate_decisions"
  ADD CONSTRAINT "gate_decisions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."gate_decisions"
  ADD CONSTRAINT "gate_decisions_evidence_window_id_fkey" FOREIGN KEY (evidence_window_id) REFERENCES public.evidence_windows(id);

ALTER TABLE "public"."gate_decisions"
  ADD CONSTRAINT "gate_decisions_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id);

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_gate_decision_id_fkey" FOREIGN KEY (gate_decision_id) REFERENCES public.gate_decisions(id);

ALTER TABLE "public"."implementation_attempts"
  ADD CONSTRAINT "implementation_attempts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."implementation_attempts"
  ADD CONSTRAINT "implementation_attempts_gate_decision_id_fkey" FOREIGN KEY (gate_decision_id) REFERENCES public.gate_decisions(id);

ALTER TABLE "public"."artifacts"
  ADD CONSTRAINT "artifacts_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."environment_leases"
  ADD CONSTRAINT "environment_leases_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."implementation_check_runs"
  ADD CONSTRAINT "implementation_check_runs_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."implementation_check_runs"
  ADD CONSTRAINT "implementation_check_runs_environment_lease_id_fkey" FOREIGN KEY (environment_lease_id) REFERENCES public.environment_leases(id);

ALTER TABLE "public"."implementation_check_runs"
  ADD CONSTRAINT "implementation_check_runs_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."implementation_check_runs"
  ADD CONSTRAINT "implementation_check_runs_log_artifact_id_fkey" FOREIGN KEY (log_artifact_id) REFERENCES public.artifacts(id);

ALTER TABLE "public"."implementation_scope_checks"
  ADD CONSTRAINT "implementation_scope_checks_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."implementation_scope_checks"
  ADD CONSTRAINT "implementation_scope_checks_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."inbound_direct_messages"
  ADD CONSTRAINT "inbound_direct_messages_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."inbox"
  ADD CONSTRAINT "inbox_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."inbox"
  ADD CONSTRAINT "inbox_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id);

ALTER TABLE "public"."ingestion_failures"
  ADD CONSTRAINT "ingestion_failures_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."ingestion_receipt_stages"
  ADD CONSTRAINT "ingestion_receipt_stages_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."ingestion_receipt_stages"
  ADD CONSTRAINT "ingestion_receipt_stages_ingestion_receipt_id_stage_key" UNIQUE (ingestion_receipt_id, stage);

ALTER TABLE "public"."ingestion_receipts"
  ADD CONSTRAINT "ingestion_receipts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."ingestion_receipts"
  ADD CONSTRAINT "ingestion_receipts_failure_only_when_refused" CHECK (((outcome = 'accepted'::public.ingestion_outcome) = (failure_code IS NULL)));

ALTER TABLE "public"."browser_observations"
  ADD CONSTRAINT "browser_observations_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id);

ALTER TABLE "public"."browser_stream_gaps"
  ADD CONSTRAINT "browser_stream_gaps_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id);

ALTER TABLE "public"."ingestion_failures"
  ADD CONSTRAINT "ingestion_failures_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id);

ALTER TABLE "public"."ingestion_receipt_stages"
  ADD CONSTRAINT "ingestion_receipt_stages_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id);

ALTER TABLE "public"."intake_author_attributions"
  ADD CONSTRAINT "intake_author_attributions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."intake_author_attributions"
  ADD CONSTRAINT "intake_author_attributions_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."intake_author_attributions"
  ADD CONSTRAINT "intake_author_attributions_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id);

ALTER TABLE "public"."intake_batches"
  ADD CONSTRAINT "intake_batches_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."intake_items"
  ADD CONSTRAINT "intake_items_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."intake_items"
  ADD CONSTRAINT "intake_items_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."intake_items"
  ADD CONSTRAINT "intake_items_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id);

ALTER TABLE "public"."intake_batches"
  ADD CONSTRAINT "intake_batches_intake_item_id_fkey" FOREIGN KEY (intake_item_id) REFERENCES public.intake_items(id);

ALTER TABLE "public"."intake_messages"
  ADD CONSTRAINT "intake_messages_batch_fk" FOREIGN KEY (intake_batch_id) REFERENCES public.intake_batches(id);

ALTER TABLE "public"."intake_messages"
  ADD CONSTRAINT "intake_messages_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."interview_sessions"
  ADD CONSTRAINT "interview_sessions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."interview_sessions"
  ADD CONSTRAINT "interview_sessions_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."acceptance_criteria_agreements"
  ADD CONSTRAINT "acceptance_criteria_agreements_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."context_bundles"
  ADD CONSTRAINT "context_bundles_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."correlation_projections"
  ADD CONSTRAINT "correlation_projections_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."dependency_holds"
  ADD CONSTRAINT "dependency_holds_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."evidence_records"
  ADD CONSTRAINT "evidence_records_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."evidence_windows"
  ADD CONSTRAINT "evidence_windows_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."extension_binding_offers"
  ADD CONSTRAINT "extension_binding_offers_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."extension_bindings"
  ADD CONSTRAINT "extension_bindings_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."extension_control_frames"
  ADD CONSTRAINT "extension_control_frames_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."flow_classification_records"
  ADD CONSTRAINT "flow_classification_records_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."inbound_direct_messages"
  ADD CONSTRAINT "inbound_direct_messages_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."invitations"
  ADD CONSTRAINT "invitations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."invitations"
  ADD CONSTRAINT "invitations_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."invitations"
  ADD CONSTRAINT "invitations_interview_session_fk" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."invitations"
  ADD CONSTRAINT "invitations_superseded_by_id_fkey" FOREIGN KEY (superseded_by_id) REFERENCES public.invitations(id);

ALTER TABLE "public"."job_runs"
  ADD CONSTRAINT "job_runs_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."jobs"
  ADD CONSTRAINT "jobs_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."intake_batches"
  ADD CONSTRAINT "intake_batches_seal_job_id_fkey" FOREIGN KEY (seal_job_id) REFERENCES public.jobs(id);

ALTER TABLE "public"."job_runs"
  ADD CONSTRAINT "job_runs_job_id_fkey" FOREIGN KEY (job_id) REFERENCES public.jobs(id);

ALTER TABLE "public"."offer_budgets"
  ADD CONSTRAINT "offer_budgets_authorized_by_auth_user_id_fkey" FOREIGN KEY (authorized_by_auth_user_id) REFERENCES auth.users(id);

ALTER TABLE "public"."offer_budgets"
  ADD CONSTRAINT "offer_budgets_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."offer_entitlements"
  ADD CONSTRAINT "offer_entitlements_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."offer_entitlements"
  ADD CONSTRAINT "offer_entitlements_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id);

ALTER TABLE "public"."offer_invoice_applications"
  ADD CONSTRAINT "offer_invoice_applications_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."offer_issuance_operations"
  ADD CONSTRAINT "offer_issuance_operations_blocked_carries_reason" CHECK (((state <> 'blocked'::public.offer_issuance_state) OR (blocked_reason IS NOT NULL)));

ALTER TABLE "public"."offer_issuance_operations"
  ADD CONSTRAINT "offer_issuance_operations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."offer_issuance_operations"
  ADD CONSTRAINT "offer_issuance_operations_command_fk" FOREIGN KEY (command_id) REFERENCES public.commands(id);

ALTER TABLE "public"."offer_issuance_operations"
  ADD CONSTRAINT "offer_issuance_operations_issued_is_verified" CHECK (((state <> 'issued'::public.offer_issuance_state) OR ((provider_transaction_ref IS
    NOT NULL) AND (verified_at IS NOT NULL))));

ALTER TABLE "public"."offer_issuance_operations"
  ADD CONSTRAINT "offer_issuance_operations_offer_entitlement_id_fkey" FOREIGN KEY (offer_entitlement_id) REFERENCES public.offer_entitlements(id);

ALTER TABLE "public"."offer_invoice_applications"
  ADD CONSTRAINT "offer_invoice_applications_offer_issuance_operation_id_fkey" FOREIGN KEY (offer_issuance_operation_id) REFERENCES public.offer_issuance_operations(id);

ALTER TABLE "public"."offer_ledger_entries"
  ADD CONSTRAINT "offer_ledger_entries_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."offer_ledger_entries"
  ADD CONSTRAINT "offer_ledger_entries_offer_entitlement_id_fkey" FOREIGN KEY (offer_entitlement_id) REFERENCES public.offer_entitlements(id);

ALTER TABLE "public"."offer_ledger_entries"
  ADD CONSTRAINT "offer_ledger_entries_offer_issuance_operation_id_fkey" FOREIGN KEY (offer_issuance_operation_id) REFERENCES public.offer_issuance_operations(id);

ALTER TABLE "public"."offer_reservations"
  ADD CONSTRAINT "offer_reservations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."outbox"
  ADD CONSTRAINT "outbox_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."outbox"
  ADD CONSTRAINT "outbox_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id);

ALTER TABLE "public"."outbox"
  ADD CONSTRAINT "outbox_job_id_fkey" FOREIGN KEY (job_id) REFERENCES public.jobs(id);

ALTER TABLE "public"."outreach_attempts"
  ADD CONSTRAINT "outreach_attempts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."outreach_attempts"
  ADD CONSTRAINT "outreach_attempts_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."inbound_direct_messages"
  ADD CONSTRAINT "inbound_direct_messages_outreach_attempt_id_fkey" FOREIGN KEY (outreach_attempt_id) REFERENCES public.outreach_attempts(id);

ALTER TABLE "public"."invitations"
  ADD CONSTRAINT "invitations_outreach_attempt_id_fkey" FOREIGN KEY (outreach_attempt_id) REFERENCES public.outreach_attempts(id);

ALTER TABLE "public"."outreach_deliveries"
  ADD CONSTRAINT "outreach_deliveries_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."outreach_deliveries"
  ADD CONSTRAINT "outreach_deliveries_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id);

ALTER TABLE "public"."outreach_deliveries"
  ADD CONSTRAINT "outreach_deliveries_outreach_attempt_id_fkey" FOREIGN KEY (outreach_attempt_id) REFERENCES public.outreach_attempts(id);

ALTER TABLE "public"."outreach_offers"
  ADD CONSTRAINT "outreach_offers_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."outreach_offers"
  ADD CONSTRAINT "outreach_offers_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id);

ALTER TABLE "public"."offer_entitlements"
  ADD CONSTRAINT "offer_entitlements_outreach_offer_id_fkey" FOREIGN KEY (outreach_offer_id) REFERENCES public.outreach_offers(id);

ALTER TABLE "public"."offer_ledger_entries"
  ADD CONSTRAINT "offer_ledger_entries_outreach_offer_id_fkey" FOREIGN KEY (outreach_offer_id) REFERENCES public.outreach_offers(id);

ALTER TABLE "public"."offer_reservations"
  ADD CONSTRAINT "offer_reservations_outreach_offer_id_fkey" FOREIGN KEY (outreach_offer_id) REFERENCES public.outreach_offers(id);

ALTER TABLE "public"."outreach_attempts"
  ADD CONSTRAINT "outreach_attempts_offer_fk" FOREIGN KEY (outreach_offer_id) REFERENCES public.outreach_offers(id);

ALTER TABLE "public"."participant_preflight_records"
  ADD CONSTRAINT "participant_preflight_records_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."participant_preflight_records"
  ADD CONSTRAINT "participant_preflight_records_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."participant_preflight_records"
  ADD CONSTRAINT "participant_preflight_records_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id);

ALTER TABLE "public"."participant_preflight_records"
  ADD CONSTRAINT "participant_preflight_records_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."prepared_workspaces"
  ADD CONSTRAINT "prepared_workspaces_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."prepared_workspaces"
  ADD CONSTRAINT "prepared_workspaces_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."preview_environments"
  ADD CONSTRAINT "preview_environments_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."preview_environments"
  ADD CONSTRAINT "preview_environments_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id);

ALTER TABLE "public"."preview_handoff_codes"
  ADD CONSTRAINT "preview_handoff_codes_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."preview_handoff_codes"
  ADD CONSTRAINT "preview_handoff_codes_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."preview_readiness_results"
  ADD CONSTRAINT "preview_readiness_results_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."preview_routes"
  ADD CONSTRAINT "preview_routes_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."preview_routes"
  ADD CONSTRAINT "preview_routes_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."interview_sessions"
  ADD CONSTRAINT "interview_sessions_current_preview_route_id_fkey" FOREIGN KEY (current_preview_route_id) REFERENCES public.preview_routes(id);

ALTER TABLE "public"."preview_handoff_codes"
  ADD CONSTRAINT "preview_handoff_codes_preview_route_id_fkey" FOREIGN KEY (preview_route_id) REFERENCES public.preview_routes(id);

ALTER TABLE "public"."preview_readiness_results"
  ADD CONSTRAINT "preview_readiness_results_preview_route_id_fkey" FOREIGN KEY (preview_route_id) REFERENCES public.preview_routes(id);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD CONSTRAINT "preview_runtime_allocations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD CONSTRAINT "preview_runtime_allocations_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD CONSTRAINT "preview_runtime_allocations_pinned_configuration_release_i_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id);

ALTER TABLE "public"."extension_binding_offers"
  ADD CONSTRAINT "extension_binding_offers_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);

ALTER TABLE "public"."extension_bindings"
  ADD CONSTRAINT "extension_bindings_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);

ALTER TABLE "public"."interview_sessions"
  ADD CONSTRAINT "interview_sessions_allocation_fk" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);

ALTER TABLE "public"."preview_readiness_results"
  ADD CONSTRAINT "preview_readiness_results_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);

ALTER TABLE "public"."preview_routes"
  ADD CONSTRAINT "preview_routes_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);

ALTER TABLE "public"."preview_runtime_templates"
  ADD CONSTRAINT "preview_runtime_templates_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."preview_runtime_templates"
  ADD CONSTRAINT "preview_runtime_templates_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id);

ALTER TABLE "public"."preview_environments"
  ADD CONSTRAINT "preview_environments_preview_runtime_template_id_fkey" FOREIGN KEY (preview_runtime_template_id) REFERENCES public.preview_runtime_templates(id);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD CONSTRAINT "preview_runtime_allocations_preview_runtime_template_id_fkey" FOREIGN KEY (preview_runtime_template_id) REFERENCES public.preview_runtime_templates(id);

ALTER TABLE "public"."preview_versions"
  ADD CONSTRAINT "preview_versions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."preview_versions"
  ADD CONSTRAINT "preview_versions_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id);

ALTER TABLE "public"."preview_readiness_results"
  ADD CONSTRAINT "preview_readiness_results_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id);

ALTER TABLE "public"."preview_routes"
  ADD CONSTRAINT "preview_routes_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD CONSTRAINT "preview_runtime_allocations_version_fk" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id);

ALTER TABLE "public"."preview_versions"
  ADD CONSTRAINT "preview_versions_superseded_by_preview_version_id_fkey" FOREIGN KEY (superseded_by_preview_version_id) REFERENCES public.preview_versions(id);

ALTER TABLE "public"."product_overview_versions"
  ADD CONSTRAINT "product_overview_versions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."product_signal_concerns"
  ADD CONSTRAINT "product_signal_concerns_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."product_signals"
  ADD CONSTRAINT "product_signals_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."product_signals"
  ADD CONSTRAINT "product_signals_intake_item_id_fkey" FOREIGN KEY (intake_item_id) REFERENCES public.intake_items(id);

ALTER TABLE "public"."product_signals"
  ADD CONSTRAINT "product_signals_originating_client_user_id_fkey" FOREIGN KEY (originating_client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."product_signals"
  ADD CONSTRAINT "product_signals_originating_customer_account_id_fkey" FOREIGN KEY (originating_customer_account_id) REFERENCES public.customer_accounts(id);

ALTER TABLE "public"."product_signals"
  ADD CONSTRAINT "product_signals_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id);

ALTER TABLE "public"."acceptance_criteria_agreements"
  ADD CONSTRAINT "acceptance_criteria_agreements_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."artifacts"
  ADD CONSTRAINT "artifacts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."code_investigation_findings"
  ADD CONSTRAINT "code_investigation_findings_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."context_bundle_invalidations"
  ADD CONSTRAINT "context_bundle_invalidations_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."context_bundles"
  ADD CONSTRAINT "context_bundles_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."deliveries"
  ADD CONSTRAINT "deliveries_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."dependency_holds"
  ADD CONSTRAINT "dependency_holds_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."evidence_records"
  ADD CONSTRAINT "evidence_records_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."evidence_windows"
  ADD CONSTRAINT "evidence_windows_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."flow_classification_records"
  ADD CONSTRAINT "flow_classification_records_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."gate_decisions"
  ADD CONSTRAINT "gate_decisions_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."implementation_attempts"
  ADD CONSTRAINT "implementation_attempts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."intake_items"
  ADD CONSTRAINT "intake_items_product_signal_fk" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."interview_sessions"
  ADD CONSTRAINT "interview_sessions_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."invitations"
  ADD CONSTRAINT "invitations_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."offer_entitlements"
  ADD CONSTRAINT "offer_entitlements_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."outreach_attempts"
  ADD CONSTRAINT "outreach_attempts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."outreach_offers"
  ADD CONSTRAINT "outreach_offers_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."prepared_workspaces"
  ADD CONSTRAINT "prepared_workspaces_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."preview_routes"
  ADD CONSTRAINT "preview_routes_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD CONSTRAINT "preview_runtime_allocations_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."preview_versions"
  ADD CONSTRAINT "preview_versions_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."product_signal_concerns"
  ADD CONSTRAINT "product_signal_concerns_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."provider_refs"
  ADD CONSTRAINT "provider_refs_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."results"
  ADD CONSTRAINT "results_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."results"
  ADD CONSTRAINT "results_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id);

ALTER TABLE "public"."results"
  ADD CONSTRAINT "results_terminal_effect_state" CHECK (((kind = 'terminal'::public.result_kind) = (effect_state IS NOT NULL)));

ALTER TABLE "public"."results"
  ADD CONSTRAINT "results_terminal_fields" CHECK (((kind = 'terminal'::public.result_kind) = (disposition IS NOT NULL)));

ALTER TABLE "public"."routing_decisions"
  ADD CONSTRAINT "routing_decisions_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."routing_decisions"
  ADD CONSTRAINT "routing_decisions_intake_item_id_fkey" FOREIGN KEY (intake_item_id) REFERENCES public.intake_items(id);

ALTER TABLE "public"."routing_decisions"
  ADD CONSTRAINT "routing_decisions_outcome_or_blocker" CHECK ((num_nonnulls(outcome, blocked_reason) = 1));

ALTER TABLE "public"."routing_decisions"
  ADD CONSTRAINT "routing_decisions_pinned_configuration_release_id_fkey" FOREIGN KEY (pinned_configuration_release_id) REFERENCES public.configuration_releases(id);

ALTER TABLE "public"."routing_decisions"
  ADD CONSTRAINT "routing_decisions_product_overview_version_id_fkey" FOREIGN KEY (product_overview_version_id) REFERENCES public.product_overview_versions(id);

ALTER TABLE "public"."routing_decisions"
  ADD CONSTRAINT "routing_decisions_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."semantic_judgements"
  ADD CONSTRAINT "semantic_judgements_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."semantic_judgements"
  ADD CONSTRAINT "semantic_judgements_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id);

ALTER TABLE "public"."semantic_judgements"
  ADD CONSTRAINT "semantic_judgements_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."dependency_holds"
  ADD CONSTRAINT "dependency_holds_semantic_judgement_id_fkey" FOREIGN KEY (semantic_judgement_id) REFERENCES public.semantic_judgements(id);

ALTER TABLE "public"."flow_classification_records"
  ADD CONSTRAINT "flow_classification_records_semantic_judgement_id_fkey" FOREIGN KEY (semantic_judgement_id) REFERENCES public.semantic_judgements(id);

ALTER TABLE "public"."semantic_judgements"
  ADD CONSTRAINT "semantic_judgements_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."server_control_commands"
  ADD CONSTRAINT "server_control_commands_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."server_health_reports"
  ADD CONSTRAINT "server_health_reports_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."server_spans"
  ADD CONSTRAINT "server_spans_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."server_spans"
  ADD CONSTRAINT "server_spans_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id);

ALTER TABLE "public"."server_streams"
  ADD CONSTRAINT "server_streams_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."server_control_commands"
  ADD CONSTRAINT "server_control_commands_server_stream_id_fkey" FOREIGN KEY (server_stream_id) REFERENCES public.server_streams(id);

ALTER TABLE "public"."server_health_reports"
  ADD CONSTRAINT "server_health_reports_server_stream_id_fkey" FOREIGN KEY (server_stream_id) REFERENCES public.server_streams(id);

ALTER TABLE "public"."server_spans"
  ADD CONSTRAINT "server_spans_server_stream_id_fkey" FOREIGN KEY (server_stream_id) REFERENCES public.server_streams(id);

ALTER TABLE "public"."session_projections"
  ADD CONSTRAINT "session_projections_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."session_projections"
  ADD CONSTRAINT "session_projections_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."session_projections"
  ADD CONSTRAINT "session_projections_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."session_projections"
  ADD CONSTRAINT "session_projections_session_id_fkey" FOREIGN KEY (session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."session_readiness"
  ADD CONSTRAINT "session_readiness_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."session_readiness"
  ADD CONSTRAINT "session_readiness_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."source_reports"
  ADD CONSTRAINT "source_reports_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."source_reports"
  ADD CONSTRAINT "source_reports_intake_item_id_fkey" FOREIGN KEY (intake_item_id) REFERENCES public.intake_items(id);

ALTER TABLE "public"."routing_decisions"
  ADD CONSTRAINT "routing_decisions_source_report_id_fkey" FOREIGN KEY (source_report_id) REFERENCES public.source_reports(id);

ALTER TABLE "public"."source_reports"
  ADD CONSTRAINT "source_reports_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."source_reports"
  ADD CONSTRAINT "source_reports_reporting_client_user_id_fkey" FOREIGN KEY (reporting_client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."source_reports"
  ADD CONSTRAINT "source_reports_reporting_customer_account_id_fkey" FOREIGN KEY (reporting_customer_account_id) REFERENCES public.customer_accounts(id);

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_browser_columns" CHECK (((kind <> 'browser'::public.telemetry_producer_kind) OR ((document_attachment_id IS NOT NULL) AND (document_nonce IS
    NOT NULL) AND (document_origin IS NOT NULL) AND (sdk_installation_id IS NOT NULL) AND (attestation_expires_at IS NOT NULL) AND (annotation_manifest_version IS
    NOT NULL) AND (annotation_manifest_digest IS NOT NULL))));

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_customer_account_id_fkey" FOREIGN KEY (customer_account_id) REFERENCES public.customer_accounts(id);

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."browser_observations"
  ADD CONSTRAINT "browser_observations_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."browser_streams"
  ADD CONSTRAINT "browser_streams_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."correlation_projections"
  ADD CONSTRAINT "correlation_projections_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."correlation_projections"
  ADD CONSTRAINT "correlation_projections_server_activation_id_fkey" FOREIGN KEY (server_activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."ingestion_failures"
  ADD CONSTRAINT "ingestion_failures_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."ingestion_receipts"
  ADD CONSTRAINT "ingestion_receipts_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."server_control_commands"
  ADD CONSTRAINT "server_control_commands_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."server_health_reports"
  ADD CONSTRAINT "server_health_reports_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."server_spans"
  ADD CONSTRAINT "server_spans_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."server_streams"
  ADD CONSTRAINT "server_streams_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_preview_environment_fk" FOREIGN KEY (preview_environment_id) REFERENCES public.preview_environments(id) NOT VALID;

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id);

ALTER TABLE "public"."telemetry_activations"
  ADD CONSTRAINT "telemetry_activations_server_columns" CHECK (((kind <> 'server'::public.telemetry_producer_kind) OR ((server_stream_id IS NOT NULL) AND (service_instance_id IS
    NOT NULL) AND (service_key IS NOT NULL) AND (preview_entry_token_digest IS NOT NULL) AND (semantic_convention_version IS NOT NULL) AND (probe_id IS NOT NULL))));

ALTER TABLE "public"."telemetry_artifacts"
  ADD CONSTRAINT "telemetry_artifacts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."ingestion_receipts"
  ADD CONSTRAINT "ingestion_receipts_raw_artifact_id_fkey" FOREIGN KEY (raw_artifact_id) REFERENCES public.telemetry_artifacts(id);

ALTER TABLE "public"."telemetry_credential_revocations"
  ADD CONSTRAINT "telemetry_credential_revocations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."telemetry_credentials"
  ADD CONSTRAINT "telemetry_credentials_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."telemetry_credentials"
  ADD CONSTRAINT "telemetry_credentials_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."ingestion_receipts"
  ADD CONSTRAINT "ingestion_receipts_telemetry_credential_id_fkey" FOREIGN KEY (telemetry_credential_id) REFERENCES public.telemetry_credentials(id);

ALTER TABLE "public"."telemetry_credential_revocations"
  ADD CONSTRAINT "telemetry_credential_revocations_telemetry_credential_id_fkey" FOREIGN KEY (telemetry_credential_id) REFERENCES public.telemetry_credentials(id);

ALTER TABLE "public"."telemetry_evidence_items"
  ADD CONSTRAINT "telemetry_evidence_items_artifact_id_fkey" FOREIGN KEY (artifact_id) REFERENCES public.telemetry_artifacts(id);

ALTER TABLE "public"."telemetry_evidence_items"
  ADD CONSTRAINT "telemetry_evidence_items_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."telemetry_evidence_items"
  ADD CONSTRAINT "telemetry_evidence_items_evidence_window_id_source_class_so_key" UNIQUE (evidence_window_id, source_class, source_record_id, claim_ref);

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD CONSTRAINT "telemetry_evidence_windows_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD CONSTRAINT "telemetry_evidence_windows_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD CONSTRAINT "telemetry_evidence_windows_control_plane_evidence_window_i_fkey" FOREIGN KEY (control_plane_evidence_window_id) REFERENCES public.evidence_windows(id);

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD CONSTRAINT "telemetry_evidence_windows_frozen_has_time" CHECK (((mode = 'frozen'::public.evidence_window_mode) = (frozen_at IS NOT NULL)));

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD CONSTRAINT "telemetry_evidence_windows_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."telemetry_evidence_items"
  ADD CONSTRAINT "telemetry_evidence_items_evidence_window_id_fkey" FOREIGN KEY (evidence_window_id) REFERENCES public.telemetry_evidence_windows(id);

ALTER TABLE "public"."telemetry_evidence_windows"
  ADD CONSTRAINT "telemetry_evidence_windows_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."telemetry_identity_conflicts"
  ADD CONSTRAINT "telemetry_identity_conflicts_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."telemetry_identity_conflicts"
  ADD CONSTRAINT "telemetry_identity_conflicts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."telemetry_identity_conflicts"
  ADD CONSTRAINT "telemetry_identity_conflicts_conflicting_ingestion_receipt_fkey" FOREIGN KEY (conflicting_ingestion_receipt_id) REFERENCES public.ingestion_receipts(id);

ALTER TABLE "public"."telemetry_identity_conflicts"
  ADD CONSTRAINT "telemetry_identity_conflicts_first_ingestion_receipt_id_fkey" FOREIGN KEY (first_ingestion_receipt_id) REFERENCES public.ingestion_receipts(id);

ALTER TABLE "public"."telemetry_readiness_projections"
  ADD CONSTRAINT "telemetry_readiness_projectio_client_id_activation_id_kind__key" UNIQUE (client_id, activation_id, kind, revision);

ALTER TABLE "public"."telemetry_readiness_projections"
  ADD CONSTRAINT "telemetry_readiness_projections_activation_id_fkey" FOREIGN KEY (activation_id) REFERENCES public.telemetry_activations(id);

ALTER TABLE "public"."telemetry_readiness_projections"
  ADD CONSTRAINT "telemetry_readiness_projections_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."telemetry_readiness_projections"
  ADD CONSTRAINT "telemetry_readiness_projections_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."users"
  ADD CONSTRAINT "users_auth_user_id_fkey" FOREIGN KEY (auth_user_id) REFERENCES auth.users(id);

ALTER TABLE "public"."client_users"
  ADD CONSTRAINT "client_users_utrace_user_id_fkey" FOREIGN KEY (utrace_user_id) REFERENCES public.users(id);

ALTER TABLE "public"."extension_installations"
  ADD CONSTRAINT "extension_installations_utrace_user_id_fkey" FOREIGN KEY (utrace_user_id) REFERENCES public.users(id);

ALTER TABLE "public"."extension_link_codes"
  ADD CONSTRAINT "extension_link_codes_utrace_user_id_fkey" FOREIGN KEY (utrace_user_id) REFERENCES public.users(id);

ALTER TABLE "public"."extension_preferences"
  ADD CONSTRAINT "extension_preferences_utrace_user_id_fkey" FOREIGN KEY (utrace_user_id) REFERENCES public.users(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_client_user_id_fkey" FOREIGN KEY (client_user_id) REFERENCES public.client_users(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_evidence_window_id_fkey" FOREIGN KEY (evidence_window_id) REFERENCES public.evidence_windows(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_frozen_result_when_completed" CHECK (((state = 'completed'::public.validation_attempt_state) = (frozen_result IS NOT NULL)));

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_outcome_when_completed" CHECK (((state = 'completed'::public.validation_attempt_state) = (outcome IS NOT NULL)));

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_validation_attempt_id_fkey" FOREIGN KEY (validation_attempt_id) REFERENCES public.validation_attempts(id);

ALTER TABLE "public"."deliveries"
  ADD CONSTRAINT "deliveries_validation_attempt_id_fkey" FOREIGN KEY (validation_attempt_id) REFERENCES public.validation_attempts(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id);

ALTER TABLE "public"."validation_attempts"
  ADD CONSTRAINT "validation_attempts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."validation_criteria"
  ADD CONSTRAINT "validation_criteria_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."validation_criteria"
  ADD CONSTRAINT "validation_criteria_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."validation_outcomes"
  ADD CONSTRAINT "validation_outcomes_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."validation_outcomes"
  ADD CONSTRAINT "validation_outcomes_confirmation_evidence_record_id_fkey" FOREIGN KEY (confirmation_evidence_record_id) REFERENCES public.evidence_records(id);

ALTER TABLE "public"."validation_outcomes"
  ADD CONSTRAINT "validation_outcomes_validation_attempt_id_fkey" FOREIGN KEY (validation_attempt_id) REFERENCES public.validation_attempts(id);

ALTER TABLE "public"."validation_outcomes"
  ADD CONSTRAINT "validation_outcomes_validation_criterion_id_fkey" FOREIGN KEY (validation_criterion_id) REFERENCES public.validation_criteria(id);

ALTER TABLE "public"."visual_command_dispatches"
  ADD CONSTRAINT "visual_command_dispatches_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."visual_command_dispatches"
  ADD CONSTRAINT "visual_command_dispatches_command_id_fkey" FOREIGN KEY (command_id) REFERENCES public.commands(id);

ALTER TABLE "public"."visual_command_dispatches"
  ADD CONSTRAINT "visual_command_dispatches_extension_binding_id_fkey" FOREIGN KEY (extension_binding_id) REFERENCES public.extension_bindings(id);

ALTER TABLE "public"."visual_command_dispatches"
  ADD CONSTRAINT "visual_command_dispatches_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."visual_command_dispatches"
  ADD CONSTRAINT "visual_command_dispatches_reason_when_not_applied"
    CHECK (((outcome IS NULL) OR ((outcome = 'applied'::public.visual_command_outcome) = (outcome_reason IS NULL))));

ALTER TABLE "public"."voice_connections"
  ADD CONSTRAINT "voice_connections_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."voice_connections"
  ADD CONSTRAINT "voice_connections_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."voice_connections"
  ADD CONSTRAINT "voice_connections_superseded_by_id_fkey" FOREIGN KEY (superseded_by_id) REFERENCES public.voice_connections(id);

ALTER TABLE "public"."voice_delegations"
  ADD CONSTRAINT "voice_delegations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."voice_delegations"
  ADD CONSTRAINT "voice_delegations_context_bundle_id_fkey" FOREIGN KEY (context_bundle_id) REFERENCES public.context_bundles(id);

ALTER TABLE "public"."voice_delegations"
  ADD CONSTRAINT "voice_delegations_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."voice_delegations"
  ADD CONSTRAINT "voice_delegations_voice_connection_id_fkey" FOREIGN KEY (voice_connection_id) REFERENCES public.voice_connections(id);

ALTER TABLE "public"."voice_playback_segments"
  ADD CONSTRAINT "voice_playback_segments_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."voice_playback_segments"
  ADD CONSTRAINT "voice_playback_segments_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."voice_playback_segments"
  ADD CONSTRAINT "voice_playback_segments_voice_connection_id_fkey" FOREIGN KEY (voice_connection_id) REFERENCES public.voice_connections(id);

ALTER TABLE "public"."voice_transcript_fragments"
  ADD CONSTRAINT "voice_transcript_fragments_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."voice_transcript_fragments"
  ADD CONSTRAINT "voice_transcript_fragments_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."voice_transcript_fragments"
  ADD CONSTRAINT "voice_transcript_fragments_voice_connection_id_fkey" FOREIGN KEY (voice_connection_id) REFERENCES public.voice_connections(id);

CREATE INDEX artifacts_attempt_idx ON public.artifacts USING btree (implementation_attempt_id, artifact_type);

CREATE INDEX artifacts_signal_idx ON public.artifacts USING btree (product_signal_id);

CREATE INDEX audit_records_client_idx ON public.audit_records USING btree (client_id, recorded_at);

CREATE INDEX audit_records_subject_idx ON public.audit_records USING btree (subject_kind, subject_id, recorded_at);

CREATE UNIQUE INDEX browser_observations_identity ON public.browser_observations USING btree (client_id, observation_id);

CREATE INDEX browser_observations_request_idx ON public.browser_observations USING btree (client_id, browser_request_id)
  WHERE (browser_request_id IS NOT NULL);

CREATE INDEX browser_observations_stream_idx ON public.browser_observations USING btree (browser_stream_id, export_sequence);

CREATE INDEX browser_observations_target_idx ON public.browser_observations USING btree (client_id, target_id, occurred_at)
  WHERE (target_id IS NOT NULL);

CREATE INDEX browser_observations_trace_idx ON public.browser_observations USING btree (client_id, trace_id)
  WHERE (trace_id IS NOT NULL);

CREATE INDEX browser_stream_gaps_stream_idx ON public.browser_stream_gaps USING btree (browser_stream_id, first_source_sequence);

CREATE INDEX browser_streams_activation_idx ON public.browser_streams USING btree (activation_id, state);

CREATE INDEX case_file_amendments_case_file_idx ON public.case_file_amendments USING btree (case_file_id, ordinal);

CREATE INDEX case_files_client_stage_idx ON public.case_files USING btree (client_id, stage);

CREATE INDEX client_members_auth_user_idx ON public.client_members USING btree (auth_user_id, client_id)
  WHERE (status = 'active'::public.membership_status);

CREATE UNIQUE INDEX client_members_one_active_identity ON public.client_members USING btree (client_id, auth_user_id)
  WHERE (status = 'active'::public.membership_status);

CREATE UNIQUE INDEX client_members_one_active_owner ON public.client_members USING btree (client_id)
  WHERE ((status = 'active'::public.membership_status) AND (ROLE = 'owner'::public.client_member_role));

CREATE INDEX client_users_user_idx ON public.client_users USING btree (utrace_user_id);

CREATE INDEX code_investigation_findings_investigation_idx ON public.code_investigation_findings USING btree (investigation_id);

CREATE INDEX code_investigation_findings_signal_idx ON public.code_investigation_findings USING btree (product_signal_id, path);

CREATE INDEX commands_cancellation_scope_idx ON public.commands USING btree (cancellation_scope_id)
  WHERE (state <> 'terminal'::public.command_state);

CREATE INDEX commands_target_idx ON public.commands USING btree (target_kind, target_id);

CREATE INDEX configuration_releases_client_idx ON public.configuration_releases USING btree (client_id, release_number DESC);

CREATE INDEX context_bundle_invalidations_signal_idx ON public.context_bundle_invalidations USING btree (product_signal_id, invalidated_at DESC);

CREATE INDEX context_bundle_items_source_idx ON public.context_bundle_items USING btree (source_kind, source_id);

CREATE INDEX context_bundles_signal_idx ON public.context_bundles USING btree (product_signal_id, purpose, context_revision DESC);

CREATE UNIQUE INDEX correlation_projections_identity ON public.correlation_projections USING btree (client_id, activation_id, trace_id);

CREATE INDEX correlation_projections_session_idx ON public.correlation_projections USING btree (interview_session_id, status);

CREATE INDEX customer_memberships_client_user_idx ON public.customer_memberships USING btree (client_user_id);

CREATE INDEX deliveries_signal_idx ON public.deliveries USING btree (product_signal_id, state);

CREATE INDEX dependency_holds_open_idx ON public.dependency_holds USING btree (product_signal_id)
  WHERE (released_at IS NULL);

CREATE UNIQUE INDEX environment_leases_one_active ON public.environment_leases USING btree (implementation_attempt_id)
  WHERE (state = 'active'::public.environment_lease_state);

CREATE INDEX events_client_stream_idx ON public.events USING btree (client_id, stream_kind, stream_id, SEQUENCE);

CREATE INDEX evidence_records_signal_idx ON public.evidence_records USING btree (product_signal_id, evidence_class);

CREATE INDEX evidence_windows_signal_idx ON public.evidence_windows USING btree (product_signal_id);

CREATE INDEX extension_binding_offers_session_idx ON public.extension_binding_offers USING btree (interview_session_id, expires_at DESC);

CREATE UNIQUE INDEX extension_bindings_offer_consumed_once ON public.extension_bindings USING btree (offer_token_id)
  WHERE (offer_token_id IS NOT NULL);

CREATE UNIQUE INDEX extension_bindings_one_current ON public.extension_bindings USING btree (interview_session_id)
  WHERE (status = 'bound'::public.extension_binding_status);

CREATE INDEX extension_bindings_session_idx ON public.extension_bindings USING btree (interview_session_id, status);

CREATE UNIQUE INDEX extension_control_frames_live_sequence ON public.extension_control_frames USING btree (extension_installation_id, SEQUENCE)
  WHERE (superseded_at IS NULL);

CREATE INDEX extension_control_frames_stream_idx ON public.extension_control_frames USING btree (extension_installation_id, SEQUENCE DESC);

CREATE INDEX extension_installations_user_idx ON public.extension_installations USING btree (utrace_user_id)
  WHERE (account_link_state = 'linked'::public.extension_account_link_state);

CREATE INDEX extension_link_codes_user_idx ON public.extension_link_codes USING btree (utrace_user_id, expires_at DESC);

CREATE INDEX extension_possession_proofs_issued_idx ON public.extension_possession_proofs USING btree (issued_at);

CREATE INDEX gate_decisions_signal_idx ON public.gate_decisions USING btree (product_signal_id, decided_at DESC);

CREATE UNIQUE INDEX implementation_attempts_one_in_flight ON public.implementation_attempts USING btree (product_signal_id)
  WHERE
    (state = ANY (ARRAY['authorized'::public.implementation_attempt_state, 'queued'::public.implementation_attempt_state,
    'acquiring_environment'::public.implementation_attempt_state,
    'executing'::public.implementation_attempt_state,
    'checking'::public.implementation_attempt_state,
    'publishing_candidate'::public.implementation_attempt_state, 'paused'::public.implementation_attempt_state, 'infrastructure_blocked'::public.implementation_attempt_state]));

CREATE INDEX implementation_check_runs_attempt_idx ON public.implementation_check_runs USING btree (implementation_attempt_id, phase, cycle_index);

CREATE INDEX implementation_scope_checks_attempt_idx ON public.implementation_scope_checks USING btree (implementation_attempt_id, cycle_index);

CREATE INDEX inbound_direct_messages_sender_idx ON public.inbound_direct_messages USING btree (PROVIDER, sender_provider_identity, occurred_at);

CREATE UNIQUE INDEX inbox_one_terminal_per_command ON public.inbox USING btree (command_id)
  WHERE ((message_kind = 'terminal_result'::text) AND (command_id IS NOT NULL));

CREATE INDEX inbox_unprocessed_idx ON public.inbox USING btree (received_at)
  WHERE (processed_at IS NULL);

CREATE INDEX ingestion_failures_activation_idx ON public.ingestion_failures USING btree (activation_id, occurred_at DESC);

CREATE INDEX ingestion_failures_code_idx ON public.ingestion_failures USING btree (code, occurred_at DESC);

CREATE INDEX ingestion_receipt_stages_receipt_idx ON public.ingestion_receipt_stages USING btree (ingestion_receipt_id, entered_at);

CREATE INDEX ingestion_receipts_activation_idx ON public.ingestion_receipts USING btree (activation_id, received_at DESC);

CREATE INDEX ingestion_receipts_stage_idx ON public.ingestion_receipts USING btree (outcome, stage, received_at);

CREATE UNIQUE INDEX ingestion_receipts_stream_batch ON public.ingestion_receipts USING btree (client_id, source_stream_id, batch_id);

CREATE UNIQUE INDEX intake_batches_one_open ON public.intake_batches USING btree (client_id, PROVIDER, channel_ref, author_provider_identity)
  WHERE (state = 'open'::public.intake_batch_state);

CREATE INDEX intake_items_client_state_idx ON public.intake_items USING btree (client_id, state);

CREATE INDEX intake_messages_batch_idx ON public.intake_messages USING btree (intake_batch_id, ordinal);

CREATE UNIQUE INDEX interview_sessions_one_open ON public.interview_sessions USING btree (product_signal_id)
  WHERE (state <> 'closed'::public.interview_session_state);

CREATE INDEX interview_sessions_participant_idx ON public.interview_sessions USING btree (client_user_id);

CREATE INDEX interview_sessions_signal_idx ON public.interview_sessions USING btree (product_signal_id, state);

CREATE UNIQUE INDEX invitations_one_live_per_signal ON public.invitations USING btree (product_signal_id)
  WHERE ((revoked_at IS NULL) AND (superseded_by_id IS NULL));

CREATE INDEX invitations_signal_idx ON public.invitations USING btree (product_signal_id);

CREATE INDEX job_runs_lease_scan_idx ON public.job_runs USING btree (lease_expires_at)
  WHERE (state = 'leased'::public.job_run_state);

CREATE UNIQUE INDEX job_runs_one_leased ON public.job_runs USING btree (job_id)
  WHERE (state = 'leased'::public.job_run_state);

CREATE INDEX jobs_cancellation_scope_idx ON public.jobs USING btree (cancellation_scope_id)
  WHERE (state = ANY (ARRAY['pending'::public.job_state, 'ready'::public.job_state, 'leased'::public.job_state]));

CREATE INDEX jobs_claimable_idx ON public.jobs USING btree (not_before, created_at)
  WHERE (state = 'ready'::public.job_state);

CREATE UNIQUE INDEX jobs_one_leased_per_concurrency_key ON public.jobs USING btree (client_id, concurrency_key)
  WHERE ((state = 'leased'::public.job_state) AND (concurrency_key IS NOT NULL));

CREATE INDEX offer_invoice_applications_client_idx ON public.offer_invoice_applications USING btree (client_id, billing_customer_ref);

CREATE UNIQUE INDEX offer_issuance_operations_idempotency_key_idx ON public.offer_issuance_operations USING btree (client_id, idempotency_key);

CREATE INDEX offer_ledger_entries_client_currency_idx ON public.offer_ledger_entries USING btree (client_id, currency, recorded_at);

CREATE INDEX offer_reservations_open_idx ON public.offer_reservations USING btree (client_id, currency)
  WHERE (released_at IS NULL);

CREATE INDEX outbox_pending_idx ON public.outbox USING btree (available_at, created_at)
  WHERE (state = 'pending'::public.outbox_state);

CREATE INDEX outreach_attempts_signal_idx ON public.outreach_attempts USING btree (product_signal_id, state);

CREATE INDEX outreach_deliveries_attempt_idx ON public.outreach_deliveries USING btree (outreach_attempt_id, created_at DESC);

CREATE INDEX participant_preflight_records_session_idx ON public.participant_preflight_records USING btree (interview_session_id, recorded_at DESC);

CREATE INDEX preview_environments_release_idx ON public.preview_environments USING btree (pinned_configuration_release_id);

CREATE INDEX preview_handoff_codes_session_idx ON public.preview_handoff_codes USING btree (interview_session_id);

CREATE INDEX preview_readiness_results_allocation_idx ON public.preview_readiness_results USING btree (preview_runtime_allocation_id, observed_at DESC);

CREATE UNIQUE INDEX preview_routes_one_active_per_host ON public.preview_routes USING btree (public_preview_host)
  WHERE (state = 'active'::public.preview_route_state);

CREATE INDEX preview_routes_session_idx ON public.preview_routes USING btree (interview_session_id, state);

CREATE UNIQUE INDEX preview_runtime_allocations_one_base_per_session ON public.preview_runtime_allocations USING btree (interview_session_id)
  WHERE ((allocation_kind = 'base'::public.preview_allocation_kind) AND (interview_session_id IS NOT NULL));

CREATE UNIQUE INDEX preview_runtime_allocations_one_live_candidate ON public.preview_runtime_allocations USING btree (preview_version_id)
  WHERE ((allocation_kind = 'candidate'::public.preview_allocation_kind) AND (preview_version_id IS NOT NULL) AND (state <> 'terminated'::public.preview_runtime_allocation_state));

CREATE INDEX preview_runtime_allocations_pool_idx ON public.preview_runtime_allocations USING btree (preview_runtime_template_id, pool_role, state);

CREATE INDEX preview_runtime_allocations_signal_idx ON public.preview_runtime_allocations USING btree (product_signal_id, state);

CREATE UNIQUE INDEX preview_runtime_templates_one_current ON public.preview_runtime_templates USING btree (client_id)
  WHERE (retired_at IS NULL);

CREATE INDEX preview_versions_signal_idx ON public.preview_versions USING btree (product_signal_id, state);

CREATE UNIQUE INDEX product_overview_versions_one_active ON public.product_overview_versions USING btree (client_id)
  WHERE (state = 'active'::public.product_overview_state);

CREATE INDEX product_signal_concerns_signal_idx ON public.product_signal_concerns USING btree (product_signal_id, concern_revision DESC);

CREATE INDEX product_signals_cancellation_scope_idx ON public.product_signals USING btree (cancellation_scope_id);

CREATE INDEX product_signals_client_state_idx ON public.product_signals USING btree (client_id, state);

CREATE INDEX product_signals_participant_idx ON public.product_signals USING btree (originating_client_user_id);

CREATE INDEX provider_refs_owner_idx ON public.provider_refs USING btree (owner_kind, owner_id);

CREATE INDEX results_command_idx ON public.results USING btree (command_id, received_at);

CREATE UNIQUE INDEX results_one_terminal ON public.results USING btree (command_id)
  WHERE (kind = 'terminal'::public.result_kind);

CREATE INDEX routing_decisions_client_idx ON public.routing_decisions USING btree (client_id, decided_at DESC);

CREATE INDEX semantic_judgements_signal_idx ON public.semantic_judgements USING btree (product_signal_id, purpose, created_at DESC);

CREATE INDEX server_control_commands_stream_idx ON public.server_control_commands USING btree (server_stream_id, issued_at DESC);

CREATE INDEX server_health_reports_activation_idx ON public.server_health_reports USING btree (activation_id, reported_at DESC);

CREATE INDEX server_health_reports_stream_idx ON public.server_health_reports USING btree (server_stream_id, reported_at DESC);

CREATE UNIQUE INDEX server_spans_identity ON public.server_spans USING btree (client_id, activation_id, trace_id, span_id);

CREATE INDEX server_spans_operation_idx ON public.server_spans USING btree (client_id, registered_operation, accepted_at)
  WHERE (registered_operation IS NOT NULL);

CREATE INDEX server_spans_parent_idx ON public.server_spans USING btree (client_id, trace_id, parent_span_id)
  WHERE (parent_span_id IS NOT NULL);

CREATE INDEX server_spans_trace_idx ON public.server_spans USING btree (client_id, trace_id);

CREATE INDEX server_streams_activation_idx ON public.server_streams USING btree (activation_id, state);

CREATE INDEX session_projections_participant_idx ON public.session_projections USING btree (client_user_id);

CREATE UNIQUE INDEX telemetry_activations_document_nonce ON public.telemetry_activations USING btree (client_id, document_nonce)
  WHERE (kind = 'browser'::public.telemetry_producer_kind);

CREATE UNIQUE INDEX telemetry_activations_document ON public.telemetry_activations USING btree (document_attachment_id)
  WHERE (kind = 'browser'::public.telemetry_producer_kind);

CREATE UNIQUE INDEX telemetry_activations_server_stream ON public.telemetry_activations USING btree (server_stream_id)
  WHERE (kind = 'server'::public.telemetry_producer_kind);

CREATE INDEX telemetry_activations_session_idx ON public.telemetry_activations USING btree (interview_session_id, kind, state);

CREATE UNIQUE INDEX telemetry_artifacts_client_content ON public.telemetry_artifacts USING btree (client_id, artifact_class, content_sha256);

CREATE INDEX telemetry_credential_revocations_credential_idx ON public.telemetry_credential_revocations USING btree (telemetry_credential_id);

CREATE INDEX telemetry_credentials_activation_idx ON public.telemetry_credentials USING btree (activation_id, state);

CREATE INDEX telemetry_evidence_items_window_idx ON public.telemetry_evidence_items USING btree (evidence_window_id, source_class);

CREATE INDEX telemetry_evidence_windows_signal_idx ON public.telemetry_evidence_windows USING btree (product_signal_id, mode, created_at DESC);

CREATE INDEX telemetry_identity_conflicts_stream_idx ON public.telemetry_identity_conflicts USING btree (source_stream_id, detected_at DESC);

CREATE INDEX telemetry_readiness_current_idx ON public.telemetry_readiness_projections USING btree (activation_id, kind, revision DESC);

CREATE INDEX telemetry_readiness_session_idx ON public.telemetry_readiness_projections USING btree (interview_session_id, kind, revision DESC);

CREATE UNIQUE INDEX validation_attempts_one_open ON public.validation_attempts USING btree (product_signal_id)
  WHERE (state = ANY (ARRAY['pending'::public.validation_attempt_state, 'in_progress'::public.validation_attempt_state, 'interrupted'::public.validation_attempt_state]));

CREATE INDEX validation_attempts_signal_idx ON public.validation_attempts USING btree (product_signal_id, state);

CREATE INDEX visual_command_dispatches_pending_idx ON public.visual_command_dispatches USING btree (interview_session_id)
  WHERE (outcome IS NULL);

CREATE INDEX visual_command_dispatches_session_idx ON public.visual_command_dispatches USING btree (interview_session_id, created_at DESC);

CREATE INDEX visual_command_dispatches_unrouted_idx ON public.visual_command_dispatches USING btree (extension_binding_id, created_at)
  WHERE ((routed_at IS NULL) AND (outcome IS NULL));

CREATE UNIQUE INDEX voice_connections_one_current ON public.voice_connections USING btree (interview_session_id)
  WHERE (state <> 'closed'::public.voice_connection_state);

CREATE INDEX voice_connections_session_idx ON public.voice_connections USING btree (interview_session_id, state);

CREATE INDEX voice_delegations_session_idx ON public.voice_delegations USING btree (interview_session_id, created_at DESC);

CREATE INDEX voice_transcript_fragments_session_idx ON public.voice_transcript_fragments USING btree (interview_session_id, start_ms);

CREATE TRIGGER acceptance_criteria_agreements_immutable
  BEFORE DELETE OR UPDATE ON public.acceptance_criteria_agreements
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER artifacts_immutable_body
  BEFORE DELETE ON public.artifacts
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER audit_records_immutable
  BEFORE DELETE OR UPDATE ON public.audit_records
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER browser_observations_immutable
  BEFORE DELETE OR UPDATE ON public.browser_observations
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER browser_observations_notify_voice_sideband
  AFTER INSERT ON public.browser_observations REFERENCING NEW TABLE AS inserted
  FOR EACH STATEMENT
  EXECUTE FUNCTION utrace.notify_voice_sideband_observations();

CREATE TRIGGER case_file_amendments_immutable
  BEFORE DELETE OR UPDATE ON public.case_file_amendments
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER code_investigation_findings_immutable
  BEFORE DELETE OR UPDATE ON public.code_investigation_findings
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER commands_notify_voice_sideband
  AFTER INSERT ON public.commands
  FOR EACH ROW
  WHEN ((new.command_type = 'voice.set_input_muted'::text))
  EXECUTE FUNCTION utrace.notify_voice_sideband('target_id');

CREATE TRIGGER configuration_releases_immutable
  BEFORE DELETE OR UPDATE ON public.configuration_releases
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER context_bundle_invalidations_immutable
  BEFORE DELETE OR UPDATE ON public.context_bundle_invalidations
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER context_bundle_items_immutable
  BEFORE DELETE OR UPDATE ON public.context_bundle_items
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER context_bundles_immutable
  BEFORE DELETE OR UPDATE ON public.context_bundles
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER events_immutable
  BEFORE DELETE OR UPDATE ON public.events
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER evidence_records_immutable
  BEFORE DELETE OR UPDATE ON public.evidence_records
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER evidence_windows_immutable
  BEFORE DELETE OR UPDATE ON public.evidence_windows
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER extension_bindings_notify_extension_control
  AFTER INSERT OR UPDATE ON public.extension_bindings
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_extension_control('interview_session_id');

CREATE TRIGGER extension_possession_proofs_immutable
  BEFORE DELETE OR UPDATE ON public.extension_possession_proofs
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER flow_classification_records_immutable
  BEFORE DELETE OR UPDATE ON public.flow_classification_records
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER gate_decisions_immutable
  BEFORE DELETE OR UPDATE ON public.gate_decisions
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER implementation_scope_checks_immutable
  BEFORE DELETE OR UPDATE ON public.implementation_scope_checks
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER inbound_direct_messages_immutable
  BEFORE DELETE OR UPDATE ON public.inbound_direct_messages
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER ingestion_failures_immutable
  BEFORE DELETE OR UPDATE ON public.ingestion_failures
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER ingestion_receipt_stages_immutable
  BEFORE DELETE OR UPDATE ON public.ingestion_receipt_stages
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER intake_messages_immutable
  BEFORE DELETE OR UPDATE ON public.intake_messages
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

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

CREATE TRIGGER offer_budgets_immutable
  BEFORE DELETE OR UPDATE ON public.offer_budgets
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER offer_entitlements_immutable
  BEFORE DELETE OR UPDATE ON public.offer_entitlements
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER offer_invoice_applications_immutable
  BEFORE DELETE OR UPDATE ON public.offer_invoice_applications
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER offer_ledger_entries_immutable
  BEFORE DELETE OR UPDATE ON public.offer_ledger_entries
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER outreach_deliveries_immutable
  BEFORE DELETE OR UPDATE ON public.outreach_deliveries
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER participant_preflight_records_immutable
  BEFORE DELETE OR UPDATE ON public.participant_preflight_records
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER preview_environments_immutable
  BEFORE DELETE OR UPDATE ON public.preview_environments
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER preview_readiness_results_immutable
  BEFORE DELETE OR UPDATE ON public.preview_readiness_results
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER preview_routes_notify_voice_sideband
  AFTER INSERT OR UPDATE OF state, readiness_state ON public.preview_routes
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_voice_sideband('interview_session_id');

CREATE TRIGGER product_signal_concerns_immutable
  BEFORE DELETE OR UPDATE ON public.product_signal_concerns
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER results_immutable
  BEFORE DELETE OR UPDATE ON public.results
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER routing_decisions_immutable
  BEFORE DELETE OR UPDATE ON public.routing_decisions
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER semantic_judgements_immutable
  BEFORE DELETE OR UPDATE ON public.semantic_judgements
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER server_health_reports_immutable
  BEFORE DELETE OR UPDATE ON public.server_health_reports
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER server_spans_immutable
  BEFORE DELETE OR UPDATE ON public.server_spans
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER session_readiness_notify_extension_control
  AFTER INSERT OR UPDATE OF readiness_revision ON public.session_readiness
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_extension_control('interview_session_id');

CREATE TRIGGER session_readiness_notify_voice_sideband
  AFTER INSERT OR UPDATE OF readiness_revision ON public.session_readiness
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_voice_sideband('interview_session_id');

CREATE TRIGGER telemetry_artifacts_immutable
  BEFORE DELETE OR UPDATE ON public.telemetry_artifacts
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER telemetry_credential_revocations_immutable
  BEFORE DELETE OR UPDATE ON public.telemetry_credential_revocations
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER telemetry_evidence_items_materialize_only
  BEFORE DELETE OR UPDATE ON public.telemetry_evidence_items
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_evidence_item_rewrite();

CREATE TRIGGER telemetry_identity_conflicts_immutable
  BEFORE DELETE OR UPDATE ON public.telemetry_identity_conflicts
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER telemetry_readiness_projections_immutable
  BEFORE DELETE OR UPDATE ON public.telemetry_readiness_projections
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER validation_criteria_immutable
  BEFORE DELETE OR UPDATE ON public.validation_criteria
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER validation_outcomes_immutable
  BEFORE DELETE OR UPDATE ON public.validation_outcomes
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER visual_command_dispatches_notify_extension_control
  AFTER INSERT OR UPDATE OF outcome ON public.visual_command_dispatches
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_extension_control('interview_session_id');

CREATE TRIGGER voice_playback_segments_immutable
  BEFORE DELETE OR UPDATE ON public.voice_playback_segments
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE TRIGGER voice_transcript_fragments_immutable
  BEFORE DELETE OR UPDATE ON public.voice_transcript_fragments
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

CREATE POLICY "case_file_amendments_member_select" ON "public"."case_file_amendments"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "case_files_member_select" ON "public"."case_files"
  FOR SELECT
  TO "authenticated"
  USING ((utrace.is_client_member(client_id) AND (stage = 'published'::public.case_file_stage)));

CREATE POLICY "client_members_member_select" ON "public"."client_members"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "clients_member_select" ON "public"."clients"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(id));

CREATE POLICY "clients_owner_update" ON "public"."clients"
  FOR UPDATE
  TO "authenticated"
  USING (utrace.is_client_owner(id))
  WITH CHECK (utrace.is_client_owner(id));

CREATE POLICY "configuration_releases_member_select" ON "public"."configuration_releases"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "customer_accounts_member_select" ON "public"."customer_accounts"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "deliveries_member_select" ON "public"."deliveries"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "extension_installations_self_select" ON "public"."extension_installations"
  FOR SELECT
  TO "authenticated"
  USING ((EXISTS ( SELECT 1
   FROM public.users u
  WHERE ((u.id = extension_installations.utrace_user_id) AND (u.auth_user_id = ( SELECT auth.uid() AS uid))))));

CREATE POLICY "extension_preferences_self_select" ON "public"."extension_preferences"
  FOR SELECT
  TO "authenticated"
  USING ((EXISTS ( SELECT 1
   FROM public.users u
  WHERE ((u.id = extension_preferences.utrace_user_id) AND (u.auth_user_id = ( SELECT auth.uid() AS uid))))));

CREATE POLICY "gate_decisions_member_select" ON "public"."gate_decisions"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "implementation_attempts_member_select" ON "public"."implementation_attempts"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "implementation_check_runs_member_select" ON "public"."implementation_check_runs"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "implementation_scope_checks_member_select" ON "public"."implementation_scope_checks"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "interview_sessions_authorized_select" ON "public"."interview_sessions"
  FOR SELECT
  TO "authenticated"
  USING ((utrace.is_client_member(client_id) OR utrace.is_participant(client_user_id)));

CREATE POLICY "offer_budgets_member_select" ON "public"."offer_budgets"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "offer_entitlements_member_select" ON "public"."offer_entitlements"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "offer_invoice_applications_member_select" ON "public"."offer_invoice_applications"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "offer_issuance_operations_member_select" ON "public"."offer_issuance_operations"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "offer_reservations_member_select" ON "public"."offer_reservations"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "outreach_attempts_member_select" ON "public"."outreach_attempts"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "outreach_offers_member_select" ON "public"."outreach_offers"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "preview_versions_member_select" ON "public"."preview_versions"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "product_overview_versions_member_select" ON "public"."product_overview_versions"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "product_signal_concerns_member_select" ON "public"."product_signal_concerns"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "product_signals_member_select" ON "public"."product_signals"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "session_projections_authorized_select" ON "public"."session_projections"
  FOR SELECT
  TO "authenticated"
  USING ((utrace.is_client_member(client_id) OR utrace.is_participant(client_user_id)));

CREATE POLICY "source_reports_member_select" ON "public"."source_reports"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "users_self_select" ON "public"."users"
  FOR SELECT
  TO "authenticated"
  USING ((auth_user_id = ( SELECT auth.uid() AS uid)));

CREATE POLICY "users_self_update" ON "public"."users"
  FOR UPDATE
  TO "authenticated"
  USING ((auth_user_id = ( SELECT auth.uid() AS uid)))
  WITH CHECK ((auth_user_id = ( SELECT auth.uid() AS uid)));

CREATE POLICY "validation_attempts_member_select" ON "public"."validation_attempts"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "validation_criteria_member_select" ON "public"."validation_criteria"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

CREATE POLICY "validation_outcomes_member_select" ON "public"."validation_outcomes"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

ALTER PUBLICATION "supabase_realtime" ADD TABLE "public"."session_projections";

COMMENT ON EXTENSION "pg_cron" IS 'Job scheduler for PostgreSQL';

COMMENT ON EXTENSION "pgmq" IS 'A lightweight message queue. Like AWS SQS and RSMQ but on Postgres.';

REVOKE ALL ON FUNCTION "utrace"."acquire_preview_allocation"(uuid) FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."enqueue_ingestion_job"(uuid, text, jsonb, text) FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."enqueue_ready_outbox"(integer) FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."expire_overdue_jobs"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."is_client_member"(uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION "utrace"."is_client_member"(uuid) TO "authenticated";

REVOKE ALL ON FUNCTION "utrace"."is_client_owner"(uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION "utrace"."is_client_owner"(uuid) TO "authenticated";

REVOKE ALL ON FUNCTION "utrace"."is_participant"(uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION "utrace"."is_participant"(uuid) TO "authenticated";

REVOKE ALL ON FUNCTION "utrace"."next_fencing_token"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."preview_warm_pool_shortfall"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."preview_warm_runtime_fresh"(timestamp WITH time zone) FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."reclaim_expired_job_leases"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."reject_evidence_item_rewrite"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."reject_mutation"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."requeue_stale_outbox"(interval) FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."rescan_stale_telemetry_readiness"(integer) FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."scan_prepared_workspace_releases"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."scan_preview_teardowns"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."scan_preview_warm_pools"() FROM PUBLIC;

REVOKE ALL ON FUNCTION "utrace"."uuid7"() FROM PUBLIC;

GRANT USAGE ON SCHEMA "utrace" TO "authenticated";

REVOKE ALL ON TABLE "public"."acceptance_criteria_agreements" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."acceptance_criteria_agreements" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."acceptance_criteria_agreements" TO "service_role";

REVOKE ALL ON TABLE "public"."artifacts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."artifacts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."artifacts" TO "service_role";

REVOKE ALL ON TABLE "public"."audit_records" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."audit_records" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."audit_records" TO "service_role";

REVOKE ALL ON TABLE "public"."browser_observations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_observations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_observations" TO "service_role";

REVOKE ALL ON TABLE "public"."browser_stream_gaps" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_stream_gaps" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_stream_gaps" TO "service_role";

REVOKE ALL ON TABLE "public"."browser_streams" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_streams" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_streams" TO "service_role";

REVOKE ALL ON TABLE "public"."case_file_amendments" FROM "authenticated";

GRANT SELECT ON TABLE "public"."case_file_amendments" TO "authenticated";

REVOKE ALL ON TABLE "public"."case_file_amendments" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."case_file_amendments" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."case_file_amendments" TO "service_role";

REVOKE ALL ON TABLE "public"."case_files" FROM "authenticated";

GRANT SELECT ON TABLE "public"."case_files" TO "authenticated";

REVOKE ALL ON TABLE "public"."case_files" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."case_files" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."case_files" TO "service_role";

REVOKE ALL ON TABLE "public"."client_members" FROM "authenticated";

GRANT SELECT ON TABLE "public"."client_members" TO "authenticated";

REVOKE ALL ON TABLE "public"."client_members" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."client_members" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."client_members" TO "service_role";

REVOKE ALL ON TABLE "public"."client_users" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."client_users" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."client_users" TO "service_role";

REVOKE ALL ON TABLE "public"."clients" FROM "authenticated";

GRANT SELECT, UPDATE ON TABLE "public"."clients" TO "authenticated";

REVOKE ALL ON TABLE "public"."clients" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."clients" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."clients" TO "service_role";

REVOKE ALL ON TABLE "public"."code_investigation_findings" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."code_investigation_findings" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."code_investigation_findings" TO "service_role";

REVOKE ALL ON TABLE "public"."commands" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."commands" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."commands" TO "service_role";

REVOKE ALL ON TABLE "public"."configuration_releases" FROM "authenticated";

GRANT SELECT ON TABLE "public"."configuration_releases" TO "authenticated";

REVOKE ALL ON TABLE "public"."configuration_releases" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."configuration_releases" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."configuration_releases" TO "service_role";

REVOKE ALL ON TABLE "public"."context_bundle_invalidations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundle_invalidations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundle_invalidations" TO "service_role";

REVOKE ALL ON TABLE "public"."context_bundle_items" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundle_items" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundle_items" TO "service_role";

REVOKE ALL ON TABLE "public"."context_bundles" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundles" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."context_bundles" TO "service_role";

REVOKE ALL ON TABLE "public"."correlation_projections" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."correlation_projections" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."correlation_projections" TO "service_role";

REVOKE ALL ON TABLE "public"."customer_accounts" FROM "authenticated";

GRANT SELECT ON TABLE "public"."customer_accounts" TO "authenticated";

REVOKE ALL ON TABLE "public"."customer_accounts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."customer_accounts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."customer_accounts" TO "service_role";

REVOKE ALL ON TABLE "public"."customer_memberships" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."customer_memberships" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."customer_memberships" TO "service_role";

REVOKE ALL ON TABLE "public"."deliveries" FROM "authenticated";

GRANT SELECT ON TABLE "public"."deliveries" TO "authenticated";

REVOKE ALL ON TABLE "public"."deliveries" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."deliveries" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."deliveries" TO "service_role";

REVOKE ALL ON TABLE "public"."dependency_holds" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."dependency_holds" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."dependency_holds" TO "service_role";

REVOKE ALL ON TABLE "public"."environment_leases" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."environment_leases" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."environment_leases" TO "service_role";

REVOKE ALL ON TABLE "public"."events" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."events" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."events" TO "service_role";

REVOKE ALL ON TABLE "public"."evidence_records" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."evidence_records" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."evidence_records" TO "service_role";

REVOKE ALL ON TABLE "public"."evidence_windows" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."evidence_windows" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."evidence_windows" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_binding_offers" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_binding_offers" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_binding_offers" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_bindings" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_bindings" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_bindings" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_control_frames" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_control_frames" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_control_frames" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_control_streams" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_control_streams" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_control_streams" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_installations" FROM "authenticated";

GRANT SELECT ON TABLE "public"."extension_installations" TO "authenticated";

REVOKE ALL ON TABLE "public"."extension_installations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_installations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_installations" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_link_codes" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_link_codes" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_link_codes" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_possession_proofs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_possession_proofs" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_possession_proofs" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_preferences" FROM "authenticated";

GRANT SELECT ON TABLE "public"."extension_preferences" TO "authenticated";

REVOKE ALL ON TABLE "public"."extension_preferences" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_preferences" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_preferences" TO "service_role";

REVOKE ALL ON TABLE "public"."flow_classification_records" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."flow_classification_records" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."flow_classification_records" TO "service_role";

REVOKE ALL ON TABLE "public"."gate_decisions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."gate_decisions" TO "authenticated";

REVOKE ALL ON TABLE "public"."gate_decisions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."gate_decisions" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."gate_decisions" TO "service_role";

REVOKE ALL ON TABLE "public"."implementation_attempts" FROM "authenticated";

GRANT SELECT ON TABLE "public"."implementation_attempts" TO "authenticated";

REVOKE ALL ON TABLE "public"."implementation_attempts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_attempts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_attempts" TO "service_role";

REVOKE ALL ON TABLE "public"."implementation_check_runs" FROM "authenticated";

GRANT SELECT ON TABLE "public"."implementation_check_runs" TO "authenticated";

REVOKE ALL ON TABLE "public"."implementation_check_runs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_check_runs" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_check_runs" TO "service_role";

REVOKE ALL ON TABLE "public"."implementation_scope_checks" FROM "authenticated";

GRANT SELECT ON TABLE "public"."implementation_scope_checks" TO "authenticated";

REVOKE ALL ON TABLE "public"."implementation_scope_checks" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_scope_checks" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."implementation_scope_checks" TO "service_role";

REVOKE ALL ON TABLE "public"."inbound_direct_messages" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."inbound_direct_messages" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."inbound_direct_messages" TO "service_role";

REVOKE ALL ON TABLE "public"."inbox" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."inbox" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."inbox" TO "service_role";

REVOKE ALL ON TABLE "public"."ingestion_failures" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_failures" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_failures" TO "service_role";

REVOKE ALL ON TABLE "public"."ingestion_receipt_stages" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_receipt_stages" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_receipt_stages" TO "service_role";

REVOKE ALL ON TABLE "public"."ingestion_receipts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_receipts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."ingestion_receipts" TO "service_role";

REVOKE ALL ON TABLE "public"."intake_author_attributions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_author_attributions" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_author_attributions" TO "service_role";

REVOKE ALL ON TABLE "public"."intake_batches" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_batches" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_batches" TO "service_role";

REVOKE ALL ON TABLE "public"."intake_items" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_items" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_items" TO "service_role";

REVOKE ALL ON TABLE "public"."intake_messages" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_messages" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."intake_messages" TO "service_role";

REVOKE ALL ON TABLE "public"."interview_sessions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."interview_sessions" TO "authenticated";

REVOKE ALL ON TABLE "public"."interview_sessions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."interview_sessions" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."interview_sessions" TO "service_role";

REVOKE ALL ON TABLE "public"."invitations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."invitations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."invitations" TO "service_role";

REVOKE ALL ON TABLE "public"."job_runs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."job_runs" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."job_runs" TO "service_role";

REVOKE ALL ON TABLE "public"."jobs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."jobs" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."jobs" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_budgets" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_budgets" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_budgets" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_budgets" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_budgets" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_entitlements" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_entitlements" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_entitlements" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_entitlements" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_entitlements" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_invoice_applications" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_invoice_applications" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_invoice_applications" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_invoice_applications" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_invoice_applications" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_issuance_operations" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_issuance_operations" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_issuance_operations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_issuance_operations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_issuance_operations" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_ledger_entries" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_ledger_entries" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_ledger_entries" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_reservations" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_reservations" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_reservations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_reservations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_reservations" TO "service_role";

REVOKE ALL ON TABLE "public"."outbox" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outbox" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outbox" TO "service_role";

REVOKE ALL ON TABLE "public"."outreach_attempts" FROM "authenticated";

GRANT SELECT ON TABLE "public"."outreach_attempts" TO "authenticated";

REVOKE ALL ON TABLE "public"."outreach_attempts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_attempts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_attempts" TO "service_role";

REVOKE ALL ON TABLE "public"."outreach_deliveries" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_deliveries" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_deliveries" TO "service_role";

REVOKE ALL ON TABLE "public"."outreach_offers" FROM "authenticated";

GRANT SELECT ON TABLE "public"."outreach_offers" TO "authenticated";

REVOKE ALL ON TABLE "public"."outreach_offers" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_offers" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."outreach_offers" TO "service_role";

REVOKE ALL ON TABLE "public"."participant_preflight_records" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."participant_preflight_records" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."participant_preflight_records" TO "service_role";

REVOKE ALL ON TABLE "public"."prepared_workspaces" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."prepared_workspaces" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."prepared_workspaces" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_environments" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_environments" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_environments" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_handoff_codes" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_handoff_codes" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_handoff_codes" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_readiness_results" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_readiness_results" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_readiness_results" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_routes" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_routes" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_routes" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_runtime_allocations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_runtime_allocations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_runtime_allocations" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_runtime_templates" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_runtime_templates" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_runtime_templates" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_versions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."preview_versions" TO "authenticated";

REVOKE ALL ON TABLE "public"."preview_versions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_versions" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_versions" TO "service_role";

REVOKE ALL ON TABLE "public"."product_overview_versions" FROM "authenticated";

GRANT SELECT ON TABLE "public"."product_overview_versions" TO "authenticated";

REVOKE ALL ON TABLE "public"."product_overview_versions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_overview_versions" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_overview_versions" TO "service_role";

REVOKE ALL ON TABLE "public"."product_signal_concerns" FROM "authenticated";

GRANT SELECT ON TABLE "public"."product_signal_concerns" TO "authenticated";

REVOKE ALL ON TABLE "public"."product_signal_concerns" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_signal_concerns" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_signal_concerns" TO "service_role";

REVOKE ALL ON TABLE "public"."product_signals" FROM "authenticated";

GRANT SELECT ON TABLE "public"."product_signals" TO "authenticated";

REVOKE ALL ON TABLE "public"."product_signals" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_signals" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."product_signals" TO "service_role";

REVOKE ALL ON TABLE "public"."provider_refs" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."provider_refs" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."provider_refs" TO "service_role";

REVOKE ALL ON TABLE "public"."results" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."results" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."results" TO "service_role";

REVOKE ALL ON TABLE "public"."routing_decisions" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."routing_decisions" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."routing_decisions" TO "service_role";

REVOKE ALL ON TABLE "public"."semantic_judgements" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."semantic_judgements" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."semantic_judgements" TO "service_role";

REVOKE ALL ON TABLE "public"."server_control_commands" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_control_commands" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_control_commands" TO "service_role";

REVOKE ALL ON TABLE "public"."server_health_reports" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_health_reports" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_health_reports" TO "service_role";

REVOKE ALL ON TABLE "public"."server_spans" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_spans" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_spans" TO "service_role";

REVOKE ALL ON TABLE "public"."server_streams" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_streams" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."server_streams" TO "service_role";

REVOKE ALL ON TABLE "public"."session_projections" FROM "authenticated";

GRANT SELECT ON TABLE "public"."session_projections" TO "authenticated";

REVOKE ALL ON TABLE "public"."session_projections" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."session_projections" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."session_projections" TO "service_role";

REVOKE ALL ON TABLE "public"."session_readiness" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."session_readiness" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."session_readiness" TO "service_role";

REVOKE ALL ON TABLE "public"."source_reports" FROM "authenticated";

GRANT SELECT ON TABLE "public"."source_reports" TO "authenticated";

REVOKE ALL ON TABLE "public"."source_reports" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."source_reports" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."source_reports" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_activations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_activations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_activations" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_artifacts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_artifacts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_artifacts" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_credential_revocations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_credential_revocations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_credential_revocations" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_credentials" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_credentials" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_credentials" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_evidence_items" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_evidence_items" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_evidence_items" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_evidence_windows" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_evidence_windows" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_evidence_windows" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_identity_conflicts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_identity_conflicts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_identity_conflicts" TO "service_role";

REVOKE ALL ON TABLE "public"."telemetry_readiness_projections" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_readiness_projections" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."telemetry_readiness_projections" TO "service_role";

REVOKE ALL ON TABLE "public"."users" FROM "authenticated";

GRANT SELECT, UPDATE ON TABLE "public"."users" TO "authenticated";

REVOKE ALL ON TABLE "public"."users" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."users" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."users" TO "service_role";

REVOKE ALL ON TABLE "public"."validation_attempts" FROM "authenticated";

GRANT SELECT ON TABLE "public"."validation_attempts" TO "authenticated";

REVOKE ALL ON TABLE "public"."validation_attempts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_attempts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_attempts" TO "service_role";

REVOKE ALL ON TABLE "public"."validation_criteria" FROM "authenticated";

GRANT SELECT ON TABLE "public"."validation_criteria" TO "authenticated";

REVOKE ALL ON TABLE "public"."validation_criteria" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_criteria" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_criteria" TO "service_role";

REVOKE ALL ON TABLE "public"."validation_outcomes" FROM "authenticated";

GRANT SELECT ON TABLE "public"."validation_outcomes" TO "authenticated";

REVOKE ALL ON TABLE "public"."validation_outcomes" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_outcomes" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."validation_outcomes" TO "service_role";

REVOKE ALL ON TABLE "public"."visual_command_dispatches" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."visual_command_dispatches" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."visual_command_dispatches" TO "service_role";

REVOKE ALL ON TABLE "public"."voice_connections" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_connections" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_connections" TO "service_role";

REVOKE ALL ON TABLE "public"."voice_delegations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_delegations" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_delegations" TO "service_role";

REVOKE ALL ON TABLE "public"."voice_playback_segments" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_playback_segments" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_playback_segments" TO "service_role";

REVOKE ALL ON TABLE "public"."voice_transcript_fragments" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_transcript_fragments" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."voice_transcript_fragments" TO "service_role";

SELECT cron.schedule_in_database('utrace-lease-scan', '* * * * *', 'select utrace.reclaim_expired_job_leases(), utrace.expire_overdue_jobs();', 'postgres', NULL, true);

SELECT
  cron.schedule_in_database('utrace-outbox-scan', '* * * * *', 'select utrace.enqueue_ready_outbox(500), utrace.requeue_stale_outbox(interval ''5 minutes'');', 'postgres', NULL,
  true);

SELECT cron.schedule_in_database('utrace-prepared-workspace-release-scan', '* * * * *', 'select utrace.scan_prepared_workspace_releases();', 'postgres', NULL, true);

SELECT cron.schedule_in_database('utrace-preview-pool-scan', '* * * * *', 'select utrace.scan_preview_warm_pools();', 'postgres', NULL, true);

SELECT cron.schedule_in_database('utrace-preview-teardown-scan', '* * * * *', 'select utrace.scan_preview_teardowns();', 'postgres', NULL, true);

SELECT cron.schedule_in_database('utrace-telemetry-readiness-scan', '* * * * *', 'select utrace.rescan_stale_telemetry_readiness(200);', 'postgres', NULL, true);

SELECT pgmq.create('utrace_outbox');
