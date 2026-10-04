-- Foreign keys in a cross-table reference cycle are split out of their
-- table's file: each file loads atomically, so keeping them inline would
-- deadlock the loader (every file would need a table another pending file
-- creates). These statements apply once all referenced tables exist.

ALTER TABLE "public"."implementation_attempts"
  ADD CONSTRAINT "implementation_attempts_gate_decision_id_fkey" FOREIGN KEY (gate_decision_id) REFERENCES public.gate_decisions(id);
