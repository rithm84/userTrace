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
  CONSTRAINT "preview_routes_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "preview_routes_pkey" PRIMARY KEY (id),
  CONSTRAINT "preview_routes_port_range" CHECK (((application_port >= 1) AND (application_port <= 65535))),
  CONSTRAINT "preview_routes_public_preview_host_route_revision_key" UNIQUE (public_preview_host, route_revision),
  CONSTRAINT "preview_routes_revision_positive" CHECK ((route_revision >= 1)),
  CONSTRAINT "preview_routes_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."preview_routes"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."preview_routes"
  ADD COLUMN "readiness_state" public.preview_route_readiness NOT NULL;

ALTER TABLE "public"."preview_routes"
  ADD COLUMN "state" public.preview_route_state NOT NULL;

CREATE UNIQUE INDEX preview_routes_one_active_per_host ON public.preview_routes USING btree (public_preview_host)
  WHERE (state = 'active'::public.preview_route_state);

CREATE INDEX preview_routes_session_idx ON public.preview_routes USING btree (interview_session_id, state);

CREATE TRIGGER preview_routes_notify_voice_sideband
  AFTER INSERT OR UPDATE OF state, readiness_state ON public.preview_routes
  FOR EACH ROW
  EXECUTE FUNCTION utrace.notify_voice_sideband('interview_session_id');

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_routes" TO "service_role";

REVOKE ALL ON TABLE "public"."preview_routes" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."preview_routes" TO "postgres";

REVOKE ALL ON TABLE "public"."preview_routes" FROM "anon", "authenticated";
