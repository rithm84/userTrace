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
  CONSTRAINT "offer_issuance_operations_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id),
  CONSTRAINT "offer_issuance_operations_command_fk" FOREIGN KEY (command_id) REFERENCES public.commands(id),
  CONSTRAINT "offer_issuance_operations_offer_entitlement_id_fkey" FOREIGN KEY (offer_entitlement_id) REFERENCES public.offer_entitlements(id),
  CONSTRAINT "offer_issuance_operations_offer_entitlement_id_key" UNIQUE (offer_entitlement_id),
  CONSTRAINT "offer_issuance_operations_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."offer_issuance_operations"
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE "public"."offer_issuance_operations"
  ADD COLUMN "state" public.offer_issuance_state NOT NULL;

ALTER TABLE "public"."offer_issuance_operations"
  ADD CONSTRAINT "offer_issuance_operations_blocked_carries_reason" CHECK (((state <> 'blocked'::public.offer_issuance_state) OR (blocked_reason IS NOT NULL)));

ALTER TABLE "public"."offer_issuance_operations"
  ADD CONSTRAINT "offer_issuance_operations_issued_is_verified" CHECK (((state <> 'issued'::public.offer_issuance_state) OR ((provider_transaction_ref IS
    NOT NULL) AND (verified_at IS NOT NULL))));

CREATE UNIQUE INDEX offer_issuance_operations_idempotency_key_idx ON public.offer_issuance_operations USING btree (client_id, idempotency_key);

CREATE POLICY "offer_issuance_operations_member_select" ON "public"."offer_issuance_operations"
  FOR SELECT
  TO "authenticated"
  USING (utrace.is_client_member(client_id));

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_issuance_operations" TO "service_role";

REVOKE ALL ON TABLE "public"."offer_issuance_operations" FROM "authenticated";

GRANT SELECT ON TABLE "public"."offer_issuance_operations" TO "authenticated";

REVOKE ALL ON TABLE "public"."offer_issuance_operations" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."offer_issuance_operations" TO "postgres";

REVOKE ALL ON TABLE "public"."offer_issuance_operations" FROM "anon";
