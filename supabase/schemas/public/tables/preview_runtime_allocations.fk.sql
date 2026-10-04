-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."preview_runtime_allocations"
  ADD CONSTRAINT "preview_runtime_allocations_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."preview_runtime_allocations"
  ADD CONSTRAINT "preview_runtime_allocations_version_fk" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id);
