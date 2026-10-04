-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."extension_bindings"
  ADD CONSTRAINT "extension_bindings_offer_fk" FOREIGN KEY (offer_token_id) REFERENCES public.extension_binding_offers(id);
