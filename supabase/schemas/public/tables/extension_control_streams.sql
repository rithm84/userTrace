CREATE TABLE "public"."extension_control_streams" (
  "extension_installation_id" uuid                     NOT NULL,
  "last_sequence"             bigint                   NOT NULL DEFAULT 0,
  "acknowledged_sequence"     bigint                   NOT NULL DEFAULT 0,
  "connected_at"              timestamp with time zone,
  "disconnected_at"           timestamp with time zone,
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  "schema_version"            smallint                 NOT NULL DEFAULT 1,
  CONSTRAINT "extension_control_streams_ordered" CHECK ((acknowledged_sequence <= last_sequence)),
  CONSTRAINT "extension_control_streams_pkey" PRIMARY KEY (extension_installation_id),
  CONSTRAINT "extension_control_streams_extension_installation_id_fkey" FOREIGN KEY (extension_installation_id) REFERENCES public.extension_installations(id)
);

ALTER TABLE "public"."extension_control_streams"
  ENABLE ROW LEVEL SECURITY;

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_control_streams" TO "service_role";

REVOKE ALL ON TABLE "public"."extension_control_streams" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."extension_control_streams" TO "postgres";

REVOKE ALL ON TABLE "public"."extension_control_streams" FROM "anon", "authenticated";
