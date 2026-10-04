-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."interview_sessions"
  ADD CONSTRAINT "interview_sessions_current_preview_route_id_fkey" FOREIGN KEY (current_preview_route_id) REFERENCES public.preview_routes(id);

ALTER TABLE "public"."interview_sessions"
  ADD CONSTRAINT "interview_sessions_allocation_fk" FOREIGN KEY (preview_runtime_allocation_id) REFERENCES public.preview_runtime_allocations(id);
