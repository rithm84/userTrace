-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."case_files"
  ADD CONSTRAINT "case_files_delivery_fk" FOREIGN KEY (delivery_id) REFERENCES public.deliveries(id);
