ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "spare_ready_at" timestamp WITH time zone;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "staged_files" jsonb;

ALTER TABLE "public"."preview_runtime_allocations"
  ADD COLUMN "staged_originals" jsonb;
