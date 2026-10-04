-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."preview_routes"
  ADD CONSTRAINT "preview_routes_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."preview_routes"
  ADD CONSTRAINT "preview_routes_preview_runtime_allocation_id_fkey" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);

ALTER TABLE "public"."preview_routes"
  ADD CONSTRAINT "preview_routes_preview_version_id_fkey" FOREIGN KEY (preview_version_id) REFERENCES public.preview_versions(id);
