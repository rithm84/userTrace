-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."intake_items"
  ADD CONSTRAINT "intake_items_product_signal_fk" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);
