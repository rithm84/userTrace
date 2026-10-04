-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."extension_binding_offers"
  ADD CONSTRAINT "extension_binding_offers_consumed_by_binding_id_fkey" FOREIGN KEY (consumed_by_binding_id) REFERENCES public.extension_bindings(id);
