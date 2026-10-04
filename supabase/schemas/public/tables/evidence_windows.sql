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
  CONSTRAINT "evidence_windows_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "evidence_windows_ordered" CHECK ((ends_at >= starts_at)),
  CONSTRAINT "evidence_windows_pkey" PRIMARY KEY (id),
  CONSTRAINT "evidence_windows_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id)
);

ALTER TABLE "public"."evidence_windows"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX evidence_windows_signal_idx ON public.evidence_windows USING btree (product_signal_id);

CREATE TRIGGER evidence_windows_immutable
  BEFORE DELETE OR UPDATE ON public.evidence_windows
  FOR EACH ROW
  EXECUTE FUNCTION utrace.reject_mutation();

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."evidence_windows" TO "service_role";

REVOKE ALL ON TABLE "public"."evidence_windows" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."evidence_windows" TO "postgres";

REVOKE ALL ON TABLE "public"."evidence_windows" FROM "anon", "authenticated";
