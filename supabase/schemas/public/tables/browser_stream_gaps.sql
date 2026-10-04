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
  CONSTRAINT "browser_stream_gaps_range_ordered" CHECK ((last_source_sequence >= first_source_sequence)),
  CONSTRAINT "browser_stream_gaps_browser_stream_id_fkey" FOREIGN KEY (browser_stream_id) REFERENCES public.browser_streams(id),
  CONSTRAINT "browser_stream_gaps_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "browser_stream_gaps_ingestion_receipt_id_fkey" FOREIGN KEY (ingestion_receipt_id) REFERENCES public.ingestion_receipts(id)
);

ALTER TABLE "public"."browser_stream_gaps"
  ENABLE ROW LEVEL SECURITY;

CREATE INDEX browser_stream_gaps_stream_idx ON public.browser_stream_gaps USING btree (browser_stream_id, first_source_sequence);

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_stream_gaps" TO "service_role";

REVOKE ALL ON TABLE "public"."browser_stream_gaps" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."browser_stream_gaps" TO "postgres";

REVOKE ALL ON TABLE "public"."browser_stream_gaps" FROM "anon", "authenticated";
