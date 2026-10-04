-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."product_signals"
  ADD CONSTRAINT "product_signals_intake_item_id_fkey" FOREIGN KEY (intake_item_id) REFERENCES public.intake_items(id);
